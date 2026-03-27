"""Pydantic schemas for TransformLog read operations."""

from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TransformLogRead(BaseModel):
    """Response model for a single transform execution audit record."""

    id: UUID
    submission_id: UUID
    rule_set_id: UUID
    source_version_id: UUID
    target_version_id: UUID
    before_snapshot: Dict[str, Any]
    after_snapshot: Dict[str, Any]
    errors: List[Any]
    warnings: List[Any]
    applied_at: datetime
    applied_by: str
    is_preview: bool

    model_config = ConfigDict(from_attributes=True)
