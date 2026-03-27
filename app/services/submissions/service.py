"""Submission service — CRUD and workflow operations for form submissions.

Submissions capture user-submitted form data against a specific template version.
The service handles:
- Creating submissions (locks to current template version)
- Updating draft submissions
- Status workflow transitions with audit trail
- Comments for collaboration
"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import asc, desc
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.context import get_current_user, jwt_platform_super_admin_context, jwt_roles_context
from app.models.tenant.submission import (
    Submission,
    SubmissionStatus,
    SubmissionStatusHistory,
    SubmissionComment,
)
from app.models.tenant.template import TenantTemplate, TenantTemplateDefinition
from app.models.tenant.product import Product
from app.models.tenant.verification import VerificationRun, VerificationStepRun
from app.schemas.submissions import (
    SubmissionCreate,
    SubmissionSearchConfigRead,
    SubmissionSearchCriterion,
    SubmissionSearchFilterRead,
    SubmissionSearchRequest,
    SubmissionSearchResultRead,
    SubmissionUpdate,
    SubmissionStatusTransition,
    SubmissionCommentCreate,
    SubmissionListFilters,
)
from app.services.submissions.answer_validator import validate_form_data
from app.services import tenant_templates as tenant_template_svc
from app.services.verifications import service as verification_svc


# Valid status transitions
VALID_TRANSITIONS: Dict[SubmissionStatus, List[SubmissionStatus]] = {
    SubmissionStatus.DRAFT: [
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.CANCELLED,
    ],
    SubmissionStatus.SUBMITTED: [
        SubmissionStatus.UNDER_REVIEW,
        SubmissionStatus.APPROVED,
        SubmissionStatus.REJECTED,
        SubmissionStatus.RETURNED,
        SubmissionStatus.CANCELLED,
    ],
    SubmissionStatus.UNDER_REVIEW: [
        SubmissionStatus.APPROVED,
        SubmissionStatus.REJECTED,
        SubmissionStatus.RETURNED,
    ],
    SubmissionStatus.RETURNED: [
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.CANCELLED,
    ],
    SubmissionStatus.APPROVED: [
        SubmissionStatus.COMPLETED,
    ],
    SubmissionStatus.REJECTED: [
        SubmissionStatus.COMPLETED,
    ],
    SubmissionStatus.COMPLETED: [],  # Terminal state
    SubmissionStatus.CANCELLED: [],  # Terminal state
}

_ELEVATED_ROLES = frozenset({"checker", "platform_admin", "tenant_admin"})
_NATIVE_SEARCH_FILTERS = [
    "status",
    "template_id",
    "product_id",
    "submitter_id",
    "external_ref",
    "created_after",
    "created_before",
]
_VERIFICATION_SEARCH_FILTERS = [
    "verification_status",
    "verification_decision",
    "verification_kyc_level",
    "verification_current_step_key",
]
_ALLOWED_CONFIGURED_FILTER_SOURCES = frozenset(
    {"form_data", "computed_data", "validation_results", "submission", "verification"}
)
_ALLOWED_CONFIGURED_FILTER_OPERATORS = frozenset({"eq", "ne", "in", "contains", "gte", "lte", "exists"})
_ALLOWED_SEARCH_SORTS = {
    "created_at": Submission.created_at,
    "updated_at": Submission.updated_at,
    "submitted_at": Submission.submitted_at,
    "reviewed_at": Submission.reviewed_at,
    "completed_at": Submission.completed_at,
}


def _current_roles() -> frozenset[str]:
    return jwt_roles_context.get() or frozenset()


def _is_super_admin() -> bool:
    return bool(jwt_platform_super_admin_context.get())


def _is_maker_only() -> bool:
    roles = _current_roles()
    return "maker" in roles and not (roles & _ELEVATED_ROLES)


def _forbidden(message: str, *, code: str = "forbidden") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


def _lookup_mapping(mapping: Any, path: str) -> Any:
    if not path:
        return mapping
    current = mapping
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _lookup_submission_field(submission: Submission, path: str) -> Any:
    current: Any = submission
    for part in path.split("."):
        if not part:
            continue
        current = getattr(current, part, None)
        if current is None:
            return None
        if hasattr(current, "value"):
            current = current.value
    return current


def _build_submission_query(
    filters: Optional[SubmissionListFilters] = None,
):
    query = select(Submission)

    if _is_maker_only():
        query = query.where(Submission.created_by == get_current_user())

    if filters:
        if filters.status:
            query = query.where(Submission.status == filters.status)
        if filters.template_id:
            query = query.where(Submission.template_id == filters.template_id)
        if filters.product_id:
            query = query.where(Submission.product_id == filters.product_id)
        if filters.submitter_id:
            query = query.where(Submission.submitter_id == filters.submitter_id)
        if filters.external_ref:
            query = query.where(Submission.external_ref == filters.external_ref)
        if filters.created_after:
            query = query.where(Submission.created_at >= filters.created_after)
        if filters.created_before:
            query = query.where(Submission.created_at <= filters.created_before)

    return query


def _serialize_submission_row(
    submission: Submission,
    *,
    verification: Any | None = None,
) -> SubmissionSearchResultRead:
    return SubmissionSearchResultRead(
        id=submission.id,
        template_id=submission.template_id,
        template_version_id=submission.template_version_id,
        baseline_version_id=submission.baseline_version_id,
        product_id=submission.product_id,
        form_data=deepcopy(submission.form_data or {}),
        computed_data=deepcopy(submission.computed_data or {}),
        validation_results=deepcopy(submission.validation_results or {}),
        attachments=deepcopy(submission.attachments or {}),
        status=submission.status,
        submitter_id=submission.submitter_id,
        external_ref=submission.external_ref,
        submitted_at=submission.submitted_at,
        reviewed_at=submission.reviewed_at,
        completed_at=submission.completed_at,
        reviewed_by=submission.reviewed_by,
        review_notes=submission.review_notes,
        maker_id=submission.created_by,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
        created_by=submission.created_by,
        updated_by=submission.updated_by,
        verification=verification,
    )


def _normalize_configured_filter(
    raw: dict[str, Any],
    *,
    template_id: UUID,
    template_version_id: UUID,
) -> SubmissionSearchFilterRead:
    key = str(raw.get("key") or "").strip()
    if not key:
        raise ValueError("Configured search filters require a non-empty key.")
    source = str(raw.get("source") or "form_data").strip()
    if source not in _ALLOWED_CONFIGURED_FILTER_SOURCES:
        raise ValueError(f"Configured search filter '{key}' has unsupported source '{source}'.")
    path = str(raw.get("path") or key).strip()
    if not path:
        raise ValueError(f"Configured search filter '{key}' requires a non-empty path.")
    operators_raw = raw.get("operators")
    if not isinstance(operators_raw, list) or not operators_raw:
        operators = ["eq"]
    else:
        operators = [str(op).strip() for op in operators_raw if isinstance(op, str) and str(op).strip()]
    invalid = sorted({op for op in operators if op not in _ALLOWED_CONFIGURED_FILTER_OPERATORS})
    if invalid:
        raise ValueError(f"Configured search filter '{key}' has unsupported operators: {', '.join(invalid)}")
    return SubmissionSearchFilterRead(
        key=key,
        label=str(raw.get("label") or key),
        source=source,
        path=path,
        operators=sorted(set(operators)),
        value_type=str(raw.get("value_type")) if raw.get("value_type") is not None else None,
        description=str(raw.get("description")) if raw.get("description") is not None else None,
        template_ids=[template_id],
        template_version_ids=[template_version_id],
        ambiguous=False,
    )


def _configured_filter_signature(item: SubmissionSearchFilterRead) -> tuple[Any, ...]:
    return (
        item.source,
        item.path,
        item.value_type,
        item.description,
    )


def _merge_configured_filters(
    existing: SubmissionSearchFilterRead,
    incoming: SubmissionSearchFilterRead,
) -> SubmissionSearchFilterRead:
    if _configured_filter_signature(existing) != _configured_filter_signature(incoming):
        existing.ambiguous = True
        return existing
    existing.operators = sorted(set(existing.operators) | set(incoming.operators))
    existing.template_ids = sorted(set(existing.template_ids) | set(incoming.template_ids), key=str)
    existing.template_version_ids = sorted(
        set(existing.template_version_ids) | set(incoming.template_version_ids),
        key=str,
    )
    return existing


async def get_submission_search_config(
    session: AsyncSession,
) -> SubmissionSearchConfigRead:
    rows = await session.exec(
        select(TenantTemplate.id, TenantTemplate.active_version_id).where(
            TenantTemplate.is_active == True,
            TenantTemplate.active_version_id != None,  # noqa: E711
        )
    )
    catalog: dict[str, SubmissionSearchFilterRead] = {}
    warnings: list[dict[str, Any]] = []

    for template_id, version_id in rows.all():
        if version_id is None:
            continue
        merged = await tenant_template_svc.get_tenant_template_definition_with_config(version_id, session)
        rules_config = merged.get("rules_config", {}) if isinstance(merged, dict) else {}
        search_cfg = (rules_config or {}).get("submission_search")
        filters = search_cfg.get("filters") if isinstance(search_cfg, dict) else None
        if not isinstance(filters, list):
            continue

        for raw in filters:
            if not isinstance(raw, dict):
                warnings.append(
                    {
                        "code": "submission_search_filter_invalid",
                        "message": f"Template {template_id} contains a non-object submission search filter entry.",
                    }
                )
                continue
            try:
                normalized = _normalize_configured_filter(
                    raw,
                    template_id=template_id,
                    template_version_id=version_id,
                )
            except ValueError as exc:
                warnings.append(
                    {
                        "code": "submission_search_filter_invalid",
                        "message": str(exc),
                        "details": {"template_id": str(template_id), "template_version_id": str(version_id)},
                    }
                )
                continue

            existing = catalog.get(normalized.key)
            if existing is None:
                catalog[normalized.key] = normalized
                continue
            catalog[normalized.key] = _merge_configured_filters(existing, normalized)
            if catalog[normalized.key].ambiguous:
                warnings.append(
                    {
                        "code": "submission_search_filter_ambiguous",
                        "message": f"Configured search filter '{normalized.key}' is defined differently across templates and cannot be used without harmonizing the config.",
                        "details": {"key": normalized.key},
                    }
                )

    configured_filters = sorted(catalog.values(), key=lambda item: item.key)
    return SubmissionSearchConfigRead(
        native_filters=list(_NATIVE_SEARCH_FILTERS),
        verification_filters=list(_VERIFICATION_SEARCH_FILTERS),
        configured_filters=configured_filters,
        warnings=warnings,
    )


async def _load_latest_verification_data(
    submission_ids: list[UUID],
    session: AsyncSession,
) -> dict[UUID, dict[str, Any]]:
    if not submission_ids:
        return {}

    run_rows = await session.exec(
        select(VerificationRun)
        .where(VerificationRun.submission_id.in_(submission_ids))
        .order_by(VerificationRun.submission_id, VerificationRun.created_at.desc())
    )
    latest_by_submission: dict[UUID, VerificationRun] = {}
    for run in run_rows.all():
        latest_by_submission.setdefault(run.submission_id, run)

    if not latest_by_submission:
        return {}

    run_ids = [run.id for run in latest_by_submission.values()]
    step_rows = await session.exec(
        select(VerificationStepRun)
        .where(VerificationStepRun.run_id.in_(run_ids))
        .order_by(VerificationStepRun.run_id, VerificationStepRun.created_at, VerificationStepRun.step_key)
    )
    steps_by_run: dict[UUID, list[VerificationStepRun]] = {}
    for step in step_rows.all():
        steps_by_run.setdefault(step.run_id, []).append(step)

    out: dict[UUID, dict[str, Any]] = {}
    for submission_id, run in latest_by_submission.items():
        steps = steps_by_run.get(run.id, [])
        summary = verification_svc.build_verification_summary(run, steps)
        filter_doc = {
            "id": str(run.id),
            "flow_key": run.flow_key,
            "journey": run.journey,
            "status": run.status,
            "decision": run.decision,
            "kyc_level": run.kyc_level,
            "current_step_key": run.current_step_key,
            "result": deepcopy(run.result_snapshot or {}),
            "facts": deepcopy(run.facts_snapshot or {}),
            "steps": {
                step.step_key: {
                    "status": step.status,
                    "outcome": step.outcome,
                    "result": deepcopy(step.result_snapshot or {}),
                    "output": deepcopy(step.output_snapshot or {}),
                    "input": deepcopy(step.input_snapshot or {}),
                }
                for step in steps
            },
        }
        out[submission_id] = {"summary": summary, "filter_doc": filter_doc}
    return out


def _resolve_search_value(
    submission: Submission,
    *,
    source: str,
    path: str,
    verification_doc: dict[str, Any] | None,
) -> Any:
    if source == "form_data":
        return _lookup_mapping(submission.form_data or {}, path)
    if source == "computed_data":
        return _lookup_mapping(submission.computed_data or {}, path)
    if source == "validation_results":
        return _lookup_mapping(submission.validation_results or {}, path)
    if source == "submission":
        return _lookup_submission_field(submission, path)
    if source == "verification":
        return _lookup_mapping(verification_doc or {}, path)
    return None


def _matches_search_operator(actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        if not isinstance(expected, list):
            return False
        return actual in expected
    if op == "contains":
        if actual is None:
            return False
        if isinstance(actual, list):
            return expected in actual
        return str(expected).lower() in str(actual).lower()
    if op == "gte":
        try:
            return float(actual) >= float(expected)
        except Exception:
            return False
    if op == "lte":
        try:
            return float(actual) <= float(expected)
        except Exception:
            return False
    if op == "exists":
        exists = actual is not None and actual != ""
        if expected is None:
            return exists
        return exists if bool(expected) else not exists
    return False


def _enforce_maker_ownership(submission: Submission) -> None:
    if _is_super_admin():
        return
    if _is_maker_only() and submission.created_by != get_current_user():
        raise _forbidden("You don't have permission to access this submission.")


async def _get_template_with_active_version(
    template_id: UUID,
    session: AsyncSession,
) -> tuple[TenantTemplate, TenantTemplateDefinition]:
    """Get template and its active version, raise if not found."""
    result = await session.exec(
        select(TenantTemplate).where(TenantTemplate.id == template_id)
    )
    template = result.first()
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found.",
        )
    
    if not template.active_version_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template has no active version. Cannot create submission.",
        )
    
    result = await session.exec(
        select(TenantTemplateDefinition).where(
            TenantTemplateDefinition.id == template.active_version_id
        )
    )
    version = result.first()
    
    if not version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template active version not found.",
        )
    
    return template, version


async def _record_status_change(
    submission: Submission,
    from_status: Optional[SubmissionStatus],
    to_status: SubmissionStatus,
    reason: Optional[str],
    extra_data: Dict[str, Any],
    session: AsyncSession,
) -> SubmissionStatusHistory:
    """Record a status change in the history."""
    history = SubmissionStatusHistory(
        submission_id=submission.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=get_current_user(),
        reason=reason,
        extra_data=extra_data,
    )
    session.add(history)
    return history


async def create_submission(
    data: SubmissionCreate,
    session: AsyncSession,
) -> Submission:
    """Create a new submission for a template.
    
    Automatically locks to the template's current active version.
    If the template extends a baseline, also captures the baseline version.
    """
    # Get template and active version
    template, version = await _get_template_with_active_version(
        data.template_id, session
    )
    
    # Validate product_id if provided
    if data.product_id is not None:
        product_result = await session.exec(
            select(Product).where(Product.id == data.product_id)
        )
        if not product_result.first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found.",
            )

    # Get baseline version from the copied_from_baseline_version_id on the template definition
    baseline_version_id = version.copied_from_baseline_version_id
    
    # Create submission
    submission = Submission(
        template_id=template.id,
        template_version_id=version.id,
        baseline_version_id=baseline_version_id,
        form_data=data.form_data,
        submitter_id=data.submitter_id,
        external_ref=data.external_ref,
        product_id=data.product_id,
        status=SubmissionStatus.DRAFT,
    )
    
    session.add(submission)
    
    # Record initial status
    await _record_status_change(
        submission,
        from_status=None,
        to_status=SubmissionStatus.DRAFT,
        reason="Submission created",
        extra_data={},
        session=session,
    )
    
    await session.commit()
    await session.refresh(submission)
    
    return submission


async def list_submissions(
    session: AsyncSession,
    filters: Optional[SubmissionListFilters] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Submission]:
    """List submissions with optional filtering."""
    query = _build_submission_query(filters)
    query = query.order_by(Submission.created_at.desc()).offset(skip).limit(limit)

    result = await session.exec(query)
    return list(result.all())


async def search_submissions(
    session: AsyncSession,
    request: SubmissionSearchRequest,
) -> List[SubmissionSearchResultRead]:
    """Search submissions using native, verification, and tenant-configured filters."""
    config = await get_submission_search_config(session)
    configured = {item.key: item for item in config.configured_filters}

    for criterion in request.criteria:
        definition = configured.get(criterion.key)
        if definition is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "submission_search_filter_unknown",
                    "message": f"Unknown configured submission filter '{criterion.key}'.",
                },
            )
        if definition.ambiguous:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "submission_search_filter_ambiguous",
                    "message": f"Configured submission filter '{criterion.key}' is ambiguous across templates.",
                },
            )
        if criterion.op not in set(definition.operators):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "submission_search_operator_invalid",
                    "message": f"Operator '{criterion.op}' is not allowed for configured filter '{criterion.key}'.",
                    "details": {"allowed_operators": definition.operators},
                },
            )

    filters = SubmissionListFilters(
        status=request.status,
        template_id=request.template_id,
        product_id=request.product_id,
        submitter_id=request.submitter_id,
        external_ref=request.external_ref,
        created_after=request.created_after,
        created_before=request.created_before,
    )
    query = _build_submission_query(filters)

    sort_col = _ALLOWED_SEARCH_SORTS.get(request.sort_by)
    if sort_col is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "submission_search_sort_invalid",
                "message": f"Unsupported sort field '{request.sort_by}'.",
                "details": {"allowed_sort_fields": sorted(_ALLOWED_SEARCH_SORTS)},
            },
        )
    sort_order = (request.sort_order or "desc").lower()
    if sort_order not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "submission_search_sort_invalid",
                "message": f"Unsupported sort order '{request.sort_order}'.",
                "details": {"allowed_sort_orders": ["asc", "desc"]},
            },
        )
    query = query.order_by(asc(sort_col) if sort_order == "asc" else desc(sort_col))

    result = await session.exec(query)
    submissions = list(result.all())
    verification_data = await _load_latest_verification_data([submission.id for submission in submissions], session)

    filtered: list[SubmissionSearchResultRead] = []
    for submission in submissions:
        verification_row = verification_data.get(submission.id)
        verification_summary = verification_row.get("summary") if verification_row else None
        verification_doc = verification_row.get("filter_doc") if verification_row else None

        if request.verification_status and (verification_doc or {}).get("status") != request.verification_status:
            continue
        if request.verification_decision and (verification_doc or {}).get("decision") != request.verification_decision:
            continue
        if request.verification_kyc_level and (verification_doc or {}).get("kyc_level") != request.verification_kyc_level:
            continue
        if request.verification_current_step_key and (verification_doc or {}).get("current_step_key") != request.verification_current_step_key:
            continue

        matches = True
        for criterion in request.criteria:
            definition = configured[criterion.key]
            actual = _resolve_search_value(
                submission,
                source=definition.source,
                path=definition.path,
                verification_doc=verification_doc,
            )
            if not _matches_search_operator(actual, criterion.op, criterion.value):
                matches = False
                break
        if not matches:
            continue

        filtered.append(_serialize_submission_row(submission, verification=verification_summary))

    return filtered[request.skip : request.skip + request.limit]


async def get_submission(
    submission_id: UUID,
    session: AsyncSession,
    include_history: bool = False,
) -> Submission:
    """Get a submission by ID."""
    query = select(Submission).where(Submission.id == submission_id)
    
    if include_history:
        query = query.options(
            selectinload(Submission.status_history)
        )
    
    result = await session.exec(query)
    submission = result.first()
    
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found.",
        )

    _enforce_maker_ownership(submission)


    return submission


async def update_submission(
    submission_id: UUID,
    data: SubmissionUpdate,
    session: AsyncSession,
) -> Submission:
    """Update a submission (draft status only)."""
    submission = await get_submission(submission_id, session)

    _enforce_maker_ownership(submission)


    if submission.status != SubmissionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update submission in '{submission.status}' status. Only DRAFT submissions can be updated.",
        )
    
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(submission, key, value)
    
    session.add(submission)
    await session.commit()
    await session.refresh(submission)
    
    return submission


async def delete_submission(
    submission_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a submission (draft or cancelled only)."""
    submission = await get_submission(submission_id, session)


    _enforce_maker_ownership(submission)

    if submission.status not in [SubmissionStatus.DRAFT, SubmissionStatus.CANCELLED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete submission in '{submission.status}' status.",
        )
    
    await session.delete(submission)
    await session.commit()


