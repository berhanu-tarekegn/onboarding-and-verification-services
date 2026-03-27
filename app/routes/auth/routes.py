"""Auth proxy routes — Kong → Template Service → Keycloak.

These endpoints are intended to be fronted by Kong.

Clients provide:
- realm (path param)
- username/password (login) or refresh_token (refresh)

Server injects:
- grant_type
- client_id (+ optional client_secret)

Keycloak endpoint:
  /realms/<realm>/protocol/openid-connect/token
"""

from __future__ import annotations

import json
import re
import time
import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.auth import AuthContext, require_role
from app.core.authz import effective_authz_roles, get_effective_policy, known_permissions, resolve_permissions_and_columns
from app.db.session import get_public_session
from app.db.session import async_session_factory
from app.models.public.tenant import Tenant
from sqlmodel import select

router = APIRouter(prefix="/api/auth", tags=["auth"])

def _debug_log(message: str) -> None:
    """Best-effort debug logging visible in uvicorn output when DEBUG=true."""
    if not get_settings().DEBUG:
        return
    # Using print here because uvicorn logging config doesn't always show
    # application loggers in all environments/IDEs.
    print(message)  # noqa: T201

_REALM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")
_REALM_DISCOVERY_LOCK = asyncio.Lock()
_REALM_DISCOVERY_CACHE: dict[str, tuple[float, bool]] = {}
_REALM_BASE_URL_CACHE: dict[str, str] = {}


def _clean_env_value(value: str) -> str:
    """Strip whitespace and inline `.env` comments (`...  # comment`)."""
    return _INLINE_COMMENT_RE.sub("", value).strip()


def _unauthorized(message: str, *, code: str = "unauthorized", details: Any | None = None) -> HTTPException:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=payload,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bad_request(message: str, *, code: str = "invalid_request", details: Any | None = None) -> HTTPException:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=payload)


async def _realm_allowed(realm: str) -> None:
    if not _REALM_RE.fullmatch(realm):
        raise _bad_request("Invalid realm.")
    settings = get_settings()
    raw_allowed = _clean_env_value(settings.KEYCLOAK_REALMS or "")
    allowed = {r.strip() for r in raw_allowed.split(",") if r.strip()}
    if allowed and realm not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "realm_not_found", "message": "Realm not found."},
        )
    if not allowed:
        exists, attempts = await _discover_realm_exists_with_attempts(realm)
        if not exists:
            detail: dict[str, Any] = {"code": "realm_not_found", "message": "Realm not found."}
            if get_settings().DEBUG:
                detail["details"] = {"attempts": attempts}
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            )


async def _read_body(request: Request) -> dict[str, Any]:
    ctype = (request.headers.get("content-type") or "").lower()

    # JSON
    if "application/json" in ctype:
        try:
            value = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise _bad_request("Invalid JSON body.") from exc
        if not isinstance(value, dict):
            raise _bad_request("Request body must be a JSON object.")
        return value

    # Form
    if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
        form = await request.form()
        return {k: v for k, v in form.items()}

    # Fallback: try JSON, then form
    raw = await request.body()
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
        if isinstance(value, dict):
            return value
    except Exception:  # noqa: BLE001
        pass
    return {}


def _client_for_realm(realm: str, *, client_alias: str | None = None) -> tuple[str, str | None]:
    settings = get_settings()
    mapped = _client_from_mapping(realm, client_alias=client_alias)
    if mapped is not None:
        return mapped

    client_id = _clean_env_value(settings.KEYCLOAK_CLIENT_ID or "")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "server_misconfigured", "message": "Auth client is not configured."},
        )
    secret_raw = _clean_env_value(settings.KEYCLOAK_CLIENT_SECRET or "")
    secret = secret_raw or None
    return client_id, secret


async def _client_for_realm_async(realm: str, *, client_alias: str | None = None) -> tuple[str, str | None]:
    """Async wrapper for client lookup supporting DB fallback."""
    settings = get_settings()
    mapped = _client_from_mapping(realm, client_alias=client_alias)
    if mapped is not None:
        return mapped

    async def _db_lookup() -> tuple[str, str | None] | None:
        async with async_session_factory() as session:
            stmt = select(Tenant).where((Tenant.keycloak_realm == realm) | (Tenant.tenant_key == realm))
            result = await session.execute(stmt)
            tenant = result.scalars().first()
            if tenant and tenant.keycloak_client_id:
                return tenant.keycloak_client_id, tenant.keycloak_client_secret
        return None

    try:
        # Keep auth proxy responsive even if DB is down.
        found = await asyncio.wait_for(_db_lookup(), timeout=0.5)
        if found is not None:
            return found
    except Exception:  # noqa: BLE001
        pass

    return _client_for_realm(realm, client_alias=client_alias)


