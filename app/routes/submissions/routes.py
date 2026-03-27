"""Submission routes — form submission management in per-tenant schemas.

These routes are scoped to the current tenant (via X-Tenant-ID header).
Submissions capture user-filled form data against template versions.

Workflow:
    POST /submissions                    → Create draft
    PATCH /submissions/{id}              → Update draft
    POST /submissions/{id}/submit        → Submit for review
    POST /submissions/{id}/transition    → Change status (approve/reject/etc)
    GET /submissions/{id}/full           → Get with merged template config
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dependencies import require_tenant_header
from app.db.session import tenant_session_for_permissions, tenant_session_for_any_permissions
from app.schemas.submissions import (
    SubmissionStatus,
    SubmissionCreate,
    SubmissionUpdate,
    SubmissionRead,
    SubmissionReadWithHistory,
    SubmissionStatusTransition,
    SubmissionStatusHistoryRead,
    SubmissionCommentCreate,
    SubmissionCommentRead,
    SubmissionListFilters,
    VerificationStartRequest,
    VerificationActionRequest,
    VerificationRunRead,
)
from app.services import submissions as submission_svc
from app.services.verifications import service as verification_svc
from app.core.authz import enforce_write_columns

router = APIRouter(
    prefix="/submissions",
    tags=["submissions"],
    dependencies=[Depends(require_tenant_header)],
)

def _mask_fields(obj, *, deny: set[str]) -> None:
    for f in deny:
        if hasattr(obj, f):
            try:
                setattr(obj, f, None)
            except Exception:
                pass


# ── Submission CRUD ───────────────────────────────────────────────────

@router.post(
    "",
    response_model=SubmissionRead,
    status_code=201,
)
async def create_submission(
    data: SubmissionCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.create")),
):
    """Create a new submission for a template.
    
    The submission is created in DRAFT status and locked to the
    template's current active version.
    """
    return await submission_svc.create_submission(data, session)


@router.get(
    "",
    response_model=list[SubmissionRead],
)
async def list_submissions(
    request: Request,
    status: Optional[SubmissionStatus] = Query(None),
    template_id: Optional[UUID] = Query(None),
    product_id: Optional[UUID] = Query(None, description="Filter by product ID"),
    submitter_id: Optional[str] = Query(None),
    external_ref: Optional[str] = Query(None),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(
        tenant_session_for_any_permissions("submissions.read", "submissions.read_all")
    ),
):
    """List submissions with optional filters."""
    filters = SubmissionListFilters(
        status=status,
        template_id=template_id,
        product_id=product_id,
        submitter_id=submitter_id,
        external_ref=external_ref,
        created_after=created_after,
        created_before=created_before,
    )
    items = await submission_svc.list_submissions(session, filters, skip, limit)
    # Field-level masking for makers (policy-driven; defaults deny reviewer fields on own-read).
    columns = getattr(request.state, "authz_columns", {})
    perms = getattr(request.state, "authz_perms", set())
    perm_key = "submissions.read_all" if "submissions.read_all" in perms else "submissions.read"
    deny = set()
    if isinstance(columns, dict):
        deny = set((columns.get(perm_key, {}) or {}).get("deny", set()) or set())
    for s in items:
        _mask_fields(s, deny=deny)
    return items


@router.get(
    "/{submission_id}",
    response_model=SubmissionRead,
)
async def get_submission(
    submission_id: UUID,
    request: Request,
    session: AsyncSession = Depends(
        tenant_session_for_any_permissions("submissions.read", "submissions.read_all")
    ),
):
    """Get a submission by ID."""
    s = await submission_svc.get_submission(submission_id, session)
    columns = getattr(request.state, "authz_columns", {})
    perms = getattr(request.state, "authz_perms", set())
    perm_key = "submissions.read_all" if "submissions.read_all" in perms else "submissions.read"
    deny = set()
    if isinstance(columns, dict):
        deny = set((columns.get(perm_key, {}) or {}).get("deny", set()) or set())
    _mask_fields(s, deny=deny)
    return s


@router.get(
    "/{submission_id}/full",
    response_model=SubmissionReadWithHistory,
)
async def get_submission_full(
    submission_id: UUID,
    request: Request,
    session: AsyncSession = Depends(
        tenant_session_for_any_permissions("submissions.read", "submissions.read_all")
    ),
):
    """Get a submission with full details including history and merged template.
    
    Returns the submission along with:
    - Status change history (audit trail)
    - Comments
    - The form_schema and rules_config that were active at submission time
    """
    result = await submission_svc.get_submission_with_merged_template(
        submission_id, session
    )
    
    submission = result["submission"]
    columns = getattr(request.state, "authz_columns", {})
    perms = getattr(request.state, "authz_perms", set())
    perm_key = "submissions.read_all" if "submissions.read_all" in perms else "submissions.read"
    deny = set()
    if isinstance(columns, dict):
        deny = set((columns.get(perm_key, {}) or {}).get("deny", set()) or set())
    return SubmissionReadWithHistory(
        id=submission.id,
        template_id=submission.template_id,
        template_version_id=submission.template_version_id,
        baseline_version_id=submission.baseline_version_id,
        form_data=submission.form_data,
        computed_data=submission.computed_data,
        validation_results=submission.validation_results,
        attachments=submission.attachments,
        status=submission.status,
        submitter_id=submission.submitter_id,
        external_ref=submission.external_ref,
        submitted_at=submission.submitted_at,
        reviewed_at=submission.reviewed_at,
        completed_at=submission.completed_at,
        reviewed_by=None if "reviewed_by" in deny else submission.reviewed_by,
        review_notes=None if "review_notes" in deny else submission.review_notes,
        maker_id=submission.created_by,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
        created_by=submission.created_by,
        updated_by=submission.updated_by,
        form_schema=result["form_schema"],
        rules_config=result["rules_config"],
        question_groups=result["question_groups"],
        ungrouped_questions=result["ungrouped_questions"],
        status_history=result["status_history"],
        comments=result["comments"],
        verification=result.get("verification"),
    )


@router.get(
    "/{submission_id}/verification",
    response_model=VerificationRunRead | None,
)
async def get_submission_verification(
    submission_id: UUID,
    session: AsyncSession = Depends(
        tenant_session_for_any_permissions("submissions.read", "submissions.read_all")
    ),
):
    """Get the latest verification flow state for a submission."""
    return await verification_svc.get_latest_verification_run(submission_id, session)


@router.post(
    "/{submission_id}/verification/start",
    response_model=VerificationRunRead,
)
async def start_submission_verification(
    submission_id: UUID,
    body: VerificationStartRequest,
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.submit")),
):
    """Create, defer, start, or resume a configurable verification flow."""
    return await verification_svc.start_verification(submission_id, body, session)


@router.post(
    "/{submission_id}/verification/steps/{step_key}/actions",
    response_model=VerificationRunRead,
)
async def submit_submission_verification_action(
    submission_id: UUID,
    step_key: str,
    body: VerificationActionRequest,
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.submit")),
):
    """Submit a user or agent action, such as an OTP code, for a waiting verification step."""
    return await verification_svc.submit_step_action(submission_id, step_key, body, session)


@router.patch(
    "/{submission_id}",
    response_model=SubmissionRead,
)
async def update_submission(
    submission_id: UUID,
    data: SubmissionUpdate,
    request: Request,
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.update")),
):
    """Update a submission (DRAFT status only).
    
    Use this to save form data while the user is filling it out.
    """
    updates = data.model_dump(exclude_unset=True)
    if updates:
        enforce_write_columns(request, "submissions.update", set(updates.keys()))
    return await submission_svc.update_submission(submission_id, data, session)


@router.delete(
    "/{submission_id}",
    status_code=204,
)
async def delete_submission(
    submission_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.delete")),
):
    """Delete a submission (DRAFT or CANCELLED only)."""
    await submission_svc.delete_submission(submission_id, session)


# ── Workflow Actions ──────────────────────────────────────────────────

@router.post(
    "/{submission_id}/submit",
    response_model=SubmissionRead,
)
async def submit_submission(
    submission_id: UUID,
    validate: bool = Query(True, description="Validate form data before submission"),
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.submit")),
):
    """Submit a draft submission for review.
    
    Transitions from DRAFT to SUBMITTED status.
    Optionally validates the form data against the template schema.
    """
    return await submission_svc.submit_submission(submission_id, session, validate)


@router.post(
    "/{submission_id}/transition",
    response_model=SubmissionRead,
)
async def transition_submission(
    submission_id: UUID,
    data: SubmissionStatusTransition,
    session: AsyncSession = Depends(tenant_session_for_permissions("submissions.transition")),
):
    """Transition a submission to a new status.
    
    Valid transitions depend on current status:
    - DRAFT → SUBMITTED, CANCELLED
    - SUBMITTED → UNDER_REVIEW, APPROVED, REJECTED, RETURNED, CANCELLED
    - UNDER_REVIEW → APPROVED, REJECTED, RETURNED
    - RETURNED → SUBMITTED, CANCELLED
    - APPROVED → COMPLETED
    - REJECTED → COMPLETED
    """
    return await submission_svc.transition_status(submission_id, data, session)


# ── Status History ────────────────────────────────────────────────────

@router.get("/{submission_id}/history", response_model=list[SubmissionStatusHistoryRead])
async def get_submission_history(
    submission_id: UUID,
    session: AsyncSession = Depends(
        tenant_session_for_any_permissions("submissions.read", "submissions.read_all")
    ),
):
    """Get the status change history for a submission."""
    submission = await submission_svc.get_submission(
        submission_id, session, include_history=True
    )
    return submission.status_history


# ── Comments ──────────────────────────────────────────────────────────

@router.post(
    "/{submission_id}/comments",
    response_model=SubmissionCommentRead,
    status_code=201,
)
async def add_comment(
    submission_id: UUID,
    data: SubmissionCommentCreate,
    session: AsyncSession = Depends(
        tenant_session_for_permissions("submissions.comment")
    ),
):
    """Add a comment to a submission.
    
    Comments can be:
    - Internal (visible to reviewers only)
    - External (visible to submitter)
    - Field-specific (reference a particular form field)
    - Threaded (reply to another comment)
    """
    return await submission_svc.add_comment(submission_id, data, session)


@router.get("/{submission_id}/comments", response_model=list[SubmissionCommentRead])
async def list_comments(
    submission_id: UUID,
    include_internal: bool = Query(True, description="Include internal comments"),
    session: AsyncSession = Depends(
        tenant_session_for_any_permissions("submissions.read", "submissions.read_all")
    ),
):
    """List comments for a submission."""
    return await submission_svc.list_comments(submission_id, session, include_internal)
