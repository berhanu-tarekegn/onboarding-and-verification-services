"""Tests — Configurable submission verification flows."""

from __future__ import annotations

from httpx import AsyncClient


def _verification_rules_config() -> dict:
    return {
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
                    },
                    {
                        "decision": "manual_review",
                        "kyc_level": "level_1",
                        "all": [
                            {"fact": "steps.phone_otp.outcome", "equals": "pass"},
                            {"fact": "steps.fayda_lookup.outcome", "equals": "pass"},
                            {"fact": "steps.name_match.result.score", "gte": 0.8},
                        ],
                        "reason_codes": ["borderline_name_match"],
                    },
                ],
                "fallback": {
                    "decision": "rejected",
                    "kyc_level": "level_0",
                    "reason_codes": ["verification_failed"],
                },
            },
        }
    }


async def _publish_template_with_verification(
    tenant_client: AsyncClient,
    tenant_template: dict,
) -> dict:
    r = await tenant_client.post(
        f"/api/v1/templates/{tenant_template['id']}/definitions",
        json={
            "version_tag": "1.0.0",
            "rules_config": _verification_rules_config(),
            "changelog": "verification demo",
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


async def _create_submission(tenant_client: AsyncClient, template_id: str) -> dict:
    r = await tenant_client.post(
        "/api/v1/submissions",
        json={
            "template_id": template_id,
            "submitter_id": "customer-001",
            "form_data": {
                "phone_number": "+251900000001",
                "national_id": "FN-123",
                "first_name": "Abel",
                "last_name": "Bekele",
            },
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestSubmissionVerificationFlow:
    async def test_can_defer_then_complete_verification(
        self,
        tenant_client: AsyncClient,
        tenant_template: dict,
    ):
        template = await _publish_template_with_verification(tenant_client, tenant_template)
        submission = await _create_submission(tenant_client, template["id"])

        r1 = await tenant_client.post(
            f"/api/v1/submissions/{submission['id']}/verification/start",
            json={"journey": "agent_assisted_offline", "deferred": True},
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "pending"

        r2 = await tenant_client.post(
            f"/api/v1/submissions/{submission['id']}/verification/start",
            json={"journey": "self_service_online", "deferred": False},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "waiting_user_action"
        assert r2.json()["current_step_key"] == "phone_otp"

        r3 = await tenant_client.post(
            f"/api/v1/submissions/{submission['id']}/verification/steps/phone_otp/actions",
            json={"action": "submit_code", "payload": {"otp_code": "000000"}},
        )
        assert r3.status_code == 200, r3.text
        phone_step = next(step for step in r3.json()["steps"] if step["step_key"] == "phone_otp")
        assert phone_step["status"] == "waiting_user_action"
        assert phone_step["error_details"]["code"] == "invalid_otp"

        r4 = await tenant_client.post(
            f"/api/v1/submissions/{submission['id']}/verification/steps/phone_otp/actions",
            json={"action": "submit_code", "payload": {"otp_code": "111111"}},
        )
        assert r4.status_code == 200, r4.text
        assert r4.json()["status"] == "waiting_user_action"
        assert r4.json()["current_step_key"] == "fayda_lookup"

        r5 = await tenant_client.post(
            f"/api/v1/submissions/{submission['id']}/verification/steps/fayda_lookup/actions",
            json={"action": "submit_code", "payload": {"otp_code": "222222"}},
        )
        assert r5.status_code == 200, r5.text
        body = r5.json()
        assert body["status"] == "completed"
        assert body["decision"] == "approved"
        assert body["kyc_level"] == "level_2"
        name_step = next(step for step in body["steps"] if step["step_key"] == "name_match")
        assert name_step["status"] == "completed"
        assert name_step["result_snapshot"]["score"] == 1.0

        r6 = await tenant_client.get(f"/api/v1/submissions/{submission['id']}/full")
        assert r6.status_code == 200, r6.text
        assert r6.json()["verification"]["decision"] == "approved"
        assert r6.json()["verification"]["kyc_level"] == "level_2"

    async def test_start_requires_verification_flow_config(
        self,
        tenant_client: AsyncClient,
        published_tenant_template: dict,
    ):
        submission = await _create_submission(tenant_client, published_tenant_template["id"])
        r = await tenant_client.post(
            f"/api/v1/submissions/{submission['id']}/verification/start",
            json={"journey": "self_service_online"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "verification_flow_missing"
