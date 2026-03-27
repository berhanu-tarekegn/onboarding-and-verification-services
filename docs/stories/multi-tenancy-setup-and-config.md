# Story: Setup & Configure Multi-Tenancy (Platform Security Boundary)

## Context

The Onboarding & Verification Platform already implements a **multi-tenant architecture**:

- **Tenant registry & provisioning**: Public `/tenants` API (create, list, get, update, delete) with schema provisioning.
- **Tenant middleware**: Reads `X-Tenant-ID` (tenant slug), sets request-scoped context; no tenant required for public routes.
- **Schema-based isolation**: Per-tenant PostgreSQL schemas; `get_tenant_session()` sets `search_path` so all tenant-scoped queries are automatically isolated.
- **Tenant-scoped APIs**: `/templates` and `/submissions` use `get_tenant_session()`, which validates tenant and enforces schema scope before any business logic.

This story focuses on **setting up and configuring** that architecture: documenting boundaries, validating behaviour, and ensuring configuration and audit practices support strict tenant isolation before building further onboarding execution features.

---

## Summary

**As a** Platform / Operations  
**I want** multi-tenancy to be clearly set up, configured, and documented with validated isolation boundaries  
**So that** data, configuration, and operations remain securely separated between tenants and the platform is ready for onboarding execution work.

---

## Scope

- **In scope**: Configuration, documentation, and validation of tenant isolation; explicit rules for public vs tenant-scoped APIs; audit expectations; any env/feature flags for multi-tenancy.
- **Out of scope**: Implementing tenant middleware, session handling, or schema provisioning from scratch (already present).

---

## Acceptance Criteria

### 1. Tenant-scoped API contract

- **Given** a request to any **tenant-scoped** API (e.g. `/v1/templates`, `/v1/submissions`)  
- **When** the request is processed  
- **Then** `X-Tenant-ID` must be present and valid (tenant exists and is active) **before** any business logic runs.  
- **And** validation is performed in the dependency chain (e.g. `get_tenant_session()`), not only in ad-hoc logic.

### 2. Template–tenant ownership

- **Given** an operation that accepts a `template_id` (e.g. create submission, get template)  
- **When** the system resolves the template  
- **Then** the template must be resolved **within the current tenant scope** (same schema/session).  
- **And** no cross-tenant template access is possible (enforced by schema/session design).

### 3. Authorization and tenant scope

- **Given** a user or client accesses tenant-scoped resources  
- **When** authorization is evaluated  
- **Then** access is restricted strictly to the tenant identified by `X-Tenant-ID` (and the corresponding schema/session).  
- **And** there is no cross-tenant data visibility.

### 4. Database layer isolation

- **Given** any tenant-scoped database operation  
- **When** data is read or written  
- **Then** the session uses the tenant’s schema only (via `search_path` set by `get_tenant_session()`).  
- **And** no tenant-scoped endpoint uses a session that is not tenant-validated and schema-bound.

### 5. Audit and traceability

- **Given** any action that creates or updates tenant-scoped data  
- **When** audit or history is recorded  
- **Then** the tenant identity (e.g. tenant slug or tenant ID) is either stored in the audit record or is inferable from the schema/context (e.g. request context or log metadata).  
- **And** the existing audit fields (e.g. `created_by`, `updated_by`, status history) remain in use where applicable.

### 6. Public vs tenant-scoped APIs

- **Given** the API surface is documented and configured  
- **When** a client calls an endpoint  
- **Then**  
  - **Public (Kifiya-level) APIs** (e.g. `/v1/tenants`, `/v1/baseline-templates`, `/health`) do **not** require `X-Tenant-ID`.  
  - **Tenant-scoped APIs** (e.g. `/v1/templates`, `/v1/submissions`) **require** `X-Tenant-ID` and return 400 (or equivalent) when it is missing or invalid.

---

## Configuration & Documentation Deliverables

- [ ] **Route classification**: Clear list (or code-level contract) of which routes are public vs tenant-scoped and that tenant-scoped routes use `get_tenant_session()` (or equivalent).
- [ ] **Header contract**: Document that tenant-scoped APIs require the `X-Tenant-ID` header (tenant slug) and the expected error when it is missing or invalid.
- [ ] **Environment / config**: Any environment variables or feature flags that affect multi-tenancy (e.g. enabling/disabling tenant validation, schema naming) are documented and, if needed, set for non-prod and prod.
- [ ] **Audit convention**: Document how tenant identity is captured in audit logs or history (e.g. context, schema, or explicit tenant_id in log/record).

---

## Technical Notes (current implementation)

