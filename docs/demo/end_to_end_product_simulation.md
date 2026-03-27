# End-to-End Product Simulation

This document is a practical runbook for:

- setting up the required services
- running the API locally
- configuring Keycloak for platform and tenant realms
- exercising the main CRUD and workflow endpoints end to end
- simulating both portal usage and third-party tenant service usage

## 1. Scope

This repo currently supports end-to-end simulation for:

- tenant initialization
- baseline template CRUD
- tenant template CRUD and review
- product CRUD and KYC config resolution
- tenant user setup in Keycloak
- tenant and global authz policy management
- submission CRUD and workflow
- verification flow execution
- submission search and partner-facing read APIs
- transform rule CRUD and migration preview/apply

Temporal is present in the repo but not wired into the live request path yet, so it is not required for this demo.

## 2. Prerequisites

Install:

- Docker
- `uv`
- `curl`

Optional but helpful:

- `jq`
- `psql`

## 3. Services You Need

Minimum required:

- PostgreSQL
- Keycloak
- this API

Optional:

- Temporal server and worker

For the current app behavior, Temporal can be skipped.

## 4. Start PostgreSQL

From the repo root:

```bash
docker compose -f docker-compose.dev.yaml up db -d
```

## 5. Start Keycloak

This repo does not ship a Keycloak container in `docker-compose.dev.yaml`, so start one separately:

```bash
docker run --name oaas-keycloak \
  -p 8080:8080 \
  -e KEYCLOAK_ADMIN=admin \
  -e KEYCLOAK_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.1.0 \
  start-dev
```

Keycloak URLs:

- admin console: `http://127.0.0.1:8080`
- default platform admin realm: `master`

## 6. Configure the App

Create `.env`:

```env
DATABASE_URL=postgresql+asyncpg://onboarding:onboarding@localhost:5432/onboarding_db
APP_NAME=Onboarding & Verification Service
DEBUG=true
API_V1_PREFIX=/api/v1

AUTH_ENABLED=true
AUTH_TENANT_CLAIM=tenant_id,{realm}_claims.tenant_id
AUTH_ALGORITHMS=RS256
AUTH_AUDIENCE=
AUTH_ISSUERS=
AUTH_EXCLUSIVE_ROLE_GROUPS=maker|checker

KEYCLOAK_BASE_URL=http://127.0.0.1:8080
KEYCLOAK_ADMIN_BASE_URL=http://127.0.0.1:8080
KEYCLOAK_TRUSTED_ISSUER_BASES=http://127.0.0.1:8080
KEYCLOAK_REALMS=

KEYCLOAK_TENANT_INITIALIZATION_ENABLED=true
KEYCLOAK_TENANT_INITIALIZATION_REQUIRED=true
KEYCLOAK_ADMIN_REALM=master
KEYCLOAK_ADMIN_CLIENT_ID=oaas-provisioner
KEYCLOAK_ADMIN_CLIENT_SECRET=REPLACE_ME

KEYCLOAK_TENANT_CLIENT_ID=oaas-client
KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL=true

KEYCLOAK_CLIENTS_JSON={"master":{"client_id":"oaas-admin-ui", "client_secret":"t6qj7LALRDf0n4wYcy2HwUsKDxR6AFHBey3S7umpuA+reuNQlSGdHDZ6vNp0ZMPQ"}}

KEYCLOAK_BOOTSTRAP_USERS_JSON=[{"username":"{realm}_tenant_admin","roles":["tenant_admin"]},{"username":"{realm}_maker","roles":["maker"]},{"username":"{realm}_checker","roles":["checker"]}]
KEYCLOAK_BOOTSTRAP_PASSWORD=ChangeMe123!
KEYCLOAK_BOOTSTRAP_EMAIL_DOMAIN=example.com

PLATFORM_INITIALIZATION_API_KEY=
TEMPORAL_ENABLED=true
```

Important:

- create the Keycloak provisioner client and platform `super_admin` user as described in [keycloak_tenant_realm_setup.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/keycloak_tenant_realm_setup.md)
- if you set `PLATFORM_INITIALIZATION_API_KEY`, include `X-Initialization-Key` on tenant create/delete and global authz policy calls

