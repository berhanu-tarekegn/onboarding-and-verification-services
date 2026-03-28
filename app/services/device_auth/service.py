"""Service layer for mobile device challenge authentication."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthContext
from app.core.config import get_settings
from app.models.public.device_auth import DeviceChallenge, DeviceCredential
from app.models.public.tenant import Tenant
from app.schemas.device_auth import (
    DeviceChallengeCompleteRequest,
    DeviceChallengeStartRequest,
    DeviceChallengeStartResponse,
    DeviceCredentialRead,
    DeviceLoginResponse,
    DeviceRegistrationCreate,
)
from app.services.device_auth.crypto import (
    fingerprint_key,
    issue_access_token,
    make_expires_at,
    make_nonce,
    make_signing_input,
    validate_public_key,
    verify_signature,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_enabled() -> None:
    settings = get_settings()
    if not settings.MOBILE_AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "device_auth_disabled", "message": "Device authentication is disabled."},
        )


async def _resolve_tenant(session: AsyncSession, tenant_identifier: str) -> Tenant:
    result = await session.exec(
        select(Tenant).where((Tenant.tenant_key == tenant_identifier) | (Tenant.keycloak_realm == tenant_identifier))
    )
    tenant = result.first()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tenant_not_found", "message": "Tenant not found."},
        )
    return tenant


def _read_credential(row: DeviceCredential) -> DeviceCredentialRead:
    return DeviceCredentialRead(
        id=row.id,
        tenant_id=row.tenant_id,
        tenant_key=row.tenant_key,
        subject=row.subject,
        login_hint=row.login_hint,
        device_id=row.device_id,
        device_name=row.device_name,
        algorithm=row.algorithm,
        roles_snapshot=list(row.roles_snapshot or []),
        client_metadata=dict(row.client_metadata or {}),
        pin_protected=row.pin_protected,
        is_active=row.is_active,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create_or_replace_registration(
    body: DeviceRegistrationCreate,
    *,
    ctx: AuthContext,
    session: AsyncSession,
) -> DeviceCredentialRead:
    _require_enabled()
    validate_public_key(body.algorithm, body.public_key_b64u)

    tenant = await _resolve_tenant(session, ctx.tenant_id)
    result = await session.exec(
        select(DeviceCredential).where(
            DeviceCredential.tenant_key == tenant.tenant_key,
            DeviceCredential.subject == ctx.user_id,
            DeviceCredential.device_id == body.device_id,
        )
    )
    row = result.first()
    if row is None:
        row = DeviceCredential(
            tenant_id=tenant.id,
            tenant_key=tenant.tenant_key,
            subject=ctx.user_id,
            login_hint=body.login_hint,
            device_id=body.device_id,
            device_name=body.device_name,
            algorithm=body.algorithm,
            public_key_b64u=body.public_key_b64u,
            roles_snapshot=sorted(set(ctx.roles)),
            client_metadata={**body.client_metadata, "fingerprint": fingerprint_key(body.public_key_b64u)},
            pin_protected=body.pin_protected,
            is_active=True,
        )
    else:
        row.login_hint = body.login_hint or row.login_hint
        row.device_name = body.device_name or row.device_name
        row.algorithm = body.algorithm
        row.public_key_b64u = body.public_key_b64u
        row.roles_snapshot = sorted(set(ctx.roles))
        row.client_metadata = {**body.client_metadata, "fingerprint": fingerprint_key(body.public_key_b64u)}
        row.pin_protected = body.pin_protected
        row.is_active = True

    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _read_credential(row)


async def list_registrations(*, ctx: AuthContext, session: AsyncSession) -> list[DeviceCredentialRead]:
    _require_enabled()
    tenant = await _resolve_tenant(session, ctx.tenant_id)
    result = await session.exec(
        select(DeviceCredential)
        .where(DeviceCredential.tenant_id == tenant.id)
        .where(DeviceCredential.subject == ctx.user_id)
        .order_by(DeviceCredential.created_at.desc())
    )
    return [_read_credential(row) for row in result.all()]


async def deactivate_registration(
    credential_id: UUID,
    *,
    ctx: AuthContext,
    session: AsyncSession,
) -> None:
    _require_enabled()
    tenant = await _resolve_tenant(session, ctx.tenant_id)
    row = await session.get(DeviceCredential, credential_id)
    if row is None or row.tenant_id != tenant.id or row.subject != ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "device_credential_not_found", "message": "Device credential not found."},
        )
    row.is_active = False
    session.add(row)
    await session.commit()


async def start_challenge(
    body: DeviceChallengeStartRequest,
    *,
    session: AsyncSession,
) -> DeviceChallengeStartResponse:
    _require_enabled()
    tenant = await _resolve_tenant(session, body.tenant_id)
    result = await session.exec(
        select(DeviceCredential).where(
            DeviceCredential.tenant_id == tenant.id,
            DeviceCredential.subject == body.subject,
            DeviceCredential.device_id == body.device_id,
            DeviceCredential.is_active == True,
        )
    )
    credential = result.first()
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "device_registration_not_found", "message": "Registered device not found."},
        )

    issued_at = _now()
    expires_at = make_expires_at(get_settings().MOBILE_AUTH_CHALLENGE_TTL_SECONDS)
    challenge = DeviceChallenge(
        credential_id=credential.id,
        tenant_key=tenant.tenant_key,
        subject=credential.subject,
        device_id=credential.device_id,
        nonce=make_nonce(),
        signing_input="pending",
        status="pending",
        expires_at=expires_at,
    )
    challenge.signing_input = make_signing_input(
        challenge_id=str(challenge.id),
        tenant_key=tenant.tenant_key,
        subject=credential.subject,
        device_id=credential.device_id,
        nonce=challenge.nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    session.add(challenge)
    await session.commit()
    await session.refresh(challenge)
    return DeviceChallengeStartResponse(
        challenge_id=challenge.id,
        credential_id=credential.id,
        algorithm=credential.algorithm,
        nonce=challenge.nonce,
        signing_input=challenge.signing_input,
        expires_at=challenge.expires_at,
    )


async def complete_challenge(
    body: DeviceChallengeCompleteRequest,
    *,
    session: AsyncSession,
) -> DeviceLoginResponse:
    _require_enabled()
    challenge = await session.get(DeviceChallenge, body.challenge_id)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "challenge_not_found", "message": "Challenge not found."},
        )
    credential = await session.get(DeviceCredential, challenge.credential_id)
    if credential is None or not credential.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "device_registration_not_found", "message": "Registered device not found."},
        )
    if challenge.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "challenge_consumed", "message": "Challenge is no longer pending."},
        )
    if challenge.expires_at <= _now():
        challenge.status = "expired"
        challenge.failure_reason = "expired"
        session.add(challenge)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "challenge_expired", "message": "Challenge expired."},
        )

    challenge.attempt_count += 1
    ok = verify_signature(
        algorithm=credential.algorithm,
        public_key_b64u=credential.public_key_b64u,
        signing_input=challenge.signing_input,
        signature_b64u=body.signature_b64u,
    )
    if not ok:
        challenge.failure_reason = "signature_invalid"
        session.add(challenge)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": "Signature verification failed."},
        )

    challenge.status = "completed"
    challenge.completed_at = _now()
    credential.last_used_at = _now()
    session.add(challenge)
    session.add(credential)
    await session.commit()

    token, expires_in = issue_access_token(
        subject=credential.subject,
        tenant_id=credential.tenant_key,
        device_id=credential.device_id,
        roles=list(credential.roles_snapshot or []),
    )
    return DeviceLoginResponse(
        access_token=token,
        expires_in=expires_in,
        subject=credential.subject,
        tenant_id=credential.tenant_key,
        device_id=credential.device_id,
        roles=list(credential.roles_snapshot or []),
    )
