"""ASGI middleware for the application."""

from app.middleware.tenants import TenantMiddleware

__all__ = ["TenantMiddleware"]