## 7. Install Dependencies and Run Migrations

```bash
uv sync
uv run alembic upgrade head
```

## 8. Start the API

```bash
uv run uvicorn app.main:app --reload --port 7090
```

Smoke test:

```bash
curl -sS http://127.0.0.1:7090/health
curl -sS http://127.0.0.1:7090/docs
```

## 9. Demo Shell Variables

Use these shell variables during the walkthrough:

```bash
export BASE_URL=http://127.0.0.1:7090
export PLATFORM_REALM=master
export TENANT_KEY=acme_bank
export TENANT_NAME="Acme Bank"
export PLATFORM_USERNAME=platform.admin
export PLATFORM_PASSWORD=REPLACE_ME
export TENANT_ADMIN_USERNAME=${TENANT_KEY}_tenant_admin
export TENANT_ADMIN_PASSWORD=ChangeMe123!
```

If you use the optional initialization key:

```bash
export INITIALIZATION_HEADER="-H X-Initialization-Key:REPLACE_ME"
```

If you do not use it:

```bash
export INITIALIZATION_HEADER=
```

## 10. Platform Auth

Login as platform super admin:

```bash
curl -sS -X POST "$BASE_URL/api/auth/login/$PLATFORM_REALM" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"$PLATFORM_USERNAME\",
    \"password\": \"$PLATFORM_PASSWORD\"
  }"
```

Save the returned `access_token` as `PLATFORM_TOKEN`.

Check the resolved auth context:

```bash
curl -sS "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $PLATFORM_TOKEN"
```

## 11. Tenant CRUD

### Create tenant

```bash
curl -sS -X POST "$BASE_URL/api/v1/tenants" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  $INITIALIZATION_HEADER \
  -d "{
    \"name\": \"$TENANT_NAME\",
    \"tenant_key\": \"$TENANT_KEY\"
  }"
```

Save:

- `TENANT_ID`
- `TENANT_REALM`

### List tenants

```bash
curl -sS "$BASE_URL/api/v1/tenants" \
  -H "Authorization: Bearer $PLATFORM_TOKEN"
```

### Get tenant

```bash
curl -sS "$BASE_URL/api/v1/tenants/$TENANT_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN"
```

### Update tenant

```bash
curl -sS -X PATCH "$BASE_URL/api/v1/tenants/$TENANT_ID" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme Bank PLC"}'
```

## 12. Baseline Template CRUD

Create a baseline template with an initial version:

```bash
curl -sS -X POST "$BASE_URL/api/v1/baseline-templates" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "KYC Level 1",
    "description": "Standard baseline",
    "category": "kyc",
    "template_type": "kyc",
    "level": 1,
    "initial_version": {
      "version_tag": "1.0.0",
      "rules_config": {},
      "changelog": "Initial baseline",
      "question_groups": [
        {
          "unique_key": "personal_info",
          "title": "Personal Information",
          "display_order": 1,
          "questions": [
            {"unique_key": "first_name", "label": "First Name", "field_type": "text", "required": true, "display_order": 1},
            {"unique_key": "last_name", "label": "Last Name", "field_type": "text", "required": true, "display_order": 2},
            {"unique_key": "national_id", "label": "National ID", "field_type": "text", "required": true, "display_order": 3},
            {"unique_key": "phone_number", "label": "Phone Number", "field_type": "text", "required": true, "display_order": 4}
          ]
        }
      ],
      "questions": []
    }
  }'
```

Save `BASELINE_TEMPLATE_ID`.

Other baseline CRUD:

```bash
curl -sS "$BASE_URL/api/v1/baseline-templates" -H "Authorization: Bearer $PLATFORM_TOKEN"
curl -sS "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID" -H "Authorization: Bearer $PLATFORM_TOKEN"
curl -sS -X PATCH "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID" -H "Authorization: Bearer $PLATFORM_TOKEN" -H "Content-Type: application/json" -d '{"description":"Updated baseline"}'
```

Create and publish another baseline version:

