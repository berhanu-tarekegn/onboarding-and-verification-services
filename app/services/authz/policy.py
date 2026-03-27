"""Authorization policy CRUD (public schema)."""

from __future__ import annotations

from uuid import UUID
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import ProgrammingError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.public.authz_policy import AuthzPolicy
from app.core.authz import validate_policy_role_permissions


def _raise_if_missing_table(exc: Exception) -> None:
    if not isinstance(exc, ProgrammingError):
        return
    orig = getattr(exc, "orig", None)
    msg = str(orig or exc)
    if "UndefinedTableError" in msg or "does not exist" in msg:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "migration_required",
                "message": "Database is not migrated. Run: alembic -x tenant_schema=public upgrade head",
            },
        ) from exc


async def get_global_policy(session: AsyncSession) -> AuthzPolicy:
    try:
        r = await session.execute(
            select(AuthzPolicy).where(AuthzPolicy.scope == "global").where(AuthzPolicy.tenant_id == None)  # noqa: E711
        )
    except Exception as exc:  # noqa: BLE001
        _raise_if_missing_table(exc)
        raise
    row = r.scalars().first()
    if row is None:
        row = AuthzPolicy(scope="global", tenant_id=None, version=1, policy={})
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def upsert_global_policy(session: AsyncSession, *, policy: dict[str, Any]) -> AuthzPolicy:
    # Validate maker/checker invariants early (policy-as-data safety).
    roles = policy.get("roles") if isinstance(policy, dict) else None
    if isinstance(roles, dict):
        try:
            for role, perms in roles.items():
                if role in {"maker", "checker"} and isinstance(perms, list):
                    validate_policy_role_permissions(role, {p for p in perms if isinstance(p, str)})
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_policy", "message": str(exc)},
            ) from exc
    row = await get_global_policy(session)
    row.policy = policy
    row.version = int(row.version or 0) + 1
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_tenant_policy(session: AsyncSession, *, tenant_id: UUID) -> AuthzPolicy | None:
    try:
        r = await session.execute(
            select(AuthzPolicy).where(AuthzPolicy.scope == "tenant").where(AuthzPolicy.tenant_id == tenant_id)
        )
    except Exception as exc:  # noqa: BLE001
        _raise_if_missing_table(exc)
        raise
    return r.scalars().first()


def _realm_scope(realm: str) -> str:
    realm = (realm or "").strip()
    return f"realm:{realm}"


async def get_realm_policy(session: AsyncSession, *, realm: str) -> AuthzPolicy | None:
    """Realm policy stored by realm key even if no tenant exists yet."""
    scope = _realm_scope(realm)
    try:
        r = await session.execute(
            select(AuthzPolicy).where(AuthzPolicy.scope == scope).where(AuthzPolicy.tenant_id == None)  # noqa: E711
        )
    except Exception as exc:  # noqa: BLE001
        _raise_if_missing_table(exc)
        raise
    return r.scalars().first()


async def upsert_realm_policy(session: AsyncSession, *, realm: str, policy: dict[str, Any]) -> AuthzPolicy:
    roles = policy.get("roles") if isinstance(policy, dict) else None
    if isinstance(roles, dict):
        try:
            for role, perms in roles.items():
                if role in {"maker", "checker"} and isinstance(perms, list):
                    validate_policy_role_permissions(role, {p for p in perms if isinstance(p, str)})
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_policy", "message": str(exc)},
            ) from exc

    scope = _realm_scope(realm)
    row = await get_realm_policy(session, realm=realm)
    if row is None:
        row = AuthzPolicy(scope=scope, tenant_id=None, version=1, policy=policy)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    row.policy = policy
    row.version = int(row.version or 0) + 1
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def upsert_tenant_policy(session: AsyncSession, *, tenant_id: UUID, policy: dict[str, Any]) -> AuthzPolicy:
    roles = policy.get("roles") if isinstance(policy, dict) else None
    if isinstance(roles, dict):
        try:
            for role, perms in roles.items():
                if role in {"maker", "checker"} and isinstance(perms, list):
                    validate_policy_role_permissions(role, {p for p in perms if isinstance(p, str)})
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_policy", "message": str(exc)},
            ) from exc
    row = await get_tenant_policy(session, tenant_id=tenant_id)
    if row is None:
        row = AuthzPolicy(scope="tenant", tenant_id=tenant_id, version=1, policy=policy)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    row.policy = policy
    row.version = int(row.version or 0) + 1
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