async def transition_status(
    submission_id: UUID,
    data: SubmissionStatusTransition,
    session: AsyncSession,
) -> Submission:
    """Transition a submission to a new status."""
    submission = await get_submission(submission_id, session)

    # Four-eyes: a user cannot review/approve/reject/return their own submission.
    if not _is_super_admin() and submission.created_by == get_current_user():
        if data.to_status in {
            SubmissionStatus.UNDER_REVIEW,
            SubmissionStatus.APPROVED,
            SubmissionStatus.REJECTED,
            SubmissionStatus.RETURNED,
        }:
            raise _forbidden(
                "You don't have permission to review your own submission.",
                code="four_eyes_violation",
            )


    # Validate transition
    valid_next = VALID_TRANSITIONS.get(submission.status, [])
    if data.to_status not in valid_next:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from '{submission.status}' to '{data.to_status}'. "
                   f"Valid transitions: {[s.value for s in valid_next]}",
        )
    
    # Record the transition
    from_status = submission.status
    submission.status = data.to_status
    
    # Update timestamps based on status
    now = datetime.now(timezone.utc)
    if data.to_status == SubmissionStatus.SUBMITTED:
        submission.submitted_at = now
    elif data.to_status in [SubmissionStatus.APPROVED, SubmissionStatus.REJECTED, SubmissionStatus.RETURNED]:
        submission.reviewed_at = now
        submission.reviewed_by = get_current_user()
        if data.review_notes:
            submission.review_notes = data.review_notes
    elif data.to_status == SubmissionStatus.COMPLETED:
        submission.completed_at = now
    
    session.add(submission)
    
    # Record in history
    await _record_status_change(
        submission,
        from_status=from_status,
        to_status=data.to_status,
        reason=data.reason,
        extra_data=data.extra_data,
        session=session,
    )
    
    await session.commit()
    await session.refresh(submission)
    
    return submission


