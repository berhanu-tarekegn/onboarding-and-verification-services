"""Transform Rule Service — CRUD for TransformRuleSets and TransformRules.

All mutating operations are restricted to DRAFT rule sets.
Publishing freezes the rule set and blocks further edits.

Sandbox validation:
- Individual rules are validated (params shape + COMPUTE expression) on
  create and update.
- The full rule set is dry-run against synthetic data on publish.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import RuleSetStatus, TransformOperation
from app.models.tenant.transform import TransformLog, TransformRule, TransformRuleSet
from app.schemas.transforms.rule import (
    TransformRuleCreate,
    TransformRuleSetCreate,
    TransformRuleSetUpdate,
    TransformRuleUpdate,
)
from app.services.transforms.sandbox import (
    sandbox_validate_rules,
    validate_compute_expression,
)


# ── Helpers ───────────────────────────────────────────────────────────

async def _get_rule_set(
    rule_set_id: UUID,
    session: AsyncSession,
    load_rules: bool = False,
) -> TransformRuleSet:
    query = select(TransformRuleSet).where(TransformRuleSet.id == rule_set_id)
    if load_rules:
        query = query.options(selectinload(TransformRuleSet.rules))
    result = await session.execute(query)
    rs = result.scalars().first()
    if not rs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TransformRuleSet not found.",
        )
    return rs


def _assert_draft(rule_set: TransformRuleSet) -> None:
    if rule_set.status != RuleSetStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify a published or archived rule set.",
        )


def _validate_compute_rule(
    operation: TransformOperation,
    params: dict,
    source_unique_key: str | None,
) -> None:
    """Validate a COMPUTE rule's expression via simpleeval sandbox.

    Raises HTTPException(422) if the expression fails evaluation.
    """
    if operation != TransformOperation.COMPUTE:
        return
    expr = params.get("expr", "")
    sources: list = params.get("sources", [source_unique_key] if source_unique_key else [])
    errors = validate_compute_expression(expr, sources)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "COMPUTE expression validation failed.",
                "errors": errors,
            },
        )


# ── RuleSet CRUD ──────────────────────────────────────────────────────

async def create_rule_set(
    template_id: UUID,
    data: TransformRuleSetCreate,
    session: AsyncSession,
) -> TransformRuleSet:
    """Manually create a draft TransformRuleSet with optional initial rules."""
    # Guard uniqueness
    existing = await session.execute(
        select(TransformRuleSet).where(
            TransformRuleSet.source_version_id == data.source_version_id,
            TransformRuleSet.target_version_id == data.target_version_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A TransformRuleSet already exists for this source→target version pair.",
        )

    for rule_data in data.rules:
        _validate_compute_rule(
            rule_data.operation, rule_data.params, rule_data.source_unique_key,
        )

    rule_set = TransformRuleSet(
        template_id=template_id,
        source_version_id=data.source_version_id,
        target_version_id=data.target_version_id,
        status=RuleSetStatus.DRAFT,
        auto_generated=False,
        changelog=data.changelog,
    )
    for i, rule_data in enumerate(data.rules):
        rule = TransformRule(
            source_unique_key=rule_data.source_unique_key,
            target_unique_key=rule_data.target_unique_key,
            operation=rule_data.operation,
            params=rule_data.params,
            display_order=rule_data.display_order if rule_data.display_order else i,
            is_required=rule_data.is_required,
        )
        rule_set.rules.append(rule)

    session.add(rule_set)
    await session.commit()
    await session.refresh(rule_set)
    return rule_set


async def list_rule_sets(
    template_id: UUID,
    session: AsyncSession,
) -> List[TransformRuleSet]:
    result = await session.execute(
        select(TransformRuleSet)
        .where(TransformRuleSet.template_id == template_id)
        .options(selectinload(TransformRuleSet.rules))
        .order_by(TransformRuleSet.created_at.desc())
    )
    return list(result.scalars().all())


async def get_rule_set(
    rule_set_id: UUID,
    session: AsyncSession,
) -> TransformRuleSet:
    return await _get_rule_set(rule_set_id, session, load_rules=True)


async def update_rule_set(
    rule_set_id: UUID,
    data: TransformRuleSetUpdate,
    session: AsyncSession,
) -> TransformRuleSet:
    """Update the metadata of a DRAFT rule set (changelog only for now)."""
    rule_set = await _get_rule_set(rule_set_id, session)
    _assert_draft(rule_set)
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(rule_set, key, value)
    session.add(rule_set)
    await session.commit()
    await session.refresh(rule_set)
    return rule_set


async def publish_rule_set(
    rule_set_id: UUID,
    session: AsyncSession,
) -> TransformRuleSet:
    """Freeze a DRAFT rule set so it can be applied to submissions.

    Before publishing, runs a full sandbox dry-run of every rule against
    synthetic data derived from the source/target version questions.
    Rejects the publish if any rule fails validation.
    """
    from app.services.transforms.executor import _load_version_question_map

    rule_set = await _get_rule_set(rule_set_id, session, load_rules=True)
    _assert_draft(rule_set)
    if not rule_set.rules:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot publish a rule set with no rules.",
        )

    src_questions = await _load_version_question_map(
        rule_set.source_version_id, session,
    )
    tgt_questions = await _load_version_question_map(
        rule_set.target_version_id, session,
    )

    result = sandbox_validate_rules(
        rule_set.rules, src_questions, tgt_questions,
    )
    if not result.valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Sandbox validation failed. Fix the following errors before publishing.",
                "valid": False,
                "errors": result.errors,
                "rule_results": [
                    {
                        "rule_index": rr.rule_index,
                        "target_unique_key": rr.target_unique_key,
                        "operation": rr.operation,
                        "success": rr.success,
                        "errors": rr.errors,
                        "warnings": rr.warnings,
                    }
                    for rr in result.rule_results
                    if not rr.success
                ],
            },
        )

    rule_set.status = RuleSetStatus.PUBLISHED
    session.add(rule_set)
    await session.commit()
    await session.refresh(rule_set)
    return rule_set


async def archive_rule_set(
    rule_set_id: UUID,
    session: AsyncSession,
) -> TransformRuleSet:
    """Archive a PUBLISHED rule set (e.g. superseded by a newer one)."""
    rule_set = await _get_rule_set(rule_set_id, session)
    if rule_set.status == RuleSetStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Publish the rule set before archiving.",
        )
    rule_set.status = RuleSetStatus.ARCHIVED
    session.add(rule_set)
    await session.commit()
    await session.refresh(rule_set)
    return rule_set


async def delete_rule_set(
    rule_set_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a DRAFT rule set. Published / archived sets cannot be deleted."""
    rule_set = await _get_rule_set(rule_set_id, session)
    _assert_draft(rule_set)
    await session.delete(rule_set)
    await session.commit()


