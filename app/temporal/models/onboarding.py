"""Data models for the onboarding workflow.

Using @dataclass (not Pydantic) as recommended by the Temporal Python SDK
for backward-compatible schema evolution. Fields can be added over time
without breaking running workflows.
"""

from dataclasses import dataclass


@dataclass
class OnboardingInput:
    """Input to start an onboarding workflow.

    Attributes:
        user_id:        Unique identifier for the user being onboarded.
        tenant_id:      Tenant schema_name this onboarding belongs to.
        definition_id:  Pinned TemplateDefinition UUID at workflow start time.
    """

    user_id: str
    tenant_id: str
    definition_id: str


@dataclass
class OnboardingResult:
    """Final result returned by the onboarding workflow.

    Attributes:
        workflow_id:  The Temporal workflow execution ID.
        status:       Terminal status (e.g. "completed", "rejected", "pending_review").
        message:      Human-readable summary of what happened.
    """

    workflow_id: str
    status: str
    message: str
