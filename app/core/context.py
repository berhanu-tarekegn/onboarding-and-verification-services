"""Request-scoped context shared across auth, routing, and DB access.

Context variables:
- tenant_context: tenant identifier from JWT or `X-Tenant-ID`
- jwt_tenant_context: raw tenant identifier from the JWT claim
- jwt_roles_context: roles extracted from the JWT
- tenant_id_context: resolved tenant UUID
- user_context: current user id for audit trails
"""

import uuid as _uuid
from contextvars import ContextVar
from typing import Optional

from fastapi import HTTPException, status

# Holds the tenant identifier for the current request.
# This may come from:
# - X-Tenant-ID header (UUID or schema_name), or
# - JWT tenant claim (when the header is absent).
tenant_context: ContextVar[str | None] = ContextVar("tenant_context", default=None)

# Holds the raw tenant identifier from the JWT claim (if present).
jwt_tenant_context: ContextVar[str | None] = ContextVar("jwt_tenant_context", default=None)

# Holds the roles from the JWT (if present).
jwt_roles_context: ContextVar[frozenset[str] | None] = ContextVar(
    "jwt_roles_context", default=None
)

# Holds the tenant UUID (resolved from the tenant identifier) for the current request.
tenant_id_context: ContextVar[_uuid.UUID | None] = ContextVar(
    "tenant_id_context", default=None
)

# Holds the current user ID for audit trails. Defaults to "system".
user_context: ContextVar[str] = ContextVar("user", default="system")


def get_current_tenant() -> str:
    """Return the current tenant identifier or raise 400 if missing.

    The tenant identifier is extracted from the X-Tenant-ID header (if present)
    or derived from the JWT tenant claim by middleware.
    """
    tenant = tenant_context.get()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context is required (JWT tenant claim or X-Tenant-ID header).",
        )
    return tenant


def get_current_tenant_optional() -> Optional[str]:
    """Return the current tenant identifier or None if not set.

    Use this when tenant context is optional (e.g., public routes).
    """
    return tenant_context.get()


def get_current_tenant_id() -> _uuid.UUID:
    """Return the current tenant UUID or raise 400 if missing.

    The tenant ID is resolved from the request tenant identifier by the session layer.
    """
    tenant_id = tenant_id_context.get()
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID not set in context.",
        )
    return tenant_id


def get_current_user() -> str:
    """Return the current user ID for audit trails."""
    return user_context.get()
