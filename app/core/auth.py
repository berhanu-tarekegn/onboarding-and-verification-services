"""JWT auth utilities (Keycloak-friendly).

Design goals
------------
- Validate JWTs at the application layer (defense-in-depth after Kong).
- Verify tokens using a cached Keycloak JWKS (no per-request HTTP calls).
- Provide FastAPI dependencies for authentication and role authorization.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from urllib.parse import urlparse
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.context import jwt_roles_context, jwt_tenant_context, user_context
from app.core.errors import error_response

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: str
    tenant_id: str
    roles: frozenset[str]
    raw_claims: dict[str, Any]


def _unauthorized(message: str, *, code: str = "invalid_token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(message: str = "Forbidden", *, code: str = "forbidden") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _get_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    prefix = "bearer "
    if auth_header[: len(prefix)].lower() != prefix:
        raise _unauthorized("Invalid authorization header", code="invalid_authorization_header")
    token = auth_header[len(prefix) :].strip()
    if not token:
        raise _unauthorized("Missing bearer token", code="missing_token")
    return token


class JWKSCache:
    def __init__(self) -> None:
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._last_refresh_epoch: float | None = None

    def set_jwks(self, jwks: dict[str, Any]) -> None:
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise TypeError("JWKS must contain a 'keys' list")

        next_keys: dict[str, dict[str, Any]] = {}
        for key in keys:
            if not isinstance(key, dict):
                continue
            kid = key.get("kid")
            if isinstance(kid, str) and kid:
                next_keys[kid] = key
        self._keys_by_kid = next_keys
        self._last_refresh_epoch = time.time()

    def get_key(self, kid: str) -> dict[str, Any] | None:
        return self._keys_by_kid.get(kid)

    def last_refresh_epoch(self) -> float | None:
        return self._last_refresh_epoch

    def is_empty(self) -> bool:
        return not self._keys_by_kid


_JWKS_CACHE = JWKSCache()
_ISSUER_JWKS_CACHE: dict[str, JWKSCache] = {}
_ISSUER_JWKS_LOCKS: dict[str, asyncio.Lock] = {}
_JWKS_STOP_EVENT: asyncio.Event | None = None
_JWKS_TASK: asyncio.Task[None] | None = None


def get_jwks_cache() -> JWKSCache:
    return _JWKS_CACHE


def _load_jwks_from_settings() -> dict[str, Any] | None:
    settings = get_settings()
    raw = (settings.KEYCLOAK_JWKS_JSON or "").strip()
    if not raw:
        return None
    try:
        jwks = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("KEYCLOAK_JWKS_JSON is not valid JSON") from exc
    if not isinstance(jwks, dict):
        raise RuntimeError("KEYCLOAK_JWKS_JSON must be a JSON object")
    return jwks


def _fetch_json(url: str) -> dict[str, Any]:
    settings = get_settings()
    headers: dict[str, str] = {"User-Agent": "onboarding-and-verification-saas/0.1"}
    extra = (settings.JWKS_FETCH_HEADERS_JSON or "").strip()
    if extra:
        try:
            decoded = json.loads(extra)
        except json.JSONDecodeError as exc:
            raise RuntimeError("JWKS_FETCH_HEADERS_JSON must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("JWKS_FETCH_HEADERS_JSON must be a JSON object")
        for k, v in decoded.items():
            if isinstance(k, str) and isinstance(v, str):
                headers[k] = v

    req = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(req, timeout=settings.JWKS_FETCH_TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = resp.read()
    decoded = json.loads(data.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise TypeError("JWKS endpoint must return a JSON object")
    return decoded


def _issuer_base_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _trusted_issuer_bases() -> list[str]:
    settings = get_settings()
    bases: list[str] = []

    # Explicit allow-list (preferred).
    if settings.KEYCLOAK_TRUSTED_ISSUER_BASES:
        bases.extend(_parse_csv(settings.KEYCLOAK_TRUSTED_ISSUER_BASES))

    # Backwards compatible defaults: use configured Keycloak base(s) host as a trusted issuer base.
    for candidate in (settings.KEYCLOAK_ADMIN_BASE_URL, settings.KEYCLOAK_BASE_URL):
        base = (candidate or "").strip()
        if not base:
            continue
        derived = _issuer_base_from_url(base)
        if derived:
            bases.append(derived)

    # Normalize, de-dup.
    out: list[str] = []
    seen: set[str] = set()
    for b in bases:
        bb = b.rstrip("/")
        if not bb:
            continue
        if bb not in seen:
            out.append(bb)
            seen.add(bb)
    return out


def _issuer_allowed(issuer: str) -> bool:
    settings = get_settings()
    if settings.AUTH_ISSUERS:
        return issuer in set(_parse_csv(settings.AUTH_ISSUERS))

    bases = _trusted_issuer_bases()
    if not bases:
        # No allow-list configured: accept only https issuers to avoid SSRF via file:// etc.
        try:
            parsed = urlparse(issuer)
        except Exception:
            return False
        return parsed.scheme == "https" and bool(parsed.netloc)

    base = _issuer_base_from_url(issuer)
    if not base:
        return False
    return base.rstrip("/") in set(bases)


def _jwks_url_from_issuer(issuer: str) -> str:
    # Keycloak issuer format: <base>/realms/<realm>
    return f"{issuer.rstrip('/')}/protocol/openid-connect/certs"


async def _get_issuer_cache(issuer: str) -> JWKSCache:
    cache = _ISSUER_JWKS_CACHE.get(issuer)
    if cache is None:
        cache = JWKSCache()
        _ISSUER_JWKS_CACHE[issuer] = cache
    lock = _ISSUER_JWKS_LOCKS.get(issuer)
    if lock is None:
        lock = asyncio.Lock()
        _ISSUER_JWKS_LOCKS[issuer] = lock

    settings = get_settings()
    ttl = float(settings.JWKS_REFRESH_SECONDS or 60 * 60 * 24)
    now = time.time()
    last = cache.last_refresh_epoch()
    if last is not None and (now - last) < ttl and not cache.is_empty():
        return cache

    # Refresh under lock to avoid stampedes.
    async with lock:
        last2 = cache.last_refresh_epoch()
        if last2 is not None and (time.time() - last2) < ttl and not cache.is_empty():
            return cache
        jwks_url = _jwks_url_from_issuer(issuer)
        jwks = await asyncio.to_thread(_fetch_json, jwks_url)
        cache.set_jwks(jwks)
    return cache


async def refresh_jwks_once() -> None:
    """Refresh the in-memory JWKS cache based on Settings.

    This is the only place that may perform I/O to retrieve JWKS.
    """
    settings = get_settings()

    static_jwks = _load_jwks_from_settings()
    if static_jwks is not None:
        _JWKS_CACHE.set_jwks(static_jwks)
        return

    urls: list[str] = []
    if settings.KEYCLOAK_JWKS_URL:
        urls.append(settings.KEYCLOAK_JWKS_URL)
    if settings.KEYCLOAK_JWKS_URLS:
        urls.extend(_parse_csv(settings.KEYCLOAK_JWKS_URLS))

    if not urls:
        return

    # Fetch all, merge keys (best-effort; skip failing URLs).
    merged: dict[str, Any] = {"keys": []}
    for url in urls:
        try:
            jwks = await asyncio.to_thread(_fetch_json, url)
        except HTTPError as exc:
            logger.warning("Failed to fetch JWKS from %s: HTTP %s", url, exc.code)
            continue
        except URLError as exc:
            logger.warning("Failed to fetch JWKS from %s: %s", url, exc.reason)
            continue
        except Exception as exc:
            logger.warning("Failed to fetch JWKS from %s: %s", url, exc)
            continue
        keys = jwks.get("keys")
        if isinstance(keys, list):
            merged["keys"].extend(keys)

    if merged["keys"]:
        _JWKS_CACHE.set_jwks(merged)


async def _jwks_refresh_loop(refresh_seconds: int, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await refresh_jwks_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Fail-closed at decode time; don't take down the whole service.
            logger.warning("JWKS refresh failed: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
        except asyncio.TimeoutError:
            continue


async def startup_jwks_refresh() -> None:
    """Initialize JWKS and start the 24h refresh loop (if configured)."""
    global _JWKS_STOP_EVENT, _JWKS_TASK

    settings = get_settings()
    if not settings.AUTH_ENABLED:
        return

    try:
        await refresh_jwks_once()
    except Exception as exc:
        logger.warning("Initial JWKS refresh failed: %s", exc)

    if _JWKS_STOP_EVENT is None:
        _JWKS_STOP_EVENT = asyncio.Event()
    if _JWKS_TASK is None:
        _JWKS_TASK = asyncio.create_task(
            _jwks_refresh_loop(settings.JWKS_REFRESH_SECONDS, _JWKS_STOP_EVENT)
        )

    if _JWKS_CACHE.is_empty():
        # Static JWKS sources are optional when dynamic issuer-based JWKS is enabled.
        msg = "Static JWKS cache is empty. Tokens will use issuer-based JWKS discovery (iss + /certs) if possible."
        if settings.JWKS_REQUIRED and not (_trusted_issuer_bases() or settings.AUTH_ISSUERS):
            # If nothing is configured to allow/discover issuers, fail fast.
            raise RuntimeError(
                "JWKS is not configured. Set KEYCLOAK_JWKS_URL(S) or KEYCLOAK_JWKS_JSON, "
                "or configure KEYCLOAK_TRUSTED_ISSUER_BASES (or AUTH_ISSUERS)."
            )
        logger.warning(msg)


async def shutdown_jwks_refresh() -> None:
    global _JWKS_STOP_EVENT, _JWKS_TASK

    if _JWKS_STOP_EVENT is not None:
        _JWKS_STOP_EVENT.set()
    if _JWKS_TASK is not None:
        _JWKS_TASK.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _JWKS_TASK
    _JWKS_TASK = None
    _JWKS_STOP_EVENT = None


async def decode_jwt(token: str) -> dict[str, Any]:
    """Validate and decode a JWT using cached JWKS.

    Strategy:
    - Determine issuer (`iss`) from unverified claims.
    - Fetch/cache JWKS for that issuer from: <iss>/protocol/openid-connect/certs
      (unless KEYCLOAK_JWKS_URL(S) / KEYCLOAK_JWKS_JSON is configured).
    """
    settings = get_settings()
    if not settings.AUTH_ENABLED:
        raise _unauthorized("Authentication is disabled")

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise _unauthorized("Invalid token") from exc

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise _unauthorized("Invalid token")

    # Determine issuer early (before verifying) so we can resolve the right JWKS.
    try:
        unverified = jwt.get_unverified_claims(token)
    except Exception as exc:
        raise _unauthorized("Invalid token") from exc
    issuer = unverified.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise _unauthorized("Invalid token")
    if not _issuer_allowed(issuer):
        raise _unauthorized("Invalid token")

    # Prefer static merged JWKS if it contains the key (fast path).
    jwk = _JWKS_CACHE.get_key(kid)
    if jwk is None:
        # Dynamic: fetch from issuer JWKS.
        try:
            issuer_cache = await _get_issuer_cache(issuer)
        except Exception as exc:
            if settings.JWKS_REQUIRED:
                raise _unauthorized("JWKS unavailable", code="jwks_unavailable") from exc
            issuer_cache = JWKSCache()
        jwk = issuer_cache.get_key(kid)
    if jwk is None:
        raise _unauthorized("Invalid token")

    options = {"verify_aud": settings.AUTH_AUDIENCE is not None}
    try:
        payload = jwt.decode(
            token,
            key=jwk,
            algorithms=_parse_algorithms(settings.AUTH_ALGORITHMS),
            audience=settings.AUTH_AUDIENCE,
            options=options,
        )
    except JWTError as exc:
        raise _unauthorized("Invalid token") from exc

    # Re-check issuer after verification (defense-in-depth).
    issuer2 = payload.get("iss")
    if not isinstance(issuer2, str) or not issuer2 or not _issuer_allowed(issuer2):
        raise _unauthorized("Invalid token")

    return payload


def _parse_algorithms(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return ["RS256"]
    # Allow JSON list for advanced cases.
    if raw.startswith("["):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return _parse_csv(raw)
        if isinstance(decoded, list):
            return [v for v in decoded if isinstance(v, str) and v]
        return _parse_csv(raw)
    return _parse_csv(raw) or [raw]


def _extract_roles(payload: dict[str, Any]) -> frozenset[str]:
    roles: set[str] = set()

    realm_access = payload.get("realm_access")
    if isinstance(realm_access, dict):
        realm_roles = realm_access.get("roles")
        if isinstance(realm_roles, list):
            roles.update({r for r in realm_roles if isinstance(r, str)})

    resource_access = payload.get("resource_access")
    if isinstance(resource_access, dict):
        for access in resource_access.values():
            if not isinstance(access, dict):
                continue
            client_roles = access.get("roles")
            if isinstance(client_roles, list):
                roles.update({r for r in client_roles if isinstance(r, str)})

    direct_roles = payload.get("roles")
    if isinstance(direct_roles, list):
        roles.update({r for r in direct_roles if isinstance(r, str)})

    return frozenset(roles)

def _parse_exclusive_role_groups(value: str) -> list[set[str]]:
    groups: list[set[str]] = []
    raw = (value or "").strip()
    if not raw:
        return groups
    for part in _parse_csv(raw):
        roles = {r.strip() for r in part.split("|") if r.strip()}
        if len(roles) >= 2:
            groups.append(roles)
    return groups


def _enforce_role_exclusivity(roles: frozenset[str]) -> None:
    # super_admin bypass: allow multi-role tokens for ops.
    if "super_admin" in roles:
        return
    settings = get_settings()
    for group in _parse_exclusive_role_groups(settings.AUTH_EXCLUSIVE_ROLE_GROUPS):
        present = sorted(group & set(roles))
        if len(present) > 1:
            raise _forbidden(
                "Conflicting roles are not allowed on the same token.",
                code="role_conflict",
            )


def _build_auth_context(payload: dict[str, Any]) -> AuthContext:
    settings = get_settings()

    user_id = payload.get("sub") or payload.get("preferred_username")
    if not isinstance(user_id, str) or not user_id:
        raise _unauthorized("Invalid token")

    tenant_id = _get_first_claim(payload, settings.AUTH_TENANT_CLAIM)
    if not isinstance(tenant_id, str) or not tenant_id:
        raise _unauthorized("Missing tenant id", code="missing_tenant_id")

    roles = _extract_roles(payload)
    _enforce_role_exclusivity(roles)

    return AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles,
        raw_claims=payload,
    )


def _get_first_claim(payload: dict[str, Any], paths: str) -> Any:
    """Return the first non-empty claim from a comma-separated list of paths.

    Supports `{realm}` template in paths; realm is derived from the JWT issuer:
      iss = "https://.../realms/kifiya"  -> realm="kifiya"

    Example:
      AUTH_TENANT_CLAIM="tenant_id,{realm}_claims.tenant_id"
    """
    for raw in _parse_csv(paths):
        path = _resolve_realm_template(payload, raw)
        value = _get_claim(payload, path)
        if value is not None and value != "":
            return value
    return None


def _resolve_realm_template(payload: dict[str, Any], template: str) -> str:
    if "{realm}" not in template:
        return template
    issuer = payload.get("iss")
    if not isinstance(issuer, str) or "/realms/" not in issuer:
        return template.replace("{realm}", "")
    realm = issuer.split("/realms/", 1)[1].split("/", 1)[0]
    return template.replace("{realm}", realm)


def _get_claim(payload: dict[str, Any], path: str) -> Any:
    """Get a claim value supporting dotted paths.

    Example:
        path="tenant_id" -> payload["tenant_id"]
        path="ovp_claims.tenant_id" -> payload["ovp_claims"]["tenant_id"]
    """
    if not path:
        return None
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def require_role(*roles: str):
    required = {r for r in roles if isinstance(r, str) and r}

    async def _dep(request: Request) -> AuthContext:
        settings = get_settings()
        if not settings.AUTH_ENABLED:
            # Dev/test mode: bypass auth entirely.
            # Include required roles so role-guarded routes stay callable.
            bypass_roles = required or {"super_admin"}
            ctx = AuthContext(
                user_id="system",
                tenant_id="public",
                roles=frozenset(bypass_roles),
                raw_claims={},
            )
            request.state.auth = ctx
            return ctx

        ctx: AuthContext | None = getattr(request.state, "auth", None)
        if ctx is None:
            token = _get_bearer_token(request)
            if token is None:
                raise _unauthorized("Missing bearer token")
            ctx = _build_auth_context(await decode_jwt(token))
            # Cache per-request so downstream dependencies don't re-decode.
            request.state.auth = ctx

        if required and not (set(ctx.roles) & required):
            raise _forbidden(
                "You don't have permission to access this resource.",
                code="insufficient_role",
            )
        return ctx

    return _dep


async def get_current_user(ctx: AuthContext = Depends(require_role())) -> AuthContext:
    return ctx


def enforce_tenant(expected_tenant_id: str, ctx: AuthContext) -> None:
    """Service-layer guard: ensure the caller can access a tenant resource."""
    if expected_tenant_id != ctx.tenant_id:
        raise _forbidden("Tenant access forbidden", code="tenant_forbidden")


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Decode JWT once per request and set tenant/user contextvars.

    - If Authorization header is missing, the request proceeds (public endpoints).
    - If Authorization header is present but invalid, the request is rejected (401).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            token = _get_bearer_token(request)
            if token is None:
                return await call_next(request)

            payload = await decode_jwt(token)
            ctx = _build_auth_context(payload)
        except HTTPException as exc:
            if isinstance(exc.detail, dict):
                return error_response(
                    status_code=exc.status_code,
                    message=str(exc.detail.get("message") or "Request failed"),
                    request=request,
                    code=str(exc.detail.get("code") or ""),
                    details=exc.detail.get("details"),
                    headers=getattr(exc, "headers", None),
                )
            return error_response(
                status_code=exc.status_code,
                message=str(exc.detail) if exc.detail else "Request failed",
                request=request,
                headers=getattr(exc, "headers", None),
            )

        request.state.auth = ctx
        jwt_tenant_token = jwt_tenant_context.set(ctx.tenant_id)
        jwt_roles_token = jwt_roles_context.set(ctx.roles)
        user_token = user_context.set(ctx.user_id)
        try:
            return await call_next(request)
        finally:
            jwt_tenant_context.reset(jwt_tenant_token)
            jwt_roles_context.reset(jwt_roles_token)
            user_context.reset(user_token)
