# End-to-End Local Runbook

This is the single runbook to:

- configure the local environment
- start all required services
- bootstrap Keycloak
- initialize a tenant
- populate realistic demo data
- run onboarding and verification
- test the full flow

Use this together with:

- app env: [`.env`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env)
- Keycloak env: [`.env.keycloak.local`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env.keycloak.local)
- Keycloak bootstrap script: [bootstrap_keycloak_local.sh](/Users/berhanu.tarekegn/git/onboarding-and-verification/scripts/bootstrap_keycloak_local.sh)
- demo seed script: [seed_demo_data.sh](/Users/berhanu.tarekegn/git/onboarding-and-verification/scripts/seed_demo_data.sh)
- realistic sample data: [real_world_onboarding_sample.json](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json)

## 1. Prerequisites

Install:

- Docker
- `uv`
- `curl`
- `jq`
- Temporal CLI
- PostgreSQL locally, or Docker if you do not already have it

## 2. Configure Environment

The repo already includes local defaults in [`.env`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env).

Important values:

- API: `http://127.0.0.1:7090`
- Keycloak: `http://127.0.0.1:8080`
- Temporal gRPC: `127.0.0.1:7233`
- Temporal UI: `http://127.0.0.1:8233`

If you want an extra guard on platform initialization routes, set:

```env
PLATFORM_INITIALIZATION_API_KEY=local-init-key
```

Then use:

```bash
export INITIALIZATION_HEADER='-H X-Initialization-Key:local-init-key'
```

If you leave it empty:

```bash
export INITIALIZATION_HEADER=
```

## 3. Start Keycloak

From the repo root:

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

This stack exposes:

- Keycloak: `http://127.0.0.1:8080`

If you do not already have PostgreSQL running locally, start it separately:

```bash
docker compose -f docker-compose.dev.yaml up db -d
```

## 4. Start Temporal Locally

Install the Temporal CLI locally if needed:

```bash
brew install temporal
```

Start the local Temporal dev server in its own terminal:

```bash
chmod +x scripts/start_temporal_local.sh
./scripts/start_temporal_local.sh
```

That gives you:

- Temporal gRPC: `127.0.0.1:7233`
- Temporal UI: `http://127.0.0.1:8233`

If you previously started Docker Temporal services and the UI showed `500 Internal Error`, stop those containers and use the local CLI server instead:

```bash
docker compose -f docker-compose.dev.yaml stop temporal temporal-ui
```

## 5. Bootstrap Keycloak

In a second terminal:

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
chmod +x scripts/bootstrap_keycloak_local.sh
./scripts/bootstrap_keycloak_local.sh
```

If you started a local Keycloak install, point the bootstrap script at it:

```bash
cd /Users/berhanu.tarekegn/git/onboarding-and-verification
export KEYCLOAK_HOME=/path/to/keycloak
./scripts/bootstrap_keycloak_local.sh
```

That script creates:

- realm `oaas-platform`
- realm role `super_admin` in `oaas-platform`
- login client `oaas-admin-ui` in `oaas-platform`
- provisioner client `oaas-provisioner` in `master`
- required hardcoded tenant claim mappers
- platform admin user `platform.admin`

The script now verifies that `service-account-oaas-provisioner` has the required
admin roles in the Keycloak admin realm. If bootstrap succeeds, tenant realm
initialization is allowed to create new realms.

Default credentials created by the script:

- platform admin username: `platform.admin`
- platform admin password: `117f19dd3c2c8164c9ee2642e0da6f65`

## 6. Start the App

In another terminal:

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 7090
```

In another terminal, start the Temporal worker:

```bash
uv run python -m app.temporal.worker
```

Temporal must stay enabled in [`.env`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env):

```env
TEMPORAL_ENABLED=true
TEMPORAL_REQUIRED=false
```

Smoke test:

```bash
curl -sS http://127.0.0.1:7090/health | jq
curl -sS http://127.0.0.1:7090/docs >/dev/null
```

## 7. Load Demo Variables

If you are using the current local no-auth mode from [`.env`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env), you can skip the token flow entirely and seed the sample data with:

```bash
chmod +x scripts/seed_demo_data.sh
./scripts/seed_demo_data.sh
```

The remaining sections below are still useful when you want to exercise the full auth-enabled path.

Use the realistic sample data file to set local shell variables:

