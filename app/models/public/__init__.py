"""Public schema models — globally accessible, system-owned.

These models live in the PostgreSQL `public` schema and are:
- Readable by all tenants
- Writable only by system administrators

Includes:
- Tenant: Registry of all onboarded tenants
- BaselineTemplate: System-defined templates that tenants can extend
- BaselineTemplateDefinition: Versioned definitions of baseline templates
"""

from app.models.public.tenant import Tenant
from app.models.public.baseline_template import BaselineTemplate, BaselineTemplateDefinition
from app.models.public.authz_policy import AuthzPolicy
from app.models.public.identity_link import IdentityLink
from app.models.public.device_auth import DeviceCredential, DeviceChallenge

__all__ = [
    "Tenant",
    "BaselineTemplate",
    "BaselineTemplateDefinition",
    "AuthzPolicy",
    "IdentityLink",
    "DeviceCredential",
    "DeviceChallenge",
]
