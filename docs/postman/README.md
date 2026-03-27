# Postman: Kong → Keycloak → JWT → Tenant APIs

Import:
- `docs/postman/oaas_collection.json`
- (optional) an environment generated from `scripts/postman/generate_env.py`

## Quick setup (recommended)

Generate an environment file and import it into Postman:

```bash
.venv/bin/python scripts/postman/generate_env.py \
  --name "OAAS Local" \
  --base-url "http://127.0.0.1:7090" \
  --realm "demo" \
  --client "mobile" \
  --tenant "demo" \
  --username "demo_super_admin" \
  --password "test123" \
  > docs/postman/oaas_local.postman_environment.json
```

If you set `PLATFORM_PROVISIONING_API_KEY` in the server, also pass:

```bash
  --provisioning-key "<your key>"
```

## Flow
1. Run **Auth → Login** (stores `access_token` + `refresh_token` into the environment)
2. (Optional) Run **Auth → Me** to inspect resolved roles/permissions and tenant context
3. Run tenant-scoped API requests (tenant context is derived from JWT; `X-Tenant-ID` is optional and must match the JWT tenant claim if provided)
4. When needed, run **Auth → Refresh** and retry

## AuthZ policy scopes
- **Global policy**: `GET/PUT /api/v1/authz/policy`
- **Realm policy (unlinked)**: `GET/PUT /api/v1/authz/realm-policy/{realm}` (use before a tenant exists)
- **Tenant policy (tenant-linked, by realm)**: `GET/PUT /api/v1/authz/policy/{realm}` (404 if no tenant is linked to that realm)

Kong routing:
- See `docs/kong/kong.yaml` for an example declarative config.
