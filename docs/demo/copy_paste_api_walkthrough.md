# Copy-Paste API Walkthrough

This is the fastest end-to-end local guide for this repo using the current design:

- local PostgreSQL
- local Keycloak or Docker Keycloak
- local Temporal CLI
- dedicated platform realm `oaas-platform`
- Keycloak admin realm `master`

This walkthrough covers:

1. local setup
2. authentication
3. tenant initialization
4. authz policy endpoints
5. baseline template CRUD
6. tenant template CRUD
7. product CRUD
8. submission CRUD and workflow
9. verification flow
10. submission search
11. transform rule CRUD and apply
12. cleanup

Supporting files:

- [local_development_env.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/local_development_env.md)
- [keycloak_tenant_realm_setup.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/keycloak_tenant_realm_setup.md)
- [real_world_onboarding_sample.json](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json)

## 1. Start the Local Stack

### 1.1 Start Keycloak

For a local Keycloak install:

```bash
cd /path/to/keycloak
export KC_BOOTSTRAP_ADMIN_USERNAME=admin
export KC_BOOTSTRAP_ADMIN_PASSWORD=admin
bin/kc.sh start-dev --http-host=127.0.0.1 --http-port=8080
```

For Docker Keycloak instead:

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
docker compose -f docker-compose.dev.yaml up keycloak -d
```

### 1.2 Start Temporal CLI

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
./scripts/start_temporal_local.sh
```

### 1.3 Bootstrap Keycloak

For a local Keycloak install:

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
export KEYCLOAK_HOME=/path/to/keycloak
./scripts/bootstrap_keycloak_local.sh
```

For Docker Keycloak:

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
./scripts/bootstrap_keycloak_local.sh
```

### 1.4 Start the API and Worker

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 7090
```

In another terminal:

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
uv run python -m app.temporal.worker
```

### 1.5 Export Shared Variables

```bash
export BASE_URL=http://127.0.0.1:7090
export SAMPLE_JSON=/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json
export INITIALIZATION_HEADER=

export PLATFORM_REALM=oaas-platform
export PLATFORM_USERNAME=platform.admin
export PLATFORM_PASSWORD=117f19dd3c2c8164c9ee2642e0da6f65

export TENANT_KEY=$(jq -r '.tenant.tenant_key' "$SAMPLE_JSON")
export TENANT_NAME=$(jq -r '.tenant.name' "$SAMPLE_JSON")
export TENANT_ADMIN_USERNAME="${TENANT_KEY}_tenant_admin"
export TENANT_ADMIN_PASSWORD=35314f15d69125e4b6789d74ff26e18b
```

Health checks:

```bash
curl -sS http://127.0.0.1:7090/health | jq
curl -sS http://127.0.0.1:8233 | head
```

## 2. Authentication

### 2.1 Platform Login

```bash
export PLATFORM_LOGIN_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/auth/login/$PLATFORM_REALM" \
    -H "Content-Type: application/json" \
    -d '{
      "username": "platform.admin",
      "password": "117f19dd3c2c8164c9ee2642e0da6f65"
    }'
)

echo "$PLATFORM_LOGIN_RESPONSE" | jq
export PLATFORM_TOKEN=$(echo "$PLATFORM_LOGIN_RESPONSE" | jq -r '.access_token')
```

### 2.2 Platform Auth Context

```bash
curl -sS "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" | jq
```

You should see:

- `realm = oaas-platform`
- `roles` includes `super_admin`

## 3. Tenant Initialization

### 3.1 Create Tenant

```bash
export TENANT_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/tenants" \
    -H "Authorization: Bearer $PLATFORM_TOKEN" \
    -H "Content-Type: application/json" \
    $INITIALIZATION_HEADER \
    -d "{
      \"name\": \"$TENANT_NAME\",
      \"tenant_key\": \"$TENANT_KEY\"
    }"
)

echo "$TENANT_CREATE_RESPONSE" | jq
export TENANT_ID=$(echo "$TENANT_CREATE_RESPONSE" | jq -r '.id')
```

