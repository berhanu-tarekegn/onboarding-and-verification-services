# Keycloak Setup for Platform and Tenant Realms

This repo now enforces this separation:

- `super_admin` is valid only from the platform admin realm.
- each tenant gets its own Keycloak realm.
- tenant realms use `tenant_admin` for tenant user and tenant policy management.
- platform super admins can operate on tenant-scoped routes only when they pass `X-Tenant-ID`.

## Realm Model

Use two identity layers:

1. Platform admin realm
   - usually `master`
   - contains platform operators
   - only this realm may issue tokens with `super_admin`

2. Tenant realms
   - one realm per tenant, usually the same as `schema_name`
   - contain tenant admins and tenant users
   - provisioned by this service when a tenant is created

## App Environment

Set the app to expect per-realm tenant claim namespaces and Keycloak provisioning:

```env
AUTH_ENABLED=true
AUTH_TENANT_CLAIM=tenant_id,{realm}_claims.tenant_id
AUTH_EXCLUSIVE_ROLE_GROUPS=maker|checker

KEYCLOAK_BASE_URL=https://sso.example.com
KEYCLOAK_ADMIN_BASE_URL=https://sso.example.com
KEYCLOAK_TRUSTED_ISSUER_BASES=https://sso.example.com
KEYCLOAK_REALMS=

KEYCLOAK_PROVISIONING_ENABLED=true
KEYCLOAK_PROVISIONING_REQUIRED=true
KEYCLOAK_ADMIN_REALM=master
KEYCLOAK_ADMIN_CLIENT_ID=oaas-provisioner
KEYCLOAK_ADMIN_CLIENT_SECRET=REPLACE_ME

KEYCLOAK_TENANT_CLIENT_ID=oaas-client
KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL=true

# If all realms reuse the same login client id/secret, keep these.
KEYCLOAK_CLIENT_ID=oaas-client
KEYCLOAK_CLIENT_SECRET=REPLACE_ME

# Optional tenant bootstrap users.
KEYCLOAK_BOOTSTRAP_USERS_JSON=[
  {"username":"{realm}_tenant_admin","roles":["tenant_admin"],"first_name":"Tenant","last_name":"Admin"},
  {"username":"{realm}_maker","roles":["maker"]},
  {"username":"{realm}_checker","roles":["checker"]}
]
KEYCLOAK_BOOTSTRAP_PASSWORD=ChangeMe123!
KEYCLOAK_BOOTSTRAP_EMAIL_DOMAIN=example.com
```

Notes:

- Leave `KEYCLOAK_REALMS` empty if you want the service to accept newly-created tenant realms after discovery.
- If the admin realm uses a different login client than tenant realms, use `KEYCLOAK_CLIENTS_JSON` instead of the global `KEYCLOAK_CLIENT_ID` and `KEYCLOAK_CLIENT_SECRET`.

Example:

```json
{
  "master": {
    "client_id": "oaas-admin-ui",
    "client_secret": "REPLACE_ME"
  }
}
```

Tenant realms created by this service store their client credentials on the tenant record and are used automatically by `/api/auth/login/{realm}`.

## Step 1: Prepare the Platform Admin Realm

If you use a dedicated platform realm instead of `master`, replace `master` everywhere below and set `KEYCLOAK_ADMIN_REALM` to that realm name.

Create these items in the platform admin realm:

1. A realm role named `super_admin`.
2. A client for platform login, usually `oaas-client` or `oaas-admin-ui`.
3. A tenant claim on that client so platform tokens still satisfy app auth.

Recommended client settings:

- `Client authentication`: on
- `Direct access grants`: on if you will use `/api/auth/login/{realm}` with username/password
- `Standard flow`: optional

Recommended claim mappers on the platform login client:

1. Hardcoded claim `tenant_id=master`
2. Hardcoded claim `master_claims.tenant_id=master`

If your admin realm is named `platform`, use `platform_claims.tenant_id=platform` instead.

The service reads `AUTH_TENANT_CLAIM=tenant_id,{realm}_claims.tenant_id`, so either mapper is enough. Adding both makes debugging easier.

Create a platform operator user:

1. Create the user.
2. Set a password.
3. Assign the realm role `super_admin`.

