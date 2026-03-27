"""Tests — Baseline Templates (Module 2).

Covers:
* Create with groups and ungrouped (groupless) questions
* template_type enum enforcement and type+level identity
* Draft → publish lifecycle
* Immutability of published definitions
* Version management (create additional versions)
* List / get / update / delete
* Groupless question handling
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import make_baseline_payload


# ── Create ────────────────────────────────────────────────────────────

class TestCreateBaselineTemplate:
    async def test_create_with_initial_version(self, client: AsyncClient):
        r = await client.post("/api/v1/baseline-templates", json=make_baseline_payload())
        assert r.status_code == 201
        body = r.json()
        assert body["template_type"] == "kyc"
        assert body["name"] == "KYC Standard"

    async def test_response_includes_initial_version(self, client: AsyncClient):
        r = await client.post("/api/v1/baseline-templates", json=make_baseline_payload())
        assert r.status_code == 201
        # active_version_id should be set (or versions list non-empty)
        body = r.json()
        assert body.get("active_version_id") or body.get("versions")

    async def test_duplicate_template_type_and_level_rejected(
        self, client: AsyncClient, baseline: dict
    ):
        r = await client.post(
            "/api/v1/baseline-templates",
            json=make_baseline_payload(
                template_type="kyc",
                level=1,
                name="KYC Duplicate",
            ),
        )
        assert r.status_code in (409, 422)

    async def test_same_template_type_with_different_level_allowed(
        self, client: AsyncClient, baseline: dict
    ):
        r = await client.post(
            "/api/v1/baseline-templates",
            json=make_baseline_payload(
                template_type="kyc",
                level=2,
                name="KYC Level 2",
            ),
        )
        assert r.status_code == 201
        assert r.json()["level"] == 2

    async def test_invalid_template_type_rejected(self, client: AsyncClient):
        payload = make_baseline_payload()
        payload["template_type"] = "invalid_type"
        r = await client.post("/api/v1/baseline-templates", json=payload)
        assert r.status_code == 422

    async def test_invalid_field_type_on_question_rejected(self, client: AsyncClient):
        payload = make_baseline_payload()
        payload["initial_version"]["question_groups"][0]["questions"][0][
            "field_type"
        ] = "magic_wand"
        r = await client.post("/api/v1/baseline-templates", json=payload)
        assert r.status_code == 422

    async def test_create_without_initial_version(self, client: AsyncClient):
        r = await client.post(
            "/api/v1/baseline-templates",
            json=make_baseline_payload(
                template_type="kyb",
                level=3,
                name="KYB Level 3",
                with_version=False,
            ),
        )
        assert r.status_code == 201
        assert r.json()["template_type"] == "kyb"
        assert r.json()["level"] == 3


# ── Groupless Questions ───────────────────────────────────────────────

class TestBaselineGrouplessQuestions:
    async def test_ungrouped_questions_present_in_response(
        self, client: AsyncClient, baseline: dict
    ):
        """The 'consent' question was added as ungrouped in the fixture payload."""
        template_id = baseline["id"]
        version_id = (
            baseline.get("active_version_id")
            or baseline["versions"][0]["id"]
        )
        r = await client.get(
            f"/api/v1/baseline-templates/{template_id}/definitions/{version_id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert "ungrouped_questions" in body
        keys = [q["unique_key"] for q in body["ungrouped_questions"]]
        assert "consent" in keys

    async def test_grouped_questions_present_in_response(
        self, client: AsyncClient, baseline: dict
    ):
        template_id = baseline["id"]
        version_id = (
            baseline.get("active_version_id")
            or baseline["versions"][0]["id"]
        )
        r = await client.get(
            f"/api/v1/baseline-templates/{template_id}/definitions/{version_id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["question_groups"]) >= 1
        group_keys = [g["unique_key"] for g in body["question_groups"]]
        assert "personal_info" in group_keys


# ── Publish lifecycle ─────────────────────────────────────────────────

class TestBaselinePublishLifecycle:
    async def test_publish_sets_is_draft_false(
        self, client: AsyncClient, baseline: dict
    ):
        version_id = (
            baseline.get("active_version_id")
            or baseline["versions"][0]["id"]
        )
        r = await client.post(
            f"/api/v1/baseline-templates/{baseline['id']}/definitions/{version_id}/publish",
            params={"set_as_active": "true"},
        )
        assert r.status_code == 200
        assert r.json()["is_draft"] is False

    async def test_publish_sets_active_version_id(
        self, client: AsyncClient, baseline: dict
    ):
        version_id = (
            baseline.get("active_version_id")
            or baseline["versions"][0]["id"]
        )
        await client.post(
            f"/api/v1/baseline-templates/{baseline['id']}/definitions/{version_id}/publish",
            params={"set_as_active": "true"},
        )
        r = await client.get(f"/api/v1/baseline-templates/{baseline['id']}")
        assert r.status_code == 200
        assert r.json()["active_version_id"] == version_id

    async def test_update_published_definition_rejected(
        self, client: AsyncClient, published_baseline: dict
    ):
        template_id = published_baseline["id"]
        version_id = published_baseline["active_version_id"]
        r = await client.patch(
            f"/api/v1/baseline-templates/{template_id}/definitions/{version_id}",
            json={"changelog": "Trying to edit published"},
        )
        assert r.status_code in (403, 422)

    async def test_delete_active_version_rejected(
        self, client: AsyncClient, published_baseline: dict
    ):
        template_id = published_baseline["id"]
        version_id = published_baseline["active_version_id"]
        r = await client.delete(
            f"/api/v1/baseline-templates/{template_id}/definitions/{version_id}"
        )
        assert r.status_code in (403, 422)

    async def test_new_draft_version_can_be_created_after_publish(
        self, client: AsyncClient, published_baseline: dict
    ):
        template_id = published_baseline["id"]
        r = await client.post(
            f"/api/v1/baseline-templates/{template_id}/definitions",
            json={"version_tag": "2.0.0", "rules_config": {}, "changelog": "v2"},
        )
        assert r.status_code == 201
        assert r.json()["is_draft"] is True


# ── List / Get / Update / Delete ──────────────────────────────────────

class TestBaselineTemplateCRUD:
    async def test_list_baseline_templates(self, client: AsyncClient, baseline: dict):
        r = await client.get("/api/v1/baseline-templates")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()]
        assert baseline["id"] in ids

    async def test_list_baseline_templates_filter_by_level(
        self, client: AsyncClient, baseline: dict
    ):
        r_create = await client.post(
            "/api/v1/baseline-templates",
            json=make_baseline_payload(name="KYC Level 2", level=2),
        )
        assert r_create.status_code == 201
        level_two_id = r_create.json()["id"]

        r = await client.get(
            "/api/v1/baseline-templates",
            params={"active_only": "false", "level": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert body
        assert all(item["level"] == 2 for item in body)
        ids = [item["id"] for item in body]
        assert level_two_id in ids
        assert baseline["id"] not in ids

    async def test_get_with_versions(self, client: AsyncClient, baseline: dict):
        r = await client.get(f"/api/v1/baseline-templates/{baseline['id']}")
        assert r.status_code == 200
        assert "versions" in r.json()

    async def test_update_template_name(self, client: AsyncClient, baseline: dict):
        r = await client.patch(
            f"/api/v1/baseline-templates/{baseline['id']}",
            json={"name": "KYC Updated"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "KYC Updated"

    async def test_delete_unlocked_template(self, client: AsyncClient):
        r = await client.post(
            "/api/v1/baseline-templates",
            json=make_baseline_payload(
                template_type="kyb",
                level=2,
                name="KYB Level 2",
                with_version=False,
            ),
        )
        assert r.status_code == 201
        tid = r.json()["id"]
        r2 = await client.delete(f"/api/v1/baseline-templates/{tid}")
        assert r2.status_code in (200, 204)

    async def test_delete_locked_template_rejected(
        self, client: AsyncClient, baseline: dict
    ):
        # Lock the template first
        await client.patch(
            f"/api/v1/baseline-templates/{baseline['id']}",
            json={"is_locked": True},
        )
        r = await client.delete(f"/api/v1/baseline-templates/{baseline['id']}")
        assert r.status_code in (403, 422)

    async def test_update_locked_template_rejected(
        self, client: AsyncClient, baseline: dict
    ):
        await client.patch(
            f"/api/v1/baseline-templates/{baseline['id']}",
            json={"is_locked": True},
        )
        r = await client.patch(
            f"/api/v1/baseline-templates/{baseline['id']}",
            json={"name": "Should Fail"},
        )
        assert r.status_code in (403, 422)

    async def test_get_nonexistent_returns_404(self, client: AsyncClient):
        r = await client.get(
            "/api/v1/baseline-templates/00000000-0000-0000-0000-000000000000"
        )
        assert r.status_code == 404