async def submit_submission(
    submission_id: UUID,
    session: AsyncSession,
    validate: bool = True,
) -> Submission:
    """Submit a draft submission.
    
    This is a convenience method that transitions from DRAFT to SUBMITTED.
    Optionally validates the form data before submission.
    """
    submission = await get_submission(submission_id, session)


    _enforce_maker_ownership(submission)

    if submission.status != SubmissionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only DRAFT submissions can be submitted.",
        )
    
    if validate:
        errors = await validate_form_data(submission.template_version_id, submission.form_data, session)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "validation_error",
                    "message": "Submission validation failed.",
                    "details": errors,
                },
            )
        submission.validation_results = {"valid": True, "errors": [], "warnings": []}
    
    return await transition_status(
        submission_id,
        SubmissionStatusTransition(
            to_status=SubmissionStatus.SUBMITTED,
            reason="User submitted the form",
        ),
        session,
    )


# ── Comments ──────────────────────────────────────────────────────────

async def add_comment(
    submission_id: UUID,
    data: SubmissionCommentCreate,
    session: AsyncSession,
) -> SubmissionComment:
    """Add a comment to a submission."""
    # Verify submission exists
    await get_submission(submission_id, session)
    
    # Validate parent comment if provided
    if data.parent_id:
        result = await session.exec(
            select(SubmissionComment).where(
                SubmissionComment.id == data.parent_id,
                SubmissionComment.submission_id == submission_id,
            )
        )
        if not result.first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment not found.",
            )
    
    comment = SubmissionComment(
        submission_id=submission_id,
        content=data.content,
        field_id=data.field_id,
        is_internal=data.is_internal,
        parent_id=data.parent_id,
    )
    
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    
    return comment


