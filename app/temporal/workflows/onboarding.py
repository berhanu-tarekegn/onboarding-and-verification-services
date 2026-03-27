"""Onboarding workflow — runs inside the Temporal sandbox.

Rules for workflow code:
  ✅ Import temporalio.workflow, dataclasses, typing
  ✅ Import activity *function references* (for execute_activity)
  ❌ No direct I/O (network, disk, DB)
  ❌ No non-deterministic calls (random, datetime.now, uuid4)
"""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities.onboarding import greet_user
    from app.temporal.models.onboarding import OnboardingInput, OnboardingResult


@workflow.defn
class OnboardingWorkflow:
    """Orchestrates the end-to-end customer onboarding lifecycle.

    Current implementation is a placeholder that calls a single activity.
    Will be expanded to the full multi-step flow:
      1. Fetch template rules (pinned definition)
      2. Wait for user data submission (signal)
      3. Trigger verification activities
      4. Wait for provider results (signal)
      5. Evaluate decision
      6. Notify bank core / escalate to manual review
    """

    @workflow.run
    async def run(self, input: OnboardingInput) -> OnboardingResult:
        workflow.logger.info(
            "Onboarding workflow started for user=%s tenant=%s",
            input.user_id,
            input.tenant_id,
        )

        # Phase 1 placeholder: call a single greeting activity
        greeting = await workflow.execute_activity(
            greet_user,
            input,
            start_to_close_timeout=timedelta(seconds=30),
        )

        workflow.logger.info("Onboarding workflow completed: %s", greeting)

        return OnboardingResult(
            workflow_id=workflow.info().workflow_id,
            status="completed",
            message=greeting,
        )
