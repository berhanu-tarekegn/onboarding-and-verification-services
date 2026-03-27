"""Temporal activities for submission verification workflows."""

from temporalio import activity

from app.services.verifications import service as verification_svc
from app.temporal.models.verification import (
    VerificationWorkflowAction,
    VerificationWorkflowInput,
    VerificationWorkflowState,
)


@activity.defn
async def advance_verification_run_activity(
    input: VerificationWorkflowInput,
) -> VerificationWorkflowState:
    """Advance a verification run until it waits or reaches a terminal status."""
    activity.logger.info("Advancing verification run %s for tenant %s", input.run_id, input.tenant_key)
    return await verification_svc.advance_verification_run_in_tenant(
        run_id=input.run_id,
        tenant_key=input.tenant_key,
    )


@activity.defn
async def apply_verification_action_activity(
    input: VerificationWorkflowInput,
    action: VerificationWorkflowAction,
) -> VerificationWorkflowState:
    """Apply a user or agent action to a waiting verification step."""
    activity.logger.info(
        "Applying verification action for run %s step %s",
        input.run_id,
        action.step_key,
    )
    return await verification_svc.apply_verification_action_in_tenant(
        run_id=input.run_id,
        tenant_key=input.tenant_key,
        step_key=action.step_key,
        action=action.action,
        payload=action.payload,
    )
