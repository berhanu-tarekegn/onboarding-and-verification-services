# Story: Define Transform Rules for Answers When Tenant Questions Change

## Context

The Onboarding & Verification Platform uses a versioned template system. Each
`TenantTemplateDefinition` is immutable once published. When a tenant publishes
a new version — renaming questions, re-typing fields, splitting one question
into two, or remapping dropdown options — any in-flight submissions (status
`DRAFT` or `RETURNED`) remain locked to the old version. Without a migration
path, those submissions are stranded: their answers reference questions that no
longer exist in the active template.

The **Answer Transform Rules** feature closes this gap by providing a
declarative, auditable mechanism to migrate submission answers from one template
version to another.

---

## Summary

**As a** Tenant Admin  
**I want** to define transform rules that describe how answers should be
converted when questions change between template versions  
**So that** in-flight submissions can be migrated to the latest template version
without data loss and without manual re-entry by applicants

---

## Scope

- **In scope:** TransformRuleSet lifecycle (draft → publish → archive), all
  nine transform operations, auto-generation from version diff, single-submission
  apply, bulk migration, dry-run preview, and full audit logging.
- **Out of scope:** Automatic application of rules without explicit admin
  action, transforming submitted/approved/rejected submissions, and real-time
  streaming of migration progress.

---

## Assumptions

- Only `DRAFT` and `RETURNED` submissions are eligible for migration.
  Completed, approved, or rejected submissions are immutable.
- A `TransformRuleSet` is always scoped to one `source_version_id → target_version_id`
  pair within a single tenant template. The uniqueness constraint on this pair
  prevents accidental duplication.
- The `unique_key` on each question is the stable identifier that bridges
  versions. Tenants should treat `unique_key` as immutable once a version is
  published and use `transform_hints` (see below) when renaming.
- A published `TransformRuleSet` cannot be edited. If corrections are needed,
  the admin must archive the old set and generate a new one.
- Transform failures on `is_required=True` rules block migration of the
  affected submission. All other errors are collected as warnings and do not
  block migration.

---

## Data Model

### TransformRuleSet

Lives in the tenant schema (`transform_rule_sets` table).

| Field | Type | Description |
|---|---|---|
| `id` | UUID7 | Primary key |
| `template_id` | UUID | FK → `tenant_templates.id` |
| `source_version_id` | UUID | FK → `tenant_template_definitions.id` |
| `target_version_id` | UUID | FK → `tenant_template_definitions.id` |
| `status` | `RuleSetStatus` | `draft` / `published` / `archived` |
| `auto_generated` | bool | `true` when created by the diff service |
| `changelog` | text | Human-readable description of the change |

**Unique constraint:** `(source_version_id, target_version_id)` — only one
rule set per version pair.

### TransformRule

| Field | Type | Description |
|---|---|---|
| `id` | UUID7 | Primary key |
| `rule_set_id` | UUID | FK → `transform_rule_sets.id` |
| `source_unique_key` | str? | `unique_key` of the source question (null for `default_value` / `drop`) |
| `target_unique_key` | str | `unique_key` of the target question |
| `operation` | `TransformOperation` | One of nine operations (see below) |
| `params` | JSON | Operation-specific parameters |
| `display_order` | int | Execution order within the rule set |
| `is_required` | bool | If `true`, failure blocks the migration |

### TransformLog

| Field | Type | Description |
|---|---|---|
| `id` | UUID7 | Primary key |
| `submission_id` | UUID | FK → `submissions.id` |
| `rule_set_id` | UUID | FK → `transform_rule_sets.id` |
| `source_version_id` | UUID | Version migrated from |
| `target_version_id` | UUID | Version migrated to |
| `before_snapshot` | JSON | `{unique_key: answer}` before transform |
| `after_snapshot` | JSON | `{unique_key: answer}` after transform |
| `errors` | JSON | List of error objects |
| `warnings` | JSON | List of warning objects |
| `applied_at` | datetime | UTC timestamp |
| `applied_by` | str | User ID or `"system"` |
| `is_preview` | bool | `true` for dry-run calls; submission not modified |

---

## Rule Set Lifecycle

```
 ┌──────────────────────────────────────────────────────────────┐
 │                     DRAFT                                    │
 │  (auto-generated or manually created; fully editable)        │
 └──────────────┬───────────────────────────────────────────────┘
                │  POST /publish
                ▼
 ┌──────────────────────────────────────────────────────────────┐
 │                    PUBLISHED                                 │
 │  (frozen; can be applied to submissions)                     │
 └──────────────┬───────────────────────────────────────────────┘
                │  POST /archive
                ▼
 ┌──────────────────────────────────────────────────────────────┐
 │                    ARCHIVED                                  │
 │  (superseded; read-only; kept for audit)                     │
 └──────────────────────────────────────────────────────────────┘
```

