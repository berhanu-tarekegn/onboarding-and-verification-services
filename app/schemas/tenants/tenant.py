"""Tenant API schemas (SQLModel, no table)."""

from datetime import datetime
from uuid import UUID

from sqlmodel import SQLModel, Field


class TenantBase(SQLModel):
    """Fields shared between create requests and DB model."""

    name: str = Field(max_length=255)
    schema_name: str = Field(
        max_length=63,
        description="PostgreSQL schema name for this tenant. "
                    "Lowercase letters, digits, underscores only (e.g. 'acme_bank').",
    )


class TenantCreate(TenantBase):
    """Body for POST /tenants."""

    keycloak_realm: str | None = Field(
        default=None,
        description="Optional Keycloak realm name. Defaults to schema_name when provisioning is enabled.",
        max_length=64,
    )


class TenantUpdate(SQLModel):
    """Body for PATCH /tenants/{id}. All fields optional."""

    name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class TenantRead(TenantBase):
    """Response model returned by all tenant endpoints."""

    id: UUID
    is_active: bool
    keycloak_realm: str | None = None
    keycloak_client_id: str | None = None
    created_at: datetime
    updated_at: datetime
