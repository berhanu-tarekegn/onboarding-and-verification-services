# Local Development Environment

This repo now uses:

- `tenant_key` as the single public machine identifier for a tenant
- `tenant initialization` as the operator-facing term for schema + Keycloak setup

Two example env files are provided:

- O&V app: [`.env.example`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env.example)
- local Keycloak: [`.env.keycloak.local`](/Users/berhanu.tarekegn/git/onboarding-and-verification/.env.keycloak.local)
- automated Keycloak bootstrap: [bootstrap_keycloak_local.sh](/Users/berhanu.tarekegn/git/onboarding-and-verification/scripts/bootstrap_keycloak_local.sh)
- local Temporal launcher: [start_temporal_local.sh](/Users/berhanu.tarekegn/git/onboarding-and-verification/scripts/start_temporal_local.sh)
- demo seed script: [seed_demo_data.sh](/Users/berhanu.tarekegn/git/onboarding-and-verification/scripts/seed_demo_data.sh)
- realistic demo payloads: [real_world_onboarding_sample.json](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json)

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
AUTH_AUDIENCE=
AUTH_EXCLUSIVE_ROLE_GROUPS=maker|checker

KEYCLOAK_BASE_URL=http://127.0.0.1:8080
KEYCLOAK_ADMIN_BASE_URL=http://127.0.0.1:8080
KEYCLOAK_TRUSTED_ISSUER_BASES=http://127.0.0.1:8080
KEYCLOAK_CLIENTS_JSON={"oaas-platform":{"client_id":"oaas-admin-ui","client_secret":"536302484a695aa30eab6b0e81ffb906c14a39a0801e2a53"}}
KEYCLOAK_PLATFORM_REALM=oaas-platform

KEYCLOAK_TENANT_INITIALIZATION_ENABLED=true
KEYCLOAK_TENANT_INITIALIZATION_REQUIRED=true
KEYCLOAK_ADMIN_REALM=master
KEYCLOAK_ADMIN_CLIENT_ID=oaas-provisioner
KEYCLOAK_ADMIN_CLIENT_SECRET=48b446e8a7e87e2439fe3a3a0a51deb9fdc88c24705d8462
KEYCLOAK_TENANT_CLIENT_ID=oaas-client
KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL=true
KEYCLOAK_BOOTSTRAP_PASSWORD=35314f15d69125e4b6789d74ff26e18b
KEYCLOAK_BOOTSTRAP_USERS_JSON=[{"username":"{realm}_tenant_admin","roles":["tenant_admin"],"first_name":"Tenant","last_name":"Admin"},{"username":"{realm}_maker","roles":["maker"]},{"username":"{realm}_checker","roles":["checker"]}]

PLATFORM_INITIALIZATION_API_KEY=

TEMPORAL_HOST=127.0.0.1:7233
TEMPORAL_ENABLED=true
TEMPORAL_REQUIRED=false
```

Notes:

- `KEYCLOAK_CLIENTS_JSON` is only needed for the platform admin realm. Tenant realm client credentials are stored on the tenant row after tenant initialization.
- If you set `PLATFORM_INITIALIZATION_API_KEY`, platform initialization routes require `X-Initialization-Key`.

## Local Keycloak `.env`

Use this env file when running Keycloak locally:

```env
KEYCLOAK_HOST=127.0.0.1
KEYCLOAK_PORT=8080
KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=admin
KC_HEALTH_ENABLED=true
KC_METRICS_ENABLED=true
KC_DB=dev-file