def _client_from_mapping(realm: str, *, client_alias: str | None = None) -> tuple[str, str | None] | None:
    """Return client credentials from KEYCLOAK_CLIENTS_JSON mapping, if present."""
    settings = get_settings()
    mapping_raw = _clean_env_value(settings.KEYCLOAK_CLIENTS_JSON or "")
    if not mapping_raw:
        return None

    try:
        mapping = json.loads(mapping_raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(mapping, dict):
        return None

    entry = mapping.get(realm)
    if not isinstance(entry, dict):
        return None

    # Simple mapping: {"realm":{"client_id":"...","client_secret":"..."}}
    cid = entry.get("client_id")
    csec = entry.get("client_secret")
    if isinstance(cid, str) and cid:
        return cid, csec if isinstance(csec, str) and csec else None

    # Multi-client mapping:
    # {"realm":{"default":"mobile","clients":{"mobile":{"client_id":"..."},"web":{"client_id":"..."}}}}
    clients = entry.get("clients")
    if not isinstance(clients, dict) or not clients:
        return None

    selected = client_alias
    if selected is None:
        default_alias = entry.get("default") or entry.get("default_client")
        if isinstance(default_alias, str) and default_alias in clients:
            selected = default_alias
        else:
            selected = next(iter(clients.keys()))

    selected_entry = clients.get(selected)
    if not isinstance(selected_entry, dict):
        raise _bad_request("Unknown client.", code="unknown_client")

    cid2 = selected_entry.get("client_id")
    csec2 = selected_entry.get("client_secret")
    if isinstance(cid2, str) and cid2:
        return cid2, csec2 if isinstance(csec2, str) and csec2 else None
    return None


def _mapping_has_realm(realm: str) -> bool:
    """Return True if KEYCLOAK_CLIENTS_JSON explicitly configures a realm."""
    settings = get_settings()
    mapping_raw = _clean_env_value(settings.KEYCLOAK_CLIENTS_JSON or "")
    if not mapping_raw:
        return False
    try:
        mapping = json.loads(mapping_raw)
    except json.JSONDecodeError:
        return False
    return isinstance(mapping, dict) and realm in mapping


async def _warn_if_default_client_mismatch(realm: str) -> None:
    """Debug-only hint when env default client differs from tenant config."""
    settings = get_settings()
    if not settings.DEBUG:
        return
    if _mapping_has_realm(realm):
        return
    default_cid = _clean_env_value(settings.KEYCLOAK_CLIENT_ID or "")
    if not default_cid:
        return
    async def _db_check() -> None:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Tenant).where((Tenant.keycloak_realm == realm) | (Tenant.tenant_key == realm))
            )
            tenant = result.scalars().first()
            if tenant and tenant.keycloak_client_id and tenant.keycloak_client_id != default_cid:
                _debug_log(
                    f"[auth-proxy] client mismatch realm={realm} env_client_id={default_cid} tenant_client_id={tenant.keycloak_client_id} (using tenant)"
                )

    try:
        # Debug-only: never block login on DB connectivity.
        await asyncio.wait_for(_db_check(), timeout=0.25)
    except Exception:  # noqa: BLE001
        return
    return


def _token_url(realm: str) -> str:
    settings = get_settings()
    base = (_REALM_BASE_URL_CACHE.get(realm) or _clean_env_value(settings.KEYCLOAK_BASE_URL or "")).rstrip("/")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "server_misconfigured", "message": "Keycloak base URL is not configured."},
        )
    return f"{base}/realms/{realm}/protocol/openid-connect/token"


def _candidate_keycloak_bases() -> list[str]:
    """Return possible Keycloak base URLs (supports legacy `/auth` prefix)."""
    raw = _clean_env_value(get_settings().KEYCLOAK_BASE_URL or "").rstrip("/")
    if not raw:
        return []

    candidates = [raw]
    if raw.endswith("/auth"):
        candidates.append(raw.removesuffix("/auth"))
    else:
        candidates.append(f"{raw}/auth")

    # De-dup preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        normalized = item.rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _keycloak_headers() -> dict[str, str]:
    settings = get_settings()
    raw = _clean_env_value(settings.KEYCLOAK_HTTP_HEADERS_JSON or "")
    headers: dict[str, str] = {}
    if raw:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(k, str) and isinstance(v, str):
                    headers[k] = v

    # Provide sane defaults. Some gateways/WAFs behave differently for
    # non-browser/non-curl user agents; defaulting helps realm discovery be
    # consistent with common curl-based diagnostics.
    lower_keys = {k.lower() for k in headers}
    if "accept" not in lower_keys:
        headers["Accept"] = "application/json"
    if "user-agent" not in lower_keys:
        headers["User-Agent"] = "curl/8.0.0"
    return headers


