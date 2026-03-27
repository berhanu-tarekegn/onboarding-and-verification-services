# Answer Transform Rules — Test Plan

## Overview

This document covers the testing strategy for the **Answer Transform Rules**
feature. The feature allows tenant admins to define, preview, and apply
migration rules that convert submission answers from one template version to
another when questions change between versions.

**Feature story:** [answer-transform-rules.md](../stories/answer-transform-rules.md)

**Testing scope:**
- TransformRuleSet lifecycle (draft → publish → archive)
- All nine transform operations
- Auto-generation (version diff service)
- Single-submission apply and preview
- Bulk migration (dry-run and real)
- Audit log completeness
- Error and edge case handling

---

## Test Environment Setup

### Prerequisites

- Application running locally (`uvicorn app.main:app --reload`)
- At least one test tenant provisioned (e.g., `Alpha Bank`, slug `alpha`)
- Migration `003_transform_tables.py` applied
- API client configured (Postman, Bruno, or curl)
- Database access for SQL validation queries

### Provision Test Tenant

```bash
POST /v1/tenants
Content-Type: application/json

{
  "name": "Alpha Bank",
  "slug": "alpha",
  "is_active": true
}
```

Save the returned `id` as `TENANT_ID`.  
All subsequent requests must include: `X-Tenant-ID: {TENANT_ID}`

### Baseline Setup

```bash
# 1. Create a KYC baseline template
POST /v1/baseline-templates
{
  "name": "KYC Baseline",
  "template_type": "kyc",
  "initial_version": {
    "version_tag": "1.0.0",
    "question_groups": [
      {
        "unique_key": "personal_info",
        "title": "Personal Information",
        "display_order": 1,
        "questions": [
          { "unique_key": "full_name",     "label": "Full Name",     "field_type": "text",  "required": true,  "display_order": 1 },
          { "unique_key": "dob",           "label": "Date of Birth", "field_type": "date",  "required": true,  "display_order": 2 },
          { "unique_key": "gender",        "label": "Gender",        "field_type": "radio", "required": true,  "display_order": 3,
            "options": [{"value": "M", "display_order": 1}, {"value": "F", "display_order": 2}] },
          { "unique_key": "phone",         "label": "Phone Number",  "field_type": "text",  "required": false, "display_order": 4 }
        ]
      }
    ]
  }
}

# 2. Publish the baseline version (use returned version ID)
POST /v1/baseline-templates/{btid}/versions/{vid}/publish
```

---

## Test Data Reference

Throughout this plan, use these variable names:

| Variable | Description |
|---|---|
| `TENANT_ID` | Test tenant UUID |
| `TEMPLATE_ID` | Tenant template UUID |
| `V1_ID` | Source template version UUID (old) |
| `V2_ID` | Target template version UUID (new) |
| `RULESET_ID` | TransformRuleSet UUID |
| `SUB_ID` | Test submission UUID |

---

## Part 1 — Rule Set Lifecycle Tests

### TC-LS-01: Auto-generate rule set on version publish

**Setup:** Create tenant template with V1, add a DRAFT submission.

**Steps:**
1. Create a new template version V2 (with a modified question set).
2. `POST /v1/templates/{TEMPLATE_ID}/versions/{V2_ID}/publish`

**Expected:**
- Response: `200 OK`, `is_draft=false`
- A `TransformRuleSet` is auto-created: `status=draft`, `auto_generated=true`
- Rule set is visible in `GET /v1/templates/{TEMPLATE_ID}/transform-rules`

**SQL validation:**
```sql
SELECT id, status, auto_generated FROM transform_rule_sets
WHERE template_id = '{TEMPLATE_ID}';
```

---

### TC-LS-02: Manually create a rule set

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules
X-Tenant-ID: {TENANT_ID}
{
  "source_version_id": "{V1_ID}",
  "target_version_id": "{V2_ID}",
  "changelog": "Manual rule set for migration test",
  "rules": [
    {
      "source_unique_key": "full_name",
      "target_unique_key": "full_name",
      "operation": "identity",
      "params": {},
      "display_order": 1
    }
  ]
}
```

**Expected:** `201 Created`, `auto_generated=false`, `status=draft`, one rule returned.

---

### TC-LS-03: Duplicate version pair rejected

**Steps:** Repeat `TC-LS-02` with the same `source_version_id` and `target_version_id`.

**Expected:** `409 Conflict`

---

### TC-LS-04: Publish a draft rule set

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/publish
X-Tenant-ID: {TENANT_ID}
```

