"""Baseline Template service — system-owned templates in public schema.

This module provides CRUD operations for baseline templates.
These operations should only be accessible to system administrators.
"""

from app.services.baseline_templates.service import (
    create_baseline_template,
    list_baseline_templates,
    get_baseline_template,
    update_baseline_template,
    delete_baseline_template,
    create_baseline_template_definition,
    get_baseline_template_definition,
    update_baseline_template_definition,
    delete_baseline_template_definition,
    publish_baseline_template_definition,
)

__all__ = [
    "create_baseline_template",
    "list_baseline_templates",
    "get_baseline_template",
    "update_baseline_template",
    "delete_baseline_template",
    "create_baseline_template_definition",
    "get_baseline_template_definition",
    "update_baseline_template_definition",
    "delete_baseline_template_definition",
    "publish_baseline_template_definition",
]
