"""Temporal dataclasses for submission verification workflows."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerificationWorkflowInput:
    run_id: str
    tenant_key: str


@dataclass
class VerificationWorkflowAction:
    step_key: str
    action: str = "submit_code"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationWorkflowState:
    run_id: str
    status: str
    current_step_key: str | None = None
    decision: str | None = None
    kyc_level: str | None = None
    is_active: bool = True
