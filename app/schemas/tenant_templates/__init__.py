"""Tenant Template API schemas — request/response models."""

from app.schemas.tenant_templates.template import (
    TenantTemplateBase,
    TenantTemplateCreate,
    TenantTemplateUpdate,
    TenantTemplateRead,
    TenantTemplateReadWithVersions,
    TenantTemplateReadWithConfig,
    TenantTemplateDefinitionBase,
    TenantTemplateDefinitionCreate,
    TenantTemplateDefinitionUpdate,
    TenantTemplateDefinitionReviewRequest,
    TenantTemplateDefinitionReviewRead,
    TenantTemplateDefinitionRead,
)

__all__ = [
    "TenantTemplateBase",
    "TenantTemplateCreate",
    "TenantTemplateUpdate",
    "TenantTemplateRead",
    "TenantTemplateReadWithVersions",
    "TenantTemplateReadWithConfig",
    "TenantTemplateDefinitionBase",
    "TenantTemplateDefinitionCreate",
    "TenantTemplateDefinitionUpdate",
    "TenantTemplateDefinitionReviewRequest",
    "TenantTemplateDefinitionReviewRead",
    "TenantTemplateDefinitionRead",
]
