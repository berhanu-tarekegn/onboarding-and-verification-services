"""Pydantic I/O schemas for normalized question groups, questions, and options.

These schemas are used across both baseline and tenant template endpoints.
The *Create schemas accept input from admin/tenant API clients.
The *Read schemas are returned in API responses.

Naming conventions
------------------
- unique_key      : Stable developer identifier for a group or question.
                    Must be unique within a template version.
- depends_on_unique_key : unique_key of the controlling question for conditional
                    visibility (replaces old dependsOn / depends_on_slug field).
"""

from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Supported field types ─────────────────────────────────────────────

FieldType = Literal[
    "text",
    "dropdown",
    "radio",
    "checkbox",
    "date",
    "fileUpload",
    "signature",
]


# ── Question Option schemas ───────────────────────────────────────────

class QuestionOptionCreate(BaseModel):
    """Request body for adding an option to a dropdown/radio question."""

    value: str = Field(max_length=500)
    display_order: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="ignore")


class QuestionOptionRead(QuestionOptionCreate):
    """Response model for a question option."""

    id: UUID


class BaselineQuestionOptionRead(QuestionOptionCreate):
    """Response model for a baseline question option."""

    id: UUID


# ── Question schemas ──────────────────────────────────────────────────

class QuestionCreate(BaseModel):
    """Request body for creating a question inside a group."""

    unique_key: str = Field(
        max_length=255,
        description="Stable developer key for this question (e.g. 'date_of_birth').",
    )
    label: str = Field(max_length=500)
    field_type: FieldType
    required: bool = False
    display_order: int = Field(default=0, ge=0)

    regex: Optional[str] = None
    keyboard_type: Optional[str] = Field(default=None, max_length=50)
    min_date: Optional[str] = Field(default=None, max_length=10)
    max_date: Optional[str] = Field(default=None, max_length=10)

    depends_on_unique_key: Optional[str] = Field(
        default=None,
        max_length=255,
        description="unique_key of the controlling question for conditional visibility.",
    )
    visible_when_equals: Optional[str] = Field(default=None, max_length=255)

    rules: Optional[Dict[str, Any]] = None

    options: List[QuestionOptionCreate] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class QuestionRead(QuestionCreate):
    """Response model for a tenant question."""

    id: UUID
    is_tenant_editable: bool
    options: List[QuestionOptionRead] = Field(default_factory=list)


class BaselineQuestionRead(BaseModel):
    """Response model for a baseline question (read-only)."""

    id: UUID
    unique_key: str
    label: str
    field_type: FieldType
    required: bool
    display_order: int
    regex: Optional[str] = None
    keyboard_type: Optional[str] = None
    min_date: Optional[str] = None
    max_date: Optional[str] = None
    depends_on_unique_key: Optional[str] = None
    visible_when_equals: Optional[str] = None
    rules: Optional[Dict[str, Any]] = None
    options: List[BaselineQuestionOptionRead] = Field(default_factory=list)


# ── Question Group schemas ────────────────────────────────────────────

class QuestionGroupCreate(BaseModel):
    """Request body for creating a question group."""

    unique_key: str = Field(
        max_length=255,
        description="Stable developer key for this group (e.g. 'personal_info').",
    )
    title: str = Field(default="", max_length=500)
    display_order: int = Field(default=0, ge=0)
    submit_api_url: Optional[str] = Field(default=None, max_length=500)
    sequential_file_upload: bool = False

    questions: List[QuestionCreate] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class QuestionGroupRead(QuestionGroupCreate):
    """Response model for a tenant question group."""

    id: UUID
    is_tenant_editable: bool
    questions: List[QuestionRead] = Field(default_factory=list)


class BaselineQuestionGroupRead(BaseModel):
    """Response model for a baseline question group (read-only)."""

    id: UUID
    unique_key: str
    title: str
    display_order: int
    submit_api_url: Optional[str] = None
    sequential_file_upload: bool
    questions: List[BaselineQuestionRead] = Field(default_factory=list)


# ── Backwards-compatible field type aliases ───────────────────────────
# The old TemplateField union is preserved so any code that imported from
# this module keeps working during the transition period.

class _LegacyBaseField(BaseModel):
    id: str
    label: str
    required: bool = False
    dependsOn: Optional[str] = None
    visibleWhenEquals: Union[str, bool, None] = None
    model_config = ConfigDict(extra="ignore")


class TextField(_LegacyBaseField):
    type: Literal["text"]
    keyboardType: Optional[str] = None
    regex: Optional[str] = None


class DropdownField(_LegacyBaseField):
    type: Literal["dropdown"]
    options: List[str] = Field(default_factory=list)


class RadioField(_LegacyBaseField):
    type: Literal["radio"]
    options: List[str] = Field(default_factory=list)


class CheckboxField(_LegacyBaseField):
    type: Literal["checkbox"]


class DateField(_LegacyBaseField):
    type: Literal["date"]
    minDate: Optional[str] = None
    maxDate: Optional[str] = None


class FileUploadField(_LegacyBaseField):
    type: Literal["fileUpload"]


class SignatureField(_LegacyBaseField):
    type: Literal["signature"]
