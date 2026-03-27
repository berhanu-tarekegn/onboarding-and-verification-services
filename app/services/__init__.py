"""Service layer — business logic for the application.

Services are organized by domain:
- tenants: Tenant registry management (public schema)
- baseline_templates: System-owned templates (public schema)
- tenant_templates: Tenant-specific templates (per-tenant schema)
- submissions: Form submissions (per-tenant schema)
- products: Tenant-owned onboarding products with KYC template linking (per-tenant schema)
- verifications: Config-driven verification flows for submissions
"""

from app.services import tenants
from app.services import baseline_templates
from app.services import tenant_templates
from app.services import submissions
from app.services import products
from app.services import verifications

__all__ = [
    "tenants",
    "baseline_templates",
    "tenant_templates",
    "submissions",
    "products",
    "verifications",
]
