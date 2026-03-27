"""Version Diff Service — auto-generate a draft TransformRuleSet.

Given two published TenantTemplateDefinitions belonging to the same template,
this service compares their question sets (keyed by unique_key) and produces a
draft TransformRuleSet with best-effort rules.  A human must review the draft
before publishing it.

Diff algorithm
--------------
1.  Build a map of {unique_key: Question} for both source and target versions.
2.  For each unique_key present in BOTH versions:
    a. Same field_type                → IDENTITY rule
    b. Different field_type           → COERCE_TYPE rule (requires manual review)
    c. Option changes (dropdown etc.) → MAP_VALUES rule appended on top
3.  For each unique_key ONLY in source → DROP rule (flagged for review).
4.  For each unique_key ONLY in target → DEFAULT_VALUE rule (value=null, review needed).
5.  Check Question.rules["transform_hints"] for additional guidance:
    - renamed_from   → emit RENAME rule instead of DROP+DEFAULT_VALUE pair
    - value_mapping  → pre-fill MAP_VALUES params

The resulting TransformRuleSet has auto_generated=True and status=DRAFT.
"""

from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import RuleSetStatus, TransformOperation
from app.models.tenant.template import (
    Question,
    QuestionGroup,
    TenantTemplateDefinition,
)
from app.models.tenant.transform import TransformRule, TransformRuleSet


# ── Helpers ───────────────────────────────────────────────────────────

async def _load_version_questions(
    version_id: UUID,
    session: AsyncSession,
) -> Dict[str, Question]:
    """Return {unique_key: Question} for all questions in a version."""
    result = await session.execute(
        select(TenantTemplateDefinition)
        .where(TenantTemplateDefinition.id == version_id)
        .options(
            selectinload(TenantTemplateDefinition.question_groups)
            .selectinload(QuestionGroup.questions),
            selectinload(TenantTemplateDefinition.ungrouped_questions),
        )
    )
    version = result.scalars().first()
    if not version:
        return {}

    questions: Dict[str, Question] = {}
    for group in version.question_groups:
        for q in group.questions:
            questions[q.unique_key] = q
    for q in version.ungrouped_questions:
        questions[q.unique_key] = q
    return questions


def _option_values(q: Question) -> set:
    return {opt.value for opt in q.options}


def _transform_hints(q: Question) -> dict:
    """Extract transform_hints from Question.rules if present."""
    if not q.rules:
        return {}
    return q.rules.get("transform_hints", {})


def _build_coerce_params(from_type: str, to_type: str) -> dict:
    params: dict = {"from_type": from_type, "to_type": to_type}
    if from_type == "text" and to_type == "date":
        params["format"] = "YYYY-MM-DD"
    return params


# ── Public entry point ────────────────────────────────────────────────