OAAS_PLATFORM_REALM=oaas-platform
OAAS_PROVISIONER_REALM=master
OAAS_PLATFORM_LOGIN_CLIENT_ID=oaas-admin-ui
OAAS_PLATFORM_LOGIN_CLIENT_SECRET=536302484a695aa30eab6b0e81ffb906c14a39a0801e2a53
OAAS_PLATFORM_ADMIN_USERNAME=platform.admin
OAAS_PLATFORM_ADMIN_PASSWORD=117f19dd3c2c8164c9ee2642e0da6f65
OAAS_PROVISIONER_CLIENT_ID=oaas-provisioner
OAAS_PROVISIONER_CLIENT_SECRET=48b446e8a7e87e2439fe3a3a0a51deb9fdc88c24705d8462
OAAS_TENANT_CLIENT_ID=oaas-client
OAAS_TENANT_BOOTSTRAP_PASSWORD=35314f15d69125e4b6789d74ff26e18b
```

The `OAAS_*` values are not consumed by Keycloak itself. They drive the bootstrap script:

- `OAAS_PLATFORM_REALM`: the app-facing platform realm used for `super_admin` login
- `OAAS_PROVISIONER_REALM`: the Keycloak admin realm used for tenant initialization, typically `master`

## Run Keycloak

For a local Keycloak install, start it from your Keycloak directory with the current
bootstrap admin variables described in the official Keycloak docs:

```bash
cd /path/to/keycloak

export KC_BOOTSTRAP_ADMIN_USERNAME=admin
export KC_BOOTSTRAP_ADMIN_PASSWORD=admin

bin/kc.sh start-dev --http-host=127.0.0.1 --http-port=8080
```

For the Docker container path instead:

```bash
docker compose -f docker-compose.dev.yaml up keycloak -d
```

Then open:

- Keycloak: [http://127.0.0.1:8080](http://127.0.0.1:8080)

If you do not already have PostgreSQL running locally, start it separately with:

```bash
docker compose -f docker-compose.dev.yaml up db -d
```

## Run Temporal Locally

Install the Temporal CLI locally. Temporal’s install guide supports Homebrew on macOS:

```bash
brew install temporal
```

Then start the local Temporal dev server:

```bash
chmod +x scripts/start_temporal_local.sh
./scripts/start_temporal_local.sh
```

This runs Temporal outside Docker and exposes:

- gRPC: `127.0.0.1:7233`
- UI: [http://127.0.0.1:8233](http://127.0.0.1:8233)

If you had already started Docker Temporal services before, stop them first so the ports are free:

```bash
docker compose -f docker-compose.dev.yaml stop temporal temporal-ui
```

## Bootstrap Keycloak

Run the local bootstrap script from the repo root:

```bash
chmod +x scripts/bootstrap_keycloak_local.sh
./scripts/bootstrap_keycloak_local.sh
```

The script now creates or updates:

- platform realm `oaas-platform`
- platform login client `oaas-admin-ui`
- platform admin user `platform.admin`
- provisioner client `oaas-provisioner` in admin realm `master`

It fails if the provisioner service account is missing the required admin roles.

If you are using a local Keycloak install instead of Docker, point the script at it:

```bash
export KEYCLOAK_HOME=/path/to/keycloak
./scripts/bootstrap_keycloak_local.sh
```

Alternatively:

```bash
export KCADM_BIN=/path/to/keycloak/bin/kcadm.sh
./scripts/bootstrap_keycloak_local.sh
```

## Run O&V

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 7090
uv run python -m app.temporal.worker
```

If you want to run tests locally too, create the test database and point
`DATABASE_TEST_URL` at it.

Temporal URLs:

- gRPC: `127.0.0.1:7233`
- UI: [http://127.0.0.1:8233](http://127.0.0.1:8233)

## Seed the Demo Data

In the current local no-auth mode, seed the sample tenant, template, product,
submissions, and verification runs with:

```bash
chmod +x scripts/seed_demo_data.sh
./scripts/seed_demo_data.sh
```

The script is idempotent for the sample records. Re-running it reuses existing
tenant, template, product, and submissions when they already exist.

## Tenant Initialization Demo Request

Get a real platform token first:

```bash
export BASE_URL=http://127.0.0.1:7090

export PLATFORM_TOKEN=$(
  curl -sS -X POST "$BASE_URL/api/auth/login/oaas-platform" \
    -H "Content-Type: application/json" \
    -d '{
      "username": "platform.admin",
      "password": "117f19dd3c2c8164c9ee2642e0da6f65"
    }' | jq -r '.access_token'
)
```

Then create a tenant with one canonical key:

```bash
curl -sS -X POST "$BASE_URL/api/v1/tenants" \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
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
