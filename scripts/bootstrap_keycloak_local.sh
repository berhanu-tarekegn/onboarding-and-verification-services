#!/usr/bin/env bash
set -euo pipefail

if [[ -f ".env.keycloak.local" ]]; then
  set -a
  source ".env.keycloak.local"
  set +a
fi

KEYCLOAK_CONTAINER="${KEYCLOAK_CONTAINER:-oaas-keycloak}"
KEYCLOAK_URL="${KEYCLOAK_URL:-http://${KEYCLOAK_HOST:-127.0.0.1}:${KEYCLOAK_PORT:-8080}}"
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-${KC_BOOTSTRAP_ADMIN_USERNAME:-${KEYCLOAK_ADMIN:-admin}}}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-${KC_BOOTSTRAP_ADMIN_PASSWORD:-${KEYCLOAK_ADMIN_PASSWORD:-admin}}}"
KEYCLOAK_HOME="${KEYCLOAK_HOME:-}"
KCADM_BIN="${KCADM_BIN:-}"

PLATFORM_REALM="${PLATFORM_REALM:-${OAAS_PLATFORM_REALM:-oaas-platform}}"
PROVISIONER_REALM="${PROVISIONER_REALM:-${OAAS_PROVISIONER_REALM:-master}}"
PLATFORM_LOGIN_CLIENT_ID="${PLATFORM_LOGIN_CLIENT_ID:-${OAAS_PLATFORM_LOGIN_CLIENT_ID:-oaas-admin-ui}}"
PLATFORM_LOGIN_CLIENT_SECRET="${PLATFORM_LOGIN_CLIENT_SECRET:-${OAAS_PLATFORM_LOGIN_CLIENT_SECRET:-change-me-admin-ui-secret}}"
PROVISIONER_CLIENT_ID="${PROVISIONER_CLIENT_ID:-${OAAS_PROVISIONER_CLIENT_ID:-oaas-provisioner}}"
PROVISIONER_CLIENT_SECRET="${PROVISIONER_CLIENT_SECRET:-${OAAS_PROVISIONER_CLIENT_SECRET:-change-me-provisioner-secret}}"
PLATFORM_ADMIN_USERNAME="${PLATFORM_ADMIN_USERNAME:-${OAAS_PLATFORM_ADMIN_USERNAME:-platform.admin}}"
PLATFORM_ADMIN_PASSWORD="${PLATFORM_ADMIN_PASSWORD:-${OAAS_PLATFORM_ADMIN_PASSWORD:-ChangeMe123!}}"

run_kcadm() {
  "${KCADM_CMD[@]}" "$@"
}