```bash
export BASE_URL=http://127.0.0.1:7090
export SAMPLE_JSON=/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json

export PLATFORM_REALM=$(jq -r '.platform_admin.realm' "$SAMPLE_JSON")
export PLATFORM_USERNAME=$(jq -r '.platform_admin.username' "$SAMPLE_JSON")
export PLATFORM_PASSWORD=$(jq -r '.platform_admin.password' "$SAMPLE_JSON")

export TENANT_KEY=$(jq -r '.tenant.tenant_key' "$SAMPLE_JSON")
export TENANT_NAME=$(jq -r '.tenant.name' "$SAMPLE_JSON")
export TENANT_ADMIN_USERNAME=$(jq -r '.tenant_bootstrap_admin.username' "$SAMPLE_JSON")
export TENANT_ADMIN_PASSWORD=$(jq -r '.tenant_bootstrap_admin.password' "$SAMPLE_JSON")
```

## 8. Get Platform Token

```bash
export PLATFORM_TOKEN=$(
  curl -sS -X POST "$BASE_URL/api/auth/login/$PLATFORM_REALM" \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"$PLATFORM_USERNAME\",
      \"password\": \"$PLATFORM_PASSWORD\"
    }" | jq -r '.access_token'
)
```

Verify:

```bash
curl -sS "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" | jq
```

## 9. Initialize the Tenant

Create the tenant from the sample file:

```bash
export TENANT_PAYLOAD=$(jq -c '.tenant' "$SAMPLE_JSON")

export TENANT_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/tenants" \
    -H "Authorization: Bearer $PLATFORM_TOKEN" \
    -H "Content-Type: application/json" \
    $INITIALIZATION_HEADER \
    -d "$TENANT_PAYLOAD"
)

echo "$TENANT_RESPONSE" | jq
export TENANT_ID=$(echo "$TENANT_RESPONSE" | jq -r '.id')
```

Expected:

- tenant row created in `public.tenants`
- PostgreSQL schema `tenant_abyssinia_corp`
- Keycloak realm `abyssinia_corp`
- bootstrap tenant users created

## 10. Login as Tenant Admin

```bash
export TENANT_TOKEN=$(
  curl -sS -X POST "$BASE_URL/api/auth/login/$TENANT_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"$TENANT_ADMIN_USERNAME\",
      \"password\": \"$TENANT_ADMIN_PASSWORD\"
    }" | jq -r '.access_token'
)
```

Verify:

```bash
curl -sS "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $TENANT_TOKEN" | jq
```

## 11. Create a Baseline Template

Create a minimal baseline template:

```bash
export BASELINE_RESPONSE=$(
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

echo "$BASELINE_RESPONSE" | jq
export BASELINE_TEMPLATE_ID=$(echo "$BASELINE_RESPONSE" | jq -r '.id')
```

## 12. Create the Tenant Template

Create the tenant template:

```bash
export TENANT_TEMPLATE_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Abyssinia Retail Onboarding",
      "template_type": "kyc",
      "baseline_level": 1
    }'
)

echo "$TENANT_TEMPLATE_RESPONSE" | jq
export TENANT_TEMPLATE_ID=$(echo "$TENANT_TEMPLATE_RESPONSE" | jq -r '.id')
```

Create the tenant template definition using the realistic `rules_config` from the sample file:

```bash
export RULES_CONFIG=$(jq -c '.template_version.rules_config' "$SAMPLE_JSON")
export CHANGELOG=$(jq -r '.template_version.changelog' "$SAMPLE_JSON")
export VERSION_TAG=$(jq -r '.template_version.version_tag' "$SAMPLE_JSON")

export TEMPLATE_DEFINITION_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "{
      \"version_tag\": \"$VERSION_TAG\",
      \"rules_config\": $RULES_CONFIG,
      \"changelog\": \"$CHANGELOG\"
    }"
)

echo "$TEMPLATE_DEFINITION_RESPONSE" | jq
export TENANT_TEMPLATE_VERSION_ID=$(echo "$TEMPLATE_DEFINITION_RESPONSE" | jq -r '.id')
```

## 13. Create the Product

```bash
export PRODUCT_PAYLOAD=$(jq -c '.product' "$SAMPLE_JSON")

export PRODUCT_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/products" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "$PRODUCT_PAYLOAD"
)

echo "$PRODUCT_RESPONSE" | jq
export PRODUCT_ID=$(echo "$PRODUCT_RESPONSE" | jq -r '.id')
```

Activate the product:

```bash
curl -sS -X PATCH "$BASE_URL/api/v1/products/$PRODUCT_ID" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"status":"active"}' | jq
```

## 14. Create Realistic Demo Submissions

### Self-service customer

```bash
export SELF_SUBMISSION_PAYLOAD=$(
  jq -c --arg template_id "$TENANT_TEMPLATE_ID" --arg product_id "$PRODUCT_ID" '
    .submissions[0] + {template_id:$template_id, product_id:$product_id}
  ' "$SAMPLE_JSON"
)

export SELF_SUBMISSION_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/submissions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "$SELF_SUBMISSION_PAYLOAD"
)

echo "$SELF_SUBMISSION_RESPONSE" | jq
export SELF_SUBMISSION_ID=$(echo "$SELF_SUBMISSION_RESPONSE" | jq -r '.id')
```