**Expected:** `200 OK`, `status=published`

---

### TC-LS-05: Publish empty rule set is rejected

**Setup:** Create a rule set with no rules.

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{EMPTY_RULESET_ID}/publish
```

**Expected:** `400 Bad Request`, error message mentions "no rules"

---

### TC-LS-06: Edit published rule set is forbidden

**Steps:** After TC-LS-04, attempt to add a rule:
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/rules
{
  "source_unique_key": "dob",
  "target_unique_key": "date_of_birth",
  "operation": "rename",
  "params": {}
}
```

**Expected:** `403 Forbidden`

---

### TC-LS-07: Archive a published rule set

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/archive
```

**Expected:** `200 OK`, `status=archived`

---

### TC-LS-08: Delete a draft rule set

**Setup:** Create a new draft rule set.

**Steps:**
```bash
DELETE /v1/templates/{TEMPLATE_ID}/transform-rules/{DRAFT_RULESET_ID}
```

**Expected:** `204 No Content`

---

### TC-LS-09: Delete published rule set is forbidden

**Steps:** Attempt to delete a published rule set.

**Expected:** `403 Forbidden`

---

## Part 2 — Transform Operation Tests

For each test, set up V1 with the listed source question, V2 with the target
question, and a DRAFT submission with the given input answer. Verify the output.

### TC-OP-01: IDENTITY

| | Value |
|---|---|
| Source question | `full_name` (text) |
| Target question | `full_name` (text) |
| Input answer | `"John Doe"` |
| Expected output | `"John Doe"` |

Rule:
```json
{"source_unique_key": "full_name", "target_unique_key": "full_name",
 "operation": "identity", "params": {}}
```

---

### TC-OP-02: RENAME

| | Value |
|---|---|
| Source question | `dob` (date) |
| Target question | `date_of_birth` (date) |
| Input answer | `"1990-03-16"` |
| Expected output | `"1990-03-16"` |

Rule:
```json
{"source_unique_key": "dob", "target_unique_key": "date_of_birth",
 "operation": "rename", "params": {}}
```

---

### TC-OP-03: MAP_VALUES — known mapping

| | Value |
|---|---|
| Source question | `gender` (radio: `["M", "F"]`) |
| Target question | `gender` (radio: `["Male", "Female", "Other"]`) |
| Input answer | `"M"` |
| Expected output | `"Male"` |

Rule:
```json
{"source_unique_key": "gender", "target_unique_key": "gender",
 "operation": "map_values",
 "params": {"mapping": {"M": "Male", "F": "Female"}, "default": "Other"}}
