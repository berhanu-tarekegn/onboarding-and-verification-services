"""Tests — Submissions & Answers (Module 5).

Covers:
* Create draft submission (version locked at creation)
* List with filters (status, template_id, submitter_id, product_id)
* Get / get full / update (draft only) / delete (draft/cancelled only)
* Submit workflow (DRAFT → SUBMITTED)
* Status transitions and invalid transition guards
* Status history audit trail
* Comments (external, internal, threaded, field-specific)
* Answer payload — flat table, one row per question
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────

async def _create_submission(
    client: AsyncClient,
    template_id: str,
    submitter_id: str = "user-001",
    product_id: str | None = None,
) -> dict:
    payload: dict = {
        "template_id": template_id,
        "submitter_id": submitter_id,
        "external_ref": "EXT-001",
    }
    if product_id:
        payload["product_id"] = product_id
    r = await client.post("/api/v1/submissions", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── Create ────────────────────────────────────────────────────────────

class TestCreateSubmission:
    async def test_create_returns_201_with_draft_status(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(
            tenant_client, published_tenant_template["id"]
        )
        assert sub["status"] == "draft"
        assert sub["template_id"] == published_tenant_template["id"]

    async def test_template_version_locked_at_creation(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(
            tenant_client, published_tenant_template["id"]
        )
        assert sub["template_version_id"] is not None

    async def test_create_with_product_id(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
        active_product: dict,
    ):
        sub = await _create_submission(
            tenant_client,
            published_tenant_template["id"],
            product_id=active_product["id"],
        )
        assert sub["product_id"] == active_product["id"]

    async def test_create_with_nonexistent_template_rejected(
        self, tenant_client: AsyncClient
    ):
        r = await tenant_client.post(
            "/api/v1/submissions",
            json={
                "template_id": "00000000-0000-0000-0000-000000000000",
                "submitter_id": "x",
            },
        )
        assert r.status_code in (404, 422)


# ── List ──────────────────────────────────────────────────────────────

class TestListSubmissions:
    async def test_list_returns_submissions(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.get("/api/v1/submissions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    async def test_filter_by_status(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.get(
            "/api/v1/submissions", params={"status": "draft"}
        )
        assert r.status_code == 200
        for s in r.json():
            assert s["status"] == "draft"

    async def test_filter_by_submitter_id(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        await _create_submission(
            tenant_client,
            published_tenant_template["id"],
            submitter_id="unique-submitter",
        )
        r = await tenant_client.get(
            "/api/v1/submissions", params={"submitter_id": "unique-submitter"}
        )
        assert r.status_code == 200
        assert all(s["submitter_id"] == "unique-submitter" for s in r.json())

    async def test_filter_by_template_id(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.get(
            "/api/v1/submissions",
            params={"template_id": published_tenant_template["id"]},
        )
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert sub["id"] in ids


# ── Get ───────────────────────────────────────────────────────────────

class TestGetSubmission:
    async def test_get_by_id(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.get(f"/api/v1/submissions/{sub['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == sub["id"]

    async def test_get_full_includes_history_and_comments(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.get(f"/api/v1/submissions/{sub['id']}/full")
        assert r.status_code == 200
        body = r.json()
        assert "status_history" in body
        assert "comments" in body
        assert "form_schema" in body
        assert "rules_config" in body
        assert "question_groups" in body
        assert len(body["question_groups"]) >= 1

    async def test_get_nonexistent_returns_404(self, tenant_client: AsyncClient):
        r = await tenant_client.get(
            "/api/v1/submissions/00000000-0000-0000-0000-000000000000"
        )
        assert r.status_code == 404


# ── Update ────────────────────────────────────────────────────────────

class TestUpdateSubmission:
    async def test_update_draft_submission(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.patch(
            f"/api/v1/submissions/{sub['id']}",
            json={"submitter_id": "updated-user"},
        )
        assert r.status_code == 200
        assert r.json()["submitter_id"] == "updated-user"

    async def test_update_submitted_submission_rejected(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        # Submit it
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        r = await tenant_client.patch(
            f"/api/v1/submissions/{sub['id']}",
            json={"submitter_id": "too-late"},
        )
        assert r.status_code in (403, 422)


# ── Delete ────────────────────────────────────────────────────────────

class TestDeleteSubmission:
    async def test_delete_draft_succeeds(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.delete(f"/api/v1/submissions/{sub['id']}")
        assert r.status_code in (200, 204)

    async def test_delete_submitted_rejected(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        r = await tenant_client.delete(f"/api/v1/submissions/{sub['id']}")
        assert r.status_code in (403, 422)


# ── Submit ────────────────────────────────────────────────────────────

class TestSubmitSubmission:
    async def test_submit_transitions_to_submitted(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == "submitted"

    async def test_submit_already_submitted_rejected(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        assert r.status_code in (409, 422)


# ── Status transitions ────────────────────────────────────────────────

class TestStatusTransitions:
    async def _submitted_sub(
        self,
        tenant_client: AsyncClient,
        template_id: str,
    ) -> dict:
        sub = await _create_submission(tenant_client, template_id)
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        return r.json()

    async def test_submitted_to_under_review(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await self._submitted_sub(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "under_review"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "under_review"

    async def test_under_review_to_approved(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await self._submitted_sub(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "under_review"},
        )
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "approved"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    async def test_under_review_to_rejected(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await self._submitted_sub(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "under_review"},
        )
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "rejected"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    async def test_returned_to_submitted(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await self._submitted_sub(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "under_review"},
        )
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "returned"},
        )
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "submitted"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "submitted"

    async def test_invalid_transition_rejected(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        """approved → draft is not a valid transition."""
        sub = await self._submitted_sub(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "under_review"},
        )
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "approved"},
        )
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "draft"},
        )
        assert r.status_code in (409, 422)

    async def test_draft_to_cancelled(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/transition",
            json={"to_status": "cancelled"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"


# ── History ───────────────────────────────────────────────────────────

class TestSubmissionHistory:
    async def test_history_records_each_transition(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/submit", params={"validate": "false"}
        )
        r = await tenant_client.get(f"/api/v1/submissions/{sub['id']}/history")
        assert r.status_code == 200
        history = r.json()
        assert len(history) >= 1
        to_statuses = [h["to_status"] for h in history]
        assert "submitted" in to_statuses

    async def test_initial_history_entry_has_null_from_status(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.get(f"/api/v1/submissions/{sub['id']}/history")
        assert r.status_code == 200
        history = r.json()
        assert any(h["from_status"] is None for h in history)


# ── Comments ──────────────────────────────────────────────────────────

class TestSubmissionComments:
    async def test_add_external_comment(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={"content": "Please clarify.", "is_internal": False},
        )
        assert r.status_code == 201
        assert r.json()["content"] == "Please clarify."
        assert r.json()["is_internal"] is False

    async def test_add_internal_comment(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={"content": "Reviewer note.", "is_internal": True},
        )
        assert r.status_code == 201
        assert r.json()["is_internal"] is True

    async def test_list_comments_includes_internal_when_requested(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={"content": "Internal.", "is_internal": True},
        )
        r = await tenant_client.get(
            f"/api/v1/submissions/{sub['id']}/comments",
            params={"include_internal": "true"},
        )
        assert r.status_code == 200
        internal = [c for c in r.json() if c["is_internal"]]
        assert len(internal) >= 1

    async def test_list_comments_excludes_internal_when_not_requested(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={"content": "Internal.", "is_internal": True},
        )
        r = await tenant_client.get(
            f"/api/v1/submissions/{sub['id']}/comments",
            params={"include_internal": "false"},
        )
        assert r.status_code == 200
        for c in r.json():
            assert c["is_internal"] is False

    async def test_threaded_reply_via_parent_id(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r1 = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={"content": "Parent comment.", "is_internal": False},
        )
        parent_id = r1.json()["id"]
        r2 = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={
                "content": "Reply.",
                "is_internal": False,
                "parent_id": parent_id,
            },
        )
        assert r2.status_code == 201
        assert r2.json()["parent_id"] == parent_id

    async def test_field_specific_comment(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        sub = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{sub['id']}/comments",
            json={
                "content": "Re-upload clearer photo.",
                "is_internal": False,
                "field_id": "id_document",
            },
        )
        assert r.status_code == 201
        assert r.json()["field_id"] == "id_document"
