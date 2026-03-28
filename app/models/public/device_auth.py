"""Public-schema models for mobile device challenge authentication."""

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, JSON
from uuid_extensions import uuid7

from app.models.base import PublicSchemaModel


class DeviceCredential(PublicSchemaModel, table=True):
    """A device-bound public key registered after OTP/bootstrap login."""

    __tablename__ = "device_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_key", "subject", "device_id", name="uq_device_credentials_subject_device"),
        {"schema": "public"},
    )

    id: _uuid.UUID = Field(default_factory=uuid7, primary_key=True, nullable=False)
    tenant_id: _uuid.UUID = Field(foreign_key="public.tenants.id", index=True, nullable=False)
    tenant_key: str = Field(index=True, max_length=63, nullable=False)
    subject: str = Field(index=True, max_length=255, nullable=False)
    login_hint: str | None = Field(default=None, max_length=255)
    device_id: str = Field(index=True, max_length=255, nullable=False)
    device_name: str | None = Field(default=None, max_length=255)
    algorithm: str = Field(default="ed25519", max_length=32, nullable=False)
    public_key_b64u: str = Field(max_length=512, nullable=False)
    roles_snapshot: list[str] = Field(default_factory=list, sa_type=JSON)
    client_metadata: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    pin_protected: bool = Field(default=True, nullable=False)
    is_active: bool = Field(default=True, index=True, nullable=False)
    last_used_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))


class DeviceChallenge(PublicSchemaModel, table=True):
    """A short-lived, single-use server nonce for device challenge login."""

    __tablename__ = "device_challenges"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(default_factory=uuid7, primary_key=True, nullable=False)
    credential_id: _uuid.UUID = Field(
        foreign_key="public.device_credentials.id",
        index=True,
        nullable=False,
    )
    tenant_key: str = Field(index=True, max_length=63, nullable=False)
    subject: str = Field(index=True, max_length=255, nullable=False)
    device_id: str = Field(index=True, max_length=255, nullable=False)
    nonce: str = Field(max_length=255, nullable=False)
    signing_input: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    status: str = Field(default="pending", max_length=32, index=True, nullable=False)
    attempt_count: int = Field(default=0, nullable=False)
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    completed_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    failure_reason: str | None = Field(default=None, max_length=255)
