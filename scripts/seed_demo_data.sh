#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:7090}"
SAMPLE_JSON="${SAMPLE_JSON:-/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq
require_cmd mktemp

if [[ ! -f "$SAMPLE_JSON" ]]; then
  echo "Sample file not found: $SAMPLE_JSON" >&2
  exit 1
fi

api() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local tenant_header="${4:-}"
  local response_file
  local status
  local -a curl_args

  response_file="$(mktemp)"
  curl_args=(
    -sS
    -o "$response_file"
    -w "%{http_code}"
    -X "$method"
    "$BASE_URL$path"
  )

  if [[ -n "$tenant_header" ]]; then
    curl_args+=(-H "X-Tenant-ID: $tenant_header")
  fi

  if [[ -n "$data" ]]; then
    curl_args+=(-H "Content-Type: application/json" -d "$data")
    status="$(curl "${curl_args[@]}")"
  else
    status="$(curl "${curl_args[@]}")"
  fi

  if [[ "$status" -lt 200 || "$status" -ge 300 ]]; then
    echo "Request failed: $method $path -> HTTP $status" >&2
    cat "$response_file" >&2
    rm -f "$response_file"
    exit 1
  fi

  cat "$response_file"
  rm -f "$response_file"
}

echo "Checking API health..."
api GET "/health" >/dev/null

TENANT_NAME="$(jq -r '.tenant.name' "$SAMPLE_JSON")"
TENANT_KEY="$(jq -r '.tenant.tenant_key' "$SAMPLE_JSON")"
PRODUCT_CODE="$(jq -r '.product.product_code' "$SAMPLE_JSON")"
PRODUCT_NAME="$(jq -r '.product.name' "$SAMPLE_JSON")"
VERSION_TAG="$(jq -r '.template_version.version_tag' "$SAMPLE_JSON")"
CHANGELOG="$(jq -r '.template_version.changelog' "$SAMPLE_JSON")"

BASELINE_NAME="Retail KYC Baseline"

echo "Resolving tenant..."
TENANTS_JSON="$(api GET "/api/v1/tenants")"
TENANT_ID="$(
  echo "$TENANTS_JSON" | jq -r --arg tenant_key "$TENANT_KEY" '
    map(select(.tenant_key == $tenant_key)) | first | .id // empty
  '
)"

if [[ -z "$TENANT_ID" ]]; then
  TENANT_PAYLOAD="$(jq -c '.tenant' "$SAMPLE_JSON")"
  TENANT_RESPONSE="$(api POST "/api/v1/tenants" "$TENANT_PAYLOAD")"
  TENANT_ID="$(echo "$TENANT_RESPONSE" | jq -r '.id')"
  echo "Created tenant: $TENANT_KEY ($TENANT_ID)"
else
  echo "Reusing tenant: $TENANT_KEY ($TENANT_ID)"
fi

echo "Resolving baseline template..."
BASELINES_JSON="$(api GET "/api/v1/baseline-templates?active_only=false")"
BASELINE_ID="$(
  echo "$BASELINES_JSON" | jq -r --arg name "$BASELINE_NAME" '
    map(select(.name == $name and .template_type == "kyc" and .level == 1)) | first | .id // empty
  '
)"

if [[ -z "$BASELINE_ID" ]]; then
  BASELINE_PAYLOAD='{
    "name": "Retail KYC Baseline",
    "description": "Baseline for onboarding",
    "category": "kyc",
    "template_type": "kyc",
    "level": 1,
    "initial_version": {
      "version_tag": "1.0.0",
      "rules_config": {},
      "changelog": "Initial baseline",
      "question_groups": [
        {
          "unique_key": "identity",
          "title": "Identity",
          "display_order": 1,
          "questions": [
            {"unique_key":"first_name","label":"First Name","field_type":"text","required":true,"display_order":1},
            {"unique_key":"last_name","label":"Last Name","field_type":"text","required":true,"display_order":2},
            {"unique_key":"national_id","label":"National ID","field_type":"text","required":true,"display_order":3},
            {"unique_key":"phone_number","label":"Phone Number","field_type":"text","required":true,"display_order":4}
          ]
        }
      ],
      "questions": []
    }
  }'
  BASELINE_RESPONSE="$(api POST "/api/v1/baseline-templates" "$BASELINE_PAYLOAD")"
  BASELINE_ID="$(echo "$BASELINE_RESPONSE" | jq -r '.id')"
  echo "Created baseline template: $BASELINE_ID"
