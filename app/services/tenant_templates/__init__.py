"""Tenant Template service — tenant-owned templates in per-tenant schemas.

This module provides CRUD operations for tenant templates.
Templates can be standalone or extend baseline templates.
"""

from app.services.tenant_templates.service import (
    create_tenant_template,
    list_tenant_templates,
    get_tenant_template,
    get_tenant_template_with_config,
    get_tenant_template_definition_with_config,
    update_tenant_template,
    delete_tenant_template,
    create_tenant_template_definition,
    get_tenant_template_definition,
    update_tenant_template_definition,
    delete_tenant_template_definition,
    publish_tenant_template_definition,
    submit_tenant_template_definition_for_review,
    approve_tenant_template_definition,
    request_changes_tenant_template_definition,
    add_question_group,
    delete_question_group,
    add_question,
    add_ungrouped_question,
    delete_question,
)

__all__ = [
    "create_tenant_template",
    "list_tenant_templates",
    "get_tenant_template",
    "get_tenant_template_with_config",
    "get_tenant_template_definition_with_config",
    "update_tenant_template",
    "delete_tenant_template",
    "create_tenant_template_definition",
    "get_tenant_template_definition",
    "update_tenant_template_definition",
    "delete_tenant_template_definition",
    "publish_tenant_template_definition",
    "submit_tenant_template_definition_for_review",
    "approve_tenant_template_definition",
    "request_changes_tenant_template_definition",
    "add_question_group",
    "delete_question_group",
    "add_question",
    "add_ungrouped_question",
    "delete_question",
]
