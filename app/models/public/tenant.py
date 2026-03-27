"""Tenant registry model — lives in the ``public`` schema.

The Tenant model stores metadata about each onboarded tenant.
The tenant's ``tenant_key`` is used directly as the PostgreSQL schema name
for data isolation.
"""

import uuid as _uuid

from sqlmodel import Field
from uuid_extensions import uuid7

from app.models.base import PublicSchemaModel


class Tenant(PublicSchemaModel, table=True):
    """Registry of onboarded tenants.

    Each tenant gets:
    - A unique UUID (used as X-Tenant-ID header value)
    - A human-readable name
    - A tenant_key that IS the PostgreSQL schema key (no spaces, no special chars)
    - An active flag for soft-delete functionality

    The ``tenant_key`` is used directly as the tenant's PostgreSQL schema name,
    enabling schema-based isolation of tenant data. It must be unique, lowercase,
    contain only letters/digits/underscores, and start with a letter.
    """

    __tablename__ = "tenants"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )
    name: str = Field(index=True, max_length=255)
    tenant_key: str = Field(
        unique=True,
        index=True,
        max_length=63,  # PostgreSQL identifier limit
        description="Used directly as the tenant's PostgreSQL schema name. "
                    "Lowercase letters, digits, underscores only. No spaces.",
    )
    is_active: bool = Field(default=True)

    # ── Keycloak provisioning metadata ───────────────────────────────
    # Realm name is typically the same as tenant_key (platform identifier).
    keycloak_realm: str | None = Field(
        default=None,
        unique=True,
        index=True,
        max_length=64,
        description="Keycloak realm associated with this tenant/platform.",
    )
    # Client credentials used by the auth proxy when exchanging user credentials for tokens.
    # NOTE: Storing secrets in the DB has security implications; consider a secrets manager.
    keycloak_client_id: str | None = Field(
        default=None,
        max_length=255,
        description="Keycloak OIDC client_id for this realm.",
    )
    keycloak_client_secret: str | None = Field(
        default=None,
        max_length=2048,
        description="Keycloak OIDC client secret for this realm (if confidential).",
    )

    @property
    def schema_name(self) -> str:
        """Compatibility alias during the tenant_key migration."""
        return self.tenant_key

    @schema_name.setter
    def schema_name(self, value: str) -> None:
        self.tenant_key = value