### Agent-assisted deferred customer

```bash
export OFFLINE_SUBMISSION_PAYLOAD=$(
  jq -c --arg template_id "$TENANT_TEMPLATE_ID" --arg product_id "$PRODUCT_ID" '
    .submissions[1] + {template_id:$template_id, product_id:$product_id}
  ' "$SAMPLE_JSON"
)

export OFFLINE_SUBMISSION_RESPONSE=$(
  curl -sS -X POST "$BASE_URL/api/v1/submissions" \
    -H "Authorization: Bearer $TENANT_TOKEN" \
    -H "X-Tenant-ID: $TENANT_ID" \
    -H "Content-Type: application/json" \
    -d "$OFFLINE_SUBMISSION_PAYLOAD"
)

echo "$OFFLINE_SUBMISSION_RESPONSE" | jq
export OFFLINE_SUBMISSION_ID=$(echo "$OFFLINE_SUBMISSION_RESPONSE" | jq -r '.id')
```

## 15. Run Verification

### Self-service flow

Start verification:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SELF_SUBMISSION_ID/verification/start" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"journey":"self_service_online","deferred":false}' | jq
```

Submit phone OTP:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SELF_SUBMISSION_ID/verification/steps/phone_otp/actions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"submit_code","payload":{"otp_code":"111111"}}' | jq
```

Submit Fayda OTP:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SELF_SUBMISSION_ID/verification/steps/fayda_lookup/actions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"submit_code","payload":{"otp_code":"222222"}}' | jq
```

### Agent-assisted deferred flow

Create deferred verification:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/start" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"journey":"agent_assisted_offline","deferred":true}' | jq
```

Resume later:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/start" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"journey":"self_service_online","deferred":false}' | jq
```

Complete OTPs:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/steps/phone_otp/actions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"submit_code","payload":{"otp_code":"111111"}}' | jq

curl -sS -X POST "$BASE_URL/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/steps/fayda_lookup/actions" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"submit_code","payload":{"otp_code":"333333"}}' | jq
```

## 16. Inspect Results

Get both submission full views:

```bash
curl -sS "$BASE_URL/api/v1/submissions/$SELF_SUBMISSION_ID/full" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

curl -sS "$BASE_URL/api/v1/submissions/$OFFLINE_SUBMISSION_ID/full" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

What to look for:

- `verification.workflow_id`
- `verification.status`
- `verification.decision`
- `verification.kyc_level`
- `computed_data.verification`
- `validation_results.decision`

## 17. Test Search

Get the tenant search config:

```bash
curl -sS "$BASE_URL/api/v1/submissions/search-config" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq
```

Search approved submissions:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/search" \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "verification_decision": "approved",
    "criteria": [
      {"key":"national_id","op":"contains","value":"FID-ETH"},
      {"key":"name_match_score","op":"gte","value":0.95}
    ],
    "limit": 25
  }' | jq
```

## 18. Test Machine-to-Machine Access

Get a tenant service token:

```bash
export SERVICE_TOKEN=$(
  curl -sS -X POST "$BASE_URL/api/auth/service-token/$TENANT_KEY" \
    -H "Content-Type: application/json" \
    -d '{"scope":"submissions.read"}' | jq -r '.access_token'
)
```

Use it for partner search:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/search" \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"verification_decision":"approved","limit":10}' | jq
```

## 19. What Counts as Success

You have a valid end-to-end demo when:

- Keycloak login works for platform admin and tenant admin
- tenant initialization creates the tenant schema and tenant realm
- the API starts cleanly
- the Temporal worker stays connected
- verification start returns a `workflow_id`
- both sample submissions end with:
  - `decision=approved`
  - `kyc_level=level_2`
- submission search returns the verified records
- partner service-token search also returns the verified records

## 20. Troubleshooting

If tenant creation fails:

- confirm Keycloak bootstrap ran successfully
- confirm `KEYCLOAK_ADMIN_CLIENT_SECRET` in [`.env`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env) matches the provisioner client secret created in Keycloak

If verification stays inline or does not get `workflow_id`:

- confirm `TEMPORAL_ENABLED=true`
- confirm the worker is running
- confirm Temporal is reachable at `127.0.0.1:7233`

If tenant admin login fails:

- confirm bootstrap users were enabled in [`.env`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env)
- confirm the tenant realm was created in Keycloak

If search returns nothing:

- inspect the submission full view first
- confirm `verification.decision=approved`
- confirm the sample rules config was applied to the tenant template definition
