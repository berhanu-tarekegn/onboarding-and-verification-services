"""Tenant CRUD service — operates on the public schema.

Manages tenant registration and their associated PostgreSQL schemas.
Each tenant gets:
- A record in public.tenants (registry)
- A dedicated PostgreSQL schema for their isolated data

The tenant UUID (id) is used as the X-Tenant-ID header value.
The schema_name field directly names the PostgreSQL schema.
"""

from uuid import UUID
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
import httpx

from app.core.config import get_settings
from app.db.migrations import provision_tenant_schema, drop_tenant_schema
from app.integrations.keycloak.admin import (
    ensure_client_with_mappers,
    ensure_realm,
    ensure_roles,
    delete_realm,
    ensure_user,
    set_user_password,
    assign_realm_roles,
)
from app.models.public.tenant import Tenant
from app.models.public.identity_link import IdentityLink
from app.schemas.tenants import TenantCreate, TenantUpdate, TenantUserCreate, TenantUserRead


async def create_tenant(
    data: TenantCreate,
    session: AsyncSession,
    engine: AsyncEngine,
    database_url: str | None = None,
) -> Tenant:
    """Create a new tenant and provision its PostgreSQL schema.

    1. Creates a tenant record in public.tenants
    2. Creates a dedicated PostgreSQL schema (e.g., tenant_acme_bank)
    3. Runs migrations to create tenant-specific tables in that schema
    """
    tenant = Tenant.model_validate(data)
    if tenant.keycloak_realm is None and get_settings().KEYCLOAK_PROVISIONING_ENABLED:
        tenant.keycloak_realm = tenant.schema_name
    session.add(tenant)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Identify the actual conflict (schema_name vs keycloak_realm, etc.) so
        # we don't mislead callers on generic integrity errors.
        existing = await get_tenant_by_schema_name(data.schema_name, session)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "tenant_already_exists",
                    "message": f"Tenant with schema_name '{data.schema_name}' already exists.",
                    "details": {"tenant_id": str(existing.id)},
                },
            )
        if data.keycloak_realm:
            result = await session.execute(select(Tenant).where(Tenant.keycloak_realm == data.keycloak_realm))
            existing_realm = result.scalars().first()
            if existing_realm is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "realm_already_linked",
                        "message": f"Keycloak realm '{data.keycloak_realm}' is already linked to a tenant.",
                        "details": {"tenant_id": str(existing_realm.id)},
                    },
                )

        details = None
        if get_settings().DEBUG:
            details = {"integrity_error": str(getattr(exc, "orig", exc))}
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "tenant_create_failed",
                "message": "Tenant could not be created.",
                "details": details,
            },
        )

    await session.refresh(tenant)

    try:
        await provision_tenant_schema(
            tenant_schema_name=tenant.schema_name,
            engine=engine,
            database_url=database_url,
        )
    except Exception as exc:  # noqa: BLE001
        # Avoid leaving partially created tenants around (confusing retries).
        # Best-effort cleanup: drop schema and delete tenant row.
        try:
            await drop_tenant_schema(tenant.schema_name, engine, cascade=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            await session.delete(tenant)
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()
        details = None
        if get_settings().DEBUG:
            details = {"error": str(exc)}
        error_text = str(exc)
        if "lock timeout" in error_text or "LockNotAvailableError" in error_text:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "database_locked",
                    "message": "Database is busy; please retry.",
                    "details": details,
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "tenant_schema_provision_failed",
                "message": "Tenant database schema provisioning failed.",
                "details": details,
            },
        ) from exc

    settings = get_settings()
    if settings.KEYCLOAK_PROVISIONING_ENABLED:
        try:
            realm = tenant.keycloak_realm or tenant.schema_name
            realm_created = await ensure_realm(realm)
            await ensure_roles(
                realm,
                roles=[
                    "super_admin",
                    "schema_author",
                    "platform_admin",
                    "maker",
                    "checker",
                ],
            )
            creds = await ensure_client_with_mappers(
                realm,
                client_id=settings.KEYCLOAK_TENANT_CLIENT_ID,
                confidential=bool(settings.KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL),
                claims_namespace=f"{realm}_claims",
                tenant_id_value=realm,
            )
            tenant.keycloak_realm = realm
            tenant.keycloak_client_id = creds.client_id
            tenant.keycloak_client_secret = creds.client_secret

            session.add(tenant)
            await session.commit()
            await session.refresh(tenant)
        except Exception as exc:  # noqa: BLE001
            if settings.KEYCLOAK_PROVISIONING_REQUIRED:
                # Best-effort: if we created the realm in this attempt, try to delete it
                # to avoid leaving orphaned realms on retries.
                try:
                    if "realm_created" in locals() and realm_created:
                        await delete_realm(realm)
                except Exception:  # noqa: BLE001
                    pass
                # Required mode: avoid leaving a tenant that cannot authenticate.
                # Best-effort cleanup: drop schema and delete tenant row.
                try:
                    tenant.is_active = False
                    session.add(tenant)
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
                try:
                    await drop_tenant_schema(tenant.schema_name, engine, cascade=True)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await session.delete(tenant)
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
                details = None
                if settings.DEBUG:
                    details = {"error": str(exc)}
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": "keycloak_provision_failed",
                        "message": "Keycloak provisioning failed.",
                        "details": details,
                    },
                ) from exc
            # Best-effort: keep tenant + DB schema, but without Keycloak linkage.
            # Caller can retry provisioning separately.
            import logging

            logging.getLogger(__name__).warning("Keycloak provisioning failed for tenant %s: %s", tenant.schema_name, exc)

    return tenant


