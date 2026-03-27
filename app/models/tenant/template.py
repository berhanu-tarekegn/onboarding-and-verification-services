"""Tenant Template models — tenant-specific templates in per-tenant schemas.

Tenant templates extend a baseline identified by `(template_type, baseline_level)`.
Each tenant definition copies the active baseline version for that pair and then
adds tenant-owned changes on top.
"""

import uuid as _uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy import Column, Enum as SAEnum, Text
from sqlmodel import Field, JSON, Relationship
from uuid_extensions import uuid7

from app.models.base import TenantSchemaModel
from app.models.enums import (
    DefinitionReviewAction,
    DefinitionReviewStatus,
    TemplateType,
)


class TenantTemplate(TenantSchemaModel, table=True):
    """A tenant-owned template of a specific type.

    Choosing a template_type automatically enforces the mandatory baseline
    questions for that type in every new version.
    """

    __tablename__ = "tenant_templates"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    name: str = Field(index=True, max_length=255)

    description: Optional[str] = Field(default=None, sa_column=Column(Text))

    template_type: TemplateType = Field(
        sa_column=Column(
            SAEnum(
                TemplateType,
                values_callable=lambda obj: [e.value for e in obj],
                schema="public",
                name="templatetype",
                create_type=False,
            ),
            nullable=False,
            index=True,
        ),
        description="Must match the type of the baseline this tenant template extends.",
    )

    baseline_level: int = Field(
        index=True,
        ge=1,
        description="Baseline business level this tenant template extends.",
    )

    is_active: bool = Field(default=True)

    active_version_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="tenant_template_definitions.id",
        description="The UUID of the TenantTemplateDefinition currently in use.",
    )

    versions: List["TenantTemplateDefinition"] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "primaryjoin": "TenantTemplate.id==TenantTemplateDefinition.template_id",
        },
    )


class TenantTemplateDefinition(TenantSchemaModel, table=True):
    """A specific, versioned snapshot of a tenant template's configuration.

    At creation time, questions are copied from the referenced baseline version.
    Tenants can add their own question groups/questions on top.
    Once published, the definition is immutable.
    """

    __tablename__ = "tenant_template_definitions"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    template_id: _uuid.UUID = Field(
        foreign_key="tenant_templates.id",
        index=True,
        nullable=False,
    )

    version_tag: str = Field(index=True, max_length=50)

    copied_from_baseline_version_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="public.baseline_template_definitions.id",
        index=True,
        description="The baseline version whose questions were copied at creation time.",
    )

    rules_config: Dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        description="Tenant-level DAF overrides merged on top of the baseline rules_config.",
    )

    changelog: Optional[str] = Field(default=None, sa_column=Column(Text))

    is_draft: bool = Field(default=True)

    review_status: DefinitionReviewStatus = Field(
        default=DefinitionReviewStatus.DRAFT,
        sa_column=Column(
            SAEnum(
                DefinitionReviewStatus,
                values_callable=lambda obj: [e.value for e in obj],
                name="definitionreviewstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="draft",
        ),
        description="draft | pending_review | approved | changes_requested",
    )

    submitted_for_review_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    submitted_for_review_by: Optional[str] = Field(default=None, max_length=255)
    reviewed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    reviewed_by: Optional[str] = Field(default=None, max_length=255)
    review_notes: Optional[str] = Field(default=None, sa_column=Column(Text))

    template: "TenantTemplate" = Relationship(
        back_populates="versions",
        sa_relationship_kwargs={"foreign_keys": "[TenantTemplateDefinition.template_id]"},
    )

    question_groups: List["QuestionGroup"] = Relationship(
        back_populates="version",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "QuestionGroup.display_order",
        },
    )

    ungrouped_questions: List["Question"] = Relationship(
        back_populates="version",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "Question.display_order",
            "primaryjoin": "and_(TenantTemplateDefinition.id==foreign(Question.version_id), Question.group_id==None)",
        },
    )

    reviews: List["TenantTemplateDefinitionReview"] = Relationship(
        back_populates="definition",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "TenantTemplateDefinitionReview.created_at",
        },
    )