---

## Transform Operations

All nine operations are defined in `app/models/enums.TransformOperation`.

| Operation | Description | Required `params` |
|---|---|---|
| `identity` | Copy the answer verbatim (same key, same type) | `{}` |
| `rename` | Key changed; value is identical | `{}` |
| `map_values` | Remap discrete option values | `{"mapping": {"old": "new"}, "default": null}` |
| `coerce_type` | Convert between field types | `{"from_type": "text", "to_type": "date", "format": "MM/DD/YYYY"}` |
| `split` | Extract one piece of a value into a target field | `{"separator": " ", "index": 0}` |
| `merge` | Join multiple source fields into one | `{"sources": ["first_name", "last_name"], "separator": " "}` |
| `default_value` | Inject a static value (new question, no source) | `{"value": "N/A"}` |
| `compute` | Derive value from a named built-in function | `{"expr": "age_from_dob", "sources": ["date_of_birth"]}` |
| `drop` | Intentionally discard; no answer carried forward | `{"reason": "Field removed in v2"}` |

### Compute built-in expressions

| Expression | Description |
|---|---|
| `age_from_dob` | Current age in years from a `YYYY-MM-DD` birth date |
| `upper` | Convert string to uppercase |
| `lower` | Convert string to lowercase |
| `strip` | Trim leading/trailing whitespace |
| `concat` | Concatenate sources with `params["separator"]` |

### Transform hints

Tenants can embed hints inside a question's `rules` JSON to help the diff
service make smarter auto-generated rules:

```json
{
  "transform_hints": {
    "renamed_from": "old_unique_key",
    "value_mapping": {"M": "Male", "F": "Female"}
  }
}
```

- `renamed_from` causes a `RENAME` rule instead of a `DROP` + `DEFAULT_VALUE` pair.
- `value_mapping` pre-fills the `MAP_VALUES` mapping when options changed.

---

## Auto-Generation (Version Diff)

When `publish_tenant_template_definition` is called, the service automatically
generates a draft `TransformRuleSet` if there was a previous active version.
The diff algorithm:

1. Build `{unique_key: Question}` maps for source and target versions.
2. Check target questions for `transform_hints.renamed_from` to detect renames.
3. For each source key:
   - Present in target, same type → `IDENTITY`
   - Present in target, different type → `COERCE_TYPE` (manual review required)
   - Options changed → `MAP_VALUES` (with best-effort mapping)
   - Only in source → `DROP`
4. For each target key not matched by a rename:
   - Only in target → `DEFAULT_VALUE` with `null` (admin must fill in)

The result has `auto_generated=true` and `status=draft`. The admin must review
and adjust rules before publishing.

---

## API Endpoints

All endpoints require the `X-Tenant-ID` header.

### Rule Set Management

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/templates/{id}/transform-rules/generate` | Auto-generate draft from version diff |
| `POST` | `/v1/templates/{id}/transform-rules` | Manually create a rule set |
| `GET` | `/v1/templates/{id}/transform-rules` | List all rule sets |
| `GET` | `/v1/templates/{id}/transform-rules/{rsid}` | Get rule set with its rules |
| `PATCH` | `/v1/templates/{id}/transform-rules/{rsid}` | Update metadata (draft only) |
| `POST` | `/v1/templates/{id}/transform-rules/{rsid}/publish` | Freeze rule set |
| `POST` | `/v1/templates/{id}/transform-rules/{rsid}/archive` | Archive rule set |
| `DELETE` | `/v1/templates/{id}/transform-rules/{rsid}` | Delete draft rule set |

### Per-Rule Management

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/templates/{id}/transform-rules/{rsid}/rules` | Add a rule |
| `PATCH` | `/v1/templates/{id}/transform-rules/{rsid}/rules/{rid}` | Update a rule |
| `DELETE` | `/v1/templates/{id}/transform-rules/{rsid}/rules/{rid}` | Remove a rule |

### Apply / Preview

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/templates/{id}/transform-rules/{rsid}/preview` | Dry-run on one submission |
| `POST` | `/v1/templates/{id}/transform-rules/{rsid}/apply/{sub_id}` | Apply to one submission |
| `POST` | `/v1/templates/{id}/transform-rules/{rsid}/bulk-apply` | Bulk migrate all eligible submissions |

### Audit

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/submissions/{id}/transform-history` | Full transform audit trail for a submission |

---

## End-to-End Flow