async def _discover_realm_exists(realm: str) -> bool:
    """Return True if realm exists (Keycloak discovery), with in-memory TTL cache."""
    exists, _attempts = await _discover_realm_exists_with_attempts(realm)
    return exists


async def _discover_realm_exists_with_attempts(realm: str) -> tuple[bool, list[dict[str, Any]]]:
    """Like `_discover_realm_exists` but also returns attempted URLs (debuggable)."""
    settings = get_settings()
    ttl = max(0, int(settings.KEYCLOAK_REALM_DISCOVERY_TTL_SECONDS or 0))
    now = time.time()
    attempts: list[dict[str, Any]] = []

    cached = _REALM_DISCOVERY_CACHE.get(realm)
    if cached is not None:
        ts, value = cached
        # Negative cache should expire quickly so we recover from transient
        # failures / configuration drift (e.g. `/auth` prefix differences).
        effective_ttl = ttl if value else min(ttl, 60)
        if ttl == 0 or (now - ts) < effective_ttl:
            return value, attempts

    async with _REALM_DISCOVERY_LOCK:
        cached = _REALM_DISCOVERY_CACHE.get(realm)
        if cached is not None:
            ts, value = cached
            effective_ttl = ttl if value else min(ttl, 60)
            if ttl == 0 or (now - ts) < effective_ttl:
                return value, attempts

        bases = _candidate_keycloak_bases()
        if not bases:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "server_misconfigured", "message": "Keycloak base URL is not configured."},
            )

        timeout = httpx.Timeout(float(settings.KEYCLOAK_HTTP_TIMEOUT_SECONDS or 10))
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=_keycloak_headers(),
                trust_env=False,
            ) as client:
                exists = False
                last_status: int | None = None
                for base in bases:
                    url = f"{base}/realms/{realm}/.well-known/openid-configuration"
                    resp = await client.get(url)
                    last_status = resp.status_code
                    attempts.append({"url": url, "status": resp.status_code})
                    _debug_log(f"[auth-proxy] discovery realm={realm} url={url} status={resp.status_code}")
                    if resp.status_code == 200:
                        exists = True
                        _REALM_BASE_URL_CACHE[realm] = base
                        _debug_log(f"[auth-proxy] discovery realm={realm} resolved_base={base}")
                        break
                    if resp.status_code == 404:
                        continue
                    # Any other status is treated as a transient auth provider failure.
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail={"code": "auth_provider_unavailable", "message": "Auth provider unavailable."},
                    )
        except httpx.RequestError as exc:
            attempts.append({"error": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "auth_provider_unavailable", "message": "Auth provider unavailable."},
            ) from exc

        if last_status not in (200, 404, None):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "auth_provider_unavailable", "message": "Auth provider unavailable."},
            )

        _REALM_DISCOVERY_CACHE[realm] = (now, exists)
        return exists, attempts


async def _post_to_keycloak(url: str, data: dict[str, Any]) -> httpx.Response:
    settings = get_settings()
    timeout = httpx.Timeout(float(settings.KEYCLOAK_HTTP_TIMEOUT_SECONDS or 10))
    async with httpx.AsyncClient(timeout=timeout, headers=_keycloak_headers(), trust_env=False) as client:
        return await client.post(url, data=data)


async def _post_to_keycloak_realm_with_fallback(realm: str, data: dict[str, Any]) -> httpx.Response:
    """POST to Keycloak token endpoint; retry with `/auth` base on 404."""
    timeout = httpx.Timeout(float(get_settings().KEYCLOAK_HTTP_TIMEOUT_SECONDS or 10))
    bases = _candidate_keycloak_bases()
    if not bases:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "server_misconfigured", "message": "Keycloak base URL is not configured."},
        )
    async with httpx.AsyncClient(timeout=timeout, headers=_keycloak_headers(), trust_env=False) as client:
        last: httpx.Response | None = None
        for base in bases:
            url = f"{base}/realms/{realm}/protocol/openid-connect/token"
            resp = await client.post(url, data=data)
            last = resp
            _debug_log(f"[auth-proxy] token realm={realm} url={url} status={resp.status_code}")
            if resp.status_code != 404:
                _REALM_BASE_URL_CACHE[realm] = base
                return resp
        assert last is not None
        return last