```bash
curl -sS -X POST "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"version_tag":"1.1.0","rules_config":{},"changelog":"Revision"}'
```

Save `BASELINE_VERSION_ID`, then:

```bash
curl -sS -X POST "$BASE_URL/api/v1/baseline-templates/$BASELINE_TEMPLATE_ID/definitions/$BASELINE_VERSION_ID/publish?set_as_active=true" \
  -H "Authorization: Bearer $PLATFORM_TOKEN"
```

## 13. Tenant Auth

Login as tenant admin from the tenant realm:

```bash
curl -sS -X POST "$BASE_URL/api/auth/login/$TENANT_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"$TENANT_ADMIN_USERNAME\",
    \"password\": \"$TENANT_ADMIN_PASSWORD\"
  }"
```

Save `TENANT_ADMIN_TOKEN`.

Inspect tenant auth:

```bash
curl -sS "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN"
```

## 14. Tenant Template CRUD

### Create tenant template

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Retail KYC",
    "description": "Tenant onboarding template",
    "template_type": "kyc",
    "baseline_level": 1
  }'
```

Save `TENANT_TEMPLATE_ID`.

### List and get tenant templates

```bash
curl -sS "$BASE_URL/api/v1/templates" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
```

### Create tenant template definition with verification and search config

Use the rules config from:

- [verification_flow_demo.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/verification_flow_demo.md)
- [submission_search_demo.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/submission_search_demo.md)

Example:

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "version_tag": "1.0.0",
    "changelog": "Initial tenant onboarding flow",
    "rules_config": {
      "submission_search": {
        "filters": [
          {"key":"national_id","label":"National ID","source":"form_data","path":"national_id","operators":["eq","contains"]},
          {"key":"name_match_score","label":"Name Match Score","source":"verification","path":"steps.name_match.result.score","operators":["gte","lte"]}
        ]
      },
      "verification_flow": {
        "flow_key": "default",
        "demo_registry": {
          "FN-123": {
            "otp_code": "222222",
            "registered_phone": "+251900000002",
            "first_name": "Abel",
            "last_name": "Bekele",
            "date_of_birth": "1990-01-01"
          }
        },
        "steps": [
          {"key":"phone_otp","name":"Phone OTP","type":"challenge_response","adapter":"demo_phone_otp","input":{"phone_number":"$answers.phone_number"},"demo_code":"111111","max_attempts":3},
          {"key":"fayda_lookup","name":"Fayda OTP","type":"challenge_response","adapter":"demo_fayda_otp","depends_on":["phone_otp"],"input":{"national_id":"$answers.national_id"},"max_attempts":3},
          {"key":"name_match","name":"Name Match","type":"comparison","adapter":"demo_fuzzy_match","depends_on":["fayda_lookup"],"pairs":[
            {"label":"First Name","left":"$answers.first_name","right":"$steps.fayda_lookup.result.attributes.first_name"},
            {"label":"Last Name","left":"$answers.last_name","right":"$steps.fayda_lookup.result.attributes.last_name"}
          ],"pass_score_gte":0.95,"review_score_gte":0.8}
        ],
        "decision": {
          "rules": [
            {"decision":"approved","kyc_level":"level_2","all":[
              {"fact":"steps.phone_otp.outcome","equals":"pass"},
              {"fact":"steps.fayda_lookup.outcome","equals":"pass"},
              {"fact":"steps.name_match.result.score","gte":0.95}
            ],"reason_codes":["all_checks_passed"]}
          ],
          "fallback": {"decision":"manual_review","kyc_level":"level_1","reason_codes":["verification_pending"]}
        }
      }
    }
  }'
```

Save `TENANT_TEMPLATE_VERSION_ID`.

### Update and publish definition

```bash
curl -sS -X PATCH "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_TEMPLATE_VERSION_ID" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"changelog":"Updated before publish"}'

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_TEMPLATE_VERSION_ID/publish?set_as_active=true" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

### Optional review flow

Submit for review:

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_TEMPLATE_VERSION_ID/submit-review" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"notes":"Ready for approval"}'
```