- **Middleware**: `TenantMiddleware` sets `tenant_context` from `X-Tenant-ID`; routes that need tenant scope call `get_current_tenant()` (via `get_tenant_session()`).
- **Sessions**: `get_tenant_session()` validates tenant in `public.tenants`, sets `search_path` to the tenant schema, and populates `tenant_id_context`; `get_public_session()` is used for public routes.
- **Isolation**: Schema-per-tenant; `TemplateId` is implicitly tenant-scoped because template resolution happens within the tenant session/schema.
- **Tenant isolation is mandatory and non-bypassable** for tenant-scoped operations; no cross-tenant data visibility. Enforcement is at middleware + session/DB layer.

---

## Sub-task: Validate X-Tenant-ID before tenant-scoped business logic

**Name:** Enforce X-Tenant-ID presence and validity for tenant data access (middleware + dependency layer)

**Description:**  
Tenant-scoped APIs grant access to tenant data only when the client supplies a valid tenant identity. The platform must validate the **X-Tenant-ID** header (tenant slug) for every tenant-scoped request: ensure it is present, resolve it to a known tenant, and confirm the tenant is active—**before** any controller or business logic runs. Invalid or missing tenant identity must be rejected with a structured error response so clients and operators can diagnose issues consistently.

### Acceptance criteria

- **AC1 – Validate TenantID in request**  
  **Given** a request to a **tenant-scoped** API (e.g. `/v1/templates`, `/v1/submissions`)  
  **When** the request is received  
  **Then** the system reads the tenant identity from the **X-Tenant-ID** header (tenant slug).  
  **And** if the product specifies body or query as alternative sources, those are documented and validated consistently; header is the primary contract for tenant-scoped access.

- **AC2 – Reject request if TenantID is missing**  
  **Given** a request to a tenant-scoped API  
  **When** the **X-Tenant-ID** header is missing or empty  
  **Then** the request is **rejected** before any business logic runs.  
  **And** the response is **400 Bad Request** (or agreed equivalent) with a **structured error body** (e.g. `{"detail": "X-Tenant-ID header is required."}` or a shared error schema with code/message).

- **AC3 – Reject request if TenantID is invalid**  
  **Given** a request to a tenant-scoped API with **X-Tenant-ID** present  
  **When** the tenant is **unknown** (not in registry) or **inactive**  
  **Then** the request is **rejected** before any business logic runs.  
  **And** the response is **404 Not Found** for unknown tenant, or **403 Forbidden** for inactive tenant, with a **structured error body** that does not leak internal data (e.g. generic message for 404, clear “tenant inactive” for 403).

- **AC4 – Validation runs before controller logic**  
  **Given** the request pipeline for tenant-scoped routes  
  **When** a request is processed  
  **Then** tenant validation (presence + correctness) is performed in the **middleware and/or dependency chain** (e.g. middleware sets context; dependency resolves and validates tenant when `get_tenant_session()` is used).  
  **And** no controller or service code runs until validation has passed (fail-fast).

- **AC5 – Structured error response**  
  **Given** any validation failure (missing or invalid TenantID)  
  **When** the API returns an error  
  **Then** the response uses a **consistent structure** (e.g. `detail` string or `error` object with `code`/`message`), appropriate status code, and `Content-Type: application/json`.  
  **And** the format is documented so clients and operators can rely on it for handling and logging.

### Checklist (implementation)

- [ ] Validate TenantID in request header (and body/query only if specified in contract).
- [ ] Reject request with structured error if X-Tenant-ID is missing (e.g. 400).
- [ ] Reject request with structured error if tenant is invalid (unknown → 404, inactive → 403).
- [ ] Ensure validation runs before controller/business logic (middleware + dependency, no bypass).
- [ ] Add and document a structured error response format for tenant validation failures.

---

## Sub-task: Validate template–tenant ownership and enforce 403 + logging

**Name:** Enforce TemplateId–TenantID validation and log cross-tenant access attempts

**Description:**  
Any operation that accepts a `template_id` (e.g. create submission, get/update/delete template, template definitions) must explicitly validate that the template exists and belongs to the current tenant. When validation fails (template not found or not in tenant scope), return 403 and log the attempt for security monitoring.

### Acceptance criteria

- **AC1 – Template existence**  
  **Given** a request that includes a `template_id` (path or body)  
  **When** the system resolves the template  
  **Then** the template must exist in the current tenant schema.  
  **And** if no template is found, the system treats it as a tenant-scope violation (see AC2).

