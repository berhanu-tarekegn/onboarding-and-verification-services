"""SQLModel/SQLAlchemy models for schema-based multi-tenancy.

Architecture:
- public/: Models in the PostgreSQL `public` schema (system-owned)
  - Tenant: Registry of all tenants
  - BaselineTemplate: System templates that tenants can extend
  
- tenant/: Models in per-tenant schemas (tenant-owned)
  - TenantTemplate: Tenant-specific templates
  
Schema isolation is achieved via PostgreSQL's search_path mechanism.
"""

from app.models import public  # noqa: F401
from app.models import tenant  # noqa: F401

# Re-export commonly used models for convenience
from app.models.public import Tenant, BaselineTemplate, BaselineTemplateDefinition
from app.models.tenant import (
    TenantTemplate,
    TenantTemplateDefinition,
    VerificationRun,
    VerificationStepRun,
)

__all__ = [
    "public",
    "tenant",
    "Tenant",
    "BaselineTemplate",
    "BaselineTemplateDefinition",
    "TenantTemplate",
    "TenantTemplateDefinition",
    "VerificationRun",
    "VerificationStepRun",
]