Approve as platform super admin:

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_TEMPLATE_VERSION_ID/approve" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"notes":"Approved"}'
```

## 15. Product CRUD

### Create product

```bash
curl -sS -X POST "$BASE_URL/api/v1/products" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Retail Account\",
    \"description\": \"Retail onboarding\",
    \"product_code\": \"RA-001\",
    \"template_id\": \"$TENANT_TEMPLATE_ID\"
  }"
```

Save `PRODUCT_ID`.

Other product flows:

```bash
curl -sS "$BASE_URL/api/v1/products" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS "$BASE_URL/api/v1/products/$PRODUCT_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS -X PATCH "$BASE_URL/api/v1/products/$PRODUCT_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID" -H "Content-Type: application/json" -d '{"description":"Updated product"}'
curl -sS -X POST "$BASE_URL/api/v1/products/$PRODUCT_ID/activate" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS "$BASE_URL/api/v1/products/$PRODUCT_ID/kyc-config" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS -X POST "$BASE_URL/api/v1/products/$PRODUCT_ID/deactivate" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
```

## 16. Tenant Users and Tenant Policy

### Create tenant users

```bash
curl -sS -X POST "$BASE_URL/api/v1/tenants/$TENANT_ID/users" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "national_id": "FN-456",
    "username": "maker.one",
    "password": "Passw0rd!",
    "roles": ["maker"],
    "first_name": "Maker",
    "last_name": "One",
    "phone_number": "+251900000003"
  }'
```

### Get and update tenant authz policy

```bash
curl -sS "$BASE_URL/api/v1/tenants/$TENANT_ID/authz/policy" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN"

curl -sS -X PUT "$BASE_URL/api/v1/tenants/$TENANT_ID/authz/policy" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "mode": "merge",
      "roles": {
        "tenant_admin": ["templates.read","templates.update","products.read","products.update","submissions.read_all","submissions.comment"],
        "maker": ["submissions.create","submissions.read","submissions.update","submissions.submit"]
      },
      "columns": {}
    }
  }'
```

## 17. Global and Realm Authz Policy

These are platform-only endpoints.

```bash
curl -sS "$BASE_URL/api/v1/authz/policy" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  $INITIALIZATION_HEADER

curl -sS -X PUT "$BASE_URL/api/v1/authz/policy" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  $INITIALIZATION_HEADER \
  -d '{
    "policy": {
      "mode": "merge",
      "roles": {
        "schema_author": ["baseline_templates.read","baseline_templates.create","baseline_templates.update","baseline_templates.publish"]
      },
      "columns": {}
    }
  }'

curl -sS "$BASE_URL/api/v1/authz/policy/$TENANT_KEY" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  $INITIALIZATION_HEADER
```

## 18. Submission CRUD and Workflow

### Create submission

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"template_id\": \"$TENANT_TEMPLATE_ID\",
    \"product_id\": \"$PRODUCT_ID\",
    \"submitter_id\": \"customer-001\",
    \"external_ref\": \"EXT-001\",
    \"form_data\": {
      \"phone_number\": \"+251900000001\",
      \"national_id\": \"FN-123\",
      \"first_name\": \"Abel\",
      \"last_name\": \"Bekele\"
    }
  }"
```

Save `SUBMISSION_ID`.

### List, get, update

```bash
curl -sS "$BASE_URL/api/v1/submissions" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/full" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS -X PATCH "$BASE_URL/api/v1/submissions/$SUBMISSION_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID" -H "Content-Type: application/json" -d '{"submitter_id":"customer-001-updated"}'
```

### Submit and transition

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/submit?validate=false" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"

curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/transition" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"to_status":"under_review","reason":"Start review"}'
```

### Comments and history

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/comments" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"content":"Looks good so far","is_internal":true}'

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/comments" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/history" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

## 19. Verification Flow

See the detailed demo in [verification_flow_demo.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/verification_flow_demo.md).

Quick flow:

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification/start" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"journey":"self_service_online","deferred":false}'

curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification/steps/phone_otp/actions" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"submit_code","payload":{"otp_code":"111111"}}'

curl -sS -X POST "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification/steps/fayda_lookup/actions" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"submit_code","payload":{"otp_code":"222222"}}'

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/verification" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

## 20. Submission Search for Portal and Partners

See [submission_search_demo.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/submission_search_demo.md).

### Discover filter catalog

```bash
curl -sS "$BASE_URL/api/v1/submissions/search-config" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

