"""Tests — Submission search with tenant-configured filters and verification results."""

from __future__ import annotations

from httpx import AsyncClient


def _rules_config_with_search() -> dict:
    return {
        "submission_search": {
            "filters": [
                {
                    "key": "national_id",
                    "label": "National ID",
                    "source": "form_data",
                    "path": "national_id",
                    "operators": ["eq", "contains"],
                },
                {
                    "key": "name_match_score",
                    "label": "Name Match Score",
                    "source": "verification",
                    "path": "steps.name_match.result.score",
                    "operators": ["gte", "lte"],
                },
            ]
        },
        "verification_flow": {
            "flow_key": "default",
            "demo_registry": {
                "FN-123": {
                    "otp_code": "222222",
                    "registered_phone": "+251900000002",
                    "first_name": "Abel",
                    "last_name": "Bekele",
                    "date_of_birth": "1990-01-01",
                }
            },
            "steps": [
                {
                    "key": "phone_otp",
                    "name": "Phone OTP",
                    "type": "challenge_response",
                    "adapter": "demo_phone_otp",
                    "input": {"phone_number": "$answers.phone_number"},
                    "demo_code": "111111",
                    "max_attempts": 3,
                },
                {
                    "key": "fayda_lookup",
                    "name": "Fayda OTP",
                    "type": "challenge_response",
                    "adapter": "demo_fayda_otp",
                    "depends_on": ["phone_otp"],
                    "input": {"national_id": "$answers.national_id"},
                    "max_attempts": 3,
                },
                {
                    "key": "name_match",
                    "name": "Name Match",
                    "type": "comparison",
                    "adapter": "demo_fuzzy_match",
                    "depends_on": ["fayda_lookup"],
                    "pairs": [
                        {
                            "label": "First Name",
                            "left": "$answers.first_name",
                            "right": "$steps.fayda_lookup.result.attributes.first_name",
                        },
                        {
                            "label": "Last Name",
                            "left": "$answers.last_name",
                            "right": "$steps.fayda_lookup.result.attributes.last_name",
                        },
                    ],
                    "pass_score_gte": 0.95,
                    "review_score_gte": 0.8,
                },
            ],
            "decision": {
                "rules": [
                    {
                        "decision": "approved",
                        "kyc_level": "level_2",
                        "all": [
                            {"fact": "steps.phone_otp.outcome", "equals": "pass"},
                            {"fact": "steps.fayda_lookup.outcome", "equals": "pass"},
                            {"fact": "steps.name_match.result.score", "gte": 0.95},
                        ],
                        "reason_codes": ["all_checks_passed"],
                    }
                ],
                "fallback": {
                    "decision": "manual_review",
                    "kyc_level": "level_1",
                    "reason_codes": ["verification_pending"],
                },
            },
        },
    }


async def _publish_template_with_search(
    tenant_client: AsyncClient,
    tenant_template: dict,
) -> dict:
    r = await tenant_client.post(
        f"/api/v1/templates/{tenant_template['id']}/definitions",
        json={
            "version_tag": "1.0.0",
            "rules_config": _rules_config_with_search(),
            "changelog": "submission search demo",
        },
    )
    assert r.status_code == 201, r.text
    version_id = r.json()["id"]

    r2 = await tenant_client.post(
        f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish",
        params={"set_as_active": "true"},
    )
    assert r2.status_code == 200, r2.text
    return tenant_template


async def _create_submission(
    tenant_client: AsyncClient,
    template_id: str,
    *,
    national_id: str,
    first_name: str,
    last_name: str,
) -> dict:
    r = await tenant_client.post(
        "/api/v1/submissions",
        json={
            "template_id": template_id,
            "submitter_id": "customer-001",
            "form_data": {
                "phone_number": "+251900000001",
                "national_id": national_id,
                "first_name": first_name,
                "last_name": last_name,
            },
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _complete_verification(tenant_client: AsyncClient, submission_id: str) -> None:
    r1 = await tenant_client.post(
        f"/api/v1/submissions/{submission_id}/verification/start",
        json={"journey": "self_service_online", "deferred": False},
    )
    assert r1.status_code == 200, r1.text

    r2 = await tenant_client.post(
        f"/api/v1/submissions/{submission_id}/verification/steps/phone_otp/actions",
        json={"action": "submit_code", "payload": {"otp_code": "111111"}},
    )
    assert r2.status_code == 200, r2.text

    r3 = await tenant_client.post(
        f"/api/v1/submissions/{submission_id}/verification/steps/fayda_lookup/actions",
        json={"action": "submit_code", "payload": {"otp_code": "222222"}},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["decision"] == "approved"


class TestSubmissionSearch:
    async def test_search_config_exposes_tenant_defined_filters(
        self,
        tenant_client: AsyncClient,
        tenant_template: dict,
    ):
        await _publish_template_with_search(tenant_client, tenant_template)

        r = await tenant_client.get("/api/v1/submissions/search-config")
        assert r.status_code == 200, r.text
        body = r.json()
        configured = {item["key"]: item for item in body["configured_filters"]}
        assert "national_id" in configured
        assert configured["national_id"]["source"] == "form_data"
        assert "name_match_score" in configured
        assert configured["name_match_score"]["source"] == "verification"

    async def test_search_can_filter_by_configured_and_verification_fields(
        self,
        tenant_client: AsyncClient,
        tenant_template: dict,
    ):
        template = await _publish_template_with_search(tenant_client, tenant_template)
        approved = await _create_submission(
            tenant_client,
            template["id"],
            national_id="FN-123",
            first_name="Abel",
            last_name="Bekele",
        )
        await _complete_verification(tenant_client, approved["id"])

        await _create_submission(
            tenant_client,
            template["id"],
            national_id="FN-999",
            first_name="Other",
            last_name="Customer",
        )

        r = await tenant_client.post(
            "/api/v1/submissions/search",
            json={
                "verification_decision": "approved",
                "criteria": [
                    {"key": "national_id", "op": "eq", "value": "FN-123"},
                    {"key": "name_match_score", "op": "gte", "value": 0.95},
                ],
            },
        )
        assert r.status_code == 200, r.text
        items = r.json()
        assert len(items) == 1
        assert items[0]["id"] == approved["id"]
        assert items[0]["verification"]["decision"] == "approved"
        assert items[0]["verification"]["kyc_level"] == "level_2"

    async def test_search_rejects_unknown_configured_filter(
        self,
        tenant_client: AsyncClient,
        tenant_template: dict,
    ):
        template = await _publish_template_with_search(tenant_client, tenant_template)
        await _create_submission(
            tenant_client,
            template["id"],
            national_id="FN-123",
            first_name="Abel",
            last_name="Bekele",
        )

        r = await tenant_client.post(
            "/api/v1/submissions/search",
            json={"criteria": [{"key": "unknown_key", "op": "eq", "value": "x"}]},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "submission_search_filter_unknown"
