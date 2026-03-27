# Kong (US-204): Login + Refresh → Keycloak

This repo does **not** run Kong. US-204 is implemented as configuration guidance.

## Kong routes (example)

See `docs/kong/kong.yaml` for the recommended setup:

- Kong forwards `POST /api/auth/login/<realm>` and `POST /api/auth/refresh/<realm>`
  to this FastAPI service (single Kong Service + two Routes).
- This FastAPI service forwards to Keycloak's token endpoint:
  `https://sso.qena.dev/realms/<realm>/protocol/openid-connect/token`

## cURL examples

Login (JSON body; server injects `grant_type` + `client_id`):
```bash
curl -sS -X POST "https://oaas-dev.qena.dev/api/auth/login/kifiya" \
  -H "Content-Type: application/json" \
  -d '{"username":"REPLACE_ME","password":"REPLACE_ME"}'
```

Select a specific client (server-configured) using a query param:
```bash
curl -sS -X POST "https://oaas-dev.qena.dev/api/auth/login/ovp?client=mobile" \
  -H "Content-Type: application/json" \
  -d '{"username":"REPLACE_ME","password":"REPLACE_ME"}'
```

Refresh:
```bash
curl -sS -X POST "https://oaas-dev.qena.dev/api/auth/refresh/kifiya" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"REPLACE_ME"}'
```

If your Keycloak client is confidential, configure `KEYCLOAK_CLIENT_SECRET` (or `KEYCLOAK_CLIENTS_JSON`) on the upstream service.

Realm handling:
- Realms are passed as path params (`/api/auth/login/<realm>`).
- If `KEYCLOAK_REALMS` is empty, the service validates the realm by calling Keycloak discovery (`.well-known/openid-configuration`) and caches the result.
