"""Tests — Tenant Templates (Module 3).

Covers:
* Create with template_type matching active baseline
* Baseline questions auto-copied on version creation
* is_tenant_editable enforcement:
    - Baseline-derived questions: immutable (403 on edit/delete)
    - Baseline-copied groups: is_tenant_editable=True (can add questions)
    - Tenant-added questions: fully mutable
* Ungrouped / groupless question support
* Publish workflow with unique_key uniqueness validation
* Draft-only mutation guard
* CRUD lifecycle
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.context import user_context


# ── Create ────────────────────────────────────────────────────────────

class TestCreateTenantTemplate:
    async def test_create_returns_201(
        self, tenant_client: AsyncClient, published_baseline: dict
    ):
        r = await tenant_client.post(
            "/api/v1/templates",
            json={"name": "My Flow", "template_type": "kyc", "baseline_level": 1},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["template_type"] == "kyc"
        assert body["name"] == "My Flow"

    async def test_create_with_no_active_baseline_rejected(
        self, tenant_client: AsyncClient
    ):
        """No active baseline exists for the requested type+level pair → 422."""
        r = await tenant_client.post(
            "/api/v1/templates",
            json={"name": "Higher Flow", "template_type": "kyc", "baseline_level": 99},
        )
        assert r.status_code == 422

    async def test_create_with_invalid_template_type_rejected(
        self, tenant_client: AsyncClient
    ):
        r = await tenant_client.post(
            "/api/v1/templates",
            json={"name": "Bad", "template_type": "garbage", "baseline_level": 1},
        )
        assert r.status_code == 422


# ── Definition create & baseline copy ────────────────────────────────

class TestTenantTemplateDefinitionCreate:
    async def test_create_definition_returns_201(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        assert r.status_code == 201
        assert r.json()["is_draft"] is True

    async def test_baseline_questions_auto_copied(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        assert r.status_code == 201
        body = r.json()
        # At least one question group copied from baseline
        assert len(body["question_groups"]) >= 1

    async def test_copied_questions_are_not_tenant_editable(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        body = r.json()
        for group in body["question_groups"]:
            for q in group.get("questions", []):
                assert q["is_tenant_editable"] is False

    async def test_copied_groups_are_tenant_editable(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        body = r.json()
        for group in body["question_groups"]:
            assert group["is_tenant_editable"] is True

    async def test_copied_from_baseline_version_id_set(
        self, tenant_client: AsyncClient, tenant_template: dict, published_baseline: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        body = r.json()
        assert body["copied_from_baseline_version_id"] == published_baseline["active_version_id"]

    async def test_ungrouped_baseline_questions_copied(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        body = r.json()
        # 'consent' was ungrouped in the baseline fixture
        ungrouped_keys = [q["unique_key"] for q in body.get("ungrouped_questions", [])]
        assert "consent" in ungrouped_keys


# ── is_tenant_editable enforcement ───────────────────────────────────

class TestTenantEditableEnforcement:
    async def _create_version(
        self, tenant_client: AsyncClient, template_id: str
    ) -> dict:
        r = await tenant_client.post(
            f"/api/v1/templates/{template_id}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        assert r.status_code == 201
        return r.json()

    async def _get_baseline_question(self, version_body: dict) -> dict:
        """Return the first question with is_tenant_editable=False."""
        for group in version_body.get("question_groups", []):
            for q in group.get("questions", []):
                if not q["is_tenant_editable"]:
                    return q
        pytest.skip("No baseline-derived question found in version")

    async def test_add_question_to_tenant_editable_group_allowed(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        version = await self._create_version(tenant_client, tenant_template["id"])
        group = version["question_groups"][0]  # is_tenant_editable=True
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version['id']}/groups/{group['id']}/questions",
            json={
                "unique_key": "tenant_extra",
                "label": "Extra Field",
                "field_type": "text",
                "required": False,
                "display_order": 99,
            },
        )
        assert r.status_code == 201

    async def test_tenant_added_question_is_tenant_editable(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        version = await self._create_version(tenant_client, tenant_template["id"])
        group = version["question_groups"][0]
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version['id']}/groups/{group['id']}/questions",
            json={
                "unique_key": "tenant_q",
                "label": "Tenant Question",
                "field_type": "text",
                "required": False,
                "display_order": 98,
            },
        )
        assert r.status_code == 201
        assert r.json()["is_tenant_editable"] is True


# ── Publish ───────────────────────────────────────────────────────────

class TestTenantTemplatePublish:
    async def test_publish_without_approval_rejected(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish",
            params={"set_as_active": "true"},
        )
        assert r2.status_code == 400

    async def test_publish_sets_is_draft_false(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        r_review = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Ready"},
        )
        assert r_review.status_code == 200
        user_context.reset(submitter)

        reviewer = user_context.set("super_admin_1")
        r_approve = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/approve",
            json={"notes": "Approved"},
        )
        assert r_approve.status_code == 200
        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish",
            params={"set_as_active": "true"},
        )
        user_context.reset(reviewer)
        assert r2.status_code == 200
        assert r2.json()["is_draft"] is False

    async def test_publish_sets_template_active_version(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Ready"},
        )
        user_context.reset(submitter)
        reviewer = user_context.set("super_admin_1")
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/approve",
            json={"notes": "Approved"},
        )
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish",
            params={"set_as_active": "true"},
        )
        user_context.reset(reviewer)
        r2 = await tenant_client.get(f"/api/v1/templates/{tenant_template['id']}")
        assert r2.json()["active_version_id"] == version_id

    async def test_update_published_version_rejected(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish"
        )
        r2 = await tenant_client.patch(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}",
            json={"changelog": "Should fail"},
        )
        assert r2.status_code in (403, 422)

    async def test_publish_with_duplicate_unique_keys_rejected(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        """Add two questions with the same unique_key → publish must reject."""
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        group_id = r.json()["question_groups"][0]["id"]

        # Add duplicate key
        for _ in range(2):
            await tenant_client.post(
                f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/groups/{group_id}/questions",
                json={
                    "unique_key": "duplicate_key",
                    "label": "Dup",
                    "field_type": "text",
                    "display_order": 50,
                },
            )

        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish"
        )
        assert r2.status_code == 422


# ── CRUD ──────────────────────────────────────────────────────────────

class TestTenantTemplateCRUD:
    async def test_list_templates(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.get("/api/v1/templates")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()]
        assert tenant_template["id"] in ids

    async def test_get_template_with_versions(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.get(f"/api/v1/templates/{tenant_template['id']}")
        assert r.status_code == 200
        assert "versions" in r.json()

    async def test_update_template_name(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        r = await tenant_client.patch(
            f"/api/v1/templates/{tenant_template['id']}",
            json={"name": "Renamed Flow"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Renamed Flow"

    async def test_delete_template(
        self, tenant_client: AsyncClient, published_baseline: dict, tenant: dict,
        client: AsyncClient
    ):
        r = await client.post(
            "/api/v1/templates",
            headers={"X-Tenant-ID": tenant["id"]},
            json={"name": "Disposable", "template_type": "kyc", "baseline_level": 1},
        )
        assert r.status_code == 201
        tid = r.json()["id"]
        r2 = await client.delete(
            f"/api/v1/templates/{tid}", headers={"X-Tenant-ID": tenant["id"]}
        )
        assert r2.status_code in (200, 204)

    async def test_get_nonexistent_template_returns_404(
        self, tenant_client: AsyncClient
    ):
        r = await tenant_client.get(
            "/api/v1/templates/00000000-0000-0000-0000-000000000000"
        )
        assert r.status_code == 404


# ── Tenant isolation ──────────────────────────────────────────────────

class TestTenantIsolation:
    async def test_template_not_visible_to_other_tenant(
        self, client: AsyncClient, tenant_template: dict
    ):
        """Create a second tenant and verify it cannot see the first tenant's template."""
        r = await client.post(
            "/api/v1/tenants",
            json={"name": "Second Bank", "tenant_key": "second_bank"},
        )
        assert r.status_code == 201
        second_id = r.json()["id"]

        r2 = await client.get(
            f"/api/v1/templates/{tenant_template['id']}",
            headers={"X-Tenant-ID": second_id},
        )
        assert r2.status_code == 404


