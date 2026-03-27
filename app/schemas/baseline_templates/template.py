"""Baseline Template API schemas — request/response models.

These schemas are used for the system-owned baseline templates
that live in the public schema. They define mandatory typed question
contracts that tenants must include in their templates.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import SQLModel, Field

from app.models.enums import TemplateType
from app.schemas.templates.form_schema import (
    BaselineQuestionGroupRead,
    BaselineQuestionRead,
    QuestionGroupCreate,
    QuestionCreate,
)


# ── Definition Schemas ────────────────────────────────────────────────

class BaselineTemplateDefinitionBase(SQLModel):
    """Base fields for baseline template definitions."""

    version_tag: str = Field(max_length=50)
    rules_config: Dict[str, Any] = Field(default_factory=dict)
    changelog: Optional[str] = None


class BaselineTemplateDefinitionCreate(BaselineTemplateDefinitionBase):
    """Request body for creating a new baseline template definition."""

    question_groups: List[QuestionGroupCreate] = Field(default_factory=list)
    questions: List[QuestionCreate] = Field(
        default_factory=list,
        description="Groupless questions attached directly to this version (no group).",
    )


class BaselineTemplateDefinitionUpdate(SQLModel):
    """Request body for updating a baseline template definition (draft only).

    Question groups / questions are managed via separate CRUD endpoints.
    """

    version_tag: Optional[str] = Field(default=None, max_length=50)
    rules_config: Optional[Dict[str, Any]] = None
    changelog: Optional[str] = None


class BaselineTemplateDefinitionRead(BaselineTemplateDefinitionBase):
    """Response model for baseline template definitions."""

    id: UUID
    template_id: UUID
    is_draft: bool
    question_groups: List[BaselineQuestionGroupRead] = Field(default_factory=list)
    ungrouped_questions: List[BaselineQuestionRead] = Field(
        default_factory=list,
        description="Questions attached directly to this version (no group).",
    )
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


# ── Template Schemas ──────────────────────────────────────────────────

class BaselineTemplateBase(SQLModel):
    """Base fields for baseline templates."""

    template_type: TemplateType
    level: int = Field(ge=1)
    name: str = Field(max_length=255)
    description: Optional[str] = None
    category: str = Field(default="general", max_length=100)


class BaselineTemplateCreate(BaselineTemplateBase):
    """Request body for creating a new baseline template."""

    initial_version: Optional[BaselineTemplateDefinitionCreate] = None


class BaselineTemplateUpdate(SQLModel):
    """Request body for updating a baseline template."""

    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None
    is_locked: Optional[bool] = None
    active_version_id: Optional[UUID] = None


class BaselineTemplateRead(BaselineTemplateBase):
    """Response model for baseline templates."""

    id: UUID
    is_active: bool
    is_locked: bool
    active_version_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


class BaselineTemplateReadWithVersions(BaselineTemplateRead):
    """Response model including all historical versions."""

    versions: List[BaselineTemplateDefinitionRead] = []
