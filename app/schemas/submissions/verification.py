"""Verification API schemas for submission-scoped flows."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import Field, SQLModel


class VerificationStartRequest(SQLModel):
    """Request body for starting or resuming a verification flow."""

    journey: str = Field(
        default="self_service_online",
        description="self_service_online | agent_assisted_offline | custom",
    )
    deferred: bool = Field(
        default=False,
        description="Create or keep the verification run in a deferred state without dispatching challenges yet.",
    )
    flow_key: str = Field(default="default", max_length=100)
    context: Dict[str, Any] = Field(default_factory=dict)


class VerificationActionRequest(SQLModel):
    """User or agent action applied to a waiting verification step."""

    action: str = Field(default="submit_code", max_length=100)
    payload: Dict[str, Any] = Field(default_factory=dict)


class VerificationStepRunRead(SQLModel):
    """Response model for a single verification step runtime row."""

    id: UUID
    run_id: UUID
    submission_id: UUID
    step_key: str
    display_name: Optional[str] = None
    step_type: str
    adapter_key: str
    status: str
    outcome: Optional[str] = None
    attempt_count: int
    waiting_for: Optional[str] = None
    correlation_id: Optional[str] = None
    input_snapshot: Dict[str, Any] = Field(default_factory=dict)
    output_snapshot: Dict[str, Any] = Field(default_factory=dict)
    result_snapshot: Dict[str, Any] = Field(default_factory=dict)
    action_schema: Dict[str, Any] = Field(default_factory=dict)
    error_details: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class VerificationStepSummaryRead(SQLModel):
    """Compact verification step view for submission list/search APIs."""

    step_key: str
    status: str
    outcome: Optional[str] = None
    result_snapshot: Dict[str, Any] = Field(default_factory=dict)


class VerificationRunSummaryRead(SQLModel):
    """Compact verification run view for submission list/search APIs."""

    id: UUID
    flow_key: str
    journey: str
    status: str
    decision: Optional[str] = None
    kyc_level: Optional[str] = None
    current_step_key: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    deferred_until: Optional[datetime] = None
    steps: List[VerificationStepSummaryRead] = Field(default_factory=list)


class VerificationRunRead(SQLModel):
    """Response model for a verification flow runtime instance."""

    id: UUID
    submission_id: UUID
    template_version_id: UUID
    flow_key: str
    journey: str
    status: str
    decision: Optional[str] = None
    kyc_level: Optional[str] = None
    current_step_key: Optional[str] = None
    is_active: bool
    rules_snapshot: Dict[str, Any] = Field(default_factory=dict)
    facts_snapshot: Dict[str, Any] = Field(default_factory=dict)
    result_snapshot: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    completed_at: Optional[datetime] = None
    deferred_until: Optional[datetime] = None
    steps: List[VerificationStepRunRead] = Field(default_factory=list)
