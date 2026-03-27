"""Onboarding activities — these run outside the Temporal sandbox.

Activities *can* perform I/O (database queries, HTTP calls, file access).
Each activity should be idempotent where possible.
"""

from temporalio import activity

from app.temporal.models.onboarding import OnboardingInput


@activity.defn
async def greet_user(input: OnboardingInput) -> str:
    """Placeholder activity — will evolve into real verification steps.

    In the final system this will be replaced by activities like:
      - fetch_template_rules
      - trigger_document_verification
      - evaluate_decision
      - notify_bank_core
    """
    activity.logger.info(
        "Starting onboarding for user=%s tenant=%s definition=%s",
        input.user_id,
        input.tenant_id,
        input.definition_id,
    )
    return (
        f"Onboarding initiated for user {input.user_id} "
        f"(tenant: {input.tenant_id}, definition: {input.definition_id})"
    )
