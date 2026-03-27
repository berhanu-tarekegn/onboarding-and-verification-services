"""Submission service — CRUD and workflow operations for form submissions.

Submissions capture user-submitted form data against a specific template version.
The service handles:
- Creating submissions (locks to current template version)
- Updating draft submissions
- Status workflow transitions with audit trail
- Comments for collaboration
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
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
from app.schemas.submissions import (
    SubmissionCreate,
    SubmissionUpdate,
    SubmissionStatusTransition,
    SubmissionCommentCreate,
    SubmissionListFilters,
)
from app.services.submissions.answer_validator import validate_form_data
from app.services import tenant_templates as tenant_template_svc


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
    query = select(Submission)


    # Maker should only see their own submissions. Elevated roles can see all.
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
    
    query = query.order_by(Submission.created_at.desc()).offset(skip).limit(limit)
    
    result = await session.exec(query)
    return list(result.all())


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