### 3.2 List/Get/Patch/Delete Tenant

```bash
curl -sS "$BASE_URL/api/v1/tenants" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" | jq

curl -sS "$BASE_URL/api/v1/tenants/$TENANT_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/tenants/$TENANT_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Abyssinia Corporate Bank Updated"
  }' | jq
```

Do not delete yet if you want to continue the walkthrough. Cleanup is at the end.

### 3.3 Login as Tenant Admin

```bash
export TENANT_LOGIN_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/auth/login/$TENANT_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"$TENANT_ADMIN_USERNAME\",
      \"password\": \"$TENANT_ADMIN_PASSWORD\"
    }"
)

echo "$TENANT_LOGIN_RESPONSE" | jq
export TENANT_TOKEN=$(echo "$TENANT_LOGIN_RESPONSE" | jq -r '.access_token')
```

### 3.4 Tenant Auth Context

```bash
curl -sS "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $TENANT_TOKEN" | jq
```

## 4. Global and Tenant AuthZ Policy

### 4.1 Get and Update Global Policy

```bash
curl -sS "$BASE_URL/api/v1/authz/policy" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  $INITIALIZATION_HEADER | jq

curl -sS -X PUT "$BASE_URL/api/v1/authz/policy" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  $INITIALIZATION_HEADER \
  -d '{
    "policy": {
      "mode": "merge",
      "roles": {
        "maker": [
          "submissions.create",
          "submissions.read",
          "submissions.update",
          "submissions.submit",
          "submissions.comment"
        ],
        "checker": [
          "submissions.read_all",
          "submissions.transition",
          "submissions.comment"
        ]
      }
    }
  }' | jq
```

### 4.2 Get and Update Tenant Policy

```bash
curl -sS "$BASE_URL/api/v1/tenants/$TENANT_ID/authz/policy" \
  -H "Authorization: Bearer $TENANT_TOKEN" | jq

curl -sS -X PUT "$BASE_URL/api/v1/tenants/$TENANT_ID/authz/policy" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "mode": "merge",
      "roles": {
        "platform_admin": [
          "products.read",
          "products.create",
          "products.update",
          "products.delete"
        ]
      }
    }
  }' | jq
```

### 4.3 Realm-Linked and Unlinked Policy Endpoints

```bash
curl -sS "$BASE_URL/api/v1/authz/policy/$TENANT_KEY" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  $INITIALIZATION_HEADER | jq

curl -sS -X PUT "$BASE_URL/api/v1/authz/realm-policy/preboarding_partner" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  $INITIALIZATION_HEADER \
  -d '{
    "policy": {
      "mode": "merge",
      "roles": {
        "partner_service": ["submissions.read"]
      }
    }
  }' | jq
```

## 5. Baseline Template CRUD

### 5.1 Create Baseline Template

```bash
export BASELINE_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/baseline-templates" \
    -H "Authorization: Bearer $PLATFORM_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Retail KYC Baseline",
      "description": "Baseline for account onboarding",
      "category": "kyc",
      "template_type": "kyc",
      "level": 1,
      "initial_version": {
        "version_tag": "1.0.0",
        "rules_config": {},
        "changelog": "Initial baseline",
        "question_groups": [
          {
            "unique_key": "identity",
            "title": "Identity",
            "display_order": 1,
            "questions": [
              {"unique_key":"first_name","label":"First Name","field_type":"text","required":true,"display_order":1},
              {"unique_key":"last_name","label":"Last Name","field_type":"text","required":true,"display_order":2},
              {"unique_key":"national_id","label":"National ID","field_type":"text","required":true,"display_order":3},
              {"unique_key":"phone_number","label":"Phone Number","field_type":"text","required":true,"display_order":4}
            ]
          }
        ]
      }
    }'
)

echo "$BASELINE_CREATE_RESPONSE" | jq
export BASELINE_TEMPLATE_ID=$(echo "$BASELINE_CREATE_RESPONSE" | jq -r '.id')
export BASELINE_ACTIVE_VERSION_ID=$(echo "$BASELINE_CREATE_RESPONSE" | jq -r '.active_version_id')
```

