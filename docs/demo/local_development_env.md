# Local Development Environment

This repo now uses:

- `tenant_key` as the single public machine identifier for a tenant
- `tenant initialization` as the operator-facing term for schema + Keycloak setup

Two example env files are provided:

- O&V app: [`.env.example`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env.example)
- local Keycloak: [`.env.keycloak.example`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env.keycloak.example)

## O&V App `.env`

The app `.env` should look like this for local development:

```env
DATABASE_URL=postgresql+asyncpg://onboarding:onboarding@localhost:5432/onboarding_db
DATABASE_TEST_URL=postgresql+asyncpg://onboarding:onboarding@localhost:5433/onboarding_test_db

APP_NAME=Onboarding & Verification Service
DEBUG=true
API_V1_PREFIX=/api/v1

AUTH_ENABLED=true
AUTH_TENANT_CLAIM=tenant_id,{realm}_claims.tenant_id
AUTH_ALGORITHMS=RS256
AUTH_AUDIENCE=account
AUTH_EXCLUSIVE_ROLE_GROUPS=maker|checker

KEYCLOAK_BASE_URL=http://127.0.0.1:8080
KEYCLOAK_ADMIN_BASE_URL=http://127.0.0.1:8080
KEYCLOAK_TRUSTED_ISSUER_BASES=http://127.0.0.1:8080
KEYCLOAK_CLIENTS_JSON={"master":{"client_id":"oaas-admin-ui","client_secret":"change-me-admin-ui-secret"}}

KEYCLOAK_TENANT_INITIALIZATION_ENABLED=true
KEYCLOAK_TENANT_INITIALIZATION_REQUIRED=true
KEYCLOAK_ADMIN_REALM=master
KEYCLOAK_ADMIN_CLIENT_ID=oaas-provisioner
KEYCLOAK_ADMIN_CLIENT_SECRET=change-me-provisioner-secret
KEYCLOAK_TENANT_CLIENT_ID=oaas-client
KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL=true
KEYCLOAK_BOOTSTRAP_PASSWORD=ChangeMe123!
KEYCLOAK_BOOTSTRAP_USERS_JSON=[{"username":"{realm}_tenant_admin","roles":["tenant_admin"],"first_name":"Tenant","last_name":"Admin"},{"username":"{realm}_maker","roles":["maker"]},{"username":"{realm}_checker","roles":["checker"]}]

PLATFORM_INITIALIZATION_API_KEY=
```

Notes:

- `KEYCLOAK_CLIENTS_JSON` is only needed for the platform admin realm. Tenant realm client credentials are stored on the tenant row after tenant initialization.
- If you set `PLATFORM_INITIALIZATION_API_KEY`, platform initialization routes require `X-Initialization-Key`.

## Local Keycloak `.env`

Use this env file when running Keycloak locally:

```env
KEYCLOAK_HOST=127.0.0.1
KEYCLOAK_PORT=8080
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=admin
KC_HEALTH_ENABLED=true
KC_METRICS_ENABLED=true
KC_DB=dev-file

OAAS_PLATFORM_REALM=master
OAAS_PLATFORM_LOGIN_CLIENT_ID=oaas-admin-ui
OAAS_PLATFORM_LOGIN_CLIENT_SECRET=change-me-admin-ui-secret
OAAS_PROVISIONER_CLIENT_ID=oaas-provisioner
OAAS_PROVISIONER_CLIENT_SECRET=change-me-provisioner-secret
OAAS_TENANT_CLIENT_ID=oaas-client
OAAS_TENANT_BOOTSTRAP_PASSWORD=ChangeMe123!
```

The `OAAS_*` values are not consumed by Keycloak itself. They are the values you should use when creating the platform clients and when copying matching values into the O&V app `.env`.

## Run Keycloak

```bash
set -a
source .env.keycloak.example
set +a

docker run --rm --name oaas-keycloak \
  -p "${KEYCLOAK_PORT}:8080" \
  -e KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN}" \
  -e KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  -e KC_HEALTH_ENABLED="${KC_HEALTH_ENABLED}" \
  -e KC_METRICS_ENABLED="${KC_METRICS_ENABLED}" \
  -e KC_DB="${KC_DB}" \
  quay.io/keycloak/keycloak:26.1.3 start-dev
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080).

## Minimum Keycloak Setup

In the `master` realm:

1. Create client `oaas-admin-ui`
2. Enable `Client authentication`
3. Enable `Direct access grants`
4. Set client secret to `change-me-admin-ui-secret`
5. Create client `oaas-provisioner`
6. Enable `Client authentication`
7. Enable `Service accounts roles`
8. Set client secret to `change-me-provisioner-secret`
9. Grant the `oaas-provisioner` service account the needed `realm-management` client roles
10. Create realm role `super_admin`
11. Create a platform operator user and assign `super_admin`
12. Add a hardcoded claim mapper for `tenant_id=master`
13. Add a hardcoded claim mapper for `master_claims.tenant_id=master`

## Run O&V

```bash
uv sync
docker compose -f docker-compose.dev.yaml up db temporal temporal-ui -d
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 7090
uv run python -m app.temporal.worker
```

Temporal URLs:

- gRPC: `127.0.0.1:7233`
- UI: [http://127.0.0.1:8233](http://127.0.0.1:8233)

## Tenant Initialization Demo Request

Create a tenant with one canonical key:

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/tenants" \
  -H "Authorization: Bearer REPLACE_WITH_PLATFORM_SUPER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Corporate Bank",
    "tenant_key": "corporate"
  }'
```

Expected result:

- PostgreSQL schema `tenant_corporate` is initialized
- Keycloak realm `corporate` is initialized
- tenant client `oaas-client` is created in that realm
- bootstrap users like `corporate_tenant_admin` are created if enabled