# ── Rule CRUD (within a draft rule set) ──────────────────────────────

async def add_rule(
    rule_set_id: UUID,
    data: TransformRuleCreate,
    session: AsyncSession,
) -> TransformRule:
    """Append a new rule to a DRAFT rule set."""
    rule_set = await _get_rule_set(rule_set_id, session, load_rules=True)
    _assert_draft(rule_set)

    _validate_compute_rule(data.operation, data.params, data.source_unique_key)

    next_order = max((r.display_order for r in rule_set.rules), default=-1) + 1
    rule = TransformRule(
        rule_set_id=rule_set_id,
        source_unique_key=data.source_unique_key,
        target_unique_key=data.target_unique_key,
        operation=data.operation,
        params=data.params,
        display_order=data.display_order if data.display_order else next_order,
        is_required=data.is_required,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def update_rule(
    rule_id: UUID,
    data: TransformRuleUpdate,
    session: AsyncSession,
) -> TransformRule:
    """Patch an existing rule in a DRAFT rule set."""
    result = await session.execute(
        select(TransformRule).where(TransformRule.id == rule_id)
    )
    rule = result.scalars().first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TransformRule not found.",
        )
    rule_set = await _get_rule_set(rule.rule_set_id, session)
    _assert_draft(rule_set)

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(rule, key, value)

    _validate_compute_rule(
        rule.operation,
        rule.params or {},
        rule.source_unique_key,
    )

    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def delete_rule(
    rule_id: UUID,
    session: AsyncSession,
) -> None:
    """Remove a rule from a DRAFT rule set."""
    result = await session.execute(
        select(TransformRule).where(TransformRule.id == rule_id)
    )
    rule = result.scalars().first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TransformRule not found.",
        )
    rule_set = await _get_rule_set(rule.rule_set_id, session)
    _assert_draft(rule_set)
    await session.delete(rule)
    await session.commit()


# ── Transform logs ────────────────────────────────────────────────────

async def list_submission_logs(
    submission_id: UUID,
    session: AsyncSession,
) -> List[TransformLog]:
    result = await session.execute(
        select(TransformLog)
        .where(TransformLog.submission_id == submission_id)
        .order_by(TransformLog.applied_at.desc())
    )
    return list(result.scalars().all())