### 5.2 List/Get/Patch Baseline Template

```bash
curl -sS "$BASE_URL/api/v1/baseline-templates" \
  -H "Authorization: Bearer $TENANT_TOKEN" | jq

curl -sS "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Baseline for onboarding and KYC"
  }' | jq
```

### 5.3 Create/Get/Patch/Publish/Delete Baseline Definition

```bash
export BASELINE_DEF_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions" \
    -H "Authorization: Bearer $PLATFORM_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "version_tag": "1.1.0",
      "rules_config": {"notes":"v1.1"},
      "changelog": "Minor baseline update",
      "question_groups": [],
      "questions": []
    }'
)

echo "$BASELINE_DEF_CREATE_RESPONSE" | jq
export BASELINE_DEF_VERSION_ID=$(echo "$BASELINE_DEF_CREATE_RESPONSE" | jq -r '.id')

curl -sS "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions/$BASELINE_DEF_VERSION_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions/$BASELINE_DEF_VERSION_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "changelog": "Minor baseline update reviewed"
  }' | jq

curl -sS -X POST "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions/$BASELINE_DEF_VERSION_ID/publish?set_as_active=false" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" | jq
```

If you want to delete an unpublished draft definition instead:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions/$BASELINE_DEF_VERSION_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" -i
```

## 6. Tenant Template CRUD

### 6.1 Create Tenant Template

```bash
export TENANT_TEMPLATE_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Abyssinia Retail Onboarding",
      "description": "Tenant-specific onboarding template",
      "template_type": "kyc",
      "baseline_level": 1
    }'
)

echo "$TENANT_TEMPLATE_CREATE_RESPONSE" | jq
export TENANT_TEMPLATE_ID=$(echo "$TENANT_TEMPLATE_CREATE_RESPONSE" | jq -r '.id')
```

### 6.2 List/Get/Patch Tenant Template

```bash
curl -sS "$BASE_URL/api/v1/templates" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Tenant-specific onboarding template updated"
  }' | jq
```

### 6.3 Create/Get/Patch Definition

```bash
export RULES_CONFIG=$(jq -c '.template_version.rules_config' "$SAMPLE_JSON")

export TENANT_DEF_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "{
      \"version_tag\": \"2026.03.27\",
      \"rules_config\": $RULES_CONFIG,
      \"changelog\": \"Initial tenant version\",
      \"question_groups\": [],
      \"questions\": []
    }"
)

echo "$TENANT_DEF_CREATE_RESPONSE" | jq
export TENANT_DEF_VERSION_ID=$(echo "$TENANT_DEF_CREATE_RESPONSE" | jq -r '.id')

curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "changelog": "Initial tenant version updated"
  }' | jq
```

### 6.4 Add/Delete Groups and Questions

```bash
export GROUP_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/groups" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "unique_key": "employment",
      "title": "Employment",
      "display_order": 2
    }'
)

echo "$GROUP_CREATE_RESPONSE" | jq
export GROUP_ID=$(echo "$GROUP_CREATE_RESPONSE" | jq -r '.id')

export GROUP_QUESTION_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/groups/$GROUP_ID/questions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "unique_key": "employer_name",
      "label": "Employer Name",
      "field_type": "text",
      "required": false,
      "display_order": 1
    }'
)

echo "$GROUP_QUESTION_RESPONSE" | jq
export GROUP_QUESTION_ID=$(echo "$GROUP_QUESTION_RESPONSE" | jq -r '.id')

