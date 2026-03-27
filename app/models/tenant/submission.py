"""Form Submission models — captures completed template forms in per-tenant schemas.

Design Principles:
1. **Version Locking**: Submissions capture the exact template version used at submission time
2. **Workflow Support**: Status tracking with audit history for approvals/rejections
3. **Data Integrity**: Submitted data is stored as JSON alongside validation results
4. **Compliance Ready**: Full audit trail of who did what and when

Submission Lifecycle:
    DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED/REJECTED/RETURNED
                                              ↓
                                          COMPLETED
"""

import uuid as _uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Enum as SAEnum, Text, DateTime
from sqlmodel import Field, JSON, Relationship
from uuid_extensions import uuid7

from app.models.base import TenantSchemaModel

from app.models.tenant.answer import SubmissionAnswer


class SubmissionStatus(str, Enum):
    """Workflow status for form submissions."""
    
    DRAFT = "draft"                    # User started but not submitted
    SUBMITTED = "submitted"            # User completed and submitted
    UNDER_REVIEW = "under_review"      # Being reviewed by approver
    APPROVED = "approved"              # Approved by reviewer
    REJECTED = "rejected"              # Rejected by reviewer
    RETURNED = "returned"              # Returned to user for corrections
    COMPLETED = "completed"            # Final state after approval actions
    CANCELLED = "cancelled"            # User cancelled the submission


class Submission(TenantSchemaModel, table=True):
    """A form submission instance — captures user-submitted data for a template.
    
    Each submission represents one user filling out one template form.
    The submission locks to a specific template version to ensure data
    integrity even if the template is updated later.
    
    Key Fields:
    - template_id: The tenant template being filled out
    - template_version_id: The EXACT version used (immutable after submission)
    - baseline_version_id: If template extends baseline, tracks which baseline version
    - form_data: The actual submitted form values (JSON)
    - computed_data: Any derived/calculated values from rules engine
    - status: Current workflow status
    """

    __tablename__ = "submissions"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )
    
    # Reference to the template being filled out
    template_id: _uuid.UUID = Field(
        foreign_key="tenant_templates.id",
        index=True,
        nullable=False,
    )
    
    # Lock to specific version used at submission time
    template_version_id: _uuid.UUID = Field(
        foreign_key="tenant_template_definitions.id",
        index=True,
        nullable=False,
        description="The exact template version used - locked at submission time",
    )
    
    # If template extends a baseline, track which baseline version was active
    baseline_version_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="public.baseline_template_definitions.id",
        description="Baseline version if template extends a baseline",
    )
    
    # The actual form data submitted by user
    # DEPRECATED: Use submission_answers table instead. Kept only for
    # backward-compatibility during transition; will be removed once all
    # clients write to submission_answers.
    form_data: Dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        description="DEPRECATED — use answers relationship / submission_answers table.",
    )
    
    # Computed/derived data from rules engine
    computed_data: Dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        description="Calculated values from business rules",
    )
    
    # Validation results from rules engine
    validation_results: Dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        description="Validation results and any errors/warnings",
    )
    
    # File attachments metadata (actual files stored elsewhere)
    attachments: Dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        description="Metadata for uploaded files (paths, checksums, etc.)",
    )
    
    # Workflow status
    status: SubmissionStatus = Field(
        default=SubmissionStatus.DRAFT,
        sa_column=Column(
            SAEnum(
                SubmissionStatus,
                values_callable=lambda obj: [e.value for e in obj],
                name="submissionstatus",
                create_type=False,
            ),
            nullable=False,
            index=True,
            server_default="draft",
        )
    )
    
    # Submitter information (may differ from created_by for delegated submissions)
    submitter_id: Optional[str] = Field(
        default=None,
        max_length=255,
        index=True,
        description="ID of the person this submission is for/about",
    )
    
    # Timestamps for workflow stages
    submitted_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    reviewed_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    completed_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    
    # Reviewer information
    reviewed_by: Optional[str] = Field(default=None, max_length=255)
    review_notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    
    # External reference (e.g., customer ID, application number)
    external_ref: Optional[str] = Field(
        default=None,
        max_length=255,
        index=True,
        description="External reference ID for integration",
    )

    # Optional product association for product-specific onboarding traceability
    product_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="products.id",
        index=True,
        description="Set when this submission is for a product-specific onboarding flow.",
    )
    
    # Relationship to status history
    status_history: List["SubmissionStatusHistory"] = Relationship(
        back_populates="submission",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "SubmissionStatusHistory.created_at",
        },
    )

    answers: List["SubmissionAnswer"] = Relationship(
        back_populates=None,
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @property
    def maker_id(self) -> str:
        """Semantic alias for the maker (creator) user id.

        The underlying persisted audit field is `created_by`.
        """
        return self.created_by


class SubmissionStatusHistory(TenantSchemaModel, table=True):
    """Audit trail of submission status changes.
    
    Every status transition is logged with who made it, when, and why.
    This provides a complete audit trail for compliance.
    """

    __tablename__ = "submission_status_history"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )
    
    submission_id: _uuid.UUID = Field(
        foreign_key="submissions.id",
        index=True,
        nullable=False,
    )
    
    # Status transition
    from_status: Optional[SubmissionStatus] = Field(
        default=None,
        sa_column=Column(
            SAEnum(
                SubmissionStatus,
                values_callable=lambda obj: [e.value for e in obj],
                name="submissionstatus",
                create_type=False,
            ),
            nullable=True,
        )
    )
    to_status: SubmissionStatus = Field(
        sa_column=Column(
            SAEnum(
                SubmissionStatus,
                values_callable=lambda obj: [e.value for e in obj],
                name="submissionstatus",
                create_type=False,
            ),
            nullable=False,
        )
    )
    
    # Who made the change and why
    changed_by: str = Field(max_length=255, nullable=False)
    reason: Optional[str] = Field(default=None, sa_column=Column(Text))
    
    # Additional context (e.g., rejection reasons, return instructions)
    extra_data: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    
    # Relationship back to submission
    submission: "Submission" = Relationship(back_populates="status_history")


class SubmissionComment(TenantSchemaModel, table=True):
    """Comments/notes on submissions for collaboration.
    
    Allows reviewers and submitters to communicate about a submission.
    """

    __tablename__ = "submission_comments"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )
    
    submission_id: _uuid.UUID = Field(
        foreign_key="submissions.id",
        index=True,
        nullable=False,
    )
    
    # Comment content
    content: str = Field(sa_column=Column(Text, nullable=False))
    
    # Optional reference to specific form field
    field_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="ID of the form field this comment relates to",
    )
    
    # Visibility (internal = reviewers only, external = visible to submitter)
    is_internal: bool = Field(default=False)
    
    # Parent comment for threading
    parent_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="submission_comments.id",
    )
