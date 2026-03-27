The repo splits these concerns pretty cleanly:

**Keycloak**
Keycloak is used in 3 different ways here.

1. As the token issuer for API auth. The login/refresh proxy lives in [routes.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/routes/auth/routes.py#L463). `POST /api/auth/login/{realm}` and `POST /api/auth/refresh/{realm}` validate the realm, resolve the client credentials for that realm, then proxy to Keycloak’s token endpoint. Client credentials can come from env mapping, from tenant DB metadata, or from the global default client in that order; see [routes.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/routes/auth/routes.py#L131).

2. As tenant identity infrastructure. The tenant record stores `keycloak_realm`, `keycloak_client_id`, and `keycloak_client_secret` in [tenant.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/models/public/tenant.py#L48). When a tenant is created, the service can also provision a Keycloak realm, roles, and an OIDC client via [tenant.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/services/tenants/tenant.py#L138) and [admin.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/integrations/keycloak/admin.py#L96).

3. As the user store for tenant users. Creating a tenant user goes through [tenant.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/services/tenants/tenant.py#L309), which ensures the realm exists, ensures the client has protocol mappers, creates or updates the Keycloak user, sets password, assigns realm roles, and then stores a local reconciliation link in [identity_link.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/models/public/identity_link.py#L13). The important design detail is the protocol mappers in [admin.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/integrations/keycloak/admin.py#L182): they project tenant-specific claims like `{realm}_claims.tenant_id`, `national_id`, `phone_number`, etc. into the token.

**Auth**
App-side auth is not delegated entirely to Kong. The service re-validates JWTs itself in [auth.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/auth.py#L351). The flow is:

- `JWTAuthMiddleware` in [auth.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/auth.py#L596) reads the bearer token once per request.
- `decode_jwt()` verifies issuer allowlisting, resolves JWKS either from configured static JWKS or dynamically from `iss + /protocol/openid-connect/certs`, and validates the signature and claims in [auth.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/auth.py#L351).
- `_build_auth_context()` extracts `user_id`, `tenant_id`, and roles from the token in [auth.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/auth.py#L483). `tenant_id` is read from `AUTH_TENANT_CLAIM`, and that setting supports a `{realm}` placeholder, so a token claim path like `{realm}_claims.tenant_id` works.
- The middleware then stores request-scoped contextvars for tenant, roles, and user. Tenant header handling is in [tenants.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/middleware/tenants.py#L7): JWT tenant context wins over `X-Tenant-ID`.

Authorization is a second layer on top of auth. Static role-to-permission defaults live in [authz.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/authz.py#L25). Runtime policy overrides are stored in `public.authz_policies` via [authz_policy.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/models/public/authz_policy.py#L18) and managed by [policy.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/services/authz/policy.py#L32). At request time, `require_permission()` in [authz.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/authz.py#L500) merges permissions in this order:

- code defaults
- global policy
- realm policy
- tenant policy

It also supports field-level column rules. `/api/auth/me` in [routes.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/routes/auth/routes.py#L555) is basically the best introspection endpoint for this whole stack because it shows the resolved tenant, roles, permissions, and active policies.

**Temporal**
Temporal exists, but it is mostly scaffolding right now.

- Config is defined in [config.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/core/config.py#L27).
- There is a client singleton in [client.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/temporal/client.py#L23).
- There is a standalone worker process in [worker.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/temporal/worker.py#L27).
- There is one workflow, [onboarding.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/temporal/workflows/onboarding.py#L19), and one placeholder activity, [onboarding.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/temporal/activities/onboarding.py#L12).

But it is not wired into the live FastAPI request path yet:
- the startup connection is commented out in [main.py](/Users/berhanu.tarekegn/git/onboarding-and-verification/app/main.py#L57)
- I could not find any route or service calling `get_temporal_client()` or `start_workflow()`

So the current state is: Temporal support is prepared, but onboarding workflows are not actually started by the API yet. The workflow itself is still a placeholder that just executes `greet_user`.

If you want, I can do a second pass that traces one concrete flow end-to-end:
1. tenant creation and Keycloak provisioning,
2. login to protected API request,
3. where Temporal would plug into submissions/onboarding once implemented.