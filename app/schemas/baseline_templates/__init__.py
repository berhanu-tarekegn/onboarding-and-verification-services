"""Baseline Template API schemas — request/response models."""

from app.schemas.baseline_templates.template import (
    BaselineTemplateBase,
    BaselineTemplateCreate,
    BaselineTemplateUpdate,
    BaselineTemplateRead,
    BaselineTemplateReadWithVersions,
    BaselineTemplateDefinitionBase,
    BaselineTemplateDefinitionCreate,
    BaselineTemplateDefinitionUpdate,
    BaselineTemplateDefinitionRead,
)

__all__ = [
    "BaselineTemplateBase",
    "BaselineTemplateCreate",
    "BaselineTemplateUpdate",
    "BaselineTemplateRead",
    "BaselineTemplateReadWithVersions",
    "BaselineTemplateDefinitionBase",
    "BaselineTemplateDefinitionCreate",
    "BaselineTemplateDefinitionUpdate",
    "BaselineTemplateDefinitionRead",
]
