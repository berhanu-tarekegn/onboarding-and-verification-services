"""Temporal workflow for configurable submission verification."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities.verification import (
        advance_verification_run_activity,
        apply_verification_action_activity,
    )
    from app.temporal.models.verification import (
        VerificationWorkflowAction,
        VerificationWorkflowInput,
        VerificationWorkflowState,
    )


@workflow.defn
class SubmissionVerificationWorkflow:
    """Orchestrates a verification run until it completes or waits for input."""

    def __init__(self) -> None:
        self._resume_requested = False
        self._pending_actions: list[VerificationWorkflowAction] = []

    @workflow.signal
    def resume(self) -> None:
        self._resume_requested = True

    @workflow.signal
    def submit_action(self, action: VerificationWorkflowAction) -> None:
        self._pending_actions.append(action)

    @workflow.run
    async def run(self, input: VerificationWorkflowInput) -> VerificationWorkflowState:
        state = await workflow.execute_activity(
            advance_verification_run_activity,
            input,
            start_to_close_timeout=timedelta(seconds=30),
        )

        while state.is_active:
            await workflow.wait_condition(lambda: self._resume_requested or bool(self._pending_actions))

            while self._pending_actions:
                action = self._pending_actions.pop(0)
                await workflow.execute_activity(
                    apply_verification_action_activity,
                    input,
                    action,
                    start_to_close_timeout=timedelta(seconds=30),
                )

            self._resume_requested = False
            state = await workflow.execute_activity(
                advance_verification_run_activity,
                input,
                start_to_close_timeout=timedelta(seconds=30),
            )

        return state