### Search submissions

```bash
curl -sS -X POST "$BASE_URL/api/v1/submissions/search" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "verification_decision": "approved",
    "criteria": [
      {"key":"national_id","op":"eq","value":"FN-123"},
      {"key":"name_match_score","op":"gte","value":0.95}
    ],
    "sort_by": "created_at",
    "sort_order": "desc",
    "limit": 25
  }'
```

## 21. Third-Party Tenant Service Simulation

Get a machine token:

```bash
curl -sS -X POST "$BASE_URL/api/auth/service-token/$TENANT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scope":"submissions.read"}'
```

Save `SERVICE_TOKEN`.

Use it against search APIs:

```bash
curl -sS "$BASE_URL/api/v1/submissions/search-config" \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"

curl -sS -X POST "$BASE_URL/api/v1/submissions/search" \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"verification_decision":"approved","limit":10}'
```

## 22. Transform Rule CRUD and Migration

To simulate template version migration:

1. create another tenant template version
2. publish it
3. create or generate transform rules between old and new versions

### Create a second template version

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/definitions" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"version_tag":"2.0.0","rules_config":{},"changelog":"Next version"}'
```

Save `TENANT_TEMPLATE_VERSION_V2_ID`.

### Generate transform rules

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/generate" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"source_version_id\": \"$TENANT_TEMPLATE_VERSION_ID\",
    \"target_version_id\": \"$TENANT_TEMPLATE_VERSION_V2_ID\",
    \"changelog\": \"Auto-generated migration\"
  }"
```

Save `RULE_SET_ID`.

### Transform CRUD

```bash
curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS -X PATCH "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID" -H "Content-Type: application/json" -d '{"changelog":"Edited migration notes"}'
```

### Add a manual rule

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/rules" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "source_unique_key": "first_name",
    "target_unique_key": "first_name",
    "operation": "copy",
    "params": {},
    "display_order": 1,
    "is_required": true
  }'
```

### Preview, publish, apply, history

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/preview" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"submission_id\":\"$SUBMISSION_ID\"}"

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/publish" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"

curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/apply/$SUBMISSION_ID" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"

curl -sS "$BASE_URL/api/v1/submissions/$SUBMISSION_ID/transform-history" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

### Bulk migrate

```bash
curl -sS -X POST "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID/transform-rules/$RULE_SET_ID/bulk-apply" \
  -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'
```

## 23. Cleanup

Delete draft-only entities where needed:

```bash
curl -sS -X DELETE "$BASE_URL/api/v1/products/$PRODUCT_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS -X DELETE "$BASE_URL/api/v1/templates/$TENANT_TEMPLATE_ID" -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -H "X-Tenant-ID: $TENANT_ID"
curl -sS -X DELETE "$BASE_URL/api/v1/tenants/$TENANT_ID?hard_delete=true" -H "Authorization: Bearer $PLATFORM_TOKEN" $INITIALIZATION_HEADER
```

## 24. Recommended Demo Order

For a clean full product walkthrough, run the steps in this order:

1. start PostgreSQL, Keycloak, and the API
2. create platform admin setup in Keycloak
3. login as platform super admin
4. create tenant
5. login as tenant admin
6. create baseline template
7. create and publish tenant template definition with verification and search config
8. create product and activate it
9. create tenant users
10. create submission
11. run verification
12. search the submission from portal style API
13. search the submission using a machine token
14. create a new template version and preview a transform

## 25. Useful References

- Keycloak platform and tenant realm setup:
  [keycloak_tenant_realm_setup.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/keycloak_tenant_realm_setup.md)
- Verification demo:
  [verification_flow_demo.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/verification_flow_demo.md)
- Submission search demo:
  [submission_search_demo.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/submission_search_demo.md)
