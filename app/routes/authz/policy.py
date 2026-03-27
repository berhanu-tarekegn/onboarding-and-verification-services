"""Authorization policy management routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthContext
from app.core.authz import require_platform_super_admin
from app.core.config import get_settings
from app.db.session import get_public_session
from app.schemas.authz.policy import AuthzPolicyRead, AuthzPolicyUpdate
from app.services.authz import policy as policy_svc
from app.models.public.tenant import Tenant


router = APIRouter(prefix="/authz", tags=["authz"])


def _require_initialization_key(
    x_initialization_key: str | None = Header(default=None, alias="X-Initialization-Key"),
) -> None:
    expected = (get_settings().PLATFORM_PROVISIONING_API_KEY or "").strip()
    if not expected:
        return
    provided = (x_initialization_key or "").strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Missing or invalid tenant initialization key."},
        )


@router.get("/policy", response_model=AuthzPolicyRead)
async def get_global_policy(
    _ctx: AuthContext = Depends(require_platform_super_admin()),
    _guard: None = Depends(_require_initialization_key),
    session: AsyncSession = Depends(get_public_session),
):
    row = await policy_svc.get_global_policy(session)
    return AuthzPolicyRead(scope=row.scope, tenant_id=None, realm=None, version=row.version, policy=row.policy)


@router.put("/policy", response_model=AuthzPolicyRead)
async def update_global_policy(
    body: AuthzPolicyUpdate,
    _ctx: AuthContext = Depends(require_platform_super_admin()),
    _guard: None = Depends(_require_initialization_key),
    session: AsyncSession = Depends(get_public_session),
):
    row = await policy_svc.upsert_global_policy(session, policy=body.policy.model_dump())
    return AuthzPolicyRead(scope=row.scope, tenant_id=None, realm=None, version=row.version, policy=row.policy)


async def _resolve_tenant_uuid_by_realm(session: AsyncSession, *, realm: str) -> UUID:
    r = await session.execute(
        select(Tenant).where((Tenant.tenant_key == realm) | (Tenant.keycloak_realm == realm))
    )
    tenant = r.scalars().first()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tenant_not_found", "message": f"No tenant is linked to realm '{realm}'."},
        )
    return tenant.id


@router.get("/policy/{realm}", response_model=AuthzPolicyRead)
async def get_realm_policy(
    realm: str,
    _ctx: AuthContext = Depends(require_platform_super_admin()),
    _guard: None = Depends(_require_initialization_key),
    session: AsyncSession = Depends(get_public_session),
):
    """Get the tenant policy resolved by tenant key / realm.

    This endpoint is tenant-linked and returns 404 if no tenant exists.
    """
    tenant_id = await _resolve_tenant_uuid_by_realm(session, realm=realm)
    row = await policy_svc.get_tenant_policy(session, tenant_id=tenant_id)
    policy = row.policy if row else {}
    version = row.version if row else 0
    return AuthzPolicyRead(scope="tenant", tenant_id=str(tenant_id), realm=realm, version=version, policy=policy)


@router.put("/policy/{realm}", response_model=AuthzPolicyRead)
async def upsert_realm_policy(
    realm: str,
    body: AuthzPolicyUpdate,
    _ctx: AuthContext = Depends(require_platform_super_admin()),
    _guard: None = Depends(_require_initialization_key),
    session: AsyncSession = Depends(get_public_session),
):
    """Upsert tenant policy resolved by tenant key / realm.

    This endpoint is tenant-linked and returns 404 if no tenant exists.
    """
    tenant_id = await _resolve_tenant_uuid_by_realm(session, realm=realm)
    row = await policy_svc.upsert_tenant_policy(session, tenant_id=tenant_id, policy=body.policy.model_dump())
    return AuthzPolicyRead(scope=row.scope, tenant_id=str(tenant_id), realm=realm, version=row.version, policy=row.policy)


@router.get("/realm-policy/{realm}", response_model=AuthzPolicyRead)
async def get_realm_policy_unlinked(
    realm: str,
    _ctx: AuthContext = Depends(require_platform_super_admin()),
    _guard: None = Depends(_require_initialization_key),
    session: AsyncSession = Depends(get_public_session),
):
    """Get realm policy stored by realm key even if no tenant exists yet."""
    row = await policy_svc.get_realm_policy(session, realm=realm)
    policy = row.policy if row else {}
    version = row.version if row else 0
    return AuthzPolicyRead(scope="realm", tenant_id=None, realm=realm, version=version, policy=policy)


@router.put("/realm-policy/{realm}", response_model=AuthzPolicyRead)
async def upsert_realm_policy_unlinked(
    realm: str,
    body: AuthzPolicyUpdate,
    _ctx: AuthContext = Depends(require_platform_super_admin()),
    _guard: None = Depends(_require_initialization_key),
    session: AsyncSession = Depends(get_public_session),
):
    """Upsert realm policy stored by realm key even if no tenant exists yet."""
    row = await policy_svc.upsert_realm_policy(session, realm=realm, policy=body.policy.model_dump())
    return AuthzPolicyRead(scope="realm", tenant_id=None, realm=realm, version=row.version, policy=row.policy)
