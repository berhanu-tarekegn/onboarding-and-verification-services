"""Transform Executor — apply a published TransformRuleSet to a submission.

Steps
-----
1.  Load the submission and its answers (from submission_answers table).
2.  Build a {unique_key: answer_value} map using the source version's questions.
3.  Apply each TransformRule in display_order, producing a new answer map keyed
    by the TARGET version's unique_keys.
4.  Validate the new answers against the target version using answer_validator.
5.  If is_preview=True: write a TransformLog with is_preview=True and return;
    do NOT modify the submission.
6.  If not preview:
    a. Delete existing SubmissionAnswer rows for this submission.
    b. Insert new SubmissionAnswer rows pointing to target version question IDs.
    c. Update Submission.template_version_id to the target version.
    d. Write a TransformLog.

Operation implementations
-------------------------
IDENTITY      → copy answer verbatim
RENAME        → copy answer verbatim (keys differ)
MAP_VALUES    → apply params["mapping"]; fallback to params["default"]
COERCE_TYPE   → convert using params["from_type"/"to_type"/"format"]
SPLIT         → split source by params["separator"], pick params["index"]
MERGE         → join params["sources"] with params["separator"]
DEFAULT_VALUE → use params["value"] (may be null)
COMPUTE       → evaluate user expression via simpleeval; legacy builtins
                (age_from_dob, upper, lower, strip, concat) remain available
DROP          → no output; source answer is discarded
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import select, delete
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.context import user_context
from app.models.enums import RuleSetStatus, TransformOperation
from app.models.tenant.answer import SubmissionAnswer
from app.models.tenant.submission import Submission, SubmissionStatus
from app.models.tenant.template import (
    Question,
    QuestionGroup,
    TenantTemplateDefinition,
)
from app.models.tenant.transform import TransformLog, TransformRule, TransformRuleSet
from app.schemas.submissions.answer import SubmissionAnswerCreate
from app.services.submissions.answer_validator import validate_answers
from app.services.transforms.sandbox import (
    ComputeExpressionError,
    evaluate_compute_expr,
)


# ── Helpers ───────────────────────────────────────────────────────────

async def _load_version_question_map(
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


async def _load_ruleset(
    rule_set_id: UUID,
    session: AsyncSession,
) -> TransformRuleSet:
    result = await session.execute(
        select(TransformRuleSet)
        .where(TransformRuleSet.id == rule_set_id)
        .options(selectinload(TransformRuleSet.rules))
    )
    rs = result.scalars().first()
    if not rs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TransformRuleSet not found.",
        )
    return rs


async def _load_submission_answers(
    submission_id: UUID,
    src_questions: Dict[str, Question],
    session: AsyncSession,
) -> Dict[str, Optional[str]]:
    """Return {unique_key: answer} for all answered questions in the submission."""
    id_to_key = {q.id: k for k, q in src_questions.items()}
    result = await session.execute(
        select(SubmissionAnswer).where(SubmissionAnswer.submission_id == submission_id)
    )
    answers: Dict[str, Optional[str]] = {}
    for row in result.scalars().all():
        key = id_to_key.get(row.question_id)
        if key:
            answers[key] = row.answer
    return answers


# ── Per-operation transform logic ─────────────────────────────────────

def _apply_operation(
    rule: TransformRule,
    answers: Dict[str, Optional[str]],
    errors: List[Dict],
    warnings: List[Dict],
) -> Tuple[str, Optional[str]]:
    """Apply one rule and return (target_unique_key, transformed_value).

    Returns (target_key, None) when the answer should remain null.
    May append to errors / warnings lists.
    """
    op = rule.operation
    src = rule.source_unique_key
    tgt = rule.target_unique_key
    params = rule.params or {}
    raw: Optional[str] = answers.get(src) if src else None

    if op == TransformOperation.IDENTITY:
        return tgt, raw

    if op == TransformOperation.RENAME:
        return tgt, raw

    if op == TransformOperation.DROP:
        # Intentional discard — no output entry produced
        return tgt, "__DROP__"

    if op == TransformOperation.DEFAULT_VALUE:
        return tgt, params.get("value")

    if op == TransformOperation.MAP_VALUES:
        mapping: dict = params.get("mapping", {})
        default = params.get("default")
        if raw is None:
            return tgt, None
        # Handle checkbox (comma-separated multi-value)
        if "," in (raw or ""):
            pieces = [v.strip() for v in raw.split(",") if v.strip()]
            mapped = []
            for piece in pieces:
                mapped_val = mapping.get(piece, default)
                if mapped_val is None:
                    warnings.append({
                        "rule_id": str(rule.id),
                        "unique_key": src,
                        "message": f"Value '{piece}' not in mapping; replaced with default.",
                    })
                mapped.append(mapped_val or "")
            return tgt, ",".join(mapped)
        mapped_val = mapping.get(raw, default)
        if mapped_val is None and raw not in mapping:
            warnings.append({
                "rule_id": str(rule.id),
                "unique_key": src,
                "message": f"Value '{raw}' not in mapping; replaced with default.",
            })
        return tgt, mapped_val

    if op == TransformOperation.COERCE_TYPE:
        to_type = params.get("to_type", "text")
        fmt = params.get("format", "")
        if raw is None:
            return tgt, None
        try:
            if to_type == "date":
                # Attempt parse from common formats
                for fmt_try in (fmt, "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                    if not fmt_try:
                        continue
                    try:
                        d = datetime.strptime(raw, fmt_try).date()
                        return tgt, d.isoformat()
                    except ValueError:
                        continue
                errors.append({
                    "rule_id": str(rule.id),
                    "unique_key": src,
                    "message": f"Cannot coerce '{raw}' to date using format '{fmt}'.",
                })
                return tgt, None
            # Default: cast to text
            return tgt, str(raw)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "rule_id": str(rule.id),
                "unique_key": src,
                "message": f"Coerce error: {exc}",
            })
            return tgt, None

    if op == TransformOperation.SPLIT:
        separator = params.get("separator", " ")
        index = params.get("index", 0)
        if raw is None:
            return tgt, None
        parts = raw.split(separator)
        if index < len(parts):
            return tgt, parts[index].strip()
        warnings.append({
            "rule_id": str(rule.id),
            "unique_key": src,
            "message": f"Split index {index} out of range for value '{raw}'.",
        })
        return tgt, None

    if op == TransformOperation.MERGE:
        sources: list = params.get("sources", [])
        sep = params.get("separator", " ")
        parts = [answers.get(k) or "" for k in sources]
        return tgt, sep.join(parts).strip() or None

    if op == TransformOperation.COMPUTE:
        expr = params.get("expr", "")
        sources: list = params.get("sources", [src] if src else [])

        source_values: Dict[str, Optional[str]] = {}
        if len(sources) == 1:
            source_values["value"] = answers.get(sources[0])
            source_values[sources[0]] = answers.get(sources[0])
        else:
            for s in sources:
                source_values[s] = answers.get(s)
        if not sources and src:
            source_values["value"] = raw

        try:
            result = evaluate_compute_expr(expr, source_values)
            return tgt, result
        except ComputeExpressionError as exc:
            errors.append({
                "rule_id": str(rule.id),
                "unique_key": tgt,
                "message": f"Compute expression failed: {exc}",
            })
            return tgt, None

    # Unknown operation — passthrough with warning
    warnings.append({
        "rule_id": str(rule.id),
        "unique_key": tgt,
        "message": f"Unknown operation '{op}' — answer not transformed.",
    })
    return tgt, raw


# ── Core apply function ───────────────────────────────────────────────

async def apply_rule_set(
    rule_set_id: UUID,
    submission_id: UUID,
    session: AsyncSession,
    is_preview: bool = False,
) -> TransformLog:
    """Apply a published TransformRuleSet to a single submission.

    Returns the TransformLog regardless of preview mode.
    Raises HTTPException on validation or required-rule failures.
    """
    rule_set = await _load_ruleset(rule_set_id, session)

    if rule_set.status != RuleSetStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published rule sets can be applied. Publish it first.",
        )

    result = await session.execute(
        select(Submission).where(Submission.id == submission_id)
    )
    submission = result.scalars().first()
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found.",
        )

    if not is_preview and submission.status not in (
        SubmissionStatus.DRAFT, SubmissionStatus.RETURNED
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only DRAFT or RETURNED submissions can be migrated. "
                   f"Current status: {submission.status}.",
        )

    src_questions = await _load_version_question_map(rule_set.source_version_id, session)
    tgt_questions = await _load_version_question_map(rule_set.target_version_id, session)

    before_answers = await _load_submission_answers(submission_id, src_questions, session)

    errors: List[Dict] = []
    warnings: List[Dict] = []
    after_answers: Dict[str, Optional[str]] = {}

    sorted_rules = sorted(rule_set.rules, key=lambda r: r.display_order)

    for rule in sorted_rules:
        tgt_key, value = _apply_operation(rule, before_answers, errors, warnings)
        if value == "__DROP__":
            continue
        after_answers[tgt_key] = value
        if rule.is_required and errors:
            break  # Stop on first required-rule error

    # Check required-rule failures
    required_errors = [e for e in errors]
    has_required_failure = any(
        r.is_required for r in sorted_rules
        if any(e.get("rule_id") == str(r.id) for e in required_errors)
    )
    if has_required_failure:
        log = TransformLog(
            submission_id=submission_id,
            rule_set_id=rule_set_id,
            source_version_id=rule_set.source_version_id,
            target_version_id=rule_set.target_version_id,
            before_snapshot=before_answers,
            after_snapshot=after_answers,
            errors=errors,
            warnings=warnings,
            applied_at=datetime.now(timezone.utc),
            applied_by=user_context.get(),
            is_preview=True,
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Required transform rules failed.", "errors": errors},
        )

    # Post-transform validation against target version
    answer_creates = [
        SubmissionAnswerCreate(
            question_id=tgt_questions[key].id,
            answer=value,
        )
        for key, value in after_answers.items()
        if key in tgt_questions
    ]
    validation_errors = await validate_answers(
        rule_set.target_version_id, answer_creates, session
    )
    if validation_errors:
        errors.extend(validation_errors)

    log = TransformLog(
        submission_id=submission_id,
        rule_set_id=rule_set_id,
        source_version_id=rule_set.source_version_id,
        target_version_id=rule_set.target_version_id,
        before_snapshot=before_answers,
        after_snapshot=after_answers,
        errors=errors,
        warnings=warnings,
        applied_at=datetime.now(timezone.utc),
        applied_by=user_context.get(),
        is_preview=is_preview,
    )
    session.add(log)

    if not is_preview and not validation_errors:
        # Delete old answers
        await session.execute(
            delete(SubmissionAnswer).where(
                SubmissionAnswer.submission_id == submission_id
            )
        )
        # Insert new answers
        for key, value in after_answers.items():
            if key not in tgt_questions:
                continue
            tgt_q = tgt_questions[key]
            session.add(SubmissionAnswer(
                submission_id=submission_id,
                question_id=tgt_q.id,
                field_type=tgt_q.field_type,
                answer=value,
            ))
        submission.template_version_id = rule_set.target_version_id
        session.add(submission)

    await session.commit()
    await session.refresh(log)
    return log