async def generate_rule_set(
    template_id: UUID,
    source_version_id: UUID,
    target_version_id: UUID,
    changelog: Optional[str],
    session: AsyncSession,
) -> TransformRuleSet:
    """Auto-generate a draft TransformRuleSet by diffing two versions.

    Raises 409 if a ruleset for this version pair already exists.
    Raises 422 if either version does not belong to the template.
    """
    # Guard: check versions belong to the template
    for vid in (source_version_id, target_version_id):
        result = await session.execute(
            select(TenantTemplateDefinition).where(
                TenantTemplateDefinition.id == vid,
                TenantTemplateDefinition.template_id == template_id,
            )
        )
        if not result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Version {vid} does not belong to template {template_id}.",
            )

    # Guard: uniqueness
    existing = await session.execute(
        select(TransformRuleSet).where(
            TransformRuleSet.source_version_id == source_version_id,
            TransformRuleSet.target_version_id == target_version_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A TransformRuleSet already exists for this source→target version pair.",
        )

    src_questions = await _load_version_questions(source_version_id, session)
    tgt_questions = await _load_version_questions(target_version_id, session)

    rules: List[TransformRule] = []
    order = 0

    # ── Build rename index from transform_hints in target questions ──
    # If a target question declares renamed_from = "old_key", we treat it
    # as a RENAME rather than DROP+DEFAULT_VALUE pair.
    renamed_from: Dict[str, str] = {}  # {source_unique_key: target_unique_key}
    for tgt_key, tgt_q in tgt_questions.items():
        hints = _transform_hints(tgt_q)
        if "renamed_from" in hints:
            renamed_from[hints["renamed_from"]] = tgt_key

    # ── Step 1: questions present in source ──────────────────────────
    handled_sources: set = set()

    for src_key, src_q in src_questions.items():
        # Was this key renamed to a target key?
        if src_key in renamed_from:
            tgt_key = renamed_from[src_key]
            tgt_q = tgt_questions[tgt_key]
            rules.append(TransformRule(
                source_unique_key=src_key,
                target_unique_key=tgt_key,
                operation=TransformOperation.RENAME,
                params={},
                display_order=order,
                is_required=False,
            ))
            order += 1
            handled_sources.add(src_key)
            continue

        if src_key in tgt_questions:
            tgt_q = tgt_questions[src_key]
            if src_q.field_type == tgt_q.field_type:
                # Same type — check if options changed
                src_opts = _option_values(src_q)
                tgt_opts = _option_values(tgt_q)
                if src_opts and tgt_opts and src_opts != tgt_opts:
                    # Build best-effort value mapping: identical values pass through,
                    # removed values map to null (needs manual review).
                    hints = _transform_hints(src_q)
                    hint_mapping: dict = hints.get("value_mapping", {})
                    mapping = {v: hint_mapping.get(v, v if v in tgt_opts else None)
                               for v in src_opts}
                    rules.append(TransformRule(
                        source_unique_key=src_key,
                        target_unique_key=src_key,
                        operation=TransformOperation.MAP_VALUES,
                        params={"mapping": mapping, "default": None},
                        display_order=order,
                        is_required=False,
                    ))
                else:
                    rules.append(TransformRule(
                        source_unique_key=src_key,
                        target_unique_key=src_key,
                        operation=TransformOperation.IDENTITY,
                        params={},
                        display_order=order,
                        is_required=False,
                    ))
            else:
                # Field type changed — need coercion review
                rules.append(TransformRule(
                    source_unique_key=src_key,
                    target_unique_key=src_key,
                    operation=TransformOperation.COERCE_TYPE,
                    params=_build_coerce_params(src_q.field_type, tgt_q.field_type),
                    display_order=order,
                    is_required=False,
                ))
            order += 1
            handled_sources.add(src_key)
        else:
            # Source key not in target — DROP
            rules.append(TransformRule(
                source_unique_key=src_key,
                target_unique_key=src_key,
                operation=TransformOperation.DROP,
                params={"reason": "Question removed in target version — review required."},
                display_order=order,
                is_required=False,
            ))
            order += 1
            handled_sources.add(src_key)

    # ── Step 2: new questions in target (not covered by renames) ─────
    renamed_targets = set(renamed_from.values())
    for tgt_key, tgt_q in tgt_questions.items():
        if tgt_key in src_questions or tgt_key in renamed_targets:
            continue
        rules.append(TransformRule(
            source_unique_key=None,
            target_unique_key=tgt_key,
            operation=TransformOperation.DEFAULT_VALUE,
            params={"value": None},  # null default — reviewer must fill in
            display_order=order,
            is_required=False,
        ))
        order += 1

    rule_set = TransformRuleSet(
        template_id=template_id,
        source_version_id=source_version_id,
        target_version_id=target_version_id,
        status=RuleSetStatus.DRAFT,
        auto_generated=True,
        changelog=changelog,
        rules=rules,
    )
    session.add(rule_set)
    await session.commit()
    await session.refresh(rule_set)
    return rule_set