else
  echo "Reusing baseline template: $BASELINE_ID"
fi

echo "Resolving tenant template..."
TENANT_TEMPLATES_JSON="$(api GET "/api/v1/templates" "" "$TENANT_ID")"
TENANT_TEMPLATE_ID="$(
  echo "$TENANT_TEMPLATES_JSON" | jq -r '
    map(select(.name == "Abyssinia Retail Onboarding" and .template_type == "kyc" and .baseline_level == 1)) | first | .id // empty
  '
)"

if [[ -z "$TENANT_TEMPLATE_ID" ]]; then
  TENANT_TEMPLATE_PAYLOAD='{
    "name": "Abyssinia Retail Onboarding",
    "description": "Tenant onboarding template",
    "template_type": "kyc",
    "baseline_level": 1
  }'
  TENANT_TEMPLATE_RESPONSE="$(api POST "/api/v1/templates" "$TENANT_TEMPLATE_PAYLOAD" "$TENANT_ID")"
  TENANT_TEMPLATE_ID="$(echo "$TENANT_TEMPLATE_RESPONSE" | jq -r '.id')"
  echo "Created tenant template: $TENANT_TEMPLATE_ID"
else
  echo "Reusing tenant template: $TENANT_TEMPLATE_ID"
fi

echo "Resolving tenant template definition..."
TENANT_TEMPLATE_FULL="$(api GET "/api/v1/templates/$TENANT_TEMPLATE_ID" "" "$TENANT_ID")"
TENANT_DEF_ID="$(
  echo "$TENANT_TEMPLATE_FULL" | jq -r --arg version_tag "$VERSION_TAG" '
    (.versions // []) | map(select(.version_tag == $version_tag)) | first | .id // empty
  '
)"

if [[ -z "$TENANT_DEF_ID" ]]; then
  RULES_CONFIG="$(jq -c '.template_version.rules_config' "$SAMPLE_JSON")"
  TENANT_DEF_PAYLOAD="$(
    jq -n \
      --arg version_tag "$VERSION_TAG" \
      --arg changelog "$CHANGELOG" \
      --argjson rules "$RULES_CONFIG" \
      '{version_tag:$version_tag, changelog:$changelog, rules_config:$rules, question_groups:[], questions:[]}'
  )"
  TENANT_DEF_RESPONSE="$(api POST "/api/v1/templates/$TENANT_TEMPLATE_ID/definitions" "$TENANT_DEF_PAYLOAD" "$TENANT_ID")"
  TENANT_DEF_ID="$(echo "$TENANT_DEF_RESPONSE" | jq -r '.id')"
  echo "Created tenant template definition: $TENANT_DEF_ID"
else
  echo "Reusing tenant template definition: $TENANT_DEF_ID"
fi

echo "Publishing tenant template definition..."
TENANT_DEF_CURRENT="$(api GET "/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_ID" "" "$TENANT_ID")"
TENANT_DEF_IS_DRAFT="$(echo "$TENANT_DEF_CURRENT" | jq -r '.is_draft')"
TENANT_DEF_REVIEW_STATUS="$(echo "$TENANT_DEF_CURRENT" | jq -r '.review_status')"