async def list_tenants(
    session: AsyncSession,
    active_only: bool = False,
) -> list[Tenant]:
    """Return all tenants."""
    query = select(Tenant)

    if active_only:
        query = query.where(Tenant.is_active == True)

    query = query.order_by(Tenant.name)

    result = await session.execute(query)
    return list(result.scalars().all())


async def get_tenant(tenant_id: UUID, session: AsyncSession) -> Tenant:
    """Return a single tenant by ID or raise 404."""
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found.")
    return tenant


async def get_tenant_by_schema_name(schema_name: str, session: AsyncSession) -> Optional[Tenant]:
    """Return a tenant by schema_name or None if not found."""
    result = await session.execute(
        select(Tenant).where(Tenant.schema_name == schema_name)
    )
    return result.scalars().first()


async def update_tenant(
    tenant_id: UUID,
    data: TenantUpdate,
    session: AsyncSession,
) -> Tenant:
    """Partially update a tenant.

    Note: schema_name cannot be changed after creation — it would require
    renaming the PostgreSQL schema and all its objects.
    """
    tenant = await get_tenant(tenant_id, session)
    updates = data.model_dump(exclude_unset=True)

    for key, value in updates.items():
        setattr(tenant, key, value)

    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)

    return tenant


async def delete_tenant(
    tenant_id: UUID,
    session: AsyncSession,
    hard_delete: bool = False,
    engine: AsyncEngine = None,
) -> Tenant:
    """Delete a tenant.

    hard_delete=True drops the PostgreSQL schema permanently.
    Default is soft-delete (is_active=False).
    """
    tenant = await get_tenant(tenant_id, session)

    if hard_delete:
        if not engine:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Engine required for hard delete.",
            )
        await drop_tenant_schema(tenant.schema_name, engine, cascade=True)
        await session.delete(tenant)
        await session.commit()
        return tenant

    tenant.is_active = False
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)

    return tenant


_ALLOWED_REALM_ROLES: set[str] = {
    "super_admin",
    "schema_author",
    "platform_admin",
    "maker",
    "checker",
}


