"""Device-auth routes for mobile challenge login."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthContext, require_role
from app.db.session import get_public_session
from app.schemas.device_auth import (
    DeviceChallengeCompleteRequest,
    DeviceChallengeStartRequest,
    DeviceChallengeStartResponse,
    DeviceCredentialRead,
    DeviceLoginResponse,
    DeviceRegistrationCreate,
)
from app.services.device_auth import service as device_auth_svc


router = APIRouter(prefix="/api/auth/device", tags=["auth"])


@router.post("/registrations", response_model=DeviceCredentialRead, status_code=201)
async def register_device(
    body: DeviceRegistrationCreate,
    ctx: AuthContext = Depends(require_role()),
    session: AsyncSession = Depends(get_public_session),
):
    return await device_auth_svc.create_or_replace_registration(body, ctx=ctx, session=session)


@router.get("/registrations", response_model=list[DeviceCredentialRead])
async def list_device_registrations(
    ctx: AuthContext = Depends(require_role()),
    session: AsyncSession = Depends(get_public_session),
):
    return await device_auth_svc.list_registrations(ctx=ctx, session=session)


@router.delete("/registrations/{credential_id}", status_code=204)
async def delete_device_registration(
    credential_id: UUID,
    ctx: AuthContext = Depends(require_role()),
    session: AsyncSession = Depends(get_public_session),
):
    await device_auth_svc.deactivate_registration(credential_id, ctx=ctx, session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/challenges/start", response_model=DeviceChallengeStartResponse)
async def start_device_challenge(
    body: DeviceChallengeStartRequest,
    session: AsyncSession = Depends(get_public_session),
):
    return await device_auth_svc.start_challenge(body, session=session)


@router.post("/challenges/complete", response_model=DeviceLoginResponse)
async def complete_device_challenge(
    body: DeviceChallengeCompleteRequest,
    session: AsyncSession = Depends(get_public_session),
):
    return await device_auth_svc.complete_challenge(body, session=session)
