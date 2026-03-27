"""Tenant management routes — public schema, no X-Tenant-ID required."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthContext, is_master_realm_super_admin
from app.core.authz import require_platform_super_admin, require_tenant_admin
from app.core.config import get_settings
from app.db.session import get_engine, get_public_session
from app.schemas.tenants import TenantCreate, TenantRead, TenantUpdate, TenantUserCreate, TenantUserRead
from app.schemas.authz.policy import AuthzPolicyRead, AuthzPolicyUpdate
from app.services import tenants as tenant_svc
from app.services.authz import policy as authz_svc
from app.models.public.tenant import Tenant

router = APIRouter(
    prefix="/tenants",
    tags=["tenants"],
)

def _require_initialization_key(
    x_initialization_key: str | None = Header(default=None, alias="X-Initialization-Key"),
    x_provisioning_key: str | None = Header(default=None, alias="X-Provisioning-Key"),
) -> None:
    """Optional internal guard for platform tenant initialization endpoints.

    When PLATFORM_INITIALIZATION_API_KEY is set, callers must include a matching
    initialization header. `X-Provisioning-Key` remains accepted for backwards
    compatibility.
    """
    expected = (get_settings().PLATFORM_PROVISIONING_API_KEY or "").strip()
    if not expected:
        return
    provided = (x_initialization_key or x_provisioning_key or "").strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Missing or invalid tenant initialization key."},
        )

def _assert_tenant_admin_scope(ctx: AuthContext, tenant: Tenant) -> None:
    if is_master_realm_super_admin(ctx):
        return
    allowed_tenants = {tenant.schema_name, (tenant.keycloak_realm or "").strip()}
    if (ctx.tenant_id or "").strip() in allowed_tenants:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "tenant_scope_forbidden", "message": "You don't have permission to manage this tenant."},
    )


@router.post("", response_model=TenantRead, status_code=201)
async def create_tenant(
    data: TenantCreate,
    session: AsyncSession = Depends(get_public_session),
    _guard: None = Depends(_require_initialization_key),
    _ctx=Depends(require_platform_super_admin()),
):
    """Register a new tenant and run tenant initialization."""
    tenant = await tenant_svc.create_tenant(
        data,
        session,
        engine=get_engine(),
        database_url=get_settings().DATABASE_URL,
    )
    return TenantRead.from_tenant(tenant)


@router.get("", response_model=list[TenantRead])
async def list_tenants(
    session: AsyncSession = Depends(get_public_session),
    _ctx=Depends(require_platform_super_admin()),
):
    """List all tenants."""
    return [TenantRead.from_tenant(tenant) for tenant in await tenant_svc.list_tenants(session)]


@router.get("/{tenant_id}", response_model=TenantRead)
async def get_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_public_session),
    _ctx=Depends(require_platform_super_admin()),
):
    """Get a tenant by ID."""
    return TenantRead.from_tenant(await tenant_svc.get_tenant(tenant_id, session))


@router.patch("/{tenant_id}", response_model=TenantRead)
async def update_tenant(
    tenant_id: UUID,
    data: TenantUpdate,
    session: AsyncSession = Depends(get_public_session),
    _ctx=Depends(require_platform_super_admin()),
):
    """Partially update a tenant."""
    return TenantRead.from_tenant(await tenant_svc.update_tenant(tenant_id, data, session))


@router.delete("/{tenant_id}", response_model=TenantRead)
async def delete_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_public_session),
    hard_delete: bool = Query(False, description="Drop tenant schema and delete tenant record."),
    _guard: None = Depends(_require_initialization_key),
    _ctx=Depends(require_platform_super_admin()),
):
    """Delete a tenant.

    - hard_delete=false (default): soft-delete (sets is_active=False)
    - hard_delete=true: drop tenant schema and delete the tenant row
    """
    tenant = await tenant_svc.delete_tenant(
        tenant_id,
        session,
        hard_delete=hard_delete,
        engine=get_engine(),
    )
    return TenantRead.from_tenant(tenant)


@router.post("/{tenant_id}/users", response_model=TenantUserRead, status_code=201)
async def create_tenant_user(
    tenant_id: UUID,
    data: TenantUserCreate,
    session: AsyncSession = Depends(get_public_session),
    ctx: AuthContext = Depends(require_tenant_admin()),
):
    """Create a Keycloak user for the tenant's realm and assign roles."""
    tenant = await tenant_svc.get_tenant(tenant_id, session)
    _assert_tenant_admin_scope(ctx, tenant)
    return await tenant_svc.create_tenant_user(tenant_id, data, session)


@router.post("/users", response_model=TenantUserRead, status_code=201)
async def create_my_tenant_user(
    data: TenantUserCreate,
    session: AsyncSession = Depends(get_public_session),
    ctx: AuthContext = Depends(require_tenant_admin()),
):
    """Create a Keycloak user for the caller's tenant (derived from JWT tenant claim)."""
    if is_master_realm_super_admin(ctx):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "tenant_id_required",
                "message": "Platform super admins must use /tenants/{tenant_id}/users.",
            },
        )
    realm_key = (ctx.tenant_id or "").strip()
    if not realm_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_tenant_id", "message": "Tenant context is missing from token."},
        )
    r = await session.execute(
        select(Tenant).where((Tenant.schema_name == realm_key) | (Tenant.keycloak_realm == realm_key))
    )
    tenant = r.scalars().first()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tenant_not_found", "message": "Tenant not found for token tenant_id."},
        )
    return await tenant_svc.create_tenant_user(tenant.id, data, session)


@router.get("/{tenant_id}/authz/policy", response_model=AuthzPolicyRead)
async def get_tenant_authz_policy(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_public_session),
    ctx: AuthContext = Depends(require_tenant_admin()),
):
    tenant = await tenant_svc.get_tenant(tenant_id, session)
    _assert_tenant_admin_scope(ctx, tenant)

    row = await authz_svc.get_tenant_policy(session, tenant_id=tenant_id)
    policy = row.policy if row else {}
    version = row.version if row else 0
    return AuthzPolicyRead(scope="tenant", tenant_id=str(tenant_id), version=version, policy=policy)


@router.put("/{tenant_id}/authz/policy", response_model=AuthzPolicyRead)
async def update_tenant_authz_policy(
    tenant_id: UUID,
    body: AuthzPolicyUpdate,
    session: AsyncSession = Depends(get_public_session),
    ctx: AuthContext = Depends(require_tenant_admin()),
):
    tenant = await tenant_svc.get_tenant(tenant_id, session)
    _assert_tenant_admin_scope(ctx, tenant)
    row = await authz_svc.upsert_tenant_policy(session, tenant_id=tenant_id, policy=body.policy.model_dump())
    return AuthzPolicyRead(scope="tenant", tenant_id=str(tenant_id), version=row.version, policy=row.policy)
