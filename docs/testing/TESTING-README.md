# Testing Guide — Onboarding & Verification SaaS

> **Audience**: QA / Testing team  
> **Purpose**: Confirm all implemented features are working correctly end-to-end  
> **Stack**: FastAPI · PostgreSQL (schema-per-tenant) · SQLModel · Alembic  

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Setup & Prerequisites](#2-setup--prerequisites)
3. [Module 1 — Tenant Management](#3-module-1--tenant-management)
4. [Module 2 — Baseline Templates (Admin)](#4-module-2--baseline-templates-admin)
5. [Module 3 — Tenant Templates](#5-module-3--tenant-templates)
6. [Module 4 — Products](#6-module-4--products)
7. [Module 5 — Submissions & Answers](#7-module-5--submissions--answers)
8. [Cross-Cutting Concerns](#8-cross-cutting-concerns)
9. [Database Constraint Verification](#9-database-constraint-verification)

---

## 1. Architecture Overview

| Layer | Description |
|-------|-------------|
| **Public schema** | Shared across all tenants. Holds `tenants`, `baseline_templates`, and all baseline question tables. |
| **Per-tenant schema** | Each tenant gets a dedicated PostgreSQL schema named after `schema_name`. Holds `tenant_templates`, `questions`, `products`, `submissions`, `submission_answers`. |
| **Tenant identification** | Every tenant-scoped request **must** include the header `X-Tenant-ID: <UUID>`. |
| **Template versioning** | Both baseline and tenant templates use draft → publish lifecycle. Published versions are immutable. |
| **Question ownership** | Baseline questions copied into a tenant template are marked `is_tenant_editable=false` and cannot be modified or deleted by the tenant. |

---

## 2. Setup & Prerequisites

- Application running locally (default: `http://localhost:8000`)
- Database is fresh with all 5 Alembic migrations applied (`001` → `005`)
- At least one tenant created before any tenant-scoped tests
- Collect the tenant UUID returned by `POST /tenants` — required as `X-Tenant-ID` throughout

---

## 3. Module 1 — Tenant Management

### 3.1 Create a Tenant

**`POST /tenants`**

```json
{
  "name": "Acme Bank",
  "schema_name": "acme_bank"
}
```

| Check | Expected |
|-------|----------|
| HTTP status | `201 Created` |
| Response contains `id` (UUID) | ✓ |
| Response contains `schema_name: "acme_bank"` | ✓ |
| PostgreSQL schema `acme_bank` created | ✓ (verify in DB: `SELECT schema_name FROM information_schema.schemata`) |
| All tenant tables exist in new schema | ✓ (`tenant_templates`, `questions`, `products`, `submissions`, `submission_answers`, etc.) |

**Negative cases**

| Scenario | Expected |
|----------|----------|
| `schema_name` with uppercase letters | `422` — validation error |
| `schema_name` with spaces or hyphens | `422` — validation error |
| Duplicate `schema_name` | `409` or `422` — unique constraint |

---

### 3.2 List & Retrieve Tenants

| Request | Expected |
|---------|----------|
| `GET /tenants` | `200`, array of tenants |
| `GET /tenants/{id}` | `200`, single tenant |
| `GET /tenants/<invalid-uuid>` | `422` |
| `GET /tenants/<nonexistent-uuid>` | `404` |

---

### 3.3 Update Tenant

**`PATCH /tenants/{id}`**

```json
{ "name": "Acme Bank Ltd", "is_active": true }
```

| Check | Expected |
|-------|----------|
| Name updated | ✓ |
| `schema_name` is **not** an accepted update field | Field ignored / not present in update schema |

---

### 3.4 Delete Tenant

| Request | Expected |
|---------|----------|
| `DELETE /tenants/{id}` | `200` or `204`; sets `is_active=false` (soft delete) |
| Subsequent `GET /tenants/{id}` | Still returns tenant, `is_active: false` |

---

## 4. Module 2 — Baseline Templates (Admin)

> Baseline templates live in the **public schema** and are managed by system admins. They define mandatory question sets per `template_type`. Tenants cannot modify them.

### 4.1 Create a Baseline Template

**`POST /baseline-templates`**

```json
{
  "name": "KYC Standard v1",
  "description": "Standard KYC questions",
  "category": "kyc",
  "template_type": "kyc",
  "initial_version": {
    "version_tag": "1.0.0",
    "rules_config": {},
    "changelog": "Initial version",
    "question_groups": [
      {
        "unique_key": "personal_info",
        "title": "Personal Information",
        "display_order": 1,
        "questions": [
          {
            "unique_key": "full_name",
            "label": "Full Name",
            "field_type": "text",
            "required": true,
            "display_order": 1
          },
          {
            "unique_key": "date_of_birth",
            "label": "Date of Birth",
            "field_type": "date",
            "required": true,
            "display_order": 2,
            "min_date": "1900-01-01",
            "max_date": "2010-01-01"
          }
        ]
      },
      {
        "unique_key": "document_upload",
        "title": "Document Upload",
        "display_order": 2,
        "questions": [
          {
            "unique_key": "id_document",
            "label": "National ID / Passport",
            "field_type": "fileUpload",
            "required": true,
            "display_order": 1
          },
          {
            "unique_key": "id_type",
            "label": "ID Type",
            "field_type": "dropdown",
            "required": true,
            "display_order": 2,
            "options": [
              { "value": "national_id", "display_order": 1 },
              { "value": "passport", "display_order": 2 },
              { "value": "driving_license", "display_order": 3 }
            ]
          }
        ]
      }
    ],
    "questions": [
      {
        "unique_key": "consent",
        "label": "I agree to the terms and conditions",
        "field_type": "checkbox",
        "required": true,
        "display_order": 99
      }
    ]
  }
}
```

| Check | Expected |
|-------|----------|
| HTTP status | `201 Created` |
| `template_type: "kyc"` in response | ✓ |
| Initial version created with `is_draft: true` | ✓ |
| Groups and questions present in response | ✓ |
| Ungrouped `consent` question present in `ungrouped_questions` | ✓ |

**Negative cases**

| Scenario | Expected |
|----------|----------|
| Duplicate `template_type` | `409` — only one baseline per type |
| Invalid `template_type` value | `422` |
| Question `field_type` not in allowed set | `422` |

---

### 4.2 Publish a Baseline Version

**`POST /baseline-templates/{template_id}/definitions/{version_id}/publish?set_as_active=true`**

| Check | Expected |
|-------|----------|
| `is_draft` becomes `false` | ✓ |
| Template's `active_version_id` updated | ✓ (when `set_as_active=true`) |
| Attempting to update a published definition | `403` or `422` — immutable |

---

### 4.3 Baseline Question Immutability

> Questions in published baseline definitions must be read-only.

| Scenario | Expected |
|----------|----------|
| Add question to published baseline definition | `403` — draft guard |
| Delete question from published baseline definition | `403` — draft guard |

---

### 4.4 Groupless Questions

> A question can belong to either a group OR be directly attached to a template version (ungrouped).

| Check | Expected |
|-------|----------|
| Ungrouped questions returned in `ungrouped_questions[]` in response | ✓ |
| Grouped questions returned in `question_groups[].questions[]` | ✓ |
| A question **cannot** have both `group_id` and `version_id` null | DB `CHECK` constraint fires |

---

## 5. Module 3 — Tenant Templates

> All requests require `X-Tenant-ID: <tenant-uuid>` header.

### 5.1 Create a Tenant Template

**`POST /templates`**

```json
{
  "name": "My KYC Flow",
  "description": "KYC for retail customers",
  "template_type": "kyc"
}
```

| Check | Expected |
|-------|----------|
| HTTP status | `201 Created` |
| `template_type` matches the `kyc` baseline | ✓ |

**Negative cases**

| Scenario | Expected |
|----------|----------|
| `template_type` with no active published baseline | `422` — no baseline found |
| Invalid `X-Tenant-ID` (not a UUID) | `422` |
| Missing `X-Tenant-ID` header | `422` |

---

### 5.2 Create a Template Version — Baseline Questions Auto-Copied

**`POST /templates/{template_id}/definitions`**

```json
{
  "version_tag": "1.0.0",
  "rules_config": {},
  "changelog": "First tenant version"
}
```

| Check | Expected |
|-------|----------|
| All baseline question groups copied into new version | ✓ |
| Baseline questions have `is_tenant_editable: false` | ✓ |
| Copied **groups** have `is_tenant_editable: true` (tenants can add questions to them) | ✓ |
| Ungrouped baseline questions also copied | ✓ |
| `copied_from_baseline_version_id` set in response | ✓ |

---

### 5.3 Tenant-Editable vs. Baseline-Protected Questions

| Scenario | Expected |
|----------|----------|
| Attempt to update a baseline-derived question (`is_tenant_editable: false`) | `403` |
| Attempt to delete a baseline-derived question | `403` |
| Attempt to update a tenant-added question (`is_tenant_editable: true`) | `200` |
| Add a **new** question to a baseline-copied group | `201` — allowed since group `is_tenant_editable: true` |
| Add a **new** ungrouped question to a tenant version | `201` |

---

### 5.4 Publish a Tenant Template Version

**`POST /templates/{template_id}/definitions/{version_id}/publish?set_as_active=true`**

| Check | Expected |
|-------|----------|
| All `unique_key` values across all groups in the version are unique | ✓ (pre-publish validation) |
| Duplicate `unique_key` within same version | `422` — uniqueness check fails |
| `is_draft` becomes `false` after publish | ✓ |
| Published version is immutable | ✓ |

---

## 6. Module 4 — Products

> All requests require `X-Tenant-ID: <tenant-uuid>` header.

### 6.1 Product Lifecycle

```
DRAFT → ACTIVE → INACTIVE → ACTIVE (reactivate)
```

**Create product**

**`POST /products`**

```json
{
  "name": "Retail Savings Account",
  "product_code": "RSA-001",
  "description": "Savings product for retail customers",
  "template_id": "<tenant-template-uuid>"
}
```

| Check | Expected |
|-------|----------|
| Status is `draft` | ✓ |
| `product_code` unique per tenant | ✓ |

**Activate product**

**`POST /products/{product_id}/activate`**

| Check | Expected |
|-------|----------|
| Requires `template_id` to be set | ✓ — `422` if not set |
| Status transitions to `active` | ✓ |
| `product_code` cannot be changed while `active` | `422` |
| `template_id` cannot be changed while `active` | `422` |

**Deactivate product**

**`POST /products/{product_id}/deactivate`**

| Check | Expected |
|-------|----------|
| Status transitions to `inactive` | ✓ |
| Can reactivate with `POST /activate` | ✓ |

**Delete product**

| Scenario | Expected |
|----------|----------|
| Delete `draft` product | `204` |
| Delete `active` product | `422` — blocked |

---

### 6.2 KYC Configuration

**`GET /products/{product_id}/kyc-config`**

| Check | Expected |
|-------|----------|
| Only available for `active` products | `422` for non-active |
| Returns `question_groups[]` from the active template version | ✓ |
| Returns `rules_config` | ✓ |
| Returns `baseline_version_id` and `baseline_version_tag` | ✓ (nullable if no baseline) |

---

## 7. Module 5 — Submissions & Answers

> All requests require `X-Tenant-ID: <tenant-uuid>` header.

### 7.1 Submission Lifecycle

```
DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED / REJECTED / RETURNED
                                  RETURNED → SUBMITTED (re-submission)
                 → CANCELLED (from DRAFT or SUBMITTED)
```

---

### 7.2 Create a Submission

**`POST /submissions`**

```json
{
  "template_id": "<tenant-template-uuid>",
  "submitter_id": "user-abc-123",
  "external_ref": "APP-2026-001",
  "product_id": "<product-uuid>"
}
```

| Check | Expected |
|-------|----------|
| Status is `draft` | ✓ |
| `template_version_id` locked to the template's current active version | ✓ |
| `baseline_version_id` populated if template has a baseline | ✓ |

---

### 7.3 Submit Answers (Flat Table)

> Answers are stored in `submission_answers` — one row per question, not a JSON blob.

**`POST /submissions/{submission_id}/answers`** *(if endpoint exists)* or via submission update.

Each answer payload:

```json
{
  "answers": [
    { "question_id": "<uuid>", "field_type": "text", "answer": "John Doe" },
    { "question_id": "<uuid>", "field_type": "date", "answer": "1990-05-15" },
    { "question_id": "<uuid>", "field_type": "dropdown", "answer": "national_id" },
    { "question_id": "<uuid>", "field_type": "checkbox", "answer": "true" },
    { "question_id": "<uuid>", "field_type": "fileUpload", "answer": "uploads/doc123.pdf" }
  ]
}
```

| Check | Expected |
|-------|----------|
| One row per `(submission_id, question_id)` in `submission_answers` | ✓ |
| Duplicate `question_id` for same submission rejected | `409` — unique constraint |

---

### 7.4 Answer Validation — Application Layer

> Validation runs at service layer before any DB write.

| Scenario | Expected |
|----------|----------|
| Required question with blank answer | `422` — "This field is required." |
| `dropdown` answer not in defined options | `422` — "not a valid option" |
| `radio` answer not in defined options | `422` — "not a valid option" |
| `checkbox` answer contains invalid option value | `422` — "Invalid option(s): ..." |
| `date` answer not in `YYYY-MM-DD` format | `422` — "Date must be in YYYY-MM-DD format" |
| `date` answer before `min_date` | `422` — "Date must be on or after ..." |
| `date` answer after `max_date` | `422` — "Date must be on or before ..." |
| `text` answer not matching `regex` | `422` — "Value does not match expected format" |
| Hidden question (dependency not met) skipped | ✓ — no validation error for hidden questions |
| Optional question left blank | ✓ — no error |

**Conditional visibility example**

If question `Q_employer` has `depends_on_unique_key: "employment_status"` and `visible_when_equals: "employed"`:

| Scenario | Expected |
|----------|----------|
| `employment_status` answer is `"employed"` → `Q_employer` is required | `422` if blank |
| `employment_status` answer is `"unemployed"` → `Q_employer` hidden | ✓ no error even if blank |

---

### 7.5 Answer Validation — Database Layer (CHECK Constraints)

> These constraints fire directly on `INSERT`/`UPDATE` to `submission_answers`, independent of the application.

| Constraint | Trigger condition | Expected DB error |
|------------|-------------------|-------------------|
| `ck_answer_field_type_valid` | `field_type` not in allowed set | `CheckViolation` |
| `ck_answer_date_format` | `field_type='date'` and answer not matching `^\d{4}-\d{2}-\d{2}$` | `CheckViolation` |
| `ck_answer_checkbox_nonempty` | `field_type='checkbox'` and `length(answer) = 0` | `CheckViolation` |
| `ck_answer_fileupload_nonempty` | `field_type='fileUpload'` and `trim(answer) = ''` | `CheckViolation` |

To verify these directly in the database:

```sql
-- Should fail:
INSERT INTO acme_bank.submission_answers (id, submission_id, question_id, field_type, answer)
VALUES (gen_random_uuid(), '<sub-id>', '<q-id>', 'date', '15-05-1990');
-- Expected: ERROR: new row violates check constraint "ck_answer_date_format"
```

---

### 7.6 Submit Submission

**`POST /submissions/{submission_id}/submit`**

| Check | Expected |
|-------|----------|
| With `?validate=true` — runs answer validation | `422` if errors found |
| With `?validate=true` — all valid answers | `200`, status becomes `submitted` |
| Submitting an already-submitted submission | `422` — invalid transition |

---

### 7.7 Status Transitions

**`POST /submissions/{submission_id}/transition`**

```json
{ "to_status": "under_review", "reason": "Assigned to reviewer" }
```

| Transition | Valid? |
|------------|--------|
| `draft` → `submitted` | ✓ (use `/submit` endpoint) |
| `submitted` → `under_review` | ✓ |
| `under_review` → `approved` | ✓ |
| `under_review` → `rejected` | ✓ |
| `under_review` → `returned` | ✓ |
| `returned` → `submitted` | ✓ (re-submission) |
| `draft` → `cancelled` | ✓ |
| `submitted` → `cancelled` | ✓ |
| `approved` → `draft` | ✗ `422` — invalid transition |

---

### 7.8 Status History Audit Trail

**`GET /submissions/{submission_id}/history`**

| Check | Expected |
|-------|----------|
| Each transition logged with `from_status`, `to_status`, `changed_by`, timestamp | ✓ |
| Initial creation has `from_status: null` | ✓ |

---

### 7.9 Comments

**`POST /submissions/{submission_id}/comments`**

```json
{
  "content": "Please re-upload a clearer photo of the document.",
  "field_id": "id_document",
  "is_internal": false
}
```

| Check | Expected |
|-------|----------|
| External comment visible to submitter | ✓ |
| Internal comment only visible when `?include_internal=true` | ✓ |
| Threaded reply using `parent_id` | ✓ |

---

## 8. Cross-Cutting Concerns

### 8.1 Tenant Isolation

| Scenario | Expected |
|----------|----------|
| Create template with tenant A's `X-Tenant-ID`, then fetch with tenant B's `X-Tenant-ID` | `404` — data isolated per schema |
| Submit answers for a submission belonging to a different tenant | `404` |

### 8.2 `X-Tenant-ID` Header Validation

| Value | Expected |
|-------|----------|
| Valid UUID: `550e8400-e29b-41d4-a716-446655440000` | ✓ accepted |
| Slug string: `acme_bank` | `422` — only UUIDs accepted |
| Empty string | `422` |
| Missing header | `422` |

### 8.3 Version Immutability

| Scenario | Expected |
|----------|----------|
| Modify a published (non-draft) template definition | `403` |
| Delete the active version of a template | `422` — blocked |
| Add a question to a published baseline definition | `403` |

### 8.4 Schema Name Format

| Value | Expected |
|-------|----------|
| `my_tenant` | ✓ valid |
| `myTenant` | `422` — uppercase not allowed |
| `my-tenant` | `422` — hyphens not allowed |
| `my tenant` | `422` — spaces not allowed |
| 64+ characters | `422` — exceeds PostgreSQL limit |

---

## 9. Database Constraint Verification

Run these SQL checks directly against the database to confirm constraint setup:

```sql
-- Confirm tenant schema was provisioned
SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'acme_bank';

-- Confirm all tables exist in tenant schema
SELECT table_name FROM information_schema.tables WHERE table_schema = 'acme_bank' ORDER BY table_name;
-- Expected: products, question_groups, question_options, questions,
--           submission_answers, submission_comments, submission_status_history,
--           submissions, tenant_template_definitions, tenant_templates

-- Confirm question integrity constraint
SELECT conname, consrc FROM pg_constraint
WHERE conname IN ('ck_question_has_parent', 'ck_baseline_question_has_parent');

-- Confirm submission_answers field_type constraint
SELECT conname FROM pg_constraint WHERE conname = 'ck_answer_field_type_valid';

-- Confirm one baseline per template_type
SELECT template_type, COUNT(*) FROM public.baseline_templates GROUP BY template_type;
-- Expected: each type appears at most once
```

---

## Appendix — Quick Reference

### Allowed `template_type` Values
`kyc` · `onboarding` · `verification` · `loan_application` · `insurance`

### Allowed `field_type` Values
`text` · `dropdown` · `radio` · `checkbox` · `date` · `fileUpload` · `signature`

### Submission Status Values
`draft` · `submitted` · `under_review` · `approved` · `rejected` · `returned` · `cancelled`

### Product Status Values
`draft` · `active` · `inactive`