```

---

### TC-OP-04: MAP_VALUES — unknown value uses default

Input answer: `"X"` (not in mapping).  
Expected output: `"Other"` (the default).  
Expected warning emitted.

---

### TC-OP-05: MAP_VALUES — checkbox multi-value

| | Value |
|---|---|
| Source question | `interests` (checkbox: `["A", "B", "C"]`) |
| Target question | `interests` (checkbox: `["Alpha", "Beta", "Gamma"]`) |
| Input answer | `"A,B"` |
| Expected output | `"Alpha,Beta"` |

---

### TC-OP-06: COERCE_TYPE — text to date

| | Value |
|---|---|
| Source question | `birth_date` (text) |
| Target question | `birth_date` (date) |
| Input answer | `"03/16/1990"` |
| Expected output | `"1990-03-16"` |

Rule params: `{"from_type": "text", "to_type": "date", "format": "%m/%d/%Y"}`

---

### TC-OP-07: COERCE_TYPE — unparseable value produces error

Input: `"not-a-date"` with format `"%m/%d/%Y"`.  
Expected: transform error added to log, answer becomes `null`.

---

### TC-OP-08: SPLIT

| | Value |
|---|---|
| Source question | `full_name` (text) |
| Target question: first | `first_name` (text), `index=0` |
| Target question: last | `last_name` (text), `index=1` |
| Input answer | `"John Doe"` |
| Expected output | `first_name="John"`, `last_name="Doe"` |

Two rules sharing the same `source_unique_key=full_name`.

---

### TC-OP-09: SPLIT — index out of range produces warning

Input: `"John"` (no space), `index=1`.  
Expected: answer becomes `null`, warning emitted.

---

### TC-OP-10: MERGE

| | Value |
|---|---|
| Source questions | `first_name="John"`, `last_name="Doe"` |
| Target question | `full_name` (text) |
| Expected output | `"John Doe"` |

Rule params: `{"sources": ["first_name", "last_name"], "separator": " "}`

---

### TC-OP-11: DEFAULT_VALUE — static value

| | Value |
|---|---|
| Target question | `consent_given` (text, new in V2) |
| Expected output | `"pending"` |

Rule params: `{"value": "pending"}`

---

### TC-OP-12: DEFAULT_VALUE — null value

Rule params: `{"value": null}`.  
Expected output: answer is `null` (question left blank).

---

### TC-OP-13: COMPUTE — age_from_dob

| | Value |
|---|---|
| Source question | `date_of_birth` (date) |
| Target question | `age` (text) |
| Input answer | `"1990-03-16"` |
| Expected output | `"36"` (as of 2026-03-16) |

Rule params: `{"expr": "age_from_dob", "sources": ["date_of_birth"]}`

---

### TC-OP-14: COMPUTE — unknown expression produces error

Rule params: `{"expr": "unknown_fn"}`.  
Expected: error added, answer becomes `null`.

---

### TC-OP-15: DROP

Source question `phone` is dropped in V2.  
Rule: `{"source_unique_key": "phone", "target_unique_key": "phone", "operation": "drop", "params": {"reason": "Removed in v2"}}`  
Expected: `phone` does not appear in `after_snapshot`.

---

## Part 3 — Preview Tests

### TC-PR-01: Preview returns correct snapshots

**Setup:** Published rule set. DRAFT submission with answers for all V1 questions.

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/preview
X-Tenant-ID: {TENANT_ID}
{"submission_id": "{SUB_ID}"}
```

**Expected:**
- `200 OK`
- `before_snapshot`: original answers by unique_key
- `after_snapshot`: transformed answers by unique_key
- `would_succeed: true` if no errors or validation failures
- Submission answers unchanged in DB
- `Submission.template_version_id` unchanged in DB

**SQL validation (confirm no modification):**
```sql
SELECT template_version_id FROM submissions WHERE id = '{SUB_ID}';
-- Must still equal V1_ID
```

---

### TC-PR-02: Preview writes log with is_preview=true

**SQL validation:**
```sql
SELECT id, is_preview FROM transform_logs
WHERE submission_id = '{SUB_ID}'
ORDER BY applied_at DESC
LIMIT 1;
-- is_preview must be true
```

---

### TC-PR-03: Preview on non-DRAFT submission

**Setup:** A submission with `status=SUBMITTED`.

**Steps:** Call preview with this submission.

**Expected:** `400 Bad Request` — only DRAFT/RETURNED eligible for migration.

> **Note:** Preview calls `apply_rule_set` with `is_preview=True`. The executor's
> status check is enforced on non-preview real applies; preview itself will
> proceed and return results without modifying data, allowing admins to inspect
> what the transform would produce even on submitted records.

---

### TC-PR-04: Preview with validation errors

**Setup:** A rule set whose output leaves a required V2 field blank.

**Expected:**
- `validation_errors` list is non-empty
- `would_succeed: false`

---

## Part 4 — Apply Tests

### TC-AP-01: Successful single apply

**Setup:** Published rule set. DRAFT submission.

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/apply/{SUB_ID}
```

**Expected:**
- `200 OK`, returns `TransformLog`
- `is_preview=false`
- `errors=[]`

**SQL validation:**
```sql
-- Submission now points to V2
SELECT template_version_id FROM submissions WHERE id = '{SUB_ID}';
-- Must equal V2_ID

-- New answers reference V2 questions
SELECT sa.answer, q.unique_key
FROM submission_answers sa
JOIN questions q ON q.id = sa.question_id
WHERE sa.submission_id = '{SUB_ID}';
```

---

### TC-AP-02: Apply to RETURNED submission

Same as TC-AP-01 but with `status=RETURNED`. Expected: success.

---

### TC-AP-03: Apply to ineligible submission

**Setup:** Submission with `status=SUBMITTED`.

**Steps:** Call apply.

**Expected:** `400 Bad Request`

---

### TC-AP-04: Required rule failure blocks migration

**Setup:** Rule with `is_required=true` targeting a coerce from unparseable text.

**Steps:** Apply to submission with invalid value.

**Expected:**
- `422 Unprocessable Entity`
- Submission NOT migrated (still on V1)
- A `TransformLog` written with `is_preview=true`, `errors` populated

---

### TC-AP-05: Draft rule set cannot be applied

**Steps:** Attempt to apply a `status=draft` rule set.

**Expected:** `400 Bad Request` — only published rule sets can be applied.

---

## Part 5 — Bulk Migration Tests

### TC-BK-01: Dry run — no data modified

**Setup:** 5 DRAFT submissions on V1, 2 SUBMITTED on V1.

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/bulk-apply
{"dry_run": true}
```

