"""Verification runtime models for configurable onboarding checks.

These models capture execution state for verification flows pinned to a
submission's template version. They are intentionally runtime-focused:

- ``VerificationRun`` tracks the overall flow for one submission.
- ``VerificationStepRun`` tracks the status and normalized outputs for each step.

The actual flow definition remains in the pinned template ``rules_config``.
"""

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, JSON
from uuid_extensions import uuid7

from app.models.base import TenantSchemaModel


class VerificationRun(TenantSchemaModel, table=True):
    """Execution state for one verification flow instance."""

    __tablename__ = "verification_runs"

    id: _uuid.UUID = Field(default_factory=uuid7, primary_key=True, nullable=False)

    submission_id: _uuid.UUID = Field(
        foreign_key="submissions.id",
        index=True,
        nullable=False,
    )
    template_version_id: _uuid.UUID = Field(
        foreign_key="tenant_template_definitions.id",
        index=True,
        nullable=False,
    )

    flow_key: str = Field(default="default", max_length=100, index=True)
    journey: str = Field(
        default="self_service_online",
        max_length=50,
        description="self_service_online | agent_assisted_offline | custom",
    )
    status: str = Field(
        default="pending",
        max_length=50,
        index=True,
        description="pending | in_progress | waiting_user_action | completed | failed | manual_review | cancelled | expired",
    )
    decision: Optional[str] = Field(
        default=None,
        max_length=50,
        description="approved | manual_review | rejected | pending",
    )
    kyc_level: Optional[str] = Field(default=None, max_length=100)
    current_step_key: Optional[str] = Field(default=None, max_length=255)
    workflow_id: Optional[str] = Field(default=None, max_length=255, index=True)
    workflow_run_id: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = Field(default=True, index=True)

    rules_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    facts_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    result_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        description="UTC timestamp for the first time the flow was started.",
    )
    completed_at: Optional[datetime] = Field(default=None)
    deferred_until: Optional[datetime] = Field(default=None)


class VerificationStepRun(TenantSchemaModel, table=True):
    """Runtime state for one configured verification step."""

    __tablename__ = "verification_step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_key", name="uq_verification_step_run_key"),
    )

    id: _uuid.UUID = Field(default_factory=uuid7, primary_key=True, nullable=False)

    run_id: _uuid.UUID = Field(
        foreign_key="verification_runs.id",
        index=True,
        nullable=False,
    )
    submission_id: _uuid.UUID = Field(
        foreign_key="submissions.id",
        index=True,
        nullable=False,
    )

    step_key: str = Field(max_length=255, index=True, nullable=False)
    display_name: Optional[str] = Field(default=None, max_length=255)
    step_type: str = Field(max_length=50, nullable=False)
    adapter_key: str = Field(max_length=100, nullable=False)

    status: str = Field(
        default="pending",
        max_length=50,
        index=True,
        description="pending | in_progress | waiting_user_action | completed | failed | skipped | expired",
    )
    outcome: Optional[str] = Field(
        default=None,
        max_length=50,
        description="pass | review | fail | pending",
    )
    attempt_count: int = Field(default=0, nullable=False)
    waiting_for: Optional[str] = Field(default=None, max_length=100)
    correlation_id: Optional[str] = Field(default=None, max_length=255, index=True)

    depends_on: list[str] = Field(default_factory=list, sa_type=JSON)
    config_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    input_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    output_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    result_snapshot: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    action_schema: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    error_details: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)

    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)
