"""Shared FastAPI dependencies for common request validation."""

import uuid
import re

from fastapi import Header, HTTPException, status
from app.core.context import jwt_tenant_context


async def require_tenant_header(
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> str:
    """Declare tenant context for tenant-scoped endpoints.

    Tenant identity is derived from the logged-in user's JWT tenant claim.
    The `X-Tenant-ID` header is optional and is only used as an additional
    guard: if provided, it must match the JWT tenant claim.

    The actual tenant lookup and schema switching happens inside get_tenant_session().
    """
    # Back-compat for local/dev/test runs where AUTH_ENABLED=false:
    # allow routing purely via X-Tenant-ID.
    from app.core.config import get_settings

    settings = get_settings()
    jwt_tenant: str | None
    if not settings.AUTH_ENABLED:
        jwt_tenant = None
        if not x_tenant_id or not x_tenant_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Tenant-ID header is required.",
            )
        value = x_tenant_id.strip()
    else:
        jwt_tenant = jwt_tenant_context.get()
        if not jwt_tenant:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "missing_token", "message": "Missing bearer token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not x_tenant_id or not x_tenant_id.strip():
            return jwt_tenant
        value = x_tenant_id.strip()
    # UUID
    try:
        uuid.UUID(value)
        return value
    except ValueError:
        pass

    # schema_name: starts with a letter, contains only letters/digits/underscore, max 63
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID must be a valid UUID or schema_name (e.g. 'ovp').",
        )
    if jwt_tenant and value != jwt_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "tenant_mismatch", "message": "Tenant mismatch."},
        )
    return jwt_tenant or value
