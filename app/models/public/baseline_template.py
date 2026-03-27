"""Baseline Template models — system-owned templates in the ``public`` schema.

Baseline templates are organized by `template_type` and business `level`.
Each type/level pair owns its own immutable version history.
"""

import uuid as _uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy import Column, Enum as SAEnum, Text
from sqlmodel import Field, JSON, Relationship
from uuid_extensions import uuid7

from app.models.base import PublicSchemaModel
from app.models.enums import TemplateType

if TYPE_CHECKING:
    from app.models.public.baseline_template import BaselineTemplateDefinition


class BaselineTemplate(PublicSchemaModel, table=True):
    """A system-owned typed template contract.

    One baseline exists per `(template_type, level)` pair. Tenant templates
    select the pair they want to extend, and new tenant versions copy the
    active published definition for that baseline.
    """

    __tablename__ = "baseline_templates"
    __table_args__ = (
        sa.UniqueConstraint(
            "template_type",
            "level",
            name="uq_baseline_template_type_level",
        ),
        {"schema": "public"},
    )

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

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
        description="Baseline family type, for example kyc or kyb.",
    )

    level: int = Field(
        index=True,
        ge=1,
        description="Business level within the template type, for example 1, 2, 3.",
    )

    name: str = Field(index=True, max_length=255)

    description: Optional[str] = Field(default=None, sa_column=Column(Text))

    category: str = Field(default="general", max_length=100, index=True)

    is_active: bool = Field(default=True)

    is_locked: bool = Field(default=False)

    active_version_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="public.baseline_template_definitions.id",
        description="The UUID of the BaselineTemplateDefinition currently in use.",
    )

    versions: List["BaselineTemplateDefinition"] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "primaryjoin": "BaselineTemplate.id==BaselineTemplateDefinition.template_id",
        },
    )


class BaselineTemplateDefinition(PublicSchemaModel, table=True):
    """A specific, immutable snapshot of a baseline template's configuration.

    Once published (is_draft=False), the definition and all its question
    groups/questions/options are frozen. New versions copy forward from the
    previous published version.
    """

    __tablename__ = "baseline_template_definitions"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    template_id: _uuid.UUID = Field(
        foreign_key="public.baseline_templates.id",
        index=True,
        nullable=False,
    )

    version_tag: str = Field(index=True, max_length=50)

    rules_config: Dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        description="Top-level DAF config: scoring model, decision thresholds, derived fields.",
    )

    changelog: Optional[str] = Field(default=None, sa_column=Column(Text))

    is_draft: bool = Field(default=True)

    template: "BaselineTemplate" = Relationship(
        back_populates="versions",
        sa_relationship_kwargs={"foreign_keys": "[BaselineTemplateDefinition.template_id]"},
    )

    question_groups: List["BaselineQuestionGroup"] = Relationship(
        back_populates="version",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "BaselineQuestionGroup.display_order",
        },
    )

    ungrouped_questions: List["BaselineQuestion"] = Relationship(
        back_populates="version",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "BaselineQuestion.display_order",
            "primaryjoin": "and_(BaselineTemplateDefinition.id==foreign(BaselineQuestion.version_id), BaselineQuestion.group_id==None)",
        },
    )


class BaselineQuestionGroup(PublicSchemaModel, table=True):
    """A named group of questions within a baseline template version.

    Equivalent to a 'page' or 'step' in the UI. Groups are ordered by
    display_order and identified by a stable unique_key.
    """

    __tablename__ = "baseline_question_groups"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    version_id: _uuid.UUID = Field(
        foreign_key="public.baseline_template_definitions.id",
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

    version: "BaselineTemplateDefinition" = Relationship(back_populates="question_groups")

    questions: List["BaselineQuestion"] = Relationship(
        back_populates="group",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "BaselineQuestion.display_order",
        },
    )


class BaselineQuestion(PublicSchemaModel, table=True):
    """A single question within a baseline template version.

    A question may belong to a group (group_id set) or sit directly on the
    version with no group (group_id=None, version_id required). Exactly one
    of group_id or version_id must anchor the question — the CHECK constraint
    in the migration enforces (group_id IS NOT NULL OR version_id IS NOT NULL).

    All questions in a published baseline definition are immutable.
    """

    __tablename__ = "baseline_questions"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    group_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="public.baseline_question_groups.id",
        index=True,
        nullable=True,
        description="Group this question belongs to. NULL means question is directly on the version.",
    )

    version_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="public.baseline_template_definitions.id",
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
        description="Per-question DAF transforms (e.g. map, score_weight, normalize).",
    )

    group: Optional["BaselineQuestionGroup"] = Relationship(back_populates="questions")

    version: Optional["BaselineTemplateDefinition"] = Relationship(
        back_populates="ungrouped_questions",
        sa_relationship_kwargs={"foreign_keys": "[BaselineQuestion.version_id]"},
    )

    options: List["BaselineQuestionOption"] = Relationship(
        back_populates="question",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "BaselineQuestionOption.display_order",
        },
    )


class BaselineQuestionOption(PublicSchemaModel, table=True):
    """An option for a dropdown or radio question in the baseline."""

    __tablename__ = "baseline_question_options"
    __table_args__ = {"schema": "public"}

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    question_id: _uuid.UUID = Field(
        foreign_key="public.baseline_questions.id",
        index=True,
        nullable=False,
    )

    value: str = Field(max_length=500)

    display_order: int = Field(default=0)

    question: "BaselineQuestion" = Relationship(back_populates="options")