class TenantTemplateDefinitionReview(TenantSchemaModel, table=True):
    """Immutable review history for tenant template definitions."""

    __tablename__ = "tenant_template_definition_reviews"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    definition_id: _uuid.UUID = Field(
        foreign_key="tenant_template_definitions.id",
        index=True,
        nullable=False,
    )

    action: DefinitionReviewAction = Field(
        sa_column=Column(
            SAEnum(
                DefinitionReviewAction,
                values_callable=lambda obj: [e.value for e in obj],
                name="definitionreviewaction",
                create_type=False,
            ),
            nullable=False,
        )
    )

    notes: Optional[str] = Field(default=None, sa_column=Column(Text))

    definition: "TenantTemplateDefinition" = Relationship(back_populates="reviews")


class QuestionGroup(TenantSchemaModel, table=True):
    """A group of questions within a tenant template version.

    Groups copied from the baseline have is_tenant_editable=False.
    Groups created by the tenant directly have is_tenant_editable=True.
    """

    __tablename__ = "question_groups"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    version_id: _uuid.UUID = Field(
        foreign_key="tenant_template_definitions.id",
        index=True,
        nullable=False,
    )

    unique_key: str = Field(
        max_length=255,
        description="Stable developer identifier for this group (e.g. 'personal_info').",
    )

    title: str = Field(max_length=500, default="")

    display_order: int = Field(default=0)

    submit_api_url: Optional[str] = Field(default=None, max_length=500)

    sequential_file_upload: bool = Field(default=False)

    is_tenant_editable: bool = Field(
        default=True,
        description="False for groups copied from baseline — tenants cannot modify them.",
    )

    version: "TenantTemplateDefinition" = Relationship(back_populates="question_groups")

    questions: List["Question"] = Relationship(
        back_populates="group",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "Question.display_order",
        },
    )


class Question(TenantSchemaModel, table=True):
    """A single question within a tenant template version.

    A question may belong to a group (group_id set) or sit directly on the
    version with no group (group_id=None, version_id required). Exactly one
    of group_id or version_id must anchor the question — the CHECK constraint
    in the migration enforces (group_id IS NOT NULL OR version_id IS NOT NULL).
    """

    __tablename__ = "questions"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    group_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="question_groups.id",
        index=True,
        nullable=True,
        description="Group this question belongs to. NULL means question is directly on the version.",
    )

    version_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="tenant_template_definitions.id",
        index=True,
        nullable=True,
        description="Set only when group_id is NULL — attaches the question directly to a version.",
    )

    unique_key: str = Field(
        max_length=255,
        description="Stable developer identifier for this question (e.g. 'date_of_birth').",
    )

    label: str = Field(max_length=500)

    field_type: str = Field(
        max_length=50,
        description="One of: text, dropdown, radio, checkbox, date, fileUpload, signature",
    )

    required: bool = Field(default=False)

    display_order: int = Field(default=0)

    regex: Optional[str] = Field(default=None, sa_column=Column(Text))

    keyboard_type: Optional[str] = Field(default=None, max_length=50)

    min_date: Optional[str] = Field(
        default=None,
        max_length=10,
        description="ISO date string YYYY-MM-DD",
    )

    max_date: Optional[str] = Field(
        default=None,
        max_length=10,
        description="ISO date string YYYY-MM-DD",
    )

    depends_on_unique_key: Optional[str] = Field(
        default=None,
        max_length=255,
        description="unique_key of the controlling question for conditional visibility.",
    )

    visible_when_equals: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Value the controlling question must equal to show this question.",
    )

    rules: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_type=JSON,
        description="Per-question DAF transforms.",
    )

    is_tenant_editable: bool = Field(
        default=True,
        description="False for questions copied from baseline — tenants cannot modify them.",
    )

    group: Optional["QuestionGroup"] = Relationship(back_populates="questions")

    version: Optional["TenantTemplateDefinition"] = Relationship(
        back_populates="ungrouped_questions",
        sa_relationship_kwargs={"foreign_keys": "[Question.version_id]"},
    )

    options: List["QuestionOption"] = Relationship(
        back_populates="question",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "QuestionOption.display_order",
        },
    )


class QuestionOption(TenantSchemaModel, table=True):
    """An option for a dropdown or radio question."""

    __tablename__ = "question_options"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    question_id: _uuid.UUID = Field(
        foreign_key="questions.id",
        index=True,
        nullable=False,
    )

    value: str = Field(max_length=500)

    display_order: int = Field(default=0)

    is_tenant_editable: bool = Field(
        default=True,
        description="False for options copied from baseline.",
    )

    question: "Question" = Relationship(back_populates="options")
