"""Bulk Migration Service — migrate all eligible submissions for a rule set.

Eligible submissions are those whose template_version_id matches the rule
set's source_version_id and whose status is DRAFT or RETURNED.

The service runs each submission through the executor.apply_rule_set function,
collecting per-submission results into a BulkMigrateResult summary.

dry_run=True mode previews every submission (writes logs with is_preview=True)
without modifying any submission data.
"""

from typing import List, Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import RuleSetStatus
from app.models.tenant.submission import Submission, SubmissionStatus
from app.models.tenant.transform import TransformRuleSet
from app.schemas.transforms.preview import (
    BulkMigrateResult,
    BulkMigrateSubmissionResult,
)
from app.services.transforms.executor import apply_rule_set


_ELIGIBLE_STATUSES = {SubmissionStatus.DRAFT, SubmissionStatus.RETURNED}


async def bulk_migrate(
    rule_set_id: UUID,
    session: AsyncSession,
    dry_run: bool = False,
    submission_ids: Optional[List[UUID]] = None,
) -> BulkMigrateResult:
    """Apply the given rule set to all eligible submissions.

    Args:
        rule_set_id: The published TransformRuleSet to apply.
        session: Async DB session for the current tenant.
        dry_run: If True, run in preview mode — no data is modified.
        submission_ids: Optional explicit list of submission IDs to process.
            If None, all eligible submissions are selected automatically.

    Returns:
        BulkMigrateResult with per-submission outcomes and a summary.
    """
    result = await session.execute(
        select(TransformRuleSet).where(TransformRuleSet.id == rule_set_id)
    )
    rule_set = result.scalars().first()
    if not rule_set:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TransformRuleSet not found.",
        )
    if rule_set.status != RuleSetStatus.PUBLISHED:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published rule sets can be applied. Publish it first.",
        )

    # Build the query for eligible submissions
    query = select(Submission).where(
        Submission.template_version_id == rule_set.source_version_id,
        Submission.status.in_([s.value for s in _ELIGIBLE_STATUSES]),
    )
    if submission_ids:
        query = query.where(Submission.id.in_(submission_ids))

    sub_result = await session.execute(query)
    submissions = list(sub_result.scalars().all())

    results: List[BulkMigrateSubmissionResult] = []
    succeeded = 0
    failed = 0
    skipped = 0

    for submission in submissions:
        # Skip if an explicit list was provided and this submission's status
        # is not eligible (shouldn't happen with query filter, but be safe)
        if submission.status not in _ELIGIBLE_STATUSES:
            skipped += 1
            results.append(BulkMigrateSubmissionResult(
                submission_id=submission.id,
                success=False,
                errors=[{"message": f"Skipped: status '{submission.status}' not eligible."}],
            ))
            continue

        try:
            log = await apply_rule_set(
                rule_set_id=rule_set_id,
                submission_id=submission.id,
                session=session,
                is_preview=dry_run,
            )
            has_blocking_errors = bool(log.errors)
            results.append(BulkMigrateSubmissionResult(
                submission_id=submission.id,
                success=not has_blocking_errors,
                errors=log.errors,
                warnings=log.warnings,
                log_id=log.id,
            ))
            if has_blocking_errors:
                failed += 1
            else:
                succeeded += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            detail = str(exc)
            if hasattr(exc, "detail"):
                detail = exc.detail  # type: ignore[attr-defined]
            results.append(BulkMigrateSubmissionResult(
                submission_id=submission.id,
                success=False,
                errors=[{"message": detail}],
            ))

    return BulkMigrateResult(
        rule_set_id=rule_set_id,
        dry_run=dry_run,
        total=len(submissions),
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        results=results,
    )
