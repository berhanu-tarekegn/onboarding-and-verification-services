"""Schemas for mobile device challenge authentication."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices
from sqlmodel import Field, SQLModel


class DeviceRegistrationCreate(SQLModel):
    device_id: str = Field(min_length=8, max_length=255)
    device_name: str | None = Field(default=None, max_length=255)
    algorithm: str = Field(default="ed25519", max_length=32)
    public_key_b64u: str = Field(min_length=32, max_length=512)
    login_hint: str | None = Field(default=None, max_length=255)
    pin_protected: bool = True
    client_metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata", "client_metadata"),
        serialization_alias="metadata",
    )


class DeviceCredentialRead(SQLModel):
    id: UUID
    tenant_id: UUID
    tenant_key: str
    subject: str
    login_hint: str | None = None
    device_id: str
    device_name: str | None = None
    algorithm: str
    roles_snapshot: list[str] = Field(default_factory=list)
    client_metadata: dict[str, Any] = Field(default_factory=dict, serialization_alias="metadata")
    pin_protected: bool
    is_active: bool
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DeviceChallengeStartRequest(SQLModel):
    tenant_id: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=255)
    device_id: str = Field(min_length=8, max_length=255)


class DeviceChallengeStartResponse(SQLModel):
    challenge_id: UUID
    credential_id: UUID
    algorithm: str
    nonce: str
    signing_input: str
    expires_at: datetime


class DeviceChallengeCompleteRequest(SQLModel):
    challenge_id: UUID
    signature_b64u: str = Field(min_length=32, max_length=1024)


class DeviceLoginResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    subject: str
    tenant_id: str
    device_id: str
    auth_provider: str = "device_challenge"
    roles: list[str] = Field(default_factory=list)
