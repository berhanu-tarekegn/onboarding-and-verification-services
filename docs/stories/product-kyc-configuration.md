# Story: Product & KYC Configuration Management

## Context

The Onboarding & Verification Platform supports a multi-tenant architecture where each tenant
(e.g. bank, fintech, institution) may offer different onboarding products — Savings Account,
Wallet, SME Account, etc.

Each product can have one or more KYC requirements configured against it. In this platform a
KYC configuration is represented by a `TenantTemplate` — a versioned, per-tenant form schema
and rules configuration record. A `ProductTemplateLink` joins a product to one or more tenant
templates, each flagged as mandatory or optional and ordered for display.

Two onboarding flows coexist without conflict:

| Flow | Description |
|---|---|
| General onboarding | A `TenantTemplate` is used directly on a `Submission` — no product association required |
| Product-specific onboarding | A `Product` links to one or more `TenantTemplate`s; the submission stores `product_id` for traceability |

---

## Summary

**As a** Tenant Admin
**I want** to create and manage products and their KYC (template) configuration
**So that** I can link products with specific KYC/template requirements and collect relevant
data per product during onboarding

---

## Scope

- **In scope**: Product lifecycle (Draft → Active → Inactive), template-to-product linking,
  dynamic KYC config resolution, and optional product traceability on submissions.
- **Out of scope**: Authentication/authorisation implementation, baseline template management,
  submission workflow execution beyond storing `product_id`.

---

## Assumptions

- Each product belongs to exactly one tenant (enforced by schema-based isolation via `search_path`).
- One tenant can have multiple products.
- A `TenantTemplate` can be linked to many products or used independently — templates are not
  exclusively owned by a product.
- Configuration changes (template re-linking) must not affect already-submitted onboarding cases.
  Submissions lock to a specific `template_version_id` at creation time (existing behaviour).
- Product versioning (`version` integer) is stored on the product record and referenced during
  onboarding for audit purposes.

---

## Acceptance Criteria

### AC1 — Product Creation

**Given** a Tenant Admin is authenticated (valid `X-Tenant-ID` header)
**When** they call `POST /api/v1/products` with a name and product code
**Then** the system creates a product record in the tenant's schema with:

| Field | Value |
|---|---|
| `id` | UUID7, auto-generated, immutable |
| `name` | As provided |
| `product_code` | As provided — unique within the tenant |
| `description` | Optional |
| `status` | `DRAFT` |
| `version` | `1` |
| Audit fields | `created_at`, `updated_at`, `created_by`, `updated_by` auto-populated |

**And** attempting to create a second product with the same `product_code` under the same
tenant returns HTTP 409 Conflict.

**Verification:**
- `POST /api/v1/products` with valid payload returns 201 with the product record
- `product_code` uniqueness constraint is enforced at the database level
- `status` defaults to `DRAFT`, `version` defaults to `1`
- Audit fields are populated automatically from request context

---

### AC2 — Product KYC Template Linking

**Given** a product exists (any status)
**When** a Tenant Admin calls `POST /api/v1/products/{id}/template-links` with a `template_id`
**Then** the system stores a `ProductTemplateLink` record with:

| Field | Value |
|---|---|
| `product_id` | ID of the product |
| `template_id` | ID of the linked `TenantTemplate` |
| `is_mandatory` | Boolean (defaults to `true`) |
| `display_order` | Integer for ordering KYC steps (defaults to `0`) |

**And** the referenced `TenantTemplate` must exist and be active (`is_active = true`) within
the same tenant, otherwise HTTP 404 is returned.
**And** linking the same template to the same product twice returns HTTP 409 Conflict.

**Verification:**
- `POST /api/v1/products/{id}/template-links` returns 201 with the link record
- Linking an inactive or non-existent template returns 404
- Duplicate link returns 409
- `PATCH /api/v1/products/{id}/template-links/{link_id}` allows updating `is_mandatory`
  and `display_order`
- `DELETE /api/v1/products/{id}/template-links/{link_id}` removes the link
- `GET /api/v1/products/{id}/template-links` lists all links for the product

---

### AC3 — Product Activation

**Given** a product in `DRAFT` status with at least one template link
**When** a Tenant Admin calls `POST /api/v1/products/{id}/activate`
**Then** the product status transitions to `ACTIVE`
**And** the product becomes available for onboarding sessions

