"""Submission API schemas — request/response models for form submissions."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import field_validator
from sqlmodel import SQLModel, Field

from app.models.tenant.submission import SubmissionStatus
from app.schemas.templates.form_schema import QuestionGroupRead, QuestionRead
from app.schemas.submissions.verification import VerificationRunRead, VerificationRunSummaryRead


# ── Status History Schemas ────────────────────────────────────────────

class SubmissionStatusHistoryRead(SQLModel):
    """Response model for status history entries."""
    
    id: UUID
    submission_id: UUID
    from_status: Optional[SubmissionStatus] = None
    to_status: SubmissionStatus
    changed_by: str
    reason: Optional[str] = None
    extra_data: Dict[str, Any] = {}
    created_at: datetime


# ── Comment Schemas ───────────────────────────────────────────────────

class SubmissionCommentCreate(SQLModel):
    """Request body for creating a comment."""
    
    content: str = Field(min_length=1)
    field_id: Optional[str] = Field(default=None, max_length=255)
    is_internal: bool = False
    parent_id: Optional[UUID] = None


class SubmissionCommentRead(SQLModel):
    """Response model for comments."""
    
    id: UUID
    submission_id: UUID
    content: str
    field_id: Optional[str] = None
    is_internal: bool
    parent_id: Optional[UUID] = None
    created_at: datetime
    created_by: str


# ── Submission Schemas ────────────────────────────────────────────────

class SubmissionBase(SQLModel):
    """Base fields for submissions."""
    
    template_id: UUID
    form_data: Dict[str, Any] = Field(default_factory=dict)
    submitter_id: Optional[str] = Field(default=None, max_length=255)
    external_ref: Optional[str] = Field(default=None, max_length=255)


class SubmissionCreate(SubmissionBase):
    """Request body for creating a new submission.
    
    The template_version_id is automatically set to the template's
    current active version at creation time.
    """

    product_id: Optional[UUID] = Field(
        default=None,
        description="Optional product ID for product-specific onboarding traceability.",
    )


class SubmissionUpdate(SQLModel):
    """Request body for updating a submission (draft only)."""
    
    form_data: Optional[Dict[str, Any]] = None
    submitter_id: Optional[str] = Field(default=None, max_length=255)
    external_ref: Optional[str] = Field(default=None, max_length=255)


class SubmissionStatusTransition(SQLModel):
    """Request body for changing submission status."""
    
    to_status: SubmissionStatus
    reason: Optional[str] = None
    review_notes: Optional[str] = None
    extra_data: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator('to_status')
    @classmethod
    def validate_status(cls, v):
        # DRAFT cannot be set via transition - it's only the initial state
        if v == SubmissionStatus.DRAFT:
            raise ValueError("Cannot transition to DRAFT status")
        return v


class SubmissionRead(SQLModel):
    """Response model for submissions."""
    
    id: UUID
    template_id: UUID
    template_version_id: UUID
    baseline_version_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    form_data: Dict[str, Any]
    computed_data: Dict[str, Any]
    validation_results: Dict[str, Any]
    attachments: Dict[str, Any]
    status: SubmissionStatus
    submitter_id: Optional[str] = None
    external_ref: Optional[str] = None
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_notes: Optional[str] = None
    maker_id: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


class SubmissionReadWithHistory(SubmissionRead):
    """Response model including status history and comments."""

    form_schema: Dict[str, Any] = Field(default_factory=dict)
    rules_config: Dict[str, Any] = Field(default_factory=dict)
    question_groups: List[QuestionGroupRead] = Field(default_factory=list)
    ungrouped_questions: List[QuestionRead] = Field(default_factory=list)
    status_history: List[SubmissionStatusHistoryRead] = []
    comments: List[SubmissionCommentRead] = []
    verification: Optional[VerificationRunRead] = None


class SubmissionListFilters(SQLModel):
    """Query parameters for filtering submission lists."""
    
    status: Optional[SubmissionStatus] = None
    template_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    submitter_id: Optional[str] = None
    external_ref: Optional[str] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None


class SubmissionSearchCriterion(SQLModel):
    """A configured tenant filter applied against submission or verification data."""

    key: str = Field(min_length=1, max_length=255)
    op: str = Field(default="eq", min_length=1, max_length=32)
    value: Any = None


class SubmissionSearchRequest(SQLModel):
    """Structured submission search request for portal and partner API use."""

    status: Optional[SubmissionStatus] = None
    template_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    submitter_id: Optional[str] = Field(default=None, max_length=255)
    external_ref: Optional[str] = Field(default=None, max_length=255)
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    verification_status: Optional[str] = Field(default=None, max_length=50)
    verification_decision: Optional[str] = Field(default=None, max_length=50)
    verification_kyc_level: Optional[str] = Field(default=None, max_length=100)
    verification_current_step_key: Optional[str] = Field(default=None, max_length=255)
    criteria: List[SubmissionSearchCriterion] = Field(default_factory=list)
    sort_by: str = Field(default="created_at", max_length=64)
    sort_order: str = Field(default="desc", max_length=8)
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class SubmissionSearchFilterRead(SQLModel):
    """Tenant-discovered configured filter exposed to portal and third parties."""

    key: str
    label: str
    source: str
    path: str
    operators: List[str] = Field(default_factory=list)
    value_type: Optional[str] = None
    description: Optional[str] = None
    template_ids: List[UUID] = Field(default_factory=list)
    template_version_ids: List[UUID] = Field(default_factory=list)
    ambiguous: bool = False


class SubmissionSearchConfigRead(SQLModel):
    """Catalog of native and tenant-configured submission search filters."""

    native_filters: List[str] = Field(default_factory=list)
    verification_filters: List[str] = Field(default_factory=list)
    configured_filters: List[SubmissionSearchFilterRead] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)


class SubmissionSearchResultRead(SubmissionRead):
    """Submission list/search row with the latest verification summary."""

    verification: Optional[VerificationRunSummaryRead] = None