def _safe_json_response(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "auth_provider_unavailable", "message": "Auth provider unavailable."},
        ) from exc


def _upstream_unavailable(resp: httpx.Response) -> HTTPException:
    detail: dict[str, Any] = {"code": "auth_provider_unavailable", "message": "Auth provider unavailable."}
    if get_settings().DEBUG:
        body = ""
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            body = ""
        detail["details"] = {
            "upstream_status": resp.status_code,
            "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
            "upstream_body_prefix": (body or "")[:500],
        }
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


@router.post("/login/{realm}")
async def login(
    realm: str,
    request: Request,
) -> Any:
    """Password-grant login via Keycloak (proxied)."""
    await _realm_allowed(realm)
    await _warn_if_default_client_mismatch(realm)
    body = await _read_body(request)
    username = body.get("username")
    password = body.get("password")
    if not isinstance(username, str) or not username.strip():
        raise _bad_request("Missing username.")
    if not isinstance(password, str) or not password.strip():
        raise _bad_request("Missing password.")

    client_id, client_secret = await _client_for_realm_async(realm, client_alias=None)
    data: dict[str, Any] = {
        "grant_type": "password",
        "client_id": client_id,
        "username": username,
        "password": password,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = await _post_to_keycloak_realm_with_fallback(realm, data=data)
    if resp.status_code in (400, 401):
        debug_details = None
        if get_settings().DEBUG:
            try:
                debug_details = {
                    "upstream_status": resp.status_code,
                    "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
                    "upstream": resp.json(),
                }
            except Exception:  # noqa: BLE001
                debug_details = {
                    "upstream_status": resp.status_code,
                    "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
                    "upstream_body_prefix": (getattr(resp, "text", "") or "")[:500],
                }
        raise _unauthorized("Invalid username or password.", code="invalid_credentials", details=debug_details)
    if resp.status_code != 200:
        raise _upstream_unavailable(resp)
    return _safe_json_response(resp)


@router.post("/refresh/{realm}")
async def refresh(
    realm: str,
    request: Request,
) -> Any:
    """Refresh-token flow via Keycloak (proxied)."""
    await _realm_allowed(realm)
    await _warn_if_default_client_mismatch(realm)
    body = await _read_body(request)
    refresh_token = body.get("refresh_token") or body.get("refreshToken")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise _bad_request("Missing refresh_token.")

    client_id, client_secret = await _client_for_realm_async(realm, client_alias=None)
    data: dict[str, Any] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = await _post_to_keycloak_realm_with_fallback(realm, data=data)
    if resp.status_code in (400, 401):
        debug_details = None
        if get_settings().DEBUG:
            try:
                debug_details = {
                    "upstream_status": resp.status_code,
                    "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
                    "upstream": resp.json(),
                }
            except Exception:  # noqa: BLE001
                debug_details = {
                    "upstream_status": resp.status_code,
                    "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
                    "upstream_body_prefix": (getattr(resp, "text", "") or "")[:500],
                }
        raise _unauthorized("Invalid refresh token.", code="invalid_refresh_token", details=debug_details)
    if resp.status_code != 200:
        raise _upstream_unavailable(resp)
    return _safe_json_response(resp)


@router.post("/service-token/{realm}")
async def service_token(
    realm: str,
    request: Request,
) -> Any:
    """Client-credentials token flow for machine-to-machine tenant integrations."""
    await _realm_allowed(realm)
    await _warn_if_default_client_mismatch(realm)
    body = await _read_body(request)
    client_alias = body.get("client") or body.get("client_alias")
    if client_alias is not None and (not isinstance(client_alias, str) or not client_alias.strip()):
        raise _bad_request("client must be a non-empty string when provided.")
    scope = body.get("scope")
    if scope is not None and (not isinstance(scope, str) or not scope.strip()):
        raise _bad_request("scope must be a non-empty string when provided.")

    client_id, client_secret = await _client_for_realm_async(
        realm,
        client_alias=client_alias.strip() if isinstance(client_alias, str) else None,
    )
    data: dict[str, Any] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if isinstance(scope, str) and scope.strip():
        data["scope"] = scope.strip()

    resp = await _post_to_keycloak_realm_with_fallback(realm, data=data)
    if resp.status_code in (400, 401):
        debug_details = None
        if get_settings().DEBUG:
            try:
                debug_details = {
                    "upstream_status": resp.status_code,
                    "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
                    "upstream": resp.json(),
                }
            except Exception:  # noqa: BLE001
                debug_details = {
                    "upstream_status": resp.status_code,
                    "upstream_url": str(getattr(getattr(resp, "request", None), "url", "") or ""),
                    "upstream_body_prefix": (getattr(resp, "text", "") or "")[:500],
                }
        raise _unauthorized(
            "Invalid service client credentials.",
            code="invalid_client_credentials",
            details=debug_details,
        )
    if resp.status_code != 200:
        raise _upstream_unavailable(resp)
    return _safe_json_response(resp)


@router.get("/me")
async def me(
    request: Request,
    ctx: AuthContext = Depends(require_role()),
    session: AsyncSession = Depends(get_public_session),
) -> Any:
    """Return the current logged-in user context + resolved permissions.

    Tenant and realm are derived from the JWT (headers are not trusted).
    """
    iss = ctx.raw_claims.get("iss")
    issuer = iss if isinstance(iss, str) else None
    realm_name: str | None = None
    if issuer and "/realms/" in issuer:
        realm_name = issuer.split("/realms/")[-1].strip("/") or None

    warnings: list[dict[str, Any]] = []

    # Resolve tenant UUID + schema from the tenant claim (supports uuid or schema/realm).
    tenant = None
    tenant_ident = (ctx.tenant_id or "").strip()
    if tenant_ident:
        try:
            import uuid

            tenant_uuid = uuid.UUID(tenant_ident)
            clause = Tenant.id == tenant_uuid
        except Exception:  # noqa: BLE001
            clause = (Tenant.tenant_key == tenant_ident) | (Tenant.keycloak_realm == tenant_ident)
        try:
            r = await session.execute(select(Tenant).where(clause))
            tenant = r.scalars().first()
        except Exception as exc:  # noqa: BLE001
            warnings.append({"code": "tenant_lookup_failed", "message": str(exc)[:200]})
            tenant = None

    tenant_uuid_str = str(tenant.id) if tenant else None
    realm_key = tenant.tenant_key if tenant else (realm_name or tenant_ident or None)

    # Resolve effective policy docs. If unavailable (e.g., DB/migration issues),
    # fall back to code defaults so `/me` remains informative.
    bundle: dict[str, Any] = {"global": {}, "realm": {}, "tenant": {}}
    try:
        bundle = await get_effective_policy(session, tenant_uuid=tenant_uuid_str, realm_key=realm_key)
    except Exception as exc:  # noqa: BLE001
        warnings.append({"code": "authz_policy_unavailable", "message": str(exc)[:200]})
        bundle = {"global": {}, "realm": {}, "tenant": {}}

    perms, columns = resolve_permissions_and_columns(
        effective_authz_roles(ctx),
        global_doc=bundle.get("global") if isinstance(bundle.get("global"), dict) else {},
        realm_doc=bundle.get("realm") if isinstance(bundle.get("realm"), dict) else None,
        tenant_doc=bundle.get("tenant") if isinstance(bundle.get("tenant"), dict) else None,
    )
    wildcard = "*" in perms
    expanded = sorted(known_permissions()) if wildcard else sorted(perms)

    return {
        "user_id": ctx.user_id,
        "tenant_claim": ctx.tenant_id,
        "roles": sorted(ctx.roles),
        "issuer": issuer,
        "realm": realm_name,
        "resolved_tenant": {
            "id": tenant_uuid_str,
            "tenant_key": getattr(tenant, "tenant_key", None),
            "keycloak_realm": getattr(tenant, "keycloak_realm", None),
        },
        "request": {
            "x_tenant_id": request.headers.get("X-Tenant-ID"),
        },
        "authz": {
            "permissions": sorted(perms),
            "wildcard": wildcard,
            "expanded_permissions": expanded,
            # normalize sets for JSON
            "columns": {k: {kk: sorted(vv) for kk, vv in (v or {}).items()} for k, v in (columns or {}).items()},
        },
        "policies": {
            "global": bundle.get("global"),
            "realm": bundle.get("realm"),
            "tenant": bundle.get("tenant"),
        },
        "warnings": warnings,
    }