**And** calling `GET /api/v1/products/{id}/kyc-config` on an `ACTIVE` product returns
the product metadata plus the fully resolved KYC configuration for each linked template
(merged baseline + tenant overrides).

**And** activating a product that has zero template links returns HTTP 422 Unprocessable Entity.

**Verification:**
- `POST /api/v1/products/{id}/activate` returns 200 with `status = ACTIVE`
- Activation with no template links returns 422
- `GET /api/v1/products/{id}/kyc-config` returns product details + per-template resolved
  `form_schema` and `rules_config`
- `POST /api/v1/products/{id}/deactivate` transitions `ACTIVE → INACTIVE`
- `INACTIVE` products can be re-activated

---

### AC4 — Tenant Isolation

**Given** multiple tenants exist
**When** a Tenant Admin queries products or template links
**Then** they only see records belonging to their own tenant (enforced by `search_path`)

**And** passing a `product_id` that belongs to a different tenant returns HTTP 404 (not a
403 — the product simply does not exist in the caller's schema).

**Verification:**
- Creating products under Tenant A and querying under Tenant B returns an empty list
- Attempting to activate or link templates to a product from another tenant returns 404
- No explicit tenant filter is needed in queries — schema isolation enforces this automatically

---

### AC5 — Dynamic KYC Config Resolution

**Given** an onboarding session starts for a selected product
**When** the system calls `GET /api/v1/products/{id}/kyc-config`
**Then** the response includes:

```json
{
  "product": { "id": "...", "name": "...", "product_code": "...", "status": "ACTIVE", ... },
  "kyc_requirements": [
    {
      "link": { "template_id": "...", "is_mandatory": true, "display_order": 0 },
      "template": { "id": "...", "name": "..." },
      "form_schema": { ... },
      "rules_config": { ... },
      "baseline_version": { "id": "...", "version_tag": "..." }
    }
  ]
}
```

**And** items in `kyc_requirements` are ordered by `display_order` ascending.
**And** for templates that extend a baseline, `form_schema` and `rules_config` reflect the
merged result (baseline + tenant overrides) using the template's current active version.
**And** calling this endpoint on a `DRAFT` or `INACTIVE` product returns HTTP 422.

**Verification:**
- Response structure matches the schema above
- `kyc_requirements` are sorted by `display_order`
- Templates with a baseline extension show merged configuration, not raw overrides
- Calling on a non-ACTIVE product returns 422
- Calling on a product with no active template version still returns the product, with
  `form_schema: {}` and `rules_config: {}` for that entry

---

### AC6 — Product Traceability on Submissions

**Given** an onboarding session is initiated for a specific product
**When** a `Submission` is created with an optional `product_id` field
**Then** the submission record stores the `product_id` reference

**And** submissions created without a `product_id` (general onboarding) are unaffected —
the field is nullable and optional.
**And** if the referenced `product_id` does not exist in the tenant schema, HTTP 404 is
returned.

**Verification:**
- `POST /api/v1/submissions` with `product_id` stores the reference on the record
- `GET /api/v1/submissions/{id}` returns `product_id` in the response
- `POST /api/v1/submissions` without `product_id` continues to work (backward compatible)
- `GET /api/v1/submissions` can be filtered by `product_id` (optional query param)

---

## Sub-tasks

### Subtask 1 — Product Model & Migration

**Goal:** Define the `Product` and `ProductTemplateLink` database models and provision them
in every tenant schema via Alembic.

**Scope:**
- Create `app/models/tenant/product.py`:
  - `ProductStatus` enum (`DRAFT`, `ACTIVE`, `INACTIVE`)
  - `Product` model (`products` table) with `id`, `name`, `product_code`, `description`,
    `status`, `version`, audit fields, and relationship to `ProductTemplateLink`
  - `ProductTemplateLink` model (`product_template_links` table) with `product_id`,
    `template_id`, `is_mandatory`, `display_order`
- Create `alembic/versions/004_product_tables.py`:
  - Runs only when `tenant_schema` x-arg is set
  - Creates `products` table with `UNIQUE(product_code)` constraint
  - Creates `product_template_links` table with FK to `products.id` (CASCADE) and
    `tenant_templates.id` (RESTRICT)
  - Adds `UNIQUE(product_id, template_id)` on `product_template_links`
  - Reversible `downgrade()`

**Acceptance Criteria:**
- Migration runs cleanly on a fresh tenant schema
- Migration rolls back cleanly
- `UNIQUE(product_code)` and `UNIQUE(product_id, template_id)` constraints exist in DB
- `Product` and `ProductTemplateLink` SQLModel classes match the migration columns exactly

---

### Subtask 2 — Product CRUD API

**Goal:** Implement the full create/read/update/delete API for products.

**Scope:**
- Create `app/schemas/products/product.py` with `ProductCreate`, `ProductUpdate`,
  `ProductRead`, `ProductReadWithTemplateLinks`
- Create `app/services/products/service.py` with:
  - `create_product` — validates unique `product_code`, creates with `status=DRAFT`, `version=1`
  - `list_products` — optional `status` filter query param
  - `get_product` — raises 404 if not found
  - `update_product` — blocks `product_code` change if `ACTIVE`
  - `delete_product` — blocks if `ACTIVE` (returns 409)
- Create `app/routes/products/routes.py` with:
  - `POST /products` → 201
  - `GET /products` → 200 (list, with optional `?status=` filter)
  - `GET /products/{id}` → 200 (with template links included)
  - `PATCH /products/{id}` → 200
  - `DELETE /products/{id}` → 204

**Acceptance Criteria:**
- All 5 endpoints return correct status codes and response schemas
- `product_code` duplicate returns 409
- Updating `product_code` on an `ACTIVE` product returns 409
- Deleting an `ACTIVE` product returns 409
- All routes require `X-Tenant-ID` header (validated via `get_tenant_session`)

---

### Subtask 3 — Product Template Linking API

**Goal:** Implement the API to link/unlink `TenantTemplate`s to a product and manage link metadata.

**Scope:**
- Add to `app/schemas/products/product.py`: `ProductTemplateLinkCreate`, `ProductTemplateLinkRead`,
  `ProductTemplateLinkUpdate`
- Add to `app/services/products/service.py`:
  - `add_template_link` — validates template exists and `is_active=True`; catches duplicate FK
  - `list_template_links` — ordered by `display_order`
  - `get_template_link` — 404 if not found
  - `update_template_link` — allows changing `is_mandatory` and `display_order`
  - `remove_template_link`
- Add to `app/routes/products/routes.py`:
  - `POST /products/{id}/template-links` → 201
  - `GET /products/{id}/template-links` → 200
  - `PATCH /products/{id}/template-links/{link_id}` → 200
  - `DELETE /products/{id}/template-links/{link_id}` → 204

**Acceptance Criteria:**
- Linking a non-existent or inactive template returns 404
- Duplicate link returns 409
- `GET /products/{id}/template-links` returns links ordered by `display_order`
- Updating a link's `is_mandatory` or `display_order` is reflected immediately
- Deleting a link does not affect the underlying `TenantTemplate`

---

### Subtask 4 — Product Lifecycle (Activate / Deactivate)

**Goal:** Implement the state machine transitions for product status.

**Scope:**
- Add to `app/services/products/service.py`:
  - `activate_product` — validates `status=DRAFT` or `INACTIVE`; validates at least one
    template link exists; transitions to `ACTIVE`
  - `deactivate_product` — validates `status=ACTIVE`; transitions to `INACTIVE`
- Add to `app/routes/products/routes.py`:
  - `POST /products/{id}/activate` → 200
  - `POST /products/{id}/deactivate` → 200

**State machine:**

```
[create] → DRAFT → (activate) → ACTIVE → (deactivate) → INACTIVE
                                    ↑                         |
                                    └──────── (activate) ─────┘
DRAFT → (delete) → [deleted]
```

**Acceptance Criteria:**
- Activating with zero template links returns 422
- Activating an already-ACTIVE product returns 422
- Deactivating a DRAFT product returns 422
- `INACTIVE` product can be re-activated (back to `ACTIVE`)
- State transitions are reflected immediately in `GET /products/{id}`

---

### Subtask 5 — KYC Config Resolution Endpoint

**Goal:** Implement the `GET /products/{id}/kyc-config` endpoint that dynamically resolves
and returns all KYC requirements for an active product.

**Scope:**
- Add to `app/schemas/products/product.py`: `ProductKycConfigRead` (product + list of
  resolved template configs)
- Add to `app/services/products/service.py`:
  - `get_product_kyc_config` — validates product is `ACTIVE`; iterates `template_links`
    ordered by `display_order`; for each link calls the existing
    `get_tenant_template_with_merged_config` from `app/services/tenant_templates/service.py`
    to get the fully merged form schema and rules config
- Add to `app/routes/products/routes.py`:
  - `GET /products/{id}/kyc-config` → 200

**Acceptance Criteria:**
- Calling on a non-`ACTIVE` product returns 422
- `kyc_requirements` list is ordered by `display_order` ascending
- Each item includes: link metadata, template metadata, resolved `form_schema`,
  resolved `rules_config`, and `baseline_version` info (if applicable)
- Templates without an active version return `form_schema: {}` and `rules_config: {}`
  rather than erroring
- Response is consistent with the schema defined in AC5

---

### Subtask 6 — Submission Product Traceability

**Goal:** Add an optional `product_id` field to `Submission` so product-specific onboarding
sessions are traceable without breaking existing general onboarding flows.

**Scope:**
- Add `product_id: Optional[UUID]` to `app/models/tenant/submission.py` with FK →
  `products.id` ON DELETE SET NULL
- Create `alembic/versions/005_submission_product_id.py` (revises `004_product_tables`):
  - `op.add_column('submissions', ...)` — nullable column
  - `op.create_foreign_key(...)` → `products.id` ON DELETE SET NULL
  - Reversible `downgrade()`
- Add `product_id: Optional[UUID]` to `SubmissionCreate` and `SubmissionRead` schemas
- Add `product_id` as an optional filter param to `GET /submissions` (list endpoint)

**Acceptance Criteria:**
- Existing submissions and `POST /submissions` without `product_id` are unaffected (backward
  compatible — field is nullable)
- `POST /submissions` with a valid `product_id` stores the reference
- `POST /submissions` with a non-existent `product_id` returns 404
- `GET /submissions/{id}` response includes `product_id` (may be `null`)
- `GET /submissions?product_id={id}` filters results to submissions for that product
- Migration runs and rolls back cleanly

---

### Subtask 7 — Router Registration & Integration

**Goal:** Wire all new routes into the FastAPI application and ensure correct package
structure.

**Scope:**
- Create `app/models/tenant/__init__.py` updates (add `product` imports)
- Create `app/schemas/products/__init__.py` with public exports
- Create `app/services/products/__init__.py` with public exports
- Create `app/routes/products/__init__.py` exposing `product_router`
- Register in `app/main.py`:
  ```python
  from app.routes.products import product_router
  app.include_router(product_router, prefix=settings.API_V1_PREFIX)
  ```

**Acceptance Criteria:**
- All product endpoints appear in `/docs` (Swagger UI)
- All product endpoints require `X-Tenant-ID` and validate the tenant before business logic
- No import errors on application startup
- Existing routes (`/templates`, `/submissions`, `/tenants`, `/baseline-templates`) are
  unaffected

---

## Definition of Done

- [ ] All 7 subtasks implemented and reviewed
- [ ] All acceptance criteria in AC1–AC6 verified via manual testing or automated tests
- [ ] Migrations run cleanly on a fresh database and roll back cleanly
- [ ] No breaking changes to existing `/templates` or `/submissions` endpoints
- [ ] All product endpoints visible in Swagger UI at `/docs`
- [ ] `product_id` on submissions is backward compatible (nullable, optional)
- [ ] Product tenant isolation verified (cross-tenant access returns 404)

---

## Dependencies

- `app/models/base.py` — `TenantSchemaModel` (audit fields + schema isolation)
- `app/db/session.py` — `get_tenant_session()` (tenant validation + `search_path`)
- `app/models/tenant/template.py` — `TenantTemplate` (referenced by `ProductTemplateLink`)
- `app/services/tenant_templates/service.py` — `get_tenant_template_with_merged_config`
  (reused in Subtask 5)
- `app/models/tenant/submission.py` — `Submission` (extended in Subtask 6)
- `alembic/versions/003_submission_tables.py` — base revision for migration chain

---

## References

- [Multi-Tenancy Setup and Config](./multi-tenancy-setup-and-config.md)
- [Subtask 1: Tenant Entity Structure](./subtask-1-tenant-entity-structure.md)