```
Tenant publishes new template version (v2)
         │
         ▼
System auto-generates draft TransformRuleSet (v1 → v2)
         │
         ▼
Admin reviews draft rules via GET /transform-rules/{rsid}
         │
         ├── Adjust MAP_VALUES mappings for renamed options
         ├── Fill DEFAULT_VALUE params for new required fields
         ├── Mark critical rules as is_required=true
         └── Remove DROP rules for fields that were actually renamed
         │
         ▼
Admin previews on a sample submission
  POST /transform-rules/{rsid}/preview
  { "submission_id": "..." }
  → Returns before/after snapshot + validation_errors + would_succeed
         │
         ▼
Admin publishes the rule set
  POST /transform-rules/{rsid}/publish
         │
         ▼
Admin runs bulk migration (dry_run first)
  POST /transform-rules/{rsid}/bulk-apply
  { "dry_run": true }
  → Returns summary: total / succeeded / failed / skipped
         │
         ▼
Admin runs real bulk migration
  POST /transform-rules/{rsid}/bulk-apply
  { "dry_run": false }
         │
         ▼
Submissions now point to v2; old answers replaced with transformed answers
TransformLog rows written for every submission (audit trail)
```

---

## File Structure

```
app/
  models/
    enums.py                              ← TransformOperation, RuleSetStatus
    tenant/
      transform.py                        ← TransformRuleSet, TransformRule, TransformLog
  schemas/
    transforms/
      __init__.py
      rule.py                             ← RuleSet + Rule create/read/update schemas
      log.py                              ← TransformLogRead
      preview.py                          ← Preview + BulkMigrate schemas
  services/
    transforms/
      __init__.py
      diff_service.py                     ← Auto-generate rules from version diff
      executor.py                         ← Apply rules to a submission
      rule_service.py                     ← CRUD for rule sets and rules
      bulk_migrate.py                     ← Bulk migration orchestrator
  routes/
    transforms/
      __init__.py
      routes.py                           ← 14 API endpoints
alembic/
  versions/
    003_transform_tables.py               ← DB migration: 3 new tables + 2 enums
```

---

## Acceptance Criteria

### AC1 — Auto-Generate Draft Rule Set on Publish

**Given** a tenant admin publishes a new template version  
**When** a previous active version exists  
**Then** a draft `TransformRuleSet` is automatically created with:
- `auto_generated=true`
- `status=draft`
- `IDENTITY` rules for unchanged questions
- `COERCE_TYPE` rules for type-changed questions
- `DROP` rules for removed questions
- `DEFAULT_VALUE` rules for new questions
- `MAP_VALUES` rules when option lists changed

### AC2 — Manual Rule Editing

**Given** a draft rule set exists  
**When** an admin adds, updates, or removes rules via the API  
**Then** changes are persisted and the rule set remains in `draft` status

### AC3 — Publish Requires At Least One Rule

**Given** a draft rule set with zero rules  
**When** the admin calls `/publish`  
**Then** the API returns `400 Bad Request`

### AC4 — Published Rule Set is Immutable

**Given** a rule set with `status=published`  
**When** an admin attempts to add, update, or delete a rule  
**Then** the API returns `403 Forbidden`

### AC5 — Preview Does Not Modify Data

**Given** a published rule set and a DRAFT submission  
**When** the admin calls `/preview` with the submission ID  
**Then**:
- The submission's answers are not modified
- The submission's `template_version_id` is not changed
- A `TransformLog` is written with `is_preview=true`
- The response includes `before_snapshot`, `after_snapshot`, `errors`, `warnings`, `validation_errors`, and `would_succeed`

### AC6 — Apply Migrates Eligible Submissions

**Given** a published rule set and a DRAFT or RETURNED submission  
**When** the admin calls `/apply/{submission_id}`  
**Then**:
- Old `SubmissionAnswer` rows are replaced with new rows pointing to target questions
- `Submission.template_version_id` is updated to `target_version_id`
- A `TransformLog` is written with `is_preview=false`
- Post-transform validation passes against the target version

### AC7 — Non-Eligible Submissions Are Rejected

**Given** a published rule set and a submission with status `SUBMITTED`, `APPROVED`, or `COMPLETED`  
**When** the admin calls `/apply/{submission_id}`  
**Then** the API returns `400 Bad Request`

### AC8 — Required Rule Failure Blocks Migration

**Given** a rule with `is_required=true` that cannot transform its value  
**When** the admin applies the rule set to a submission  
**Then** the submission is NOT migrated and a `TransformLog` is written with `is_preview=true` and the errors listed

### AC9 — Bulk Migration Summary

**Given** a published rule set with eligible submissions  
**When** the admin calls `/bulk-apply` with `dry_run=false`  
**Then** the response contains:
- `total`: count of eligible submissions found
- `succeeded`: count migrated successfully
- `failed`: count where migration failed
- `results`: per-submission outcome with `log_id`

### AC10 — Transform Audit Trail

**Given** any apply or preview operation has been executed  
**When** the admin calls `GET /submissions/{id}/transform-history`  
**Then** all `TransformLog` entries for that submission are returned, newest first

---

**Last Updated:** 2026-03-16  
**Version:** 1.0  
**Status:** Implemented