export UNGROUPED_QUESTION_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/questions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "unique_key": "pep_flag",
      "label": "Politically Exposed Person",
      "field_type": "boolean",
      "required": false,
      "display_order": 100
    }'
)

echo "$UNGROUPED_QUESTION_RESPONSE" | jq
export UNGROUPED_QUESTION_ID=$(echo "$UNGROUPED_QUESTION_RESPONSE" | jq -r '.id')
```

Delete question/group examples:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/questions/$UNGROUPED_QUESTION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i

curl -sS -X DELETE "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/groups/$GROUP_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i
```

### 6.5 Review Flow and Publish

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/submit-review" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"notes":"Ready for platform review"}' | jq

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/approve" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"notes":"Approved for publish"}' | jq

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/publish?set_as_active=true" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

Request-changes example:

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_VERSION_ID/request-changes" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"notes":"Add one more required field"}' | jq
```

## 7. Product CRUD

### 7.1 Create/List/Get/Patch Product

```bash
export PRODUCT_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/products" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"Digital Current Account\",
      \"product_code\": \"dca_001\",
      \"description\": \"Current account onboarding for salaried and SME applicants\",
      \"template_id\": \"$TENANT_TEMPLATE_ID\"
    }"
)

echo "$PRODUCT_CREATE_RESPONSE" | jq
export PRODUCT_ID=$(echo "$PRODUCT_CREATE_RESPONSE" | jq -r '.id')

curl -sS "$BASE_URL/api/v1/products" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/products/$PRODUCT_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/products/$PRODUCT_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Current account onboarding for retail and SME applicants"
  }' | jq
```

### 7.2 Activate/Deactivate/Get KYC Config/Delete

```bash
curl -sS -X POST "$BASE_URL/api/v1/products/$PRODUCT_ID/activate" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/products/$PRODUCT_ID/kyc-config" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X POST "$BASE_URL/api/v1/products/$PRODUCT_ID/deactivate" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

Delete at cleanup time:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/products/$PRODUCT_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i
```

## 8. Submission CRUD and Workflow

### 8.1 Create/List/Get/Full/Patch Submission

```bash
export SELF_SUBMISSION_PAYLOAD=$(jq -c --arg template_id "$TENANT_TEMPLATE_ID" --arg product_id "$PRODUCT_ID" '
  .submissions[0] + {template_id:$template_id, product_id:$product_id}
' "$SAMPLE_JSON")

export SUBMISSION_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/submissions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "$SELF_SUBMISSION_PAYLOAD"
)

echo "$SUBMISSION_CREATE_RESPONSE" | jq
export SUBMISSION_ID=$(echo "$SUBMISSION_CREATE_RESPONSE" | jq -r '.id')

curl -sS "$BASE_URL/api/v1/submissions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/full" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/submissions/$SUBMISSION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "external_ref": "portal-case-1001"
  }' | jq
```

### 8.2 Submit, Transition, History, Comments

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/submit?validate=true" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/comments" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Customer submitted through portal",
    "is_internal": false
  }' | jq

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/comments" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/transition" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "to_status": "under_review",
    "reason": "Checker picked up the case"
  }' | jq

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/history" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

Delete a draft/cancelled submission at cleanup time:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/submissions/$SUBMISSION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i
```

## 9. Verification Flow

### 9.1 Start and Inspect Verification

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification/start" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"mode":"start"}' | jq

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

### 9.2 Submit OTP Actions

The sample data uses:

- phone OTP: `111111`
- Fayda OTP for `FID-ETH-0003456789`: `222222`

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification/steps/phone_otp/actions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "submit_code",
    "payload": {"code":"111111"}
  }' | jq

curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification/steps/fayda_lookup/actions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "submit_code",
    "payload": {"code":"222222"}
  }' | jq
```

Final state:

```bash
curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/full" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

## 10. Submission Search

