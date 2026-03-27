"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration consumed by every layer of the service."""

    _REPO_ROOT = Path(__file__).resolve().parents[2]

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ──────────────────────────────
    APP_NAME: str = "Template Service"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # ── Database ─────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/template_service"
    )

    # ── Temporal ─────────────────────────────────
    TEMPORAL_HOST: str = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"
    TEMPORAL_TASK_QUEUE: str = "onboarding-task-queue"
    TEMPORAL_ENABLED: bool = True
    # Local-first default: if Temporal isn't running, let the API still start.
    # Set TEMPORAL_REQUIRED=true in environments where Temporal must be available.
    TEMPORAL_REQUIRED: bool = False

    # ── Auth / JWT (Keycloak) ────────────────────
    AUTH_ENABLED: bool = True
    AUTH_TENANT_CLAIM: str = "tenant_id"
    # Comma-separated string (dotenv friendly), e.g. "RS256" or "RS256,PS256"
    AUTH_ALGORITHMS: str = "RS256"
    AUTH_AUDIENCE: str | None = None
    # Optional allow-list, comma-separated, e.g. "https://kc/realms/ovp,https://kc/realms/daf"
    AUTH_ISSUERS: str = ""
    # Optional: mutually exclusive role groups (comma-separated).
    # Within a group, roles are separated by "|".
    #
    # Example (default):
    #   AUTH_EXCLUSIVE_ROLE_GROUPS=maker|checker
    #
    # When a token contains more than one role in the same group, requests are rejected (403).
    AUTH_EXCLUSIVE_ROLE_GROUPS: str = "maker|checker"

    # JWKS sources (at least one required when AUTH_ENABLED=true)
    KEYCLOAK_JWKS_URL: str = ""
    KEYCLOAK_JWKS_URLS: str = ""
    # Raw JWKS JSON (useful for tests / offline dev)
    KEYCLOAK_JWKS_JSON: str = ""
    # Optional: allow dynamic JWKS resolution from the token's `iss` claim.
    # When set, the service will accept issuers whose base matches one of these values
    # and fetch JWKS from: <iss>/protocol/openid-connect/certs.
    #
    # Example:
    #   KEYCLOAK_TRUSTED_ISSUER_BASES=https://sso.qena.dev
    KEYCLOAK_TRUSTED_ISSUER_BASES: str = ""
    JWKS_REFRESH_SECONDS: int = 60 * 60 * 24
    JWKS_REQUIRED: bool = True
    # Optional JSON object of headers to use when fetching JWKS URLs.
    # Example: {"Authorization":"Bearer <token>"}
    JWKS_FETCH_HEADERS_JSON: str = ""
    JWKS_FETCH_TIMEOUT_SECONDS: int = 10

    # Endpoint authorization (comma-separated roles)
    TEMPLATES_REQUIRED_ROLES: str = ""

    # ── Keycloak token proxy (US-204) ───────────
    # Base URL of Keycloak (no realm), e.g. "https://sso.qena.dev"
    KEYCLOAK_BASE_URL: str = ""
    # Default client used for all realms (unless KEYCLOAK_CLIENTS_JSON overrides per realm)
    KEYCLOAK_CLIENT_ID: str = ""
    KEYCLOAK_CLIENT_SECRET: str = ""
    # Optional allow-list of realms (comma-separated). If empty, allow any realm matching [a-zA-Z0-9_-]{1,64}.
    KEYCLOAK_REALMS: str = ""
    # Optional per-realm client mapping JSON:
    # Simple per-realm:
    # {"ovp":{"client_id":"...","client_secret":"..."},"daf":{"client_id":"..."}}
    #
    # Multiple clients per realm (select with ?client=mobile):
    # {"ovp":{"default":"mobile","clients":{"mobile":{"client_id":"..."},"web":{"client_id":"..."}}}}
    KEYCLOAK_CLIENTS_JSON: str = ""
    # Optional JSON headers used for Keycloak token + realm discovery calls (dotenv friendly).
    # Example: {"X-Forwarded-Proto":"https","Authorization":"Bearer ..."}
    KEYCLOAK_HTTP_HEADERS_JSON: str = ""
    KEYCLOAK_HTTP_TIMEOUT_SECONDS: int = 10
    # When KEYCLOAK_REALMS is empty, realm existence is validated via:
    #   /realms/<realm>/.well-known/openid-configuration
    # Results are cached for this many seconds.
    KEYCLOAK_REALM_DISCOVERY_TTL_SECONDS: int = 60 * 60

    # ── Keycloak provisioning (US-201) ──────────
    # Optional dedicated base URL for Keycloak Admin API calls. This is useful
    # when user-facing traffic goes through Kong (e.g. /keycloak) but the Admin
    # API is only reachable via a different internal URL.
    KEYCLOAK_ADMIN_BASE_URL: str = ""
    KEYCLOAK_PROVISIONING_ENABLED: bool = False
    KEYCLOAK_PROVISIONING_REQUIRED: bool = False
    KEYCLOAK_ADMIN_REALM: str = "master"
    KEYCLOAK_ADMIN_CLIENT_ID: str = "admin-cli"
    # Optional: prefer client_credentials (service account) over admin user/pass.
    # If set, provisioning will authenticate with grant_type=client_credentials.
    KEYCLOAK_ADMIN_CLIENT_SECRET: str = ""
    KEYCLOAK_ADMIN_USERNAME: str = ""
    KEYCLOAK_ADMIN_PASSWORD: str = ""
    # Client ID to create inside each realm when provisioning.
    KEYCLOAK_TENANT_CLIENT_ID: str = "oaas-client"
    KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL: bool = True

    # Optional: bootstrap users created in each newly-provisioned realm.
    # Provide a JSON list like:
    # [{"username":"{realm}_super_admin","roles":["super_admin"]},
    #  {"username":"{realm}_platform_admin","roles":["platform_admin"]},
    #  {"username":"{realm}_maker","roles":["maker"]},
    #  {"username":"{realm}_checker","roles":["checker"]}]
    # `{realm}` is replaced with the tenant schema_name / realm name.
    KEYCLOAK_BOOTSTRAP_USERS_JSON: str = ""
    # Password to set for all bootstrap users (dev only). If empty, no users are created.
    KEYCLOAK_BOOTSTRAP_PASSWORD: str = ""
    # When bootstrapping users and no explicit "email" is provided, an email
    # will be derived as: <username>@<domain>.
    KEYCLOAK_BOOTSTRAP_EMAIL_DOMAIN: str = "example.com"

    # ── AuthZ policy cache ─────────────────────────────────────────
    AUTHZ_CACHE_TTL_SECONDS: int = 30

    # ── Internal provisioning guard ─────────────
    # Optional shared secret to protect "platform provisioning" endpoints
    # (e.g. POST /api/v1/tenants) in addition to JWT role checks.
    # If empty, no extra guard is applied.
    PLATFORM_PROVISIONING_API_KEY: str = ""

    # ── User management integration ──────────────────
    USER_MANAGEMENT_SERVICE_ENABLED: bool = False
    USER_MANAGEMENT_SERVICE_URL: str = ""
    USER_MANAGEMENT_USER_PATH: str = "users"
    USER_MANAGEMENT_TIMEOUT_SECONDS: int = 10

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug(cls, value):  # noqa: ANN001
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "t", "yes", "y", "on", "debug", "dev", "development"}:
                return True
            if normalized in {"0", "false", "f", "no", "n", "off", "release", "prod", "production"}:
                return False
        return value

    @field_validator("AUTH_AUDIENCE", mode="before")
    @classmethod
    def _parse_auth_audience(cls, value):  # noqa: ANN001
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