**Expected:**
- `200 OK`
- `total=5` (SUBMITTED submissions skipped by query)
- `dry_run=true`
- All submissions still on V1 in DB

**SQL validation:**
```sql
SELECT COUNT(*) FROM submissions
WHERE template_version_id = '{V1_ID}';
-- Must still be 5 (all DRAFT ones)
```

---

### TC-BK-02: Real bulk migration

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/bulk-apply
{"dry_run": false}
```

**Expected:**
- `succeeded=5` (all eligible migrated)
- `failed=0`
- Each result has a `log_id`

**SQL validation:**
```sql
SELECT COUNT(*) FROM submissions WHERE template_version_id = '{V2_ID}';
-- Must equal 5
```

---

### TC-BK-03: Partial list of submission IDs

**Steps:**
```bash
POST /v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/bulk-apply
{"dry_run": false, "submission_ids": ["{SUB_ID_1}", "{SUB_ID_2}"]}
```

**Expected:** `total=2`, only those two submissions migrated.

---

### TC-BK-04: Mixed success and failure

**Setup:** 3 DRAFT submissions, one has an invalid value for a `is_required=true` coerce rule.

**Expected:**
- `succeeded=2`, `failed=1`
- Failed submission still on V1
- `results[2].errors` non-empty

---

## Part 6 — Audit Trail Tests

### TC-AU-01: Transform history for a submission

**Steps:**
```bash
GET /v1/submissions/{SUB_ID}/transform-history
```

**Expected:**
- List of `TransformLog` entries, newest first
- Each entry has `before_snapshot`, `after_snapshot`, `applied_at`, `applied_by`

---

### TC-AU-02: Preview logs are flagged

After a preview call, confirm the most recent log has `is_preview=true`.

---

### TC-AU-03: Real apply logs are flagged

After a real apply, confirm the most recent log has `is_preview=false`.

---

### TC-AU-04: Log snapshots are accurate

After a real apply, verify:
- `before_snapshot` matches the answers the submission had before migration
- `after_snapshot` matches the new answers in `submission_answers`

```sql
-- Compare after_snapshot to actual answers
SELECT q.unique_key, sa.answer
FROM submission_answers sa
JOIN questions q ON q.id = sa.question_id
WHERE sa.submission_id = '{SUB_ID}';
```

---

## Part 7 — Edge Case and Error Tests

### TC-EC-01: Version from different template rejected

**Steps:** Call generate with `source_version_id` belonging to a different template.

**Expected:** `422 Unprocessable Entity`

---

### TC-EC-02: Source question with no answer in submission

**Setup:** IDENTITY rule for `phone`; submission has no answer for `phone`.

**Expected:** `after_snapshot["phone"]=null`, no error.

---

### TC-EC-03: MERGE when one source is blank

Input: `first_name="John"`, `last_name=""`.  
Expected output: `"John"` (trailing separator stripped).

---

### TC-EC-04: MAP_VALUES on null answer

Input: `null` answer.  
Expected: output remains `null`, no error.

---

### TC-EC-05: Get rule set for wrong template (path mismatch)

**Steps:** Call `GET /templates/{WRONG_TEMPLATE_ID}/transform-rules/{VALID_RULESET_ID}`.

**Expected:** `404 Not Found`

---

### TC-EC-06: Apply rule set to submission from wrong template

**Setup:** RULESET_ID belongs to Template A. SUB_ID belongs to Template B.

**Expected:** Either a `404` (submission not found under that rule set's version) or no answers migrated, depending on version isolation.

---

## Part 8 — Auto-Generation Quality Tests

### TC-AG-01: Identical questions produce IDENTITY rules

Create V2 with exactly the same questions as V1. Publish V2.

**Expected:** All rules in auto-generated set are `operation=identity`.

---

### TC-AG-02: Removed question produces DROP rule

V1 has `phone`; V2 does not. Publish V2.

**Expected:** Rule `{"source_unique_key": "phone", "operation": "drop"}` exists.

---

### TC-AG-03: New question produces DEFAULT_VALUE rule

V1 lacks `email`; V2 adds it. Publish V2.

**Expected:** Rule `{"target_unique_key": "email", "operation": "default_value", "params": {"value": null}}` exists.

---

### TC-AG-04: Type-changed question produces COERCE_TYPE rule

V1 has `birth_year` as `text`; V2 changes it to `date`. Publish V2.

**Expected:** Rule `{"operation": "coerce_type", "params": {"from_type": "text", "to_type": "date"}}`.

---

### TC-AG-05: Option change produces MAP_VALUES rule

V1 has `gender` with options `["M", "F"]`; V2 changes to `["Male", "Female"]`. Publish V2.

**Expected:** Rule `{"operation": "map_values", "params": {"mapping": {"M": "M", "F": "F"}, ...}}` — diff maps identical surviving values; admin must correct non-matching ones.

---

### TC-AG-06: transform_hints.renamed_from produces RENAME rule

V2 question `date_of_birth` includes:
```json
{"rules": {"transform_hints": {"renamed_from": "dob"}}}
```

Publish V2.

**Expected:** Rule `{"source_unique_key": "dob", "target_unique_key": "date_of_birth", "operation": "rename"}` — no DROP for `dob`, no DEFAULT_VALUE for `date_of_birth`.

---

## Test Coverage Matrix

| Area | Unit | Integration | Manual |
|---|---|---|---|
| IDENTITY / RENAME ops | ✅ | ✅ | - |
| MAP_VALUES (single + multi) | ✅ | ✅ | - |
| COERCE_TYPE (date) | ✅ | ✅ | - |
| SPLIT / MERGE | ✅ | ✅ | - |
| DEFAULT_VALUE / DROP | ✅ | ✅ | - |
| COMPUTE built-ins | ✅ | ✅ | - |
| Rule set lifecycle | - | ✅ | ✅ |
| Auto-generation diff | ✅ | ✅ | - |
| Preview (no-op) | - | ✅ | ✅ |
| Single apply | - | ✅ | ✅ |
| Bulk apply (dry-run) | - | ✅ | ✅ |
| Bulk apply (real) | - | ✅ | ✅ |
| Audit trail | - | ✅ | ✅ |
| Required rule blocking | ✅ | ✅ | - |
| Ineligible submission rejection | - | ✅ | - |

---

## Quick Commands

```bash
# Run the server
uvicorn app.main:app --reload --port 8000