if [[ "$TENANT_DEF_IS_DRAFT" == "true" ]]; then
  if [[ "$TENANT_DEF_REVIEW_STATUS" == "draft" || "$TENANT_DEF_REVIEW_STATUS" == "changes_requested" ]]; then
    api POST "/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_ID/submit-review" '{"notes":"Automated demo seed review submission"}' "$TENANT_ID" >/dev/null
    TENANT_DEF_REVIEW_STATUS="pending_review"
  fi
  if [[ "$TENANT_DEF_REVIEW_STATUS" == "pending_review" ]]; then
    api POST "/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_ID/approve" '{"notes":"Automated demo seed approval"}' "$TENANT_ID" >/dev/null
  fi
  api POST "/api/v1/templates/$TENANT_TEMPLATE_ID/definitions/$TENANT_DEF_ID/publish?set_as_active=true" "" "$TENANT_ID" >/dev/null
fi

echo "Resolving product..."
PRODUCTS_JSON="$(api GET "/api/v1/products" "" "$TENANT_ID")"
PRODUCT_ID="$(
  echo "$PRODUCTS_JSON" | jq -r --arg product_code "$PRODUCT_CODE" '
    map(select(.product_code == $product_code)) | first | .id // empty
  '
)"

if [[ -z "$PRODUCT_ID" ]]; then
  PRODUCT_PAYLOAD="$(
    jq -c --arg template_id "$TENANT_TEMPLATE_ID" '.product + {template_id:$template_id}' "$SAMPLE_JSON"
  )"
  PRODUCT_RESPONSE="$(api POST "/api/v1/products" "$PRODUCT_PAYLOAD" "$TENANT_ID")"
  PRODUCT_ID="$(echo "$PRODUCT_RESPONSE" | jq -r '.id')"
  echo "Created product: $PRODUCT_NAME ($PRODUCT_ID)"
else
  PRODUCT_DETAIL="$(api GET "/api/v1/products/$PRODUCT_ID" "" "$TENANT_ID")"
  PRODUCT_TEMPLATE_ID="$(echo "$PRODUCT_DETAIL" | jq -r '.template_id // empty')"
  PRODUCT_STATUS="$(echo "$PRODUCT_DETAIL" | jq -r '.status')"
  if [[ "$PRODUCT_TEMPLATE_ID" != "$TENANT_TEMPLATE_ID" ]]; then
    PRODUCT_PATCH_PAYLOAD="$(
      jq -n --arg template_id "$TENANT_TEMPLATE_ID" '{template_id:$template_id}'
    )"
    api PATCH "/api/v1/products/$PRODUCT_ID" "$PRODUCT_PATCH_PAYLOAD" "$TENANT_ID" >/dev/null
  fi
  echo "Reusing product: $PRODUCT_NAME ($PRODUCT_ID)"
fi

PRODUCT_DETAIL="$(api GET "/api/v1/products/$PRODUCT_ID" "" "$TENANT_ID")"
PRODUCT_STATUS="$(echo "$PRODUCT_DETAIL" | jq -r '.status')"
if [[ "$PRODUCT_STATUS" != "active" ]]; then
  echo "Activating product..."
  api POST "/api/v1/products/$PRODUCT_ID/activate" "" "$TENANT_ID" >/dev/null
fi

create_or_reuse_submission() {
  local index="$1"
  local external_ref
  local payload
  local existing
  local response

  external_ref="$(jq -r ".submissions[$index].external_ref" "$SAMPLE_JSON")"
  existing="$(
    api GET "/api/v1/submissions?external_ref=$external_ref" "" "$TENANT_ID" | jq -r 'first | .id // empty'
  )"
  if [[ -n "$existing" ]]; then
    echo "$existing"
    return
  fi

  payload="$(
    jq -c \
      --arg template_id "$TENANT_TEMPLATE_ID" \
      --arg product_id "$PRODUCT_ID" \
      ".submissions[$index] + {template_id:\$template_id, product_id:\$product_id} | del(.name,.expected_demo_codes,.expected_outcome)" \
      "$SAMPLE_JSON"
  )"
  response="$(api POST "/api/v1/submissions" "$payload" "$TENANT_ID")"
  echo "$response" | jq -r '.id'
}

