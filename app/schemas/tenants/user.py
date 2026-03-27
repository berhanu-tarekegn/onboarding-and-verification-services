"""Tenant user provisioning schemas (Keycloak-backed)."""

from datetime import date

from sqlmodel import SQLModel, Field


class TenantUserCreate(SQLModel):
    """Body for POST /tenants/{tenant_id}/users.

    Creates a Keycloak user in the tenant's realm and assigns realm roles.
    """

    national_id: str = Field(
        min_length=4,
        max_length=64,
        description="Ethiopian national ID (stored as a Keycloak user attribute and exposed as a JWT claim).",
    )
    username: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
        description="Keycloak username. Defaults to national_id when omitted.",
    )
    password: str = Field(
        min_length=6,
        max_length=256,
        description="Initial password for the user (stored only in Keycloak).",
    )
    roles: list[str] = Field(
        default_factory=list,
        description="Realm roles to assign (e.g., tenant_admin, maker, checker, platform_admin).",
    )
    email: str | None = Field(default=None, max_length=255)
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    birth_date: date | None = Field(
        default=None,
        description="Date of birth from Ethiopian national ID record (stored as attribute and exposed as claim).",
    )
    phone_number: str | None = Field(
        default=None,
        max_length=64,
        description="Phone number from Ethiopian national ID record (stored as attribute and exposed as claim).",
    )
    address: str | None = Field(
        default=None,
        max_length=512,
        description="Address from Ethiopian national ID record (stored as attribute and exposed as claim).",
    )


class TenantUserRead(SQLModel):
    """Response model for created Keycloak user."""

    realm: str
    user_id: str
    username: str
    national_id: str
    roles: list[str]
    birth_date: date | None = None
    phone_number: str | None = None
    address: str | None = None
