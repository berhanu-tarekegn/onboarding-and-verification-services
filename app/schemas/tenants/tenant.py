"""Tenant API schemas (SQLModel, no table)."""

from datetime import datetime
import re
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, field_validator, model_validator
from sqlmodel import SQLModel, Field

_TENANT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class TenantBase(SQLModel):
    """Fields shared between create requests and API responses."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str = Field(max_length=255)
    tenant_key: str = Field(
        max_length=63,
        description=(
            "Stable tenant identifier used for tenant initialization, PostgreSQL schema naming, "
            "and Keycloak realm naming. Lowercase letters, digits, underscores only "
            "(e.g. 'acme_bank')."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        legacy_schema_name = data.pop("schema_name", None)
        if legacy_schema_name is not None and "tenant_key" not in data:
            data["tenant_key"] = legacy_schema_name

        realm = data.pop("keycloak_realm", None)
        tenant_key = data.get("tenant_key")
        if realm and tenant_key and realm != tenant_key:
            raise ValueError("keycloak_realm must match tenant_key. Use tenant_key as the single canonical identifier.")
        return data

    @field_validator("tenant_key")
    @classmethod
    def _validate_tenant_key(cls, value: str) -> str:
        normalized = value.strip()
        if not _TENANT_KEY_RE.fullmatch(normalized):
            raise ValueError(
                "tenant_key must start with a lowercase letter and contain only lowercase letters, digits, or underscores."
            )
        return normalized


class TenantCreate(TenantBase):
    """Body for POST /tenants."""


class TenantUpdate(SQLModel):
    """Body for PATCH /tenants/{id}. All fields optional."""

    name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class TenantRead(TenantBase):
    """Response model returned by all tenant endpoints."""

    id: UUID
    is_active: bool
    keycloak_client_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_tenant(cls, tenant: Any) -> "TenantRead":
        return cls(
            id=tenant.id,
            name=tenant.name,
            tenant_key=tenant.tenant_key,
            is_active=tenant.is_active,
            keycloak_client_id=tenant.keycloak_client_id,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )
