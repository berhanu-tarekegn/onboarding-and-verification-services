"""API routes for the application.

Routes are organized by domain:
- tenants: Tenant registry management (public schema)
- baseline_templates: System-owned templates (public schema)
- tenant_templates: Tenant-specific templates (per-tenant schema)
- submissions: Form submissions (per-tenant schema)
"""

from app.routes.tenants import tenant_router
from app.routes.baseline_templates import baseline_template_router
from app.routes.tenant_templates import tenant_template_router
from app.routes.submissions import submission_router

__all__ = [
    "tenant_router",
    "baseline_template_router",
    "tenant_template_router",
    "submission_router",
]