async def list_comments(
    submission_id: UUID,
    session: AsyncSession,
    include_internal: bool = True,
) -> List[SubmissionComment]:
    """List comments for a submission."""
    # Verify submission exists
    await get_submission(submission_id, session)
    
    query = select(SubmissionComment).where(
        SubmissionComment.submission_id == submission_id
    )
    
    if not include_internal:
        query = query.where(SubmissionComment.is_internal == False)
    
    query = query.order_by(SubmissionComment.created_at)
    
    result = await session.exec(query)
    return list(result.all())


# ── Merged Template Data ──────────────────────────────────────────────

async def get_submission_with_merged_template(
    submission_id: UUID,
    session: AsyncSession,
) -> Dict[str, Any]:
    """Get a submission with its template configuration merged.
    
    Returns the submission along with the form_schema and rules_config
    that were active at submission time.
    """
    submission = await get_submission(submission_id, session, include_history=True)
    
    template_config = await tenant_template_svc.get_tenant_template_definition_with_config(
        submission.template_version_id,
        session,
    )
    question_groups = template_config.get("question_groups", [])
    ungrouped_questions = template_config.get("ungrouped_questions", [])
    form_schema = {
        "question_groups": question_groups,
        "ungrouped_questions": ungrouped_questions,
    }
    rules_config = template_config.get("rules_config", {})

    # Get comments
    comments = await list_comments(submission_id, session)
    from app.services.verifications import service as verification_svc
    verification = await verification_svc.get_latest_verification_run(submission_id, session)

    return {
        "submission": submission,
        "form_schema": form_schema,
        "rules_config": rules_config,
        "question_groups": question_groups,
        "ungrouped_questions": ungrouped_questions,
        "status_history": submission.status_history,
        "comments": comments,
        "verification": verification,
    }
