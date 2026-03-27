"""Async database engine, session dependencies, and schema-based tenant isolation.

This app uses **schema-based** multi-tenancy:
- Each tenant has its own PostgreSQL schema (``tenant_<schema_name>``).
- For tenant-scoped requests, we switch ``search_path`` per DB session to:
  1) the tenant schema, then 2) ``public``.

Tenant context
--------------
- When `AUTH_ENABLED=true`, tenant context is derived from the JWT tenant claim
  (set by `AuthMiddleware` into `jwt_tenant_context`). The `X-Tenant-ID` header
  is optional and may be used as an extra guard at the API layer.
- When `AUTH_ENABLED=false` (dev/test), tenant context typically comes from
  `X-Tenant-ID` and is propagated by `TenantMiddleware`.
"""

from __future__ import annotations

import contextlib
import uuid as _uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.context import get_current_tenant, jwt_tenant_context, tenant_id_context
from app.models.public.tenant import Tenant


_ENGINE_CACHE: dict[str, AsyncEngine] = {}


def _build_engine(database_url: str) -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        database_url,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def get_engine(database_url: str | None = None) -> AsyncEngine:
    resolved_url = (database_url or get_settings().DATABASE_URL).strip()
    engine = _ENGINE_CACHE.get(resolved_url)
    if engine is None:
        engine = _build_engine(resolved_url)
        _ENGINE_CACHE[resolved_url] = engine
    return engine


def get_async_session_factory(
    database_url: str | None = None,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(database_url),
        class_=AsyncSession,
        expire_on_commit=False,
    )


def async_session_factory():
    """Backwards-compatible session factory callable.

    Some route modules import ``async_session_factory`` directly and expect it
    to be callable as ``async_session_factory()``. Keep that shape while still
    resolving the engine at runtime.
    """
    return get_async_session_factory()()


async def dispose_engines() -> None:
    while _ENGINE_CACHE:
        _, engine = _ENGINE_CACHE.popitem()
        await engine.dispose()


def _sanitize_schema_name(schema_name: str) -> str:
    """Return a safe PostgreSQL schema identifier for a tenant schema_name."""
    base = (schema_name or "").strip().lower()
    sanitized = "".join(c if c.isalnum() else "_" for c in base)
    if not sanitized:
        sanitized = "tenant"
    # Always prefix with tenant_ to avoid clashing with reserved schemas.
    return f"tenant_{sanitized}"[:63]


async def _set_search_path(session: AsyncSession, schema_name: str) -> None:
    await session.execute(text(f"SET search_path TO {schema_name}, public"))


async def _reset_search_path(session: AsyncSession) -> None:
    await session.execute(text("SET search_path TO public"))


def _tenant_clause(tenant_identifier: str) -> Any:
    """Build a query clause from a tenant identifier (UUID or schema/realm)."""
    try:
        tenant_uuid = _uuid.UUID(tenant_identifier)
        return Tenant.id == tenant_uuid
    except Exception:
        ident = (tenant_identifier or "").strip()
        return (Tenant.schema_name == ident) | (Tenant.keycloak_realm == ident)


async def _validate_tenant(session: AsyncSession, tenant_identifier: str) -> Tenant:
    """Validate tenant exists and is active; returns the Tenant row."""
    result = await session.execute(select(Tenant).where(_tenant_clause(tenant_identifier)))
    tenant = result.scalars().first()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant '{tenant_identifier}' not found.",
        )
    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant '{tenant_identifier}' is inactive.",
        )
    return tenant


async def get_tenant_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a tenant-scoped session (tenant schema + public)."""
    settings = get_settings()
    if settings.AUTH_ENABLED and not jwt_tenant_context.get():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_token", "message": "Missing bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    tenant_identifier = get_current_tenant()

    async with get_async_session_factory()() as session:
        tenant = await _validate_tenant(session, tenant_identifier)
        token = tenant_id_context.set(tenant.id)
        pg_schema = _sanitize_schema_name(tenant.schema_name)
        await _set_search_path(session, pg_schema)

        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            # Best-effort reset: after a DB error the transaction may be aborted.
            with contextlib.suppress(Exception):
                await _reset_search_path(session)
            tenant_id_context.reset(token)


async def get_public_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session scoped to the ``public`` schema only."""
    async with get_async_session_factory()() as session:
        await session.execute(text("SET search_path TO public"))
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def tenant_session_for_roles(*roles: str):
    """Tenant session dependency that requires one of the given roles."""
    from app.core.auth import require_role

    async def _dep(_ctx=Depends(require_role(*roles))):  # noqa: ANN001,ARG001
        async for s in get_tenant_session():
            yield s

    return _dep


def public_session_for_roles(*roles: str):
    """Public session dependency that requires one of the given roles."""
    from app.core.auth import require_role

    async def _dep(_ctx=Depends(require_role(*roles))):  # noqa: ANN001,ARG001
        async for s in get_public_session():
            yield s

    return _dep


def tenant_session_for_permissions(*perms: str):
    """Tenant session dependency that requires all given permissions."""
    from app.core.authz import require_permission

    async def _dep(_ctx=Depends(require_permission(*perms))):  # noqa: ANN001,ARG001
        async for s in get_tenant_session():
            yield s

    return _dep


def public_session_for_permissions(*perms: str):
    """Public session dependency that requires all given permissions."""
    from app.core.authz import require_permission

    async def _dep(_ctx=Depends(require_permission(*perms))):  # noqa: ANN001,ARG001
        async for s in get_public_session():
            yield s

    return _dep


def tenant_session_for_any_permissions(*perms: str):
    """Tenant session dependency that requires any of the given permissions."""
    from app.core.authz import require_any_permission

    async def _dep(_ctx=Depends(require_any_permission(*perms))):  # noqa: ANN001,ARG001
        async for s in get_tenant_session():
            yield s

    return _dep


def public_session_for_any_permissions(*perms: str):
    """Public session dependency that requires any of the given permissions."""
    from app.core.authz import require_any_permission

    async def _dep(_ctx=Depends(require_any_permission(*perms))):  # noqa: ANN001,ARG001
        async for s in get_public_session():
            yield s

    return _dep


async def get_tenant_readonly_session() -> AsyncGenerator[AsyncSession, None]:
    """Semantic alias for get_tenant_session()."""
    async for session in get_tenant_session():
        yield session


# Backwards compatibility alias
get_db_session = get_tenant_session


async def get_schema_name_for_tenant(tenant_schema_name: str) -> str:
    """Return the full PostgreSQL schema identifier for a tenant."""
    return _sanitize_schema_name(tenant_schema_name)
