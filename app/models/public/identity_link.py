"""Identity link between OAAS tenant and Keycloak user."""

from __future__ import annotations

import uuid as _uuid

from sqlmodel import Field
from uuid_extensions import uuid7

from app.models.base import PublicSchemaModel


class IdentityLink(PublicSchemaModel, table=True):
    __tablename__ = "identity_links"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(default_factory=uuid7, primary_key=True, nullable=False)

    tenant_id: _uuid.UUID = Field(index=True, nullable=False)
    realm: str = Field(index=True, max_length=64, nullable=False)
    keycloak_user_id: str = Field(index=True, max_length=64, nullable=False)

    username: str = Field(max_length=255, nullable=False)
    national_id: str = Field(index=True, max_length=64, nullable=False)