# Apply migrations (tenant schema)
alembic -x tenant_schema=tenant_alpha upgrade head

# Quick smoke test: list rule sets
curl -s -H "X-Tenant-ID: {TENANT_ID}" \
  http://localhost:8000/v1/templates/{TEMPLATE_ID}/transform-rules | jq .

# Dry-run bulk migrate
curl -s -X POST \
  -H "X-Tenant-ID: {TENANT_ID}" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}' \
  http://localhost:8000/v1/templates/{TEMPLATE_ID}/transform-rules/{RULESET_ID}/bulk-apply | jq .
```

---

## Database Validation Queries

```sql
-- Count rule sets per template
SELECT template_id, status, COUNT(*) 
FROM transform_rule_sets 
GROUP BY template_id, status;

-- List all rules in a rule set
SELECT display_order, operation, source_unique_key, target_unique_key, params
FROM transform_rules
WHERE rule_set_id = '{RULESET_ID}'
ORDER BY display_order;

-- Verify submission migrated to target version
SELECT id, template_version_id, status 
FROM submissions 
WHERE id = '{SUB_ID}';

-- Audit log for a submission
SELECT applied_at, applied_by, is_preview, 
       jsonb_array_length(errors) AS error_count,
       jsonb_array_length(warnings) AS warning_count
FROM transform_logs
WHERE submission_id = '{SUB_ID}'
ORDER BY applied_at DESC;

-- Compare before/after snapshots
SELECT before_snapshot, after_snapshot
FROM transform_logs
WHERE submission_id = '{SUB_ID}'
  AND is_preview = false
ORDER BY applied_at DESC
LIMIT 1;
```

---

**Last Updated:** 2026-03-16  
**Version:** 1.0  
**Status:** Ready for Testing