echo "Resolving submissions..."
SELF_SUBMISSION_ID="$(create_or_reuse_submission 0)"
OFFLINE_SUBMISSION_ID="$(create_or_reuse_submission 1)"
echo "Self-service submission: $SELF_SUBMISSION_ID"
echo "Offline submission: $OFFLINE_SUBMISSION_ID"

echo "Running self-service verification..."
api POST "/api/v1/submissions/$SELF_SUBMISSION_ID/verification/start" '{"journey":"self_service_online","deferred":false}' "$TENANT_ID" >/dev/null
api POST "/api/v1/submissions/$SELF_SUBMISSION_ID/verification/steps/phone_otp/actions" '{"action":"submit_code","payload":{"otp_code":"111111"}}' "$TENANT_ID" >/dev/null
api POST "/api/v1/submissions/$SELF_SUBMISSION_ID/verification/steps/fayda_lookup/actions" '{"action":"submit_code","payload":{"otp_code":"222222"}}' "$TENANT_ID" >/dev/null

echo "Running deferred verification flow..."
api POST "/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/start" '{"journey":"agent_assisted_offline","deferred":true}' "$TENANT_ID" >/dev/null
api POST "/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/start" '{"journey":"self_service_online","deferred":false}' "$TENANT_ID" >/dev/null
api POST "/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/steps/phone_otp/actions" '{"action":"submit_code","payload":{"otp_code":"111111"}}' "$TENANT_ID" >/dev/null
api POST "/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification/steps/fayda_lookup/actions" '{"action":"submit_code","payload":{"otp_code":"333333"}}' "$TENANT_ID" >/dev/null

SELF_VERIFICATION="$(api GET "/api/v1/submissions/$SELF_SUBMISSION_ID/verification" "" "$TENANT_ID")"
OFFLINE_VERIFICATION="$(api GET "/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification" "" "$TENANT_ID")"

echo
echo "Demo seed complete."
echo "BASE_URL=$BASE_URL"
echo "SAMPLE_JSON=$SAMPLE_JSON"
echo "TENANT_ID=$TENANT_ID"
echo "TENANT_KEY=$TENANT_KEY"
echo "BASELINE_ID=$BASELINE_ID"
echo "TENANT_TEMPLATE_ID=$TENANT_TEMPLATE_ID"
echo "TENANT_TEMPLATE_DEFINITION_ID=$TENANT_DEF_ID"
echo "PRODUCT_ID=$PRODUCT_ID"
echo "SELF_SUBMISSION_ID=$SELF_SUBMISSION_ID"
echo "OFFLINE_SUBMISSION_ID=$OFFLINE_SUBMISSION_ID"
echo "SELF_DECISION=$(echo "$SELF_VERIFICATION" | jq -r '.decision // empty')"
echo "SELF_KYC_LEVEL=$(echo "$SELF_VERIFICATION" | jq -r '.kyc_level // empty')"
echo "OFFLINE_DECISION=$(echo "$OFFLINE_VERIFICATION" | jq -r '.decision // empty')"
echo "OFFLINE_KYC_LEVEL=$(echo "$OFFLINE_VERIFICATION" | jq -r '.kyc_level // empty')"
echo
echo "Inspect data with:"
echo "curl -sS \"$BASE_URL/api/v1/submissions/$SELF_SUBMISSION_ID/full\" -H \"X-Tenant-ID: $TENANT_ID\" | jq"
echo "curl -sS \"$BASE_URL/api/v1/submissions/$OFFLINE_SUBMISSION_ID/verification\" -H \"X-Tenant-ID: $TENANT_ID\" | jq"
echo "curl -sS -X POST \"$BASE_URL/api/v1/submissions/search\" -H \"X-Tenant-ID: $TENANT_ID\" -H \"Content-Type: application/json\" -d '{\"verification_decision\":\"approved\",\"verification_kyc_level\":\"level_2\"}' | jq"