- **AC2 – Template belongs to tenant**  
  **Given** a resolved template (or attempted resolution)  
  **When** the template is not in the current tenant’s scope (e.g. not found in current schema, or explicit tenant check fails)  
  **Then** the API returns **403 Forbidden** (not 404).  
  **And** the response does not reveal whether the template exists in another tenant.

- **AC3 – Logging on mismatch**  
  **Given** a request where template resolution fails due to missing template or tenant mismatch  
  **When** the 403 is returned  
  **Then** a security-relevant log entry is written (e.g. template_id, current tenant slug/ID, endpoint, timestamp).  
  **And** the log is suitable for audit and detection of cross-tenant access attempts.

- **AC4 – Consistent behaviour**  
  **Given** any endpoint that takes `template_id` (e.g. templates CRUD, submissions create/read, template definitions)  
  **When** validation is implemented  
  **Then** the same pattern is used: resolve within current tenant session → 403 + logging on failure.  
  **And** 404 is not used for “template not in this tenant” to avoid leaking existence across tenants.

### Checklist (implementation)

- [ ] Validate template existence within current tenant session/schema.
- [ ] Verify template belongs to current TenantID (implicit via schema; explicit check if needed).
- [ ] Return **403 Forbidden** (not 404) when template is missing or not in tenant scope.
- [ ] Add structured logging for mismatch/unauthorized attempts (template_id, tenant, endpoint).
- [ ] Apply the pattern to all operations that accept `template_id`.

---

## Sub-task: Enforce tenant isolation at the database layer and prevent bypass

**Name:** Enforce TenantID filtering at DB/ORM layer and add DB-level validation tests

**Description:**  
All tenant-scoped data access must be constrained by tenant scope at the database layer. Schema-based isolation (search_path) must be the primary mechanism; the ORM/query layer must use tenant-scoped sessions only, raw queries must not bypass tenant scope, and automated tests must verify isolation at the DB level.

### Acceptance criteria

- **AC1 – Schema-level isolation**  
  **Given** tenant-scoped tables (e.g. templates, submissions)  
  **When** the platform is configured and running  
  **Then** tenant data lives in per-tenant PostgreSQL schemas (e.g. `tenant_<slug>`).  
  **And** the session used for tenant-scoped operations has `search_path` set to the current tenant schema (and public where needed), so queries resolve only to that tenant’s data.

- **AC2 – Tenant filter in ORM/query layer**  
  **Given** any tenant-scoped read or write operation  
  **When** the application runs a query  
  **Then** the query runs in a tenant-bound session (obtained via `get_tenant_session()` or equivalent).  
  **And** no tenant-scoped endpoint uses `get_public_session()` (or an unbound session) for tenant data. ORM models in tenant schemas are accessed only through tenant-scoped sessions.

- **AC3 – No raw-query bypass**  
  **Given** any use of raw SQL or text() (e.g. for reporting, migrations, or optimizations)  
  **When** the query touches tenant-scoped data  
  **Then** it runs in a tenant-scoped session so that schema/scope is enforced.  
  **And** there is a documented rule and/or code review guideline: no raw queries that target tenant tables without going through the tenant session mechanism. Static analysis or tests should flag or prevent obvious bypasses where feasible.

- **AC4 – DB-level validation tests**  
  **Given** the tenant isolation design (schema-per-tenant + tenant-scoped session)  
  **When** the test suite runs  
  **Then** there are tests that assert tenant isolation at the database level (e.g. create data as tenant A, open session as tenant B, assert B cannot see or modify A’s data).  
  **And** tests cover at least one tenant-scoped entity (e.g. templates or submissions) and confirm that cross-tenant reads/writes fail or are impossible.

### Checklist (implementation)

- [ ] Confirm tenant data is isolated via schema-per-tenant and session `search_path`.
- [ ] Enforce tenant scope in ORM/query layer: tenant-scoped operations use only tenant-scoped sessions.
- [ ] Prevent raw-query bypass: document rule and ensure raw SQL runs in tenant-scoped session when touching tenant data.
- [ ] Add DB-level validation tests that verify cross-tenant access is impossible or returns errors.

---

## Sub-task: Platform Admin template activation and deactivation

**Name:** Allow Platform Admin to activate or deactivate templates (status field, selection rules, delete guard)

**Description:**  
Platform Admins must be able to set templates as active or inactive. Inactive templates must not be selectable for new use (e.g. new submissions or new tenant templates extending a baseline). Templates that are in use (e.g. have submissions or dependent tenant templates) must not be deletable—or deletion must be explicitly guarded and documented.

### Acceptance criteria

