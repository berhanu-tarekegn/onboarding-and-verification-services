import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.tenant.submission import Submission, SubmissionStatus
from app.services.submissions import service as submissions_service


def _make_question(*, unique_key: str, field_type: str = "text", required: bool = False):
    return SimpleNamespace(
        id=uuid4(),
        unique_key=unique_key,
        field_type=field_type,
        required=required,
        regex=None,
        min_date=None,
        max_date=None,
        options=[],
        depends_on_unique_key=None,
        visible_when_equals=None,
    )


class TestSubmissionValidation(unittest.IsolatedAsyncioTestCase):
    async def test_submit_with_missing_required_field_returns_422(self) -> None:
        submission = Submission(
            template_id=uuid4(),
            template_version_id=uuid4(),
            status=SubmissionStatus.DRAFT,
            form_data={},  # missing required q1
        )
        submission.created_by = "user-1"

        session = AsyncMock()

        with patch.object(
            submissions_service, "get_submission", new=AsyncMock(return_value=submission)
        ):
            with patch(
                "app.services.submissions.answer_validator._load_questions",
                new=AsyncMock(return_value=[_make_question(unique_key="q1", required=True)]),
            ):
                with self.assertRaises(HTTPException) as cm:
                    await submissions_service.submit_submission(
                        submission_id=uuid4(),
                        session=session,
                        validate=True,
                    )
                self.assertEqual(cm.exception.status_code, 422)

