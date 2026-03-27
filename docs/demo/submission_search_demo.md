# Submission Search Demo

This demo shows how tenant-defined submission filters and verification-result filters work for:

- portal search screens
- third-party tenant services
- verification-aware submission retrieval

## Rules Config

Add this to the tenant template definition `rules_config`:

```json
{
  "submission_search": {
    "filters": [
      {
        "key": "national_id",
        "label": "National ID",
        "source": "form_data",
        "path": "national_id",
        "operators": ["eq", "contains"]
      },
      {
        "key": "name_match_score",
        "label": "Name Match Score",
        "source": "verification",
        "path": "steps.name_match.result.score",
        "operators": ["gte", "lte"]
      }
    ]
  }
}
```

These definitions are discovered from active tenant template versions and exposed by the API.

## Discover the Filter Catalog

```bash
curl -sS "http://127.0.0.1:7090/api/v1/submissions/search-config" \
  -H "Authorization: Bearer REPLACE_WITH_TENANT_OR_SERVICE_TOKEN" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID"
```

Expected response shape:

```json
{
  "native_filters": [
    "status",
    "template_id",
    "product_id",
    "submitter_id",
    "external_ref",
    "created_after",
    "created_before"
  ],
  "verification_filters": [
    "verification_status",
    "verification_decision",
    "verification_kyc_level",
    "verification_current_step_key"
  ],
  "configured_filters": [
    {
      "key": "national_id",
      "source": "form_data",
      "path": "national_id",
      "operators": ["contains", "eq"]
    },
    {
      "key": "name_match_score",
      "source": "verification",
      "path": "steps.name_match.result.score",
      "operators": ["gte", "lte"]
    }
  ],
  "warnings": []
}
```

## Search for Approved KYC Results

This example combines:

- native verification filter `verification_decision`
- configured filter `national_id`
- configured verification-step filter `name_match_score`

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/submissions/search" \
  -H "Authorization: Bearer REPLACE_WITH_TENANT_OR_SERVICE_TOKEN" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "verification_decision": "approved",
    "criteria": [
      {"key": "national_id", "op": "eq", "value": "FN-123"},
      {"key": "name_match_score", "op": "gte", "value": 0.95}
    ],
    "sort_by": "created_at",
    "sort_order": "desc",
    "limit": 25
  }'
```

Expected result shape:

```json
[
  {
    "id": "REPLACE_WITH_SUBMISSION_ID",
    "status": "draft",
    "submitter_id": "customer-001",
    "external_ref": null,
    "verification": {
      "status": "completed",
      "decision": "approved",
      "kyc_level": "level_2",
      "current_step_key": null,
      "steps": [
        {"step_key": "phone_otp", "status": "completed", "outcome": "pass"},
        {"step_key": "fayda_lookup", "status": "completed", "outcome": "pass"},
        {"step_key": "name_match", "status": "completed", "outcome": "pass"}
      ]
    }
  }
]
```

## Third-Party Tenant Service Flow

1. Obtain a machine token:

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/auth/service-token/REPLACE_WITH_REALM" \
  -H "Content-Type: application/json" \
  -d '{"scope":"submissions.read"}'
```

2. Call the search-config endpoint to discover allowed filters.
3. Call the search endpoint with the configured filter keys and `X-Tenant-ID`.

This keeps the partner API aligned with the portal because both use the same filter catalog and same submission search contract.
