"""Pydantic schemas for transform preview and bulk migration operations."""

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.transforms.log import TransformLogRead


class TransformPreviewRequest(BaseModel):
    """Request body for a dry-run transform on a single submission."""

    submission_id: UUID = Field(
        description="The submission to preview the transform against.",
    )

    model_config = ConfigDict(extra="ignore")


class TransformPreviewResult(BaseModel):
    """Result of a dry-run transform — no data is persisted.

    The caller can inspect before/after snapshots and any errors before
    committing to the real apply.
    """

    submission_id: UUID
    rule_set_id: UUID
    source_version_id: UUID
    target_version_id: UUID
    before_snapshot: Dict[str, Any] = Field(
        description="Answers keyed by unique_key before the transform.",
    )
    after_snapshot: Dict[str, Any] = Field(
        description="Answers keyed by unique_key after the transform.",
    )
    errors: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Transform errors that would block migration (if is_required rules fail).",
    )
    warnings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Non-fatal warnings (e.g. unmapped optional fields).",
    )
    validation_errors: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Post-transform validation errors against the target version questions.",
    )
    would_succeed: bool = Field(
        description="True if there are no blocking errors and validation passes.",
    )

    model_config = ConfigDict(from_attributes=True)


class BulkMigrateRequest(BaseModel):
    """Request body for bulk-migrating eligible submissions."""

    dry_run: bool = Field(
        default=False,
        description="If True, simulate the migration without persisting any changes.",
    )
    submission_ids: Optional[List[UUID]] = Field(
        default=None,
        description="Explicit list of submission IDs to migrate. "
                    "If omitted, all eligible submissions (DRAFT + RETURNED) are migrated.",
    )

    model_config = ConfigDict(extra="ignore")


class BulkMigrateSubmissionResult(BaseModel):
    """Per-submission outcome of a bulk migration."""

    submission_id: UUID
    success: bool
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    log_id: Optional[UUID] = Field(
        default=None,
        description="TransformLog ID written for this submission (null on dry-run).",
    )


class BulkMigrateResult(BaseModel):
    """Summary result of a bulk migration run."""

    rule_set_id: UUID
    dry_run: bool
    total: int
    succeeded: int
    failed: int
    skipped: int
    results: List[BulkMigrateSubmissionResult]
