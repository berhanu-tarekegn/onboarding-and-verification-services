"""Tenant middleware."""
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from app.core.context import jwt_platform_super_admin_context, jwt_tenant_context, tenant_context

class TenantMiddleware(BaseHTTPMiddleware):
    """Extract ``X-Tenant-ID`` header and populate the tenant ContextVar.

    Endpoints that don't require tenant scoping (e.g. ``/health``,
    tenant registration) should simply not call ``get_current_tenant()``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        jwt_tenant_id = jwt_tenant_context.get()
        header_tenant_id = request.headers.get("X-Tenant-ID")

        if jwt_platform_super_admin_context.get():
            tenant_id = header_tenant_id
        else:
            # Prefer JWT-derived tenant context when available. The header is treated
            # as an optional guard and is validated elsewhere (dependency/session layer).
            tenant_id = jwt_tenant_id or header_tenant_id

        token = tenant_context.set(tenant_id)  # may be None — that's OK
        try:
            response = await call_next(request)
        finally:
            tenant_context.reset(token)
        return response
