"""Answer validation service — validate submission answers against question rules.

This module:
1. Loads all visible questions for a given template version
2. Resolves conditional visibility based on depends_on_unique_key / visible_when_equals
3. Validates each required visible question has an answer
4. Validates field-type-specific rules (regex, date range, valid option values)
5. Collects ALL errors non-fail-fast and returns them together

Usage
-----
    errors = await validate_answers(version_id, answers_map, session)
    if errors:
        raise HTTPException(422, detail=errors)
"""

import re
from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.tenant.template import (
    TenantTemplateDefinition,
    QuestionGroup,
    Question,
    QuestionOption,
)
from app.schemas.submissions.answer import SubmissionAnswerCreate


# ── Public entry point ────────────────────────────────────────────────

def _coerce_answer_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (int, float, bool)):
                parts.append(str(item))
            else:
                parts.append(str(item))
        return ",".join(parts)
    return str(value)


async def validate_form_data(
    version_id: UUID,
    form_data: Dict[str, Any],
    session: AsyncSession,
) -> List[Dict[str, Any]]:
    """Validate legacy dict-based `form_data` (keyed by question unique_key).

    This is a compatibility shim while clients migrate to `submission_answers`.
    """
    all_questions = await _load_questions(version_id, session)
    answers_by_key: Dict[str, Optional[str]] = {
        str(k): _coerce_answer_value(v) for k, v in (form_data or {}).items()
    }

    errors: List[Dict[str, Any]] = []
    for q in all_questions:
        if not _is_visible(q, answers_by_key):
            continue
        raw = answers_by_key.get(q.unique_key)
        field_errors = _validate_question(q, raw)
        errors.extend(
            {"question_id": str(q.id), "unique_key": q.unique_key, "message": msg}
            for msg in field_errors
        )
    return errors


async def validate_answers(
    version_id: UUID,
    answers: List[SubmissionAnswerCreate],
    session: AsyncSession,
) -> List[Dict[str, Any]]:
    """Validate a list of answers against the questions in the given template version.

    Returns a (possibly empty) list of validation error dicts:
        {"question_id": ..., "unique_key": ..., "message": "..."}
    """
    all_questions = await _load_questions(version_id, session)

    answers_by_id: Dict[UUID, Optional[str]] = {
        a.question_id: a.answer for a in answers
    }

    # Build answer map by unique_key for visibility resolution
    answers_by_key: Dict[str, Optional[str]] = {}
    for q in all_questions:
        if q.id in answers_by_id:
            answers_by_key[q.unique_key] = answers_by_id[q.id]

    errors: List[Dict[str, Any]] = []

    for q in all_questions:
        if not _is_visible(q, answers_by_key):
            continue

        raw = answers_by_id.get(q.id)
        field_errors = _validate_question(q, raw)
        errors.extend(
            {"question_id": str(q.id), "unique_key": q.unique_key, "message": msg}
            for msg in field_errors
        )

    return errors


# ── Private helpers ───────────────────────────────────────────────────

async def _load_questions(
    version_id: UUID,
    session: AsyncSession,
) -> List[Question]:
    """Load all questions for a version (grouped and ungrouped), ordered."""
    result = await session.execute(
        select(TenantTemplateDefinition)
        .where(TenantTemplateDefinition.id == version_id)
        .options(
            selectinload(TenantTemplateDefinition.question_groups)
            .selectinload(QuestionGroup.questions)
            .selectinload(Question.options),
            selectinload(TenantTemplateDefinition.ungrouped_questions)
            .selectinload(Question.options),
        )
    )
    version = result.scalars().first()
    if not version:
        return []

    questions: List[Question] = []
    for group in sorted(version.question_groups, key=lambda g: g.display_order):
        for q in sorted(group.questions, key=lambda x: x.display_order):
            questions.append(q)
    # Append ungrouped questions after all grouped ones
    for q in sorted(version.ungrouped_questions, key=lambda x: x.display_order):
        questions.append(q)
    return questions


def _is_visible(q: Question, answers_by_key: Dict[str, Optional[str]]) -> bool:
    """Return True if this question is visible given current answers."""
    if not q.depends_on_unique_key:
        return True
    controlling_answer = answers_by_key.get(q.depends_on_unique_key)
    if q.visible_when_equals is None:
        return controlling_answer is not None and controlling_answer != ""
    return controlling_answer == q.visible_when_equals


def _validate_question(q: Question, raw: Optional[str]) -> List[str]:
    """Return a list of validation error strings for a single question."""
    errors: List[str] = []

    is_blank = raw is None or raw.strip() == ""

    if q.required and is_blank:
        errors.append("This field is required.")
        return errors  # No point in further validation if blank+required

    ft = q.field_type

    # A provided-but-blank file/upload reference is always invalid, even when optional
    if ft == "fileUpload" and raw is not None and raw.strip() == "":
        errors.append("File upload reference cannot be empty.")
        return errors

    if is_blank:
        return errors  # Optional and blank — nothing to validate

    if ft == "text":
        if q.regex:
            try:
                if not re.fullmatch(q.regex, raw):
                    errors.append(f"Value does not match expected format.")
            except re.error:
                pass  # Bad regex pattern — skip validation rather than crash

    elif ft in ("dropdown", "radio"):
        valid_values = {opt.value for opt in q.options}
        if valid_values and raw not in valid_values:
            errors.append(f"'{raw}' is not a valid option.")

    elif ft == "checkbox":
        # Multi-select: comma-separated list; each value must be a valid option
        valid_values = {opt.value for opt in q.options}
        if valid_values:
            submitted = [v.strip() for v in raw.split(",") if v.strip()]
            invalid = [v for v in submitted if v not in valid_values]
            if invalid:
                errors.append(f"Invalid option(s): {', '.join(invalid)}")

    elif ft == "date":
        parsed = _parse_date(raw)
        if parsed is None:
            errors.append("Date must be in YYYY-MM-DD format.")
        else:
            if q.min_date:
                min_d = _parse_date(q.min_date)
                if min_d and parsed < min_d:
                    errors.append(f"Date must be on or after {q.min_date}.")
            if q.max_date:
                max_d = _parse_date(q.max_date)
                if max_d and parsed > max_d:
                    errors.append(f"Date must be on or before {q.max_date}.")

    elif ft == "fileUpload":
        pass  # Non-blank fileUpload answers are valid; blank handled above

    return errors


def _parse_date(value: str) -> Optional[date]:
    """Parse a YYYY-MM-DD date string, returning None on failure."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
