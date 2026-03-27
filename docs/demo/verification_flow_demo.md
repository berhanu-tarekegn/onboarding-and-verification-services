# Verification Flow Demo

This demo shows a configurable onboarding verification flow with:

- deferred agent-assisted onboarding
- later self-service verification
- phone OTP
- Fayda OTP using a registered phone
- fuzzy name matching
- a demo decision stage that returns a KYC level

The verification runtime is submission-scoped and driven by `rules_config.verification_flow`
on the pinned tenant template definition.

## Demo Rules Config

Use this `rules_config` when creating a tenant template definition:

```json
{
  "verification_flow": {
    "flow_key": "default",
    "demo_registry": {
      "FN-123": {
        "otp_code": "222222",
        "registered_phone": "+251900000002",
        "first_name": "Abel",
        "last_name": "Bekele",
        "date_of_birth": "1990-01-01"
      }
    },
    "steps": [
      {
        "key": "phone_otp",
        "name": "Phone OTP",
        "type": "challenge_response",
        "adapter": "demo_phone_otp",
        "input": {
          "phone_number": "$answers.phone_number"
        },
        "demo_code": "111111",
        "max_attempts": 3
      },
      {
        "key": "fayda_lookup",
        "name": "Fayda OTP",
        "type": "challenge_response",
        "adapter": "demo_fayda_otp",
        "depends_on": ["phone_otp"],
        "input": {
          "national_id": "$answers.national_id"
        },
        "max_attempts": 3
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
            "right": "$steps.fayda_lookup.result.attributes.first_name"
          },
          {
            "label": "Last Name",
            "left": "$answers.last_name",
            "right": "$steps.fayda_lookup.result.attributes.last_name"
          }
        ],
        "pass_score_gte": 0.95,
        "review_score_gte": 0.8
      }
    ],
    "decision": {
      "rules": [
        {
          "decision": "approved",
          "kyc_level": "level_2",
          "all": [
            { "fact": "steps.phone_otp.outcome", "equals": "pass" },
            { "fact": "steps.fayda_lookup.outcome", "equals": "pass" },
            { "fact": "steps.name_match.result.score", "gte": 0.95 }
          ],
          "reason_codes": ["all_checks_passed"]
        },
        {
          "decision": "manual_review",
          "kyc_level": "level_1",
          "all": [
            { "fact": "steps.phone_otp.outcome", "equals": "pass" },
            { "fact": "steps.fayda_lookup.outcome", "equals": "pass" },
            { "fact": "steps.name_match.result.score", "gte": 0.8 }
          ],
          "reason_codes": ["borderline_name_match"]
        }
      ],
      "fallback": {
        "decision": "rejected",
        "kyc_level": "level_0",
        "reason_codes": ["verification_failed"]
      }
    }
  }
}
```

## Demo Submission Payload

Create a submission with answers that match the Fayda demo registry:

```json
{
  "template_id": "REPLACE_WITH_TEMPLATE_ID",
  "submitter_id": "customer-001",
  "form_data": {
    "phone_number": "+251900000001",
    "national_id": "FN-123",
    "first_name": "Abel",
    "last_name": "Bekele"
  }
}
```

## Demo Requests

### 1. Create the submission

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/submissions" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID" \
  -d '{
    "template_id": "REPLACE_WITH_TEMPLATE_ID",
    "submitter_id": "customer-001",
    "form_data": {
      "phone_number": "+251900000001",
      "national_id": "FN-123",
      "first_name": "Abel",
      "last_name": "Bekele"
    }
  }'
```

### 2. Defer verification after agent capture

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/submissions/REPLACE_WITH_SUBMISSION_ID/verification/start" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID" \
  -d '{
    "journey": "agent_assisted_offline",
    "deferred": true
  }'
```

Expected outcome:

- run status is `pending`
- no OTP is dispatched yet

### 3. Start the verification later

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/submissions/REPLACE_WITH_SUBMISSION_ID/verification/start" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID" \
  -d '{
    "journey": "self_service_online",
    "deferred": false
  }'
```

Expected outcome:

- run status becomes `waiting_user_action`
- `current_step_key` is `phone_otp`
- `steps[].action_schema.delivery.demo_code` shows `111111`

### 4. Submit the phone OTP

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/submissions/REPLACE_WITH_SUBMISSION_ID/verification/steps/phone_otp/actions" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID" \
  -d '{
    "action": "submit_code",
    "payload": {
      "otp_code": "111111"
    }
  }'
```

Expected outcome:

- `phone_otp` completes with `outcome=pass`
- run waits on `fayda_lookup`

### 5. Submit the Fayda OTP

```bash
curl -sS -X POST "http://127.0.0.1:7090/api/v1/submissions/REPLACE_WITH_SUBMISSION_ID/verification/steps/fayda_lookup/actions" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID" \
  -d '{
    "action": "submit_code",
    "payload": {
      "otp_code": "222222"
    }
  }'
```

Expected outcome:

- `fayda_lookup` completes with Fayda attributes
- `name_match` runs automatically
- decision returns `approved`
- KYC level becomes `level_2`

### 6. Inspect the full submission view

```bash
curl -sS "http://127.0.0.1:7090/api/v1/submissions/REPLACE_WITH_SUBMISSION_ID/full" \
  -H "X-Tenant-ID: REPLACE_WITH_TENANT_ID"
```

Look for:

- `verification.status`
- `verification.decision`
- `verification.kyc_level`
- `verification.steps`
- `computed_data.verification`
- `validation_results.decision`

## Notes

- This implementation is intentionally config-driven. The runtime tables store execution state, while the
  step definition remains in the pinned template version.
- The demo adapters are:
  - `demo_phone_otp`
  - `demo_fayda_otp`
  - `demo_fuzzy_match`
- The demo decision stage reads normalized facts and returns a KYC level. A future external rule engine
  can replace that stage without changing the verification step model.
