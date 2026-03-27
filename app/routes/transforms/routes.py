"""Transform routes — define and apply answer migration rules between template versions.

All routes are scoped to the current tenant via X-Tenant-ID header.

Route groups
------------
Rule-set management
  POST   /templates/{id}/transform-rules/generate          Auto-generate from version diff
  POST   /templates/{id}/transform-rules                   Create manually
  GET    /templates/{id}/transform-rules                   List all rule sets
  GET    /templates/{id}/transform-rules/{rsid}            Get one rule set
  PATCH  /templates/{id}/transform-rules/{rsid}            Update metadata (draft only)
  POST   /templates/{id}/transform-rules/{rsid}/publish    Freeze rule set
  POST   /templates/{id}/transform-rules/{rsid}/archive    Archive rule set
  DELETE /templates/{id}/transform-rules/{rsid}            Delete draft rule set

Per-rule management (within a draft rule set)
  POST   /templates/{id}/transform-rules/{rsid}/rules            Add a rule
  PATCH  /templates/{id}/transform-rules/{rsid}/rules/{rid}      Update a rule
  DELETE /templates/{id}/transform-rules/{rsid}/rules/{rid}      Delete a rule

Apply / preview
  POST   /templates/{id}/transform-rules/{rsid}/preview          Dry-run on one submission
  POST   /templates/{id}/transform-rules/{rsid}/apply/{sub_id}   Apply to one submission
  POST   /templates/{id}/transform-rules/{rsid}/bulk-apply       Bulk migrate eligible submissions

Audit
  GET    /submissions/{id}/transform-history                      Transform log for a submission
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dependencies import require_tenant_header
from app.db.session import tenant_session_for_permissions
from app.schemas.transforms import (
    BulkMigrateRequest,
    BulkMigrateResult,
    TransformLogRead,
    TransformPreviewRequest,
    TransformPreviewResult,
    TransformRuleCreate,
    TransformRuleRead,
    TransformRuleSetCreate,
    TransformRuleSetGenerateRequest,
    TransformRuleSetRead,
    TransformRuleSetUpdate,
    TransformRuleUpdate,
)
from app.services.transforms import bulk_migrate as bulk_migrate_svc
from app.services.transforms import diff_service
from app.services.transforms import executor
from app.services.transforms import rule_service

router = APIRouter(
    tags=["transform-rules"],
    dependencies=[Depends(require_tenant_header)],
)


# ── Rule-set management ───────────────────────────────────────────────

@router.post(
    "/templates/{template_id}/transform-rules/generate",
    response_model=TransformRuleSetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Auto-generate a draft rule set by diffing two template versions",
)
async def generate_rule_set(
    template_id: UUID,
    data: TransformRuleSetGenerateRequest,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> TransformRuleSetRead:
    """Compare source and target versions question-by-question and produce a
    draft TransformRuleSet with best-effort rules.

    The returned rule set has status=DRAFT and auto_generated=True.
    Review and adjust the rules, then call /publish before applying.
    """
    return await diff_service.generate_rule_set(
        template_id=template_id,
        source_version_id=data.source_version_id,
        target_version_id=data.target_version_id,
        changelog=data.changelog,
        session=session,
    )


@router.post(
    "/templates/{template_id}/transform-rules",
    response_model=TransformRuleSetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Manually create a transform rule set",
)
async def create_rule_set(
    template_id: UUID,
    data: TransformRuleSetCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> TransformRuleSetRead:
    """Create a draft TransformRuleSet with an optional initial set of rules."""
    return await rule_service.create_rule_set(
        template_id=template_id,
        data=data,
        session=session,
    )


@router.get(
    "/templates/{template_id}/transform-rules",
    response_model=list[TransformRuleSetRead],
    summary="List all transform rule sets for a template",
)
async def list_rule_sets(
    template_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
) -> list[TransformRuleSetRead]:
    return await rule_service.list_rule_sets(template_id=template_id, session=session)


@router.get(
    "/templates/{template_id}/transform-rules/{rule_set_id}",
    response_model=TransformRuleSetRead,
    summary="Get a transform rule set with its rules",
)
async def get_rule_set(
    template_id: UUID,
    rule_set_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
) -> TransformRuleSetRead:
    return await rule_service.get_rule_set(rule_set_id=rule_set_id, session=session)


@router.patch(
    "/templates/{template_id}/transform-rules/{rule_set_id}",
    response_model=TransformRuleSetRead,
    summary="Update rule set metadata (draft only)",
)
async def update_rule_set(
    template_id: UUID,
    rule_set_id: UUID,
    data: TransformRuleSetUpdate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> TransformRuleSetRead:
    return await rule_service.update_rule_set(
        rule_set_id=rule_set_id, data=data, session=session
    )


@router.post(
    "/templates/{template_id}/transform-rules/{rule_set_id}/publish",
    response_model=TransformRuleSetRead,
    summary="Publish (freeze) a draft rule set",
)
async def publish_rule_set(
    template_id: UUID,
    rule_set_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.publish")),
) -> TransformRuleSetRead:
    """Freeze the rule set. No further edits are possible after publishing."""
    return await rule_service.publish_rule_set(rule_set_id=rule_set_id, session=session)


@router.post(
    "/templates/{template_id}/transform-rules/{rule_set_id}/archive",
    response_model=TransformRuleSetRead,
    summary="Archive a published rule set",
)
async def archive_rule_set(
    template_id: UUID,
    rule_set_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.publish")),
) -> TransformRuleSetRead:
    return await rule_service.archive_rule_set(rule_set_id=rule_set_id, session=session)


@router.delete(
    "/templates/{template_id}/transform-rules/{rule_set_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft rule set",
)
async def delete_rule_set(
    template_id: UUID,
    rule_set_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> None:
    await rule_service.delete_rule_set(rule_set_id=rule_set_id, session=session)


# ── Per-rule management ───────────────────────────────────────────────

@router.post(
    "/templates/{template_id}/transform-rules/{rule_set_id}/rules",
    response_model=TransformRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a rule to a draft rule set",
)
async def add_rule(
    template_id: UUID,
    rule_set_id: UUID,
    data: TransformRuleCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> TransformRuleRead:
    return await rule_service.add_rule(
        rule_set_id=rule_set_id, data=data, session=session
    )


@router.patch(
    "/templates/{template_id}/transform-rules/{rule_set_id}/rules/{rule_id}",
    response_model=TransformRuleRead,
    summary="Update an existing rule in a draft rule set",
)
async def update_rule(
    template_id: UUID,
    rule_set_id: UUID,
    rule_id: UUID,
    data: TransformRuleUpdate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> TransformRuleRead:
    return await rule_service.update_rule(
        rule_id=rule_id, data=data, session=session
    )


@router.delete(
    "/templates/{template_id}/transform-rules/{rule_set_id}/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a rule from a draft rule set",
)
async def delete_rule(
    template_id: UUID,
    rule_set_id: UUID,
    rule_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
) -> None:
    await rule_service.delete_rule(rule_id=rule_id, session=session)


# ── Apply / preview ───────────────────────────────────────────────────

@router.post(
    "/templates/{template_id}/transform-rules/{rule_set_id}/preview",
    response_model=TransformPreviewResult,
    summary="Dry-run a transform on one submission (no data modified)",
)
async def preview_transform(
    template_id: UUID,
    rule_set_id: UUID,
    data: TransformPreviewRequest,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
) -> TransformPreviewResult:
    """Preview the before/after answer snapshots without persisting any changes.

    Use this to verify the rule set behaves correctly before applying it.
    """
    log = await executor.apply_rule_set(
        rule_set_id=rule_set_id,
        submission_id=data.submission_id,
        session=session,
        is_preview=True,
    )
    from app.services.submissions.answer_validator import validate_answers
    from app.schemas.submissions.answer import SubmissionAnswerCreate
    from app.services.transforms.executor import _load_version_question_map

    tgt_questions = await _load_version_question_map(log.target_version_id, session)
    answer_creates = [
        SubmissionAnswerCreate(
            question_id=tgt_questions[key].id,
            answer=value,
        )
        for key, value in log.after_snapshot.items()
        if key in tgt_questions
    ]
    validation_errors = await validate_answers(log.target_version_id, answer_creates, session)

    return TransformPreviewResult(
        submission_id=log.submission_id,
        rule_set_id=log.rule_set_id,
        source_version_id=log.source_version_id,
        target_version_id=log.target_version_id,
        before_snapshot=log.before_snapshot,
        after_snapshot=log.after_snapshot,
        errors=log.errors,
        warnings=log.warnings,
        validation_errors=validation_errors,
        would_succeed=not log.errors and not validation_errors,
    )


@router.post(
    "/templates/{template_id}/transform-rules/{rule_set_id}/apply/{submission_id}",
    response_model=TransformLogRead,
    summary="Apply a published rule set to a single submission",
)
async def apply_transform(
    template_id: UUID,
    rule_set_id: UUID,
    submission_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.publish")),
) -> TransformLogRead:
    """Migrate one submission's answers from the source version to the target version.

    Only DRAFT and RETURNED submissions are eligible.
    Returns the TransformLog audit record.
    """
    return await executor.apply_rule_set(
        rule_set_id=rule_set_id,
        submission_id=submission_id,
        session=session,
        is_preview=False,
    )


@router.post(
    "/templates/{template_id}/transform-rules/{rule_set_id}/bulk-apply",
    response_model=BulkMigrateResult,
    summary="Bulk-migrate all eligible submissions",
)
async def bulk_apply(
    template_id: UUID,
    rule_set_id: UUID,
    data: BulkMigrateRequest,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.publish")),
) -> BulkMigrateResult:
    """Migrate all DRAFT and RETURNED submissions locked to the source version.

    Set dry_run=true to preview results without modifying any data.
    Optionally pass submission_ids to restrict the migration to a specific subset.
    """
    return await bulk_migrate_svc.bulk_migrate(
        rule_set_id=rule_set_id,
        session=session,
        dry_run=data.dry_run,
        submission_ids=data.submission_ids,
    )


# ── Audit ─────────────────────────────────────────────────────────────

@router.get(
    "/submissions/{submission_id}/transform-history",
    response_model=list[TransformLogRead],
    summary="List all transform logs for a submission",
)
async def get_transform_history(
    submission_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
) -> list[TransformLogRead]:
    """Return the full transform audit trail for a submission, newest first."""
    return await rule_service.list_submission_logs(
        submission_id=submission_id, session=session
    )