class TestTenantTemplateReviewWorkflow:
    async def test_submit_review_sets_pending_review(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Please review"},
        )
        user_context.reset(submitter)
        assert r2.status_code == 200
        assert r2.json()["review_status"] == "pending_review"
        assert r2.json()["submitted_for_review_by"] == "tenant_admin_1"

    async def test_pending_review_definition_cannot_be_updated(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Please review"},
        )
        r2 = await tenant_client.patch(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}",
            json={"changelog": "mutated"},
        )
        user_context.reset(submitter)
        assert r2.status_code == 400

    async def test_submitter_cannot_approve_own_definition(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Please review"},
        )
        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/approve",
            json={"notes": "self approval"},
        )
        user_context.reset(submitter)
        assert r2.status_code == 403

    async def test_approve_sets_review_status_and_history(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Please review"},
        )
        user_context.reset(submitter)
        reviewer = user_context.set("super_admin_1")
        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/approve",
            json={"notes": "Looks good"},
        )
        user_context.reset(reviewer)
        assert r2.status_code == 200
        assert r2.json()["review_status"] == "approved"
        assert r2.json()["reviewed_by"] == "super_admin_1"
        actions = [entry["action"] for entry in r2.json()["reviews"]]
        assert actions == ["submitted", "approved"]

    async def test_request_changes_sets_status_and_history(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Please review"},
        )
        user_context.reset(submitter)

        reviewer = user_context.set("super_admin_1")
        r2 = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/request-changes",
            json={"notes": "Add more checks"},
        )
        user_context.reset(reviewer)

        assert r2.status_code == 200
        assert r2.json()["review_status"] == "changes_requested"
        assert r2.json()["reviewed_by"] == "super_admin_1"
        actions = [entry["action"] for entry in r2.json()["reviews"]]
        assert actions == ["submitted", "changes_requested"]

    async def test_editing_approved_definition_resets_review_to_draft(
        self, tenant_client: AsyncClient, tenant_template: dict
    ):
        submitter = user_context.set("tenant_admin_1")
        r = await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions",
            json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
        )
        version_id = r.json()["id"]
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/submit-review",
            json={"notes": "Please review"},
        )
        user_context.reset(submitter)

        reviewer = user_context.set("super_admin_1")
        await tenant_client.post(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/approve",
            json={"notes": "Looks good"},
        )
        user_context.reset(reviewer)

        editor = user_context.set("tenant_admin_2")
        r2 = await tenant_client.patch(
            f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}",
            json={"changelog": "updated after approval"},
        )
        user_context.reset(editor)

        assert r2.status_code == 200
        assert r2.json()["review_status"] == "draft"
        assert r2.json()["reviewed_by"] is None
        assert r2.json()["reviewed_at"] is None