extract_id() {
  sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

resolve_kcadm() {
  if [[ -n "${KCADM_BIN}" && -x "${KCADM_BIN}" ]]; then
    KCADM_CMD=("${KCADM_BIN}")
    return
  fi

  if [[ -n "${KEYCLOAK_HOME}" && -x "${KEYCLOAK_HOME}/bin/kcadm.sh" ]]; then
    KCADM_CMD=("${KEYCLOAK_HOME}/bin/kcadm.sh")
    return
  fi

  if docker inspect "${KEYCLOAK_CONTAINER}" >/dev/null 2>&1; then
    KCADM_CMD=(docker exec "${KEYCLOAK_CONTAINER}" /opt/keycloak/bin/kcadm.sh)
    return
  fi

  echo "Could not find a usable Keycloak admin CLI." >&2
  echo "Use one of these options:" >&2
  echo "  1. Start the Docker Keycloak container: docker compose -f docker-compose.dev.yaml up keycloak -d" >&2
  echo "  2. Set KEYCLOAK_HOME to your local Keycloak install directory" >&2
  echo "  3. Set KCADM_BIN to the full path of kcadm.sh" >&2
  exit 1
}

ensure_role() {
  local realm="$1"
  local role_name="$2"
  if ! run_kcadm get "roles/${role_name}" -r "${realm}" >/dev/null 2>&1; then
    run_kcadm create roles -r "${realm}" -s "name=${role_name}" >/dev/null
  fi
}

ensure_realm() {
  local realm="$1"
  if run_kcadm get "realms/${realm}" >/dev/null 2>&1; then
    return
  fi
  run_kcadm create realms -s "realm=${realm}" -s enabled=true >/dev/null
}

ensure_user() {
  local realm="$1"
  local username="$2"
  local password="$3"

  if ! run_kcadm get users -r "${realm}" -q "username=${username}" | grep -q "\"username\" : \"${username}\""; then
    run_kcadm create users -r "${realm}" -s "username=${username}" -s enabled=true >/dev/null
  fi

  run_kcadm set-password -r "${realm}" --username "${username}" --new-password "${password}" --temporary=false >/dev/null
}

ensure_confidential_client() {
  local realm="$1"
  local client_id="$2"
  local secret="$3"
  local service_accounts_enabled="$4"
  local direct_access_grants="$5"

  local client_uuid
  client_uuid="$(run_kcadm get clients -r "${realm}" -q "clientId=${client_id}" | extract_id)"
  if [[ -z "${client_uuid}" ]]; then
    run_kcadm create clients -r "${realm}" \
      -s "clientId=${client_id}" \
      -s enabled=true \
      -s protocol=openid-connect \
      -s publicClient=false \
      -s clientAuthenticatorType=client-secret \
      -s "secret=${secret}" \
      -s standardFlowEnabled=true \
      -s directAccessGrantsEnabled="${direct_access_grants}" \
      -s serviceAccountsEnabled="${service_accounts_enabled}" >/dev/null
    client_uuid="$(run_kcadm get clients -r "${realm}" -q "clientId=${client_id}" | extract_id)"
  else
    run_kcadm update "clients/${client_uuid}" -r "${realm}" \
      -s enabled=true \
      -s publicClient=false \
      -s clientAuthenticatorType=client-secret \
      -s "secret=${secret}" \
      -s directAccessGrantsEnabled="${direct_access_grants}" \
      -s serviceAccountsEnabled="${service_accounts_enabled}" >/dev/null
  fi

  printf '%s\n' "${client_uuid}"
}

ensure_hardcoded_mapper() {
  local realm="$1"
  local client_uuid="$2"
  local mapper_name="$3"
  local claim_name="$4"
  local claim_value="$5"

  if run_kcadm get "clients/${client_uuid}/protocol-mappers/models" -r "${realm}" | grep -q "\"name\" : \"${mapper_name}\""; then
    return
  fi

  run_kcadm create "clients/${client_uuid}/protocol-mappers/models" -r "${realm}" \
    -s "name=${mapper_name}" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-hardcoded-claim-mapper" \
    -s 'config."claim.name"='"${claim_name}" \
    -s 'config."claim.value"='"${claim_value}" \
    -s 'config."jsonType.label"=String' \
    -s 'config."access.token.claim"=true' \
    -s 'config."id.token.claim"=true' \
    -s 'config."userinfo.token.claim"=true' >/dev/null
}

ensure_service_account_roles() {
  local realm="$1"
  local client_id="$2"
  shift 2
  local required_roles=("$@")

  local client_uuid
  client_uuid="$(run_kcadm get clients -r "${realm}" -q "clientId=${client_id}" | extract_id)"
  if [[ -z "${client_uuid}" ]]; then
    echo "Client '${client_id}' was not found in realm '${realm}'." >&2
    exit 1
  fi

  local service_account_user_id
  service_account_user_id="$(run_kcadm get "clients/${client_uuid}/service-account-user" -r "${realm}" | extract_id)"
  if [[ -z "${service_account_user_id}" ]]; then
    echo "Service account user for client '${client_id}' was not found." >&2
    exit 1
  fi

  if [[ "${realm}" == "master" ]]; then
    run_kcadm add-roles -r "${realm}" \
      --uusername "service-account-${client_id}" \
      --rolename admin \
      --rolename create-realm >/dev/null

    local assigned_realm_roles
    assigned_realm_roles="$(run_kcadm get "users/${service_account_user_id}/role-mappings/realm" -r "${realm}")"

    if ! grep -q '"name" : "admin"' <<<"${assigned_realm_roles}" || ! grep -q '"name" : "create-realm"' <<<"${assigned_realm_roles}"; then
      echo "Provisioner service account in realm 'master' is missing required realm roles: admin create-realm" >&2
      echo "Grant those roles in Keycloak and rerun the bootstrap." >&2
      exit 1
    fi

    return
  fi

  local realm_management_uuid
  realm_management_uuid="$(run_kcadm get clients -r "${realm}" -q "clientId=realm-management" | extract_id)"
  if [[ -z "${realm_management_uuid}" ]]; then
    echo "Keycloak client 'realm-management' was not found in realm '${realm}'." >&2
    exit 1
  fi

  local role
  for role in "${required_roles[@]}"; do
    run_kcadm add-roles -r "${realm}" \
      --uusername "service-account-${client_id}" \
      --cclientid realm-management \
      --rolename "${role}" >/dev/null
  done

  local assigned_roles
  assigned_roles="$(run_kcadm get "users/${service_account_user_id}/role-mappings/clients/${realm_management_uuid}" -r "${realm}")"

  local missing=()
  for role in "${required_roles[@]}"; do
    if ! grep -q "\"name\" : \"${role}\"" <<<"${assigned_roles}"; then
      missing+=("${role}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "Provisioner service account is missing required realm-management roles: ${missing[*]}" >&2
    echo "Fix the service-account role grants in Keycloak and rerun the bootstrap." >&2
    exit 1
  fi
}

declare -a KCADM_CMD
resolve_kcadm

echo "Logging into ${KEYCLOAK_URL} as ${KEYCLOAK_ADMIN_USER}..."
run_kcadm config credentials --server "${KEYCLOAK_URL}" --realm master --user "${KEYCLOAK_ADMIN_USER}" --password "${KEYCLOAK_ADMIN_PASSWORD}" >/dev/null

echo "Ensuring platform realm '${PLATFORM_REALM}' exists..."
ensure_realm "${PLATFORM_REALM}"

echo "Ensuring platform realm role..."
ensure_role "${PLATFORM_REALM}" "super_admin"

echo "Ensuring platform login client..."
platform_client_uuid="$(ensure_confidential_client "${PLATFORM_REALM}" "${PLATFORM_LOGIN_CLIENT_ID}" "${PLATFORM_LOGIN_CLIENT_SECRET}" false true)"
ensure_hardcoded_mapper "${PLATFORM_REALM}" "${platform_client_uuid}" "tenant_id" "tenant_id" "${PLATFORM_REALM}"
ensure_hardcoded_mapper "${PLATFORM_REALM}" "${platform_client_uuid}" "${PLATFORM_REALM}_claims.tenant_id" "${PLATFORM_REALM}_claims.tenant_id" "${PLATFORM_REALM}"

echo "Ensuring provisioner client in admin realm '${PROVISIONER_REALM}'..."
ensure_confidential_client "${PROVISIONER_REALM}" "${PROVISIONER_CLIENT_ID}" "${PROVISIONER_CLIENT_SECRET}" true false >/dev/null

echo "Granting provisioner service-account admin roles..."
ensure_service_account_roles "${PROVISIONER_REALM}" "${PROVISIONER_CLIENT_ID}" \
  create-realm \
  manage-realm \
  manage-users \
  view-users \
  query-users \
  view-realm \
  manage-clients \
  query-clients

echo "Ensuring platform admin user..."
ensure_user "${PLATFORM_REALM}" "${PLATFORM_ADMIN_USERNAME}" "${PLATFORM_ADMIN_PASSWORD}"
run_kcadm add-roles -r "${PLATFORM_REALM}" --uusername "${PLATFORM_ADMIN_USERNAME}" --rolename super_admin >/dev/null || true

cat <<EOF
Keycloak bootstrap complete.

Platform realm: ${PLATFORM_REALM}
Provisioner realm: ${PROVISIONER_REALM}
Platform login client: ${PLATFORM_LOGIN_CLIENT_ID}
Provisioner client: ${PROVISIONER_CLIENT_ID}
Platform admin username: ${PLATFORM_ADMIN_USERNAME}

Use these values in .env:
  KEYCLOAK_CLIENTS_JSON={"${PLATFORM_REALM}":{"client_id":"${PLATFORM_LOGIN_CLIENT_ID}","client_secret":"${PLATFORM_LOGIN_CLIENT_SECRET}"}}
  KEYCLOAK_PLATFORM_REALM=${PLATFORM_REALM}
  KEYCLOAK_ADMIN_REALM=${PROVISIONER_REALM}
  KEYCLOAK_ADMIN_CLIENT_ID=${PROVISIONER_CLIENT_ID}
  KEYCLOAK_ADMIN_CLIENT_SECRET=${PROVISIONER_CLIENT_SECRET}
EOF
