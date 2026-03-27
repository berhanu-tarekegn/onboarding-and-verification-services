"""Authorization policy schemas."""

from __future__ import annotations

from typing import Any

from sqlmodel import SQLModel, Field


class AuthzPolicyDoc(SQLModel):
    """Policy document stored in DB."""

    mode: str = Field(default="merge", description="merge | replace")
    roles: dict[str, list[str]] = Field(default_factory=dict, description="role -> permissions")
    columns: dict[str, dict[str, dict[str, list[str]]]] = Field(
        default_factory=dict,
        description="role -> permission -> {allow|deny: [fields]} for field-level access control (read masking and write blocking)",
    )


class AuthzPolicyRead(SQLModel):
    scope: str
    tenant_id: str | None = None
    realm: str | None = None
    version: int
    policy: dict[str, Any]


class AuthzPolicyUpdate(SQLModel):
    """PUT body; replaces the stored policy doc."""

    policy: AuthzPolicyDoc
