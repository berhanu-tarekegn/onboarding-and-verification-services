"""Pydantic schemas for SubmissionAnswer — flat, per-question answers.

These schemas replace the old form_data JSON payload with individual,
typed answer rows that map directly to the submission_answers table.
"""

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubmissionAnswerCreate(BaseModel):
    """A single answer entry within a submission payload.

    question_id references the Question in the tenant's questions table.
    answer is always a string; multi-select checkboxes are comma-separated.
    """

    question_id: UUID = Field(description="UUID of the Question being answered.")
    answer: Optional[str] = Field(
        default=None,
        description="String-serialised answer. Null/empty means the question was skipped.",
    )

    model_config = ConfigDict(extra="ignore")


class SubmissionAnswerRead(BaseModel):
    """A single answer row returned in API responses."""

    id: UUID
    submission_id: UUID
    question_id: UUID
    field_type: str
    answer: Optional[str] = None


class SubmissionAnswersPayload(BaseModel):
    """Request body for bulk-creating or upserting answers for a submission.

    POST /submissions/{id}/answers accepts this payload and:
    1. Validates each answer against the corresponding Question rules
    2. Inserts / replaces rows in submission_answers
    """

    answers: List[SubmissionAnswerCreate] = Field(
        min_length=1,
        description="One entry per question being answered.",
    )
