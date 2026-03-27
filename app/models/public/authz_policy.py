"""Authorization policy storage (public schema).

Policies are used to map roles -> permissions at runtime without redeploying.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field
from uuid_extensions import uuid7

from app.models.base import PublicSchemaModel


class AuthzPolicy(PublicSchemaModel, table=True):
    __tablename__ = "authz_policies"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(default_factory=uuid7, primary_key=True, nullable=False)

    # "global" or "tenant"
    scope: str = Field(index=True, max_length=32, nullable=False)

    # Only set for scope="tenant"
    tenant_id: _uuid.UUID | None = Field(default=None, index=True, nullable=True)

    version: int = Field(default=1, nullable=False)

    # JSON policy doc: {"mode":"merge","roles":{...}}
    policy: dict[str, Any] = Field(
        default_factory=dict,
        nullable=False,
        sa_type=sa.JSON(),
    )

