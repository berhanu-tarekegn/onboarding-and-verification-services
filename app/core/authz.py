"""Authorization (role → permissions) utilities.

Keycloak roles identify *who* the user is.
OAAS permissions define *what* the user can do.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from uuid import UUID

from app.core.auth import AuthContext, require_role
from app.core.config import get_settings
from app.models.public.authz_policy import AuthzPolicy


# ── Permission registry (CRUD + actions) ─────────────────────────────

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    # Platform roles
    "maker": {
        "submissions.create",
        "submissions.read",
        "submissions.update",
        "submissions.delete",
        "submissions.submit",
        "submissions.comment",
        "templates.read",
        "products.read",
        "baseline_templates.read",
    },
    "checker": {
        "submissions.read_all",
        "submissions.transition",
        "submissions.comment",
        "templates.read",
        "products.read",
        "baseline_templates.read",
    },
    "platform_admin": {
        "templates.read",
        "templates.create",
        "templates.update",
        "templates.publish",
        "products.read",
        "products.create",
        "products.update",
        "products.delete",
        "products.activate",
        "products.deactivate",
        "submissions.read_all",
        "submissions.comment",
        "baseline_templates.read",
    },
    "schema_author": {
        "baseline_templates.create",
        "baseline_templates.update",
        "baseline_templates.delete",
        "baseline_templates.definitions.create",
        "baseline_templates.definitions.update",
        "baseline_templates.definitions.delete",
        "baseline_templates.read",
    },
    # Admin roles
    "super_admin": {"*"},
}

DEFAULT_ROLE_COLUMN_RULES: dict[str, dict[str, dict[str, set[str]]]] = {
    # Makers should not see reviewer-only fields when using own-read endpoints.
    "maker": {
        "submissions.read": {
            "deny": {"review_notes", "reviewed_by", "reviewed_at"},
        },
    },
}


def _normalize_permission(p: str) -> str:
    """Normalize permission names for backward compatibility."""
    # Old -> new naming
    if p == "submissions.read_own":
        return "submissions.read"
    return p


def _permission_closure(start: set[str]) -> set[str]:
    """Compute effective permissions via implication edges (DFS).

    This is used to prevent accidental privilege escalation where a granted
    action implicitly enables another action.
    """
    # Minimal implication graph (extend as needed).
    implies: dict[str, set[str]] = {
        # Submissions
        "submissions.read_all": {"submissions.read"},
        "submissions.transition": {"submissions.read_all"},
        "submissions.update": {"submissions.read"},
        "submissions.delete": {"submissions.read"},
        "submissions.submit": {"submissions.read"},
        "submissions.comment": {"submissions.read"},
        # Products
        "products.create": {"products.read"},
        "products.update": {"products.read"},
        "products.delete": {"products.read"},
        "products.activate": {"products.read"},
        "products.deactivate": {"products.read"},
        # Templates
        "templates.create": {"templates.read"},
        "templates.update": {"templates.read"},
        "templates.publish": {"templates.read"},
        # Baseline templates
        "baseline_templates.create": {"baseline_templates.read"},
        "baseline_templates.update": {"baseline_templates.read"},
        "baseline_templates.delete": {"baseline_templates.read"},
        "baseline_templates.definitions.create": {"baseline_templates.read"},
        "baseline_templates.definitions.update": {"baseline_templates.read"},
        "baseline_templates.definitions.delete": {"baseline_templates.read"},
    }

    visited: set[str] = set()
    stack: list[str] = list(start)
    while stack:
        p = stack.pop()
        if p in visited:
            continue
        visited.add(p)
        for nxt in implies.get(p, set()):
            if nxt not in visited:
                stack.append(nxt)
    return visited


def validate_policy_role_permissions(role: str, permissions: set[str]) -> None:
    """Validate role permission sets (DFS closure) for safety invariants.

    - maker must never be able to read all submissions or transition them.
    - checker must never be able to create/update/delete/submit submissions.
    """
    role = (role or "").strip()
    if not role:
        return
    perms = {_normalize_permission(p) for p in permissions if isinstance(p, str)}
    effective = _permission_closure(perms)

    if role == "maker":
        forbidden = {"submissions.read_all", "submissions.transition"}
        if effective & forbidden:
            raise ValueError(
                "maker role cannot be granted permissions that reach submissions.read_all or submissions.transition"
            )
    if role == "checker":
        forbidden = {"submissions.create", "submissions.update", "submissions.delete", "submissions.submit"}
        if effective & forbidden:
            raise ValueError(
                "checker role cannot be granted permissions that reach submissions.create/update/delete/submit"
            )


def known_permissions() -> set[str]:
    """Return the set of permissions known to the service.

    Used for debugging (e.g. `/api/auth/me`) to expand wildcard permissions.
    """
    base: set[str] = set()
    for perms in DEFAULT_ROLE_PERMISSIONS.values():
        for p in perms:
            if p != "*":
                base.add(_normalize_permission(p))

    # Keep in sync with the implication graph inside `_permission_closure`.
    implies_keys = {
        # Submissions
        "submissions.read_all",
        "submissions.transition",
        "submissions.update",
        "submissions.delete",
        "submissions.submit",
        "submissions.comment",
        # Products
        "products.create",
        "products.update",
        "products.delete",
        "products.activate",
        "products.deactivate",
        # Templates
        "templates.create",
        "templates.update",
        "templates.publish",
        # Baseline templates
        "baseline_templates.create",
        "baseline_templates.update",
        "baseline_templates.delete",
        "baseline_templates.definitions.create",
        "baseline_templates.definitions.update",
        "baseline_templates.definitions.delete",
        "baseline_templates.read",
    }
    base |= {_normalize_permission(p) for p in implies_keys}
    # Ensure closure includes implied read permissions.
    return _permission_closure(base)


@dataclass(frozen=True)
class _PolicyCacheEntry:
    expires_at: float
    policy: dict[str, Any]
    version: int


_GLOBAL_CACHE: _PolicyCacheEntry | None = None
_TENANT_CACHE: dict[str, _PolicyCacheEntry] = {}


def _now() -> float:
    return time.time()


async def _get_public_session() -> Any:
    """Local dependency wrapper to avoid circular imports (db.session -> core.authz)."""
    from app.db.session import get_public_session

    async for s in get_public_session():
        yield s


async def _load_policy(
    session: AsyncSession,
    *,
    scope: str,
    tenant_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    from sqlmodel import select

    q = select(AuthzPolicy).where(AuthzPolicy.scope == scope)
    if tenant_id is None:
        q = q.where(AuthzPolicy.tenant_id == None)  # noqa: E711
    else:
        try:
            q = q.where(AuthzPolicy.tenant_id == UUID(tenant_id))
        except Exception:
            q = q.where(AuthzPolicy.tenant_id == None)  # noqa: E711
    r = await session.execute(q)
    row = r.scalars().first()
    if row is None:
        return {}, 0
    return (row.policy or {}), int(row.version or 0)


async def get_effective_policy(
    session: AsyncSession,
    *,
    tenant_uuid: str | None = None,
    realm_key: str | None = None,
) -> dict[str, Any]:
    """Return effective policy (global + optional realm + optional tenant overlay)."""
    settings = get_settings()
    ttl = float(getattr(settings, "AUTHZ_CACHE_TTL_SECONDS", 30) or 30)
    now = _now()

    global _GLOBAL_CACHE
    if _GLOBAL_CACHE is None or _GLOBAL_CACHE.expires_at <= now:
        policy, version = await _load_policy(session, scope="global")
        _GLOBAL_CACHE = _PolicyCacheEntry(expires_at=now + ttl, policy=policy, version=version)

    global_policy = _GLOBAL_CACHE.policy
    out: dict[str, Any] = {"global": global_policy}

    # Realm overlay (keyed by ctx.tenant_id, which is typically the realm/schema name).
    if realm_key:
        cache_key = f"realm:{realm_key}"
        entry = _TENANT_CACHE.get(cache_key)
        if entry is None or entry.expires_at <= now:
            policy, version = await _load_policy(session, scope=f"realm:{realm_key}", tenant_id=None)
            _TENANT_CACHE[cache_key] = _PolicyCacheEntry(expires_at=now + ttl, policy=policy, version=version)
            entry = _TENANT_CACHE[cache_key]
        out["realm"] = entry.policy

    if not tenant_uuid:
        return out

    entry = _TENANT_CACHE.get(tenant_uuid)
    if entry is None or entry.expires_at <= now:
        tenant_policy, tenant_version = await _load_policy(session, scope="tenant", tenant_id=tenant_uuid)
        _TENANT_CACHE[tenant_uuid] = _PolicyCacheEntry(
            expires_at=now + ttl,
            policy=tenant_policy,
            version=tenant_version,
        )
        entry = _TENANT_CACHE[tenant_uuid]

    out["tenant"] = entry.policy
    return out


def resolve_permissions_and_columns(
    roles: set[str],
    *,
    global_doc: dict[str, Any],
    realm_doc: dict[str, Any] | None = None,
    tenant_doc: dict[str, Any] | None = None,
) -> tuple[set[str], dict[str, dict[str, set[str]]]]:
    """Resolve effective permissions + column rules for roles.

    Intended for debugging endpoints (e.g. `/api/auth/me`) and for sharing the
    same merge logic used by request-time permission checks.
    """
    perms = _merge_role_permissions(roles, global_doc=global_doc, realm_doc=realm_doc, tenant_doc=tenant_doc)
    columns = _merge_role_columns(roles, global_doc=global_doc, realm_doc=realm_doc, tenant_doc=tenant_doc)
    return perms, columns


def _merge_role_permissions(
    roles: set[str],
    *,
    global_doc: dict[str, Any],
    realm_doc: dict[str, Any] | None,
    tenant_doc: dict[str, Any] | None,
) -> set[str]:
    perms: set[str] = set()

    global_mode = (global_doc or {}).get("mode") if isinstance(global_doc, dict) else None
    global_roles = (global_doc or {}).get("roles") if isinstance(global_doc, dict) else None

    # Base: static defaults (code), unless global policy is in replace mode.
    if global_mode != "replace":
        for r in roles:
            perms |= {_normalize_permission(x) for x in DEFAULT_ROLE_PERMISSIONS.get(r, set())}

    # Global policy doc
    if isinstance(global_roles, dict):
        if global_mode == "replace":
            perms2: set[str] = set()
            for r in roles:
                # super_admin bypass should always work even if policy is replace mode
                if "*" in DEFAULT_ROLE_PERMISSIONS.get(r, set()):
                    perms2.add("*")
                p = global_roles.get(r)
                if isinstance(p, list):
                    perms2 |= {_normalize_permission(x) for x in p if isinstance(x, str)}
            perms = perms2
        else:
            for r in roles:
                p = global_roles.get(r)
                if isinstance(p, list):
                    perms |= {_normalize_permission(x) for x in p if isinstance(x, str)}

    if not tenant_doc or not isinstance(tenant_doc, dict):
        # Still apply realm overlay (if present) before returning.
        if realm_doc and isinstance(realm_doc, dict):
            mode = realm_doc.get("mode")
            realm_roles = realm_doc.get("roles")
            if mode == "replace" and isinstance(realm_roles, dict):
                perms2: set[str] = set()
                for r in roles:
                    if "*" in DEFAULT_ROLE_PERMISSIONS.get(r, set()):
                        perms2.add("*")
                    p = realm_roles.get(r)
                    if isinstance(p, list):
                        perms2 |= {_normalize_permission(x) for x in p if isinstance(x, str)}
                return perms2
            if isinstance(realm_roles, dict):
                for r in roles:
                    p = realm_roles.get(r)
                    if isinstance(p, list):
                        perms |= {_normalize_permission(x) for x in p if isinstance(x, str)}
        return perms

    # Realm overlay (applies before tenant overlay).
    if realm_doc and isinstance(realm_doc, dict):
        mode = realm_doc.get("mode")
        realm_roles = realm_doc.get("roles")
        if mode == "replace" and isinstance(realm_roles, dict):
            perms2: set[str] = set()
            for r in roles:
                if "*" in DEFAULT_ROLE_PERMISSIONS.get(r, set()):
                    perms2.add("*")
                p = realm_roles.get(r)
                if isinstance(p, list):
                    perms2 |= {_normalize_permission(x) for x in p if isinstance(x, str)}
            perms = perms2
        elif isinstance(realm_roles, dict):
            for r in roles:
                p = realm_roles.get(r)
                if isinstance(p, list):
                    perms |= {_normalize_permission(x) for x in p if isinstance(x, str)}

    mode = tenant_doc.get("mode")
    tenant_roles = tenant_doc.get("roles")
    if mode == "replace" and isinstance(tenant_roles, dict):
        # Replace role perms entirely (still respects super_admin "*")
        perms2: set[str] = set()
        for r in roles:
            if "*" in DEFAULT_ROLE_PERMISSIONS.get(r, set()):
                perms2.add("*")
            p = tenant_roles.get(r)
            if isinstance(p, list):
                perms2 |= {_normalize_permission(x) for x in p if isinstance(x, str)}
        return perms2

    if isinstance(tenant_roles, dict):
        for r in roles:
            p = tenant_roles.get(r)
            if isinstance(p, list):
                perms |= {_normalize_permission(x) for x in p if isinstance(x, str)}
    return perms


def _merge_role_columns(
    roles: set[str],
    *,
    global_doc: dict[str, Any],
    realm_doc: dict[str, Any] | None,
    tenant_doc: dict[str, Any] | None,
) -> dict[str, dict[str, set[str]]]:
    """Return merged field-level rules keyed by permission.

    Output shape:
      {
        "submissions.read": {"allow": {...}, "deny": {...}},
        "products.update": {"allow": {...}, "deny": {...}},
      }
    """
    merged: dict[str, dict[str, set[str]]] = {}

    def _apply_role_rules(role: str, rules_doc: Any) -> None:
        if not isinstance(rules_doc, dict):
            return
        for perm, spec in rules_doc.items():
            if not isinstance(perm, str) or not perm:
                continue
            perm = _normalize_permission(perm)
            if not isinstance(spec, dict):
                continue
            allow = spec.get("allow")
            deny = spec.get("deny")
            entry = merged.setdefault(perm, {"allow": set(), "deny": set()})
            if isinstance(allow, list):
                entry["allow"] |= {x for x in allow if isinstance(x, str) and x}
            if isinstance(deny, list):
                entry["deny"] |= {x for x in deny if isinstance(x, str) and x}

    # Base defaults from code.
    for r in roles:
        for perm, spec in DEFAULT_ROLE_COLUMN_RULES.get(r, {}).items():
            entry = merged.setdefault(perm, {"allow": set(), "deny": set()})
            entry["allow"] |= set(spec.get("allow", set()))
            entry["deny"] |= set(spec.get("deny", set()))

    # Global doc: optional `columns` overlay.
    global_mode = (global_doc or {}).get("mode") if isinstance(global_doc, dict) else None
    global_columns = (global_doc or {}).get("columns") if isinstance(global_doc, dict) else None
    if global_mode == "replace" and isinstance(global_columns, dict):
        merged = {}
    if isinstance(global_columns, dict):
        for r in roles:
            _apply_role_rules(r, global_columns.get(r))

    # Realm overlay: optional `columns` overlay.
    if realm_doc and isinstance(realm_doc, dict):
        mode = realm_doc.get("mode")
        cols = realm_doc.get("columns")
        if mode == "replace" and isinstance(cols, dict):
            merged = {}
        if isinstance(cols, dict):
            for r in roles:
                _apply_role_rules(r, cols.get(r))

    # Tenant overlay: optional `columns` overlay.
    if tenant_doc and isinstance(tenant_doc, dict):
        mode = tenant_doc.get("mode")
        cols = tenant_doc.get("columns")
        if mode == "replace" and isinstance(cols, dict):
            merged = {}
        if isinstance(cols, dict):
            for r in roles:
                _apply_role_rules(r, cols.get(r))

    # If allow is empty, treat as "no restriction" (deny-only).
    return merged


def _is_master_admin(ctx: AuthContext) -> bool:
    settings = get_settings()
    issuer = ctx.raw_claims.get("iss")
    if not isinstance(issuer, str):
        return False
    if "/realms/" not in issuer:
        return False
    realm = issuer.split("/realms/", 1)[1].split("/", 1)[0]
    master = (settings.KEYCLOAK_ADMIN_REALM or "master").strip() or "master"
    return realm == master and ("super_admin" in set(ctx.roles))


def require_permission(*required_perms: str):
    required = {p for p in required_perms if isinstance(p, str) and p}

    async def _dep(
        request: Request,
        ctx: AuthContext = Depends(require_role()),
        session: AsyncSession = Depends(_get_public_session),
    ) -> AuthContext:
        # When auth is disabled (dev/test), don't block on policy checks.
        if not get_settings().AUTH_ENABLED:
            return ctx

        # super_admin bypass
        if "super_admin" in set(ctx.roles):
            return ctx

        # Resolve tenant UUID for tenant policy overlay (best-effort).
        tenant_uuid: str | None = None
        try:
            from sqlmodel import select
            from app.models.public.tenant import Tenant

            # ctx.tenant_id is realm/platform id (schema_name)
            r = await session.execute(
                select(Tenant).where((Tenant.schema_name == ctx.tenant_id) | (Tenant.keycloak_realm == ctx.tenant_id))
            )
            t = r.scalars().first()
            if t is not None:
                tenant_uuid = str(t.id)
        except Exception:
            tenant_uuid = None

        effective = await get_effective_policy(session, tenant_uuid=tenant_uuid, realm_key=ctx.tenant_id)
        global_doc = effective.get("global") if isinstance(effective, dict) else {}
        realm_doc = effective.get("realm") if isinstance(effective, dict) else None
        tenant_doc = effective.get("tenant") if isinstance(effective, dict) else None
        perms = _merge_role_permissions(set(ctx.roles), global_doc=global_doc, realm_doc=realm_doc, tenant_doc=tenant_doc)
        request.state.authz_perms = perms
        request.state.authz_columns = _merge_role_columns(set(ctx.roles), global_doc=global_doc, realm_doc=realm_doc, tenant_doc=tenant_doc)

        if "*" in perms:
            return ctx

        missing = [p for p in required if p not in perms]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": "You don't have permission to perform this action.",
                    "details": {"missing_permissions": missing},
                },
            )
        return ctx

    return _dep


def require_any_permission(*any_perms: str):
    required = {p for p in any_perms if isinstance(p, str) and p}

    async def _dep(
        request: Request,
        ctx: AuthContext = Depends(require_role()),
        session: AsyncSession = Depends(_get_public_session),
    ) -> AuthContext:
        if not get_settings().AUTH_ENABLED:
            return ctx

        if "super_admin" in set(ctx.roles):
            return ctx

        tenant_uuid: str | None = None
        try:
            from sqlmodel import select
            from app.models.public.tenant import Tenant

            r = await session.execute(
                select(Tenant).where((Tenant.schema_name == ctx.tenant_id) | (Tenant.keycloak_realm == ctx.tenant_id))
            )
            t = r.scalars().first()
            if t is not None:
                tenant_uuid = str(t.id)
        except Exception:
            tenant_uuid = None

        effective = await get_effective_policy(session, tenant_uuid=tenant_uuid, realm_key=ctx.tenant_id)
        global_doc = effective.get("global") if isinstance(effective, dict) else {}
        realm_doc = effective.get("realm") if isinstance(effective, dict) else None
        tenant_doc = effective.get("tenant") if isinstance(effective, dict) else None
        perms = _merge_role_permissions(set(ctx.roles), global_doc=global_doc, realm_doc=realm_doc, tenant_doc=tenant_doc)
        request.state.authz_perms = perms
        request.state.authz_columns = _merge_role_columns(set(ctx.roles), global_doc=global_doc, realm_doc=realm_doc, tenant_doc=tenant_doc)
        if "*" in perms:
            return ctx

        if required and not (perms & required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": "You don't have permission to perform this action.",
                    "details": {"required_any_of": sorted(required)},
                },
            )
        return ctx

    return _dep


def get_column_rules(request: Request, permission: str) -> tuple[set[str], set[str]]:
    """Return (allow, deny) field sets for the given permission."""
    columns = getattr(request.state, "authz_columns", {})
    if not isinstance(columns, dict):
        return set(), set()
    spec = columns.get(permission)
    if not isinstance(spec, dict):
        return set(), set()
    allow = spec.get("allow")
    deny = spec.get("deny")
    allow_set = set(allow) if isinstance(allow, set) else set(allow or []) if isinstance(allow, list) else set()
    deny_set = set(deny) if isinstance(deny, set) else set(deny or []) if isinstance(deny, list) else set()
    return allow_set, deny_set


def enforce_write_columns(request: Request, permission: str, incoming_fields: set[str]) -> None:
    """Block writes to disallowed fields based on policy column rules.

    Semantics:
    - If `allow` is non-empty: only fields in allow are permitted.
    - If `deny` contains a field: that field cannot be written.
    """
    allow, deny = get_column_rules(request, permission)
    forbidden = set()
    if allow:
        forbidden |= {f for f in incoming_fields if f not in allow}
    forbidden |= (incoming_fields & deny)
    if forbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "field_forbidden",
                "message": "You don't have permission to modify one or more fields.",
                "details": {"permission": permission, "fields": sorted(forbidden)},
            },
        )
