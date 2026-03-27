import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi import HTTPException

from app.core.context import jwt_roles_context, user_context
from app.models.tenant.submission import Submission, SubmissionStatus
from app.schemas.submissions import SubmissionStatusTransition
from app.services.submissions import service as submissions_service


class TestFourEyes(unittest.IsolatedAsyncioTestCase):
    async def test_checker_cannot_approve_own_submission(self) -> None:
        submission = Submission(
            template_id=UUID("00000000-0000-0000-0000-000000000001"),
            template_version_id=UUID("00000000-0000-0000-0000-000000000002"),
            status=SubmissionStatus.UNDER_REVIEW,
        )
        submission.created_by = "user-1"

        user_token = user_context.set("user-1")
        roles_token = jwt_roles_context.set(frozenset({"checker"}))
        try:
            with patch.object(
                submissions_service, "get_submission", new=AsyncMock(return_value=submission)
            ):
                with self.assertRaises(HTTPException) as cm:
                    await submissions_service.transition_status(
                        UUID("00000000-0000-0000-0000-000000000000"),
                        SubmissionStatusTransition(to_status=SubmissionStatus.APPROVED),
                        session=AsyncMock(),
                    )
                exc = cm.exception
                self.assertEqual(getattr(exc, "status_code", None), 403)
        finally:
            user_context.reset(user_token)
            jwt_roles_context.reset(roles_token)
