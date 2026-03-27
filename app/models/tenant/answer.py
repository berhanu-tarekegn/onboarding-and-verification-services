"""SubmissionAnswer model — flat, validated answer rows for each submission.

Replaces the form_data JSON blob on Submission with individual, queryable rows.
One row per question answered, with CHECK constraints at the DB level for
type-appropriate values.
"""

import uuid as _uuid
from typing import Optional

from sqlmodel import Field
from uuid_extensions import uuid7

from app.models.base import TenantSchemaModel


class SubmissionAnswer(TenantSchemaModel, table=True):
    """A single answer to a single question for a specific submission.

    Schema
    ------
    Each row binds a submission to one question (identified by the question UUID
    in the tenant's questions table) and stores the raw string answer value.

    The DB-level CHECK constraints ensure:
    - checkbox answers are "true" or "false"
    - date answers match YYYY-MM-DD format (ten digit ISO date)
    - fileUpload answers are non-empty (actual metadata lives in Submission.attachments)

    The field_type column is denormalized here so that the constraints can
    reference it without a join. It is populated by the service layer and must
    exactly match the Question.field_type value at answer write time.
    """

    __tablename__ = "submission_answers"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    submission_id: _uuid.UUID = Field(
        foreign_key="submissions.id",
        index=True,
        nullable=False,
    )

    question_id: _uuid.UUID = Field(
        foreign_key="questions.id",
        index=True,
        nullable=False,
        description="FK to the tenant questions table.",
    )

    field_type: str = Field(
        max_length=50,
        description="Denormalized copy of Question.field_type for fast constraint checks.",
    )

    answer: Optional[str] = Field(
        default=None,
        description="String-serialised answer value. "
                    "Lists (checkbox multi-select) are stored as comma-separated values.",
    )