async def create_tenant_user(
    tenant_id: UUID,
    data: TenantUserCreate,
    session: AsyncSession,
) -> TenantUserRead:
    """Create a Keycloak user inside this tenant's realm.

    Stores Ethiopian national id as a Keycloak user attribute (national_id) and ensures
    the realm's OIDC client maps it into the JWT claim namespace.
    """
    settings = get_settings()
    if not settings.KEYCLOAK_PROVISIONING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "keycloak_disabled", "message": "Keycloak provisioning is disabled."},
        )

    tenant = await get_tenant(tenant_id, session)
    realm = (tenant.keycloak_realm or tenant.schema_name or "").strip()
    if not realm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "realm_not_configured", "message": "Tenant has no Keycloak realm configured."},
        )

    username = (data.username or data.national_id).strip()
    national_id = data.national_id.strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request", "message": "Missing username."},
        )
    if not national_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request", "message": "Missing national_id."},
        )

    roles = [r.strip() for r in (data.roles or []) if isinstance(r, str) and r.strip()]
    unknown = sorted({r for r in roles if r not in _ALLOWED_REALM_ROLES})
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_roles",
                "message": "One or more roles are invalid.",
                "details": {"unknown_roles": unknown, "allowed_roles": sorted(_ALLOWED_REALM_ROLES)},
            },
        )
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_roles",
                "message": "At least one role is required.",
                "details": {"allowed_roles": sorted(_ALLOWED_REALM_ROLES)},
            },
        )
    # Enforce maker/checker exclusivity at creation time too.
    if "maker" in roles and "checker" in roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_roles",
                "message": "maker and checker roles are mutually exclusive.",
                "details": {"conflicts": ["maker", "checker"]},
            },
        )

    # Make sure the realm and required roles exist (idempotent).
    await ensure_realm(realm)
    await ensure_roles(realm, roles=list(_ALLOWED_REALM_ROLES))

    # Ensure the tenant client exists and has all claim mappers (including national_id).
    # Uses the configured tenant client id (e.g. "oaas-client").
    client_id = (tenant.keycloak_client_id or settings.KEYCLOAK_TENANT_CLIENT_ID or "").strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "server_misconfigured", "message": "Tenant auth client is not configured."},
        )
    await ensure_client_with_mappers(
        realm,
        client_id=client_id,
        confidential=bool(settings.KEYCLOAK_TENANT_CLIENT_CONFIDENTIAL),
        claims_namespace=f"{realm}_claims",
        tenant_id_value=realm,
    )

    try:
        attrs: dict[str, str] = {"national_id": national_id}
        if data.birth_date is not None:
            attrs["birth_date"] = data.birth_date.isoformat()
        if data.phone_number is not None and data.phone_number.strip():
            attrs["phone_number"] = data.phone_number.strip()
        if data.address is not None and data.address.strip():
            attrs["address"] = data.address.strip()

        user_id = await ensure_user(
            realm,
            username=username,
            email=(data.email.strip() if data.email else None),
            first_name=(data.first_name.strip() if data.first_name else None),
            last_name=(data.last_name.strip() if data.last_name else None),
            attributes=attrs,
        )
        await set_user_password(realm, user_id=user_id, password=data.password, temporary=False)
        await assign_realm_roles(realm, user_id=user_id, roles=roles)
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        upstream_url = str(exc.request.url)
        upstream_body = exc.response.text or ""
        details = None
        if settings.DEBUG:
            details = {
                "upstream_status": upstream_status,
                "upstream_url": upstream_url,
                "upstream_body_prefix": upstream_body[:400],
            }
        if upstream_status == 403:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "keycloak_forbidden",
                    "message": "Keycloak provisioning account is missing permissions (grant manage-users/view-users in master realm).",
                    "details": details,
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "keycloak_user_provision_failed",
                "message": "Keycloak user provisioning failed.",
                "details": details,
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        details = None
        if settings.DEBUG:
            details = {"error": str(exc)}
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "keycloak_user_provision_failed",
                "message": "Keycloak user provisioning failed.",
                "details": details,
            },
        ) from exc

    # Persist a minimal OAAS↔Keycloak link for audit/debug and reconciliation.
    try:
        await _upsert_identity_link(
            session,
            tenant_uuid=tenant.id,
            realm=realm,
            keycloak_user_id=user_id,
            username=username,
            national_id=national_id,
        )
    except Exception:  # noqa: BLE001
        # Non-fatal: Keycloak is source-of-truth; OAAS link is best-effort.
        pass

    return TenantUserRead(
        realm=realm,
        user_id=user_id,
        username=username,
        national_id=national_id,
        roles=roles,
        birth_date=data.birth_date,
        phone_number=(data.phone_number.strip() if data.phone_number else None),
        address=(data.address.strip() if data.address else None),
    )


async def _upsert_identity_link(
    session: AsyncSession,
    *,
    tenant_uuid,
    realm: str,
    keycloak_user_id: str,
    username: str,
    national_id: str,
) -> None:
    from sqlmodel import select

    r = await session.execute(
        select(IdentityLink).where(
            (IdentityLink.tenant_id == tenant_uuid) & (IdentityLink.keycloak_user_id == keycloak_user_id)
        )
    )
    row = r.scalars().first()
    if row is None:
        row = IdentityLink(
            tenant_id=tenant_uuid,
            realm=realm,
            keycloak_user_id=keycloak_user_id,
            username=username,
            national_id=national_id,
        )
        session.add(row)
        await session.commit()
        return
    row.realm = realm
    row.username = username
    row.national_id = national_id
    session.add(row)
    await session.commit()
