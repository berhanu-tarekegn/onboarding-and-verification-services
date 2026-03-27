"""Tenant Template API schemas — request/response models.

These schemas are used for tenant-owned templates that live in per-tenant schemas.
Templates are bound to a TemplateType; mandatory baseline questions for that type
are automatically copied into each new version.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import SQLModel, Field

from app.models.enums import (
    DefinitionReviewAction,
    DefinitionReviewStatus,
    TemplateType,
)
from app.schemas.templates.form_schema import QuestionGroupCreate, QuestionGroupRead, QuestionCreate, QuestionRead


# ── Definition Schemas ────────────────────────────────────────────────

class TenantTemplateDefinitionBase(SQLModel):
    """Base fields for tenant template definitions."""

    version_tag: str = Field(max_length=50)
    rules_config: Dict[str, Any] = Field(default_factory=dict)
    changelog: Optional[str] = None


class TenantTemplateDefinitionCreate(TenantTemplateDefinitionBase):
    """Request body for creating a new tenant template definition.

    The service layer automatically copies questions from the active baseline
    version for this template's type. Tenants can add extra question groups
    via the question_groups field, or groupless questions via the questions field.
    """

    question_groups: List[QuestionGroupCreate] = Field(default_factory=list)
    questions: List[QuestionCreate] = Field(
        default_factory=list,
        description="Groupless questions attached directly to this version (no group).",
    )


class TenantTemplateDefinitionUpdate(SQLModel):
    """Request body for updating a tenant template definition (draft only).

    Changing rules_config is allowed on draft versions.
    Question groups / questions are managed via separate CRUD endpoints.
    """

    version_tag: Optional[str] = Field(default=None, max_length=50)
    rules_config: Optional[Dict[str, Any]] = None
    changelog: Optional[str] = None


class TenantTemplateDefinitionReviewRequest(SQLModel):
    """Request body for submit/approve/request-changes review actions."""

    notes: Optional[str] = None


class TenantTemplateDefinitionReviewRead(SQLModel):
    """Immutable review history entry for a tenant template definition."""

    id: UUID
    definition_id: UUID
    action: DefinitionReviewAction
    notes: Optional[str] = None
    created_at: datetime
    created_by: str


class TenantTemplateDefinitionRead(TenantTemplateDefinitionBase):
    """Response model for tenant template definitions."""

    id: UUID
    template_id: UUID
    copied_from_baseline_version_id: Optional[UUID] = None
    is_draft: bool
    review_status: DefinitionReviewStatus
    submitted_for_review_at: Optional[datetime] = None
    submitted_for_review_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_notes: Optional[str] = None
    question_groups: List[QuestionGroupRead] = Field(default_factory=list)
    ungrouped_questions: List[QuestionRead] = Field(
        default_factory=list,
        description="Questions attached directly to this version (no group).",
    )
    reviews: List[TenantTemplateDefinitionReviewRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


# ── Template Schemas ──────────────────────────────────────────────────

class TenantTemplateBase(SQLModel):
    """Base fields for tenant templates."""

    name: str = Field(max_length=255)
    description: Optional[str] = None


class TenantTemplateCreate(TenantTemplateBase):
    """Request body for creating a new tenant template.

    `template_type` and `baseline_level` must correspond to an active
    BaselineTemplate. On creation, the service copies questions from the
    baseline's active version.
    """

    template_type: TemplateType
    baseline_level: int = Field(ge=1)
    initial_version: Optional[TenantTemplateDefinitionCreate] = None


class TenantTemplateUpdate(SQLModel):
    """Request body for updating a tenant template header."""

    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    active_version_id: Optional[UUID] = None


class TenantTemplateRead(TenantTemplateBase):
    """Response model for tenant templates."""

    id: UUID
    template_type: TemplateType
    baseline_level: int
    is_active: bool
    active_version_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


class TenantTemplateReadWithVersions(TenantTemplateRead):
    """Response model including all historical versions."""

    versions: List[TenantTemplateDefinitionRead] = []


class TenantTemplateReadWithConfig(TenantTemplateRead):
    """Response model with the active version's fully resolved configuration."""

    question_groups: List[QuestionGroupRead] = Field(default_factory=list)
    rules_config: Dict[str, Any] = Field(default_factory=dict)
    baseline_version_id: Optional[UUID] = None
    baseline_version_tag: Optional[str] = None