```bash
curl -sS "$BASE_URL/api/v1/submissions/search-config" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X POST "$BASE_URL/api/v1/submissions/search" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "verification_decision": "approved",
    "criteria": [
      {"key":"national_id","op":"eq","value":"FID-ETH-0003456789"}
    ],
    "limit": 20
  }' | jq
```

## 11. Service Token and Tenant User Management

### 11.1 Create Tenant User

```bash
export TENANT_USER_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/tenants/$TENANT_ID/users" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "national_id": "FN-12345",
      "username": "maker.one",
      "password": "Passw0rd!",
      "roles": ["maker"],
      "first_name": "Maker",
      "last_name": "One",
      "phone_number": "+251900000001"
    }'
)

echo "$TENANT_USER_RESPONSE" | jq
```

### 11.2 Tenant Service Token

```bash
export SERVICE_TOKEN_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/auth/service-token/$TENANT_KEY" \
    -H "Content-Type: application/json" \
    -d '{}'
)

echo "$SERVICE_TOKEN_RESPONSE" | jq
export SERVICE_TOKEN=$(echo "$SERVICE_TOKEN_RESPONSE" | jq -r '.access_token')
```

## 12. Transform Rule CRUD and Apply

Create a second tenant template version first:

```bash
export TENANT_DEF_V2_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "version_tag": "2026.04.01",
      "rules_config": {},
      "changelog": "Second version for transform demo",
      "question_groups": [],
      "questions": [
        {
          "unique_key": "full_name",
          "label": "Full Name",
          "field_type": "text",
          "required": false,
          "display_order": 999
        }
      ]
    }'
)

echo "$TENANT_DEF_V2_RESPONSE" | jq
export TENANT_DEF_VERSION_ID_V2=$(echo "$TENANT_DEF_V2_RESPONSE" | jq -r '.id')
```

### 12.1 Generate/Create/List/Get/Patch Rule Set

```bash
export RULESET_GENERATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/generate" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "{
      \"source_version_id\": \"$TENANT_DEF_VERSION_ID\",
      \"target_version_id\": \"$TENANT_DEF_VERSION_ID_V2\",
      \"changelog\": \"Auto-generated transform set\"
    }"
)

echo "$RULESET_GENERATE_RESPONSE" | jq
export RULE_SET_ID=$(echo "$RULESET_GENERATE_RESPONSE" | jq -r '.id')

curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X PATCH "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "changelog": "Updated transform changelog"
  }' | jq
```

### 12.2 Add/Update/Delete Rule

```bash
export RULE_CREATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/rules" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "source_unique_key": null,
      "target_unique_key": "full_name",
      "operation": "merge",
      "params": {
        "sources": ["first_name", "last_name"],
        "separator": " "
      },
      "display_order": 1,
      "is_required": false
    }'
)

echo "$RULE_CREATE_RESPONSE" | jq
export RULE_ID=$(echo "$RULE_CREATE_RESPONSE" | jq -r '.id')

curl -sS -X PATCH "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/rules/$RULE_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "display_order": 2
  }' | jq
```

Delete example:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/rules/$RULE_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i
```

### 12.3 Publish/Archive/Preview/Apply/Bulk Apply

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/publish" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/preview" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"submission_id\": \"$SUBMISSION_ID\"
  }" | jq

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/apply/$SUBMISSION_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/bulk-apply" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "submission_ids": [],
    "dry_run": true
  }' | jq

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/archive" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

If the rule set is still draft and you want to delete it:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i
```

## 13. Cleanup

Run these only when you are done:

```bash
curl -sS -X POST "$BASE_URL/api/v1/products/$PRODUCT_ID/deactivate" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS -X DELETE "$BASE_URL/api/v1/products/$PRODUCT_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i

curl -sS -X DELETE "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" -i

curl -sS -X DELETE "$BASE_URL/api/v1/tenants/$TENANT_ID?hard_delete=true" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  $INITIALIZATION_HEADER -i
```
