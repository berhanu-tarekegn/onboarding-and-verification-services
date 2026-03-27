# Onboarding & Verification SaaS

A **multi-tenant onboarding and KYC platform** built with FastAPI. Each tenant gets a fully isolated PostgreSQL schema. System admins define baseline question templates per `template_type`; tenants extend them with their own questions, link them to products, and collect structured submissions.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | [FastAPI](https://fastapi.tiangolo.com/) |
| ORM | [SQLModel](https://sqlmodel.tiangolo.com/) (SQLAlchemy + Pydantic) |
| Database | PostgreSQL 15+ with [asyncpg](https://github.com/MagicStack/asyncpg) |
| Migrations | [Alembic](https://alembic.sqlalchemy.org/) |
| Config | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |
| Package Manager | [uv](https://docs.astral.sh/uv/) |
| Testing | pytest + pytest-asyncio + httpx |

---

## Architecture Overview

### Schema-Based Multi-Tenancy

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          PostgreSQL Database                              │
├──────────────────────────────────────────────────────────────────────────┤
│  PUBLIC SCHEMA (shared, system-owned)                                    │
│  ├── tenants                         # Tenant registry                   │
│  ├── baseline_templates              # One per template_type             │
│  ├── baseline_template_definitions   # Versioned, immutable once pub.    │
│  ├── baseline_question_groups        # Grouped question definitions      │
│  ├── baseline_questions              # Individual questions (grouped OR  │
│  │                                   # ungrouped, attached to version)   │
│  └── baseline_question_options       # Options for dropdown/radio/checkbox│
├──────────────────────────────────────────────────────────────────────────┤
│  TENANT_ACME SCHEMA (per tenant)     TENANT_FOO SCHEMA (per tenant)      │
│  ├── tenant_templates                ├── tenant_templates                │
│  ├── tenant_template_definitions     ├── tenant_template_definitions     │
│  ├── question_groups (copied+owned)  ├── question_groups                 │
│  ├── questions (copied+owned)        ├── questions                       │
│  ├── question_options                ├── question_options                │
│  ├── products                        ├── products                        │
│  ├── submissions                     ├── submissions                     │
│  ├── submission_answers              ├── submission_answers              │
│  ├── submission_status_history       ├── submission_status_history       │
│  └── submission_comments             └── submission_comments             │
└──────────────────────────────────────────────────────────────────────────┘
```

### Baseline → Tenant Copy Model

Baseline templates are **not extended at runtime** — they are **copied into a tenant version on creation**. This guarantees immutability of the baseline while allowing tenants to add their own questions.

```
BaselineTemplate  (public, admin-managed, one per template_type + level)
    │
    │  copy-on-create
    ▼
TenantTemplateDefinition  (per-tenant schema, draft → publish)
    ├── Copied question groups    (is_tenant_editable = true  → tenant can append questions)
    ├── Copied questions          (is_tenant_editable = false → immutable by tenant)
    └── Tenant-added questions    (is_tenant_editable = true  → fully mutable)
```

Key rules:
- A question can belong to a **group** or be attached **directly to a version** (ungrouped)
- Baseline-derived questions (`is_tenant_editable=false`) **cannot be edited or deleted** by tenants
- Copied groups (`is_tenant_editable=true`) allow tenants to **append new questions**
- Published definitions are **immutable** — create a new draft version to make changes

---

## Project Structure

```
├── app/
│   ├── core/
│   │   ├── config.py              # Settings via pydantic-settings
│   │   ├── context.py             # Tenant ContextVar (per-request)
│   │   └── dependencies.py        # require_tenant_header (UUID validation)
│   ├── db/
│   │   ├── session.py             # Async engine, search_path switching per session
│   │   └── migrations.py          # Schema provisioning helpers
│   ├── middleware/
│   │   └── tenants.py             # Extracts X-Tenant-ID from request headers
│   ├── models/
│   │   ├── base.py                # PublicSchemaModel, TenantSchemaModel, AuditBase
│   │   ├── enums.py               # TemplateType enum
│   │   ├── public/
│   │   │   ├── tenant.py          # Tenant registry (tenant_key/schema_name, is_active)
│   │   │   └── baseline_template.py  # Baseline templates, groups, questions, options
│   │   └── tenant/
│   │       ├── template.py        # TenantTemplate, TenantTemplateDefinition, QuestionGroup,
│   │       │                      #   Question, QuestionOption
│   │       ├── product.py         # Product (DRAFT/ACTIVE/INACTIVE lifecycle)
│   │       ├── submission.py      # Submission, SubmissionStatusHistory, SubmissionComment
│   │       └── answer.py          # SubmissionAnswer (flat answer table)
│   ├── schemas/                   # Pydantic request/response models
│   │   ├── tenants/
│   │   ├── baseline_templates/
│   │   ├── tenant_templates/
│   │   ├── templates/             # Shared form_schema (QuestionCreate/Read etc.)
│   │   ├── products/
│   │   └── submissions/
│   ├── services/                  # Business logic
│   │   ├── tenants/
│   │   ├── baseline_templates/
│   │   ├── tenant_templates/
│   │   ├── products/
│   │   └── submissions/
│   │       └── answer_validator.py  # Application-layer answer validation
│   ├── routes/                    # FastAPI routers
│   │   ├── tenants/
│   │   ├── baseline_templates/
│   │   ├── tenant_templates/
│   │   ├── products/
│   │   └── submissions/
│   └── main.py                    # FastAPI app entrypoint
├── alembic/
│   ├── env.py
│   └── versions/
│       ├── 001_initial_public_schema.py   # tenants, baseline_templates, groups, questions
│       ├── 002_tenant_schema_tables.py    # tenant_templates, question_groups, questions
│       ├── 001_initial_public_schema.py
│       ├── 002_tenant_schema_tables.py
│       └── 003_tenant_template_definition_reviews.py
├── tests/
│   ├── conftest.py                # Shared fixtures and payload builders
│   ├── test_tenants.py
│   ├── test_baseline_templates.py
│   ├── test_tenant_templates.py
│   ├── test_products.py
│   ├── test_submissions.py
│   └── test_answer_validator.py   # Pure unit tests (no DB)
├── docker-compose.dev.yaml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Quick Start

For the cleanest local setup and end-to-end demo flow, start with:

- [docs/demo/README.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/README.md)
- [docs/demo/end_to_end_product_simulation.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/end_to_end_product_simulation.md)

### Option 1: Docker (Recommended)

```bash
# Start the dev stack (database + API)
docker compose -f docker-compose.dev.yaml up --build -d

# API:  http://127.0.0.1:7090
# Docs: http://127.0.0.1:7090/docs

# If the host port is already taken on your server, change API_PORT in .env
# before starting the stack, for example:
# API_PORT=17090
```

### Option 2: Local Development

```bash
# 1. Start the database only
docker compose -f docker-compose.dev.yaml up db -d

# 2. Install dependencies (including test extras)
uv sync

# 3. Configure environment
cp .env.example .env

# 4. Run migrations
uv run alembic upgrade head

# 5. Start the API server
uv run uvicorn app.main:app --reload --port 7090
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/template_service` | Async PostgreSQL DSN |
| `APP_NAME` | `Template Service` | Application name shown in docs |
| `DEBUG` | `false` | Enable debug mode and SQL echo |
| `API_V1_PREFIX` | `/api/v1` | API version prefix |

---

## API Reference

### Tenant Identification

All tenant-scoped routes require the `X-Tenant-ID` header set to the tenant's **UUID**:

```
X-Tenant-ID: 018e1a2b-3c4d-5e6f-7890-abcdef012345
```

Slugs and schema names are **not accepted** — only UUIDs.

---

### Public Routes (no `X-Tenant-ID`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/tenants` | Register a tenant and provision its PostgreSQL schema |
| `GET` | `/api/v1/tenants` | List all tenants |
| `GET` | `/api/v1/tenants/{id}` | Get a tenant by UUID |
| `PATCH` | `/api/v1/tenants/{id}` | Update tenant name / is_active |
| `DELETE` | `/api/v1/tenants/{id}` | Soft-delete (sets is_active=false) |
| `POST` | `/api/v1/baseline-templates` | Create a baseline template (admin) |
| `GET` | `/api/v1/baseline-templates` | List baselines (filter: category, active_only) |
| `GET` | `/api/v1/baseline-templates/{id}` | Get baseline with all versions |
| `PATCH` | `/api/v1/baseline-templates/{id}` | Update header (blocked if locked) |
| `DELETE` | `/api/v1/baseline-templates/{id}` | Delete (blocked if locked) |
| `POST` | `/api/v1/baseline-templates/{id}/definitions` | Create a new draft version |
| `GET` | `/api/v1/baseline-templates/{id}/definitions/{vid}` | Get a specific version |
| `PATCH` | `/api/v1/baseline-templates/{id}/definitions/{vid}` | Update draft version |
| `DELETE` | `/api/v1/baseline-templates/{id}/definitions/{vid}` | Delete draft version |
| `POST` | `/api/v1/baseline-templates/{id}/definitions/{vid}/publish` | Publish (optionally set as active) |

---

### Tenant-Scoped Routes (require `X-Tenant-ID`)

#### Templates

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/templates` | Create tenant template (baseline questions auto-copied) |
| `GET` | `/api/v1/templates` | List templates |
| `GET` | `/api/v1/templates/{id}` | Get template with all versions |
| `PATCH` | `/api/v1/templates/{id}` | Update header |
| `DELETE` | `/api/v1/templates/{id}` | Delete template and all its versions |
| `POST` | `/api/v1/templates/{id}/definitions` | Create a new draft version |
| `GET` | `/api/v1/templates/{id}/definitions/{vid}` | Get a specific version |
| `PATCH` | `/api/v1/templates/{id}/definitions/{vid}` | Update draft version |
| `DELETE` | `/api/v1/templates/{id}/definitions/{vid}` | Delete draft version |
| `POST` | `/api/v1/templates/{id}/definitions/{vid}/publish` | Publish (validates unique_key uniqueness) |

#### Products

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/products` | Create product (starts in DRAFT) |
| `GET` | `/api/v1/products` | List products (filter by status) |
| `GET` | `/api/v1/products/{id}` | Get product by ID |
| `PATCH` | `/api/v1/products/{id}` | Update (product_code / template_id locked while ACTIVE) |
| `DELETE` | `/api/v1/products/{id}` | Delete (blocked if ACTIVE) |
| `POST` | `/api/v1/products/{id}/activate` | DRAFT/INACTIVE → ACTIVE (requires template_id) |
| `POST` | `/api/v1/products/{id}/deactivate` | ACTIVE → INACTIVE |
| `GET` | `/api/v1/products/{id}/kyc-config` | Resolved KYC config (ACTIVE products only) |

#### Submissions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/submissions` | Create draft submission (version locked at creation) |
| `GET` | `/api/v1/submissions` | List (filter: status, template_id, product_id, submitter_id, external_ref, dates) |
| `GET` | `/api/v1/submissions/{id}` | Get submission |
| `GET` | `/api/v1/submissions/{id}/full` | Get with status history and comments |
| `PATCH` | `/api/v1/submissions/{id}` | Update draft (blocked once submitted) |
| `DELETE` | `/api/v1/submissions/{id}` | Delete DRAFT or CANCELLED submission |
| `POST` | `/api/v1/submissions/{id}/submit` | DRAFT → SUBMITTED (`?validate=true` runs answer validation) |
| `POST` | `/api/v1/submissions/{id}/transition` | Change status (see transitions below) |
| `GET` | `/api/v1/submissions/{id}/history` | Full status audit trail |
| `POST` | `/api/v1/submissions/{id}/comments` | Add comment (internal / external / threaded) |
| `GET` | `/api/v1/submissions/{id}/comments` | List comments |

---

## Key Concepts

### template_type Enum

Each baseline template is tied to one `template_type`. Creating a tenant template with a `template_type` automatically copies the corresponding active baseline questions.

| Value | Use case |
|-------|----------|
| `kyc` | Know Your Customer identity verification |
| `onboarding` | General onboarding workflow |
| `verification` | Document or identity verification |
| `loan_application` | Loan application questionnaire |
| `insurance` | Insurance underwriting questionnaire |

### Question field_type Values

`text` · `dropdown` · `radio` · `checkbox` · `date` · `fileUpload` · `signature`

### Submission Status Transitions

```
DRAFT ──────────────────────────────────────────────► CANCELLED
  │
  └► SUBMITTED ──────────────────────────────────────► CANCELLED
         │
         └► UNDER_REVIEW ──► APPROVED ──► COMPLETED
                    │
                    ├──► REJECTED ──► COMPLETED
                    │
                    └──► RETURNED ──► SUBMITTED (re-submission)
```

### Product Lifecycle

```
DRAFT ──► ACTIVE ──► INACTIVE ──► ACTIVE (reactivate)
```

A product must have a `template_id` pointing to a published tenant template before it can be activated.

### Answer Storage

Submission answers are stored as a **flat table** (`submission_answers`) — one row per question — rather than a JSON blob. This enables:

- SQL-level filtering and reporting by question
- DB-level `CHECK` constraints for type-appropriate values
- Application-layer validation against question definitions (options, regex, date ranges, conditional visibility)

---

## Alembic Migrations

| Revision | What it creates |
|----------|-----------------|
| `001` | Public schema: `tenants`, `baseline_templates`, `baseline_question_groups`, `baseline_questions`, `baseline_question_options` |
| `002` | Tenant schema: `tenant_templates`, `tenant_template_definitions`, `question_groups`, `questions`, `question_options` |
| `003` | Tenant schema: `submissions`, `submission_status_history`, `submission_comments` |
| `004` | Tenant schema: `products` + `submissions.product_id` FK |
| `005` | Tenant schema: `submission_answers` (flat answer table with CHECK constraints) |

### Common Commands

```bash
# Apply all migrations
uv run alembic upgrade head

# Apply to a specific tenant schema
uv run alembic -x tenant_schema=tenant_acme_bank upgrade head

# Create a new migration
uv run alembic revision -m "your description"

# Downgrade one step
uv run alembic downgrade -1

# Reset for local dev (drops and recreates the DB)
docker compose -f docker-compose.dev.yaml down -v
docker compose -f docker-compose.dev.yaml up --build
```

---

## Testing

```bash
# Install test dependencies
uv sync

# Run pure unit tests (no DB required — fast, ~0.02s)
uv run pytest tests/test_answer_validator.py -v

# Start the disposable test database
docker compose -f docker-compose.test.yaml up -d

# Apply migrations to the disposable test database
DATABASE_URL="$DATABASE_TEST_URL" uv run alembic upgrade head

# Run full integration test suite (uses DATABASE_TEST_URL from .env)
uv run pytest tests/ -v

# Run with coverage report
uv run pytest tests/ --cov=app --cov-report=term-missing
```

The test database must exist and be migrated to the current Alembic head before
running integration tests. The default disposable test DB is exposed on
`localhost:${DB_TEST_PORT:-5433}` and uses `DATABASE_TEST_URL` from `.env`.

### Test structure

| File | Type | Coverage |
|------|------|----------|
| `test_tenants.py` | Integration | Tenant CRUD, tenant_key validation, header checks |
| `test_baseline_templates.py` | Integration | Baseline lifecycle, groupless questions, immutability |
| `test_tenant_templates.py` | Integration | Copy mechanics, is_tenant_editable, publish guards |
| `test_products.py` | Integration | Product lifecycle, KYC config endpoint |
| `test_submissions.py` | Integration | Submission workflow, status transitions, comments |
| `test_answer_validator.py` | Unit | All field types, regex, date ranges, conditional visibility |

---

## End-to-End Workflow Example

```bash
BASE=http://localhost:7090/api/v1

# 1. Create a tenant
TENANT=$(curl -sX POST $BASE/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Bank", "tenant_key": "acme_bank"}')
TENANT_ID=$(echo $TENANT | jq -r '.id')

# 2. Create a KYC baseline template (admin)
BASELINE=$(curl -sX POST $BASE/baseline-templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "KYC Standard",
    "template_type": "kyc",
    "category": "kyc",
    "initial_version": {
      "version_tag": "1.0.0",
      "rules_config": {},
      "question_groups": [{
        "unique_key": "personal_info",
        "title": "Personal Information",
        "display_order": 1,
        "questions": [{
          "unique_key": "full_name",
          "label": "Full Name",
          "field_type": "text",
          "required": true,
          "display_order": 1
        }]
      }]
    }
  }')
BASELINE_ID=$(echo $BASELINE | jq -r '.id')
VERSION_ID=$(echo $BASELINE | jq -r '.active_version_id')

# 3. Publish the baseline version
curl -sX POST "$BASE/baseline-templates/$BASELINE_ID/definitions/$VERSION_ID/publish?set_as_active=true"

# 4. Create a tenant template (baseline questions auto-copied)
TMPL=$(curl -sX POST $BASE/templates \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme KYC", "template_type": "kyc"}')
TMPL_ID=$(echo $TMPL | jq -r '.id')

# 5. Create and publish a tenant template version
DEFN=$(curl -sX POST "$BASE/templates/$TMPL_ID/definitions" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"version_tag": "1.0.0", "rules_config": {}}')
DEFN_ID=$(echo $DEFN | jq -r '.id')
curl -sX POST "$BASE/templates/$TMPL_ID/definitions/$DEFN_ID/publish?set_as_active=true" \
  -H "X-Tenant-ID: $TENANT_ID"

# 6. Create a product and activate it
PROD=$(curl -sX POST $BASE/products \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"Savings Account\", \"product_code\": \"SA-001\", \"template_id\": \"$TMPL_ID\"}")
PROD_ID=$(echo $PROD | jq -r '.id')
curl -sX POST "$BASE/products/$PROD_ID/activate" -H "X-Tenant-ID: $TENANT_ID"

# 7. Create and submit a submission
SUB=$(curl -sX POST $BASE/submissions \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"template_id\": \"$TMPL_ID\", \"submitter_id\": \"user-001\"}")
SUB_ID=$(echo $SUB | jq -r '.id')
curl -sX POST "$BASE/submissions/$SUB_ID/submit?validate=false" -H "X-Tenant-ID: $TENANT_ID"
```

---

## License

Proprietary — Kifiya Financial Technologies.