## Step 2: Prepare the Provisioner Client

The service provisions tenant realms through the Keycloak Admin API. The client configured by:

- `KEYCLOAK_ADMIN_REALM`
- `KEYCLOAK_ADMIN_CLIENT_ID`
- `KEYCLOAK_ADMIN_CLIENT_SECRET`

must have enough admin privileges in the platform admin realm.

Grant the provisioner service account the admin roles required to:

- create realms
- manage realms
- manage clients
- manage users
- view users
- query users
- view realms

On recent Keycloak versions this is usually done with client roles under `realm-management`.

## Step 3: What the App Provisions for Each Tenant

When you create a tenant through the API and `KEYCLOAK_PROVISIONING_ENABLED=true`, the app will:

1. create the tenant realm if it does not exist
2. create realm roles:
   - `tenant_admin`
   - `platform_admin`
   - `schema_author`
   - `maker`
   - `checker`
3. create the tenant OIDC client
4. add protocol mappers for:
   - `{realm}_claims.tenant_id`
   - `{realm}_claims.allowed_roles`
   - `{realm}_claims.user_id`
   - `{realm}_claims.national_id`
   - `{realm}_claims.birth_date`
   - `{realm}_claims.phone_number`
   - `{realm}_claims.address`
5. optionally create bootstrap users from `KEYCLOAK_BOOTSTRAP_USERS_JSON`

The service no longer creates `super_admin` in tenant realms.

## Step 4: Create a Tenant

Login as a platform super admin:

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/auth/login/master" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "platform.admin",
    "password": "REPLACE_ME"
  }'
```

Create a tenant:

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/tenants" \
  -H "Authorization: Bearer REPLACE_WITH_PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Bank",
    "schema_name": "acme_bank",
    "display_name": "Acme Bank",
    "is_active": true
  }'
```

Expected result:

- PostgreSQL tenant schema is provisioned
- Keycloak realm `acme_bank` is provisioned
- tenant client `oaas-client` is created
- bootstrap tenant users are created if configured

## Step 5: Login as a Tenant Admin

If bootstrap users are enabled, login with the generated tenant admin:

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/auth/login/acme_bank" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "acme_bank_tenant_admin",
    "password": "ChangeMe123!"
  }'
```

Inspect the resolved auth context:

```bash
curl -sS "http://127.0.0.1:7090/api/auth/me" \
  -H "Authorization: Bearer REPLACE_WITH_TENANT_ADMIN_TOKEN"
```

You should see:

- `realm=acme_bank`
- `roles` containing `tenant_admin`
- `tenant_claim=acme_bank`

## Step 6: Create Tenant Users

As the tenant admin, create a user in the same tenant realm:

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/tenants/REPLACE_WITH_TENANT_UUID/users" \
  -H "Authorization: Bearer REPLACE_WITH_TENANT_ADMIN_TOKEN" \
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
```

A tenant admin cannot manage another tenant's realm.

## Step 7: Platform Super Admin on Tenant-Scoped Routes

For tenant-scoped routes, a platform super admin must pass `X-Tenant-ID`.

Example:

```bash
curl -sS "http://127.0.0.1:7090/api/v1/templates" \
  -H "Authorization: Bearer REPLACE_WITH_PLATFORM_TOKEN" \
  -H "X-Tenant-ID: acme_bank"
```

Without `X-Tenant-ID`, tenant-scoped routes will reject the request.

## Demo Flow

Use this order for a quick demo:

1. Login as platform `super_admin` in the admin realm.
2. Create tenant `acme_bank`.
3. Login as `acme_bank_tenant_admin`.
4. Call `/api/auth/me` to confirm `tenant_admin` and tenant claim resolution.
5. Create tenant users for `maker` and `checker`.
6. Use those tenant users for onboarding and verification flows.

## Resulting Role Model

Use these roles going forward:

- platform admin realm:
  - `super_admin`
- tenant realms:
  - `tenant_admin`
  - `platform_admin`
  - `schema_author`
  - `maker`
  - `checker`

Practical guidance:

- use `tenant_admin` for tenant user management and tenant authz policy management
- use `platform_admin` for tenant business administration inside the tenant schema
- do not assign `super_admin` in tenant realms
