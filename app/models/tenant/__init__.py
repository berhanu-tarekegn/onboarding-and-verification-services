"""Tenant schema models — per-tenant isolated data.

These models live in per-tenant PostgreSQL schemas (e.g., tenant_acme, tenant_foo).
The schema is determined at runtime via the `search_path` setting.

Includes:
- TenantTemplate: Tenant-specific templates (can extend baseline templates)
- TenantTemplateDefinition: Versioned definitions of tenant templates
- Submission: Form submissions capturing user data
- SubmissionStatusHistory: Audit trail for submission workflow
- SubmissionComment: Comments/notes on submissions

Key design principles:
- No explicit schema in table args (schema is set via search_path)
- Can reference public schema tables via fully qualified names
- Full CRUD access for the owning tenant
"""

from app.models.tenant.template import TenantTemplate, TenantTemplateDefinition
from app.models.tenant.submission import (
    Submission,
    SubmissionStatus,
    SubmissionStatusHistory,
    SubmissionComment,
)

__all__ = [
    "TenantTemplate",
    "TenantTemplateDefinition",
    "Submission",
    "SubmissionStatus",
    "SubmissionStatusHistory",
    "SubmissionComment",
]