- **AC1 – Status/active field**  
  **Given** a template (baseline or tenant-scoped, as per product scope)  
  **When** the template is managed by the platform  
  **Then** it has an explicit **status** or **active** field (e.g. `is_active` boolean or `status` enum).  
  **And** Platform Admin can set the template to active or inactive via API (e.g. PATCH) and the value is persisted and returned in read responses.

- **AC2 – Restrict selection of inactive templates**  
  **Given** an operation that selects a template (e.g. creating a submission, creating a tenant template that extends a baseline)  
  **When** the user or system chooses a template  
  **Then** only **active** templates are available for selection (e.g. list endpoints filter to `is_active=True`, or selection validates active status).  
  **And** requests that reference an inactive template for such operations receive a **403 Forbidden** or **422 Unprocessable Entity** with a clear message (e.g. "Template is inactive and cannot be used").

- **AC3 – Prevent deletion if template is in use**  
  **Given** a request to delete (or hard-delete) a template  
  **When** the template is **in use** (e.g. has at least one submission, or at least one tenant template extending it in the baseline case)  
  **Then** the API rejects the request (e.g. **409 Conflict** or **422**) with a message that the template cannot be deleted because it is in use.  
  **And** the definition of "in use" is documented (e.g. submissions referencing template_id; tenant templates referencing baseline_id). Soft-deactivate (set inactive) may be allowed even when in use; hard delete remains blocked.

- **AC4 – Consistent behaviour**  
  **Given** the template type(s) in scope (e.g. baseline templates, tenant templates)  
  **When** activation, selection, and deletion rules are implemented  
  **Then** the same rules apply consistently: active flag exposed and updatable, inactive excluded from selection, in-use templates protected from deletion.

### Checklist (implementation)

- [ ] Add or expose status/active field on the template model and in API (read + update).
- [ ] Restrict selection of inactive templates (list/filter and validate on create/use).
- [ ] Prevent deletion when template is in use (check submissions and/or dependent templates); return 409/422 with clear message.
- [ ] Document "in use" definition and any difference between soft-deactivate and delete.

---

## Sub-task: Build Tenant Configuration Store (secure + versioned)

**Name:** Build Tenant Configuration Store (secure + versioned)

**Description:**  
Provide a secure, versioned store for tenant-specific configuration (e.g. feature flags, integration settings, branding, limits). Configuration must be tenant-isolated, versioned so changes can be tracked and rolled back, and all updates must be auditable. This supports platform operators and tenants in managing their settings safely and in compliance with audit requirements.

### Acceptance criteria

- **AC1 – Config schema design**  
  **Given** the need to store tenant-specific settings  
  **When** the configuration store is designed  
  **Then** a clear **config schema** is defined (e.g. key/value structure, namespaced keys, allowed types, validation rules).  
  **And** the schema is documented so that consumers (APIs, services) and operators know what can be stored and how it is validated.

- **AC2 – Versioning mechanism**  
  **Given** a change to tenant configuration  
  **When** the configuration is updated  
  **Then** each change is stored with **version tracking** (e.g. version number or timestamp, previous value or diff).  
  **And** the system supports at least one of: read current version, list version history, or restore/rollback to a previous version (as per product scope).

- **AC3 – Secure storage**  
  **Given** tenant configuration data  
  **When** it is persisted and accessed  
  **Then** storage is **tenant-isolated** (e.g. per-tenant schema, tenant_id on rows, or dedicated store per tenant) so that no tenant can read or write another tenant’s config.  
  **And** access control ensures only authorized callers (e.g. platform admin for global tenant config, or tenant-scoped API with X-Tenant-ID) can read or update the config; sensitive values are handled according to security policy (e.g. encryption at rest if required).

- **AC4 – Audit logging for config updates**  
  **Given** any create, update, or delete of tenant configuration  
  **When** the operation is performed  
  **Then** an **audit log entry** is written (e.g. tenant_id, config key/scope, action, previous/new value or version, timestamp, actor/user).  
  **And** the audit trail is sufficient for compliance and operational troubleshooting (who changed what, when).

### Checklist (implementation)

- [ ] Design and document the config schema (structure, validation, namespacing).
- [ ] Implement versioning (e.g. version field, history table, or append-only log).
- [ ] Implement secure storage with tenant isolation and access control; apply encryption if required.
- [ ] Add audit logging for all config create/update/delete operations.

---

## Definition of Done

- Acceptance criteria 1–6 are met (verified by implementation review and/or tests).
- Route classification and header contract are documented.
- Any multi-tenancy-related configuration is documented and applied as needed.
- Audit convention for tenant identity is documented (and implemented if not already covered).
- Story is signed off by product/tech lead as the **architectural security boundary** for the platform before onboarding execution features are built.
