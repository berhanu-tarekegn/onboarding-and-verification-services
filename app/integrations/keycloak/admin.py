from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeycloakClientCredentials:
    realm: str
    client_id: str
    client_secret: str | None


def _admin_base_url() -> str:
    settings = get_settings()
    base = (settings.KEYCLOAK_ADMIN_BASE_URL or settings.KEYCLOAK_BASE_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("KEYCLOAK_ADMIN_BASE_URL or KEYCLOAK_BASE_URL is required for Keycloak provisioning")
    return base


def _timeout() -> httpx.Timeout:
    settings = get_settings()
    return httpx.Timeout(float(settings.KEYCLOAK_HTTP_TIMEOUT_SECONDS or 10))


def _headers() -> dict[str, str]:
    settings = get_settings()
    raw = (get_settings().KEYCLOAK_HTTP_HEADERS_JSON or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
    return out


async def _admin_token() -> str:
    settings = get_settings()
    realm = settings.KEYCLOAK_ADMIN_REALM or "master"
    token_url = f"{_admin_base_url()}/realms/{realm}/protocol/openid-connect/token"

    # Preferred: service account client_credentials (no admin user/pass).
    if settings.KEYCLOAK_ADMIN_CLIENT_SECRET:
        data = {
            "grant_type": "client_credentials",
            "client_id": settings.KEYCLOAK_ADMIN_CLIENT_ID or "admin-cli",
            "client_secret": settings.KEYCLOAK_ADMIN_CLIENT_SECRET,
        }
    else:
        if not settings.KEYCLOAK_ADMIN_USERNAME or not settings.KEYCLOAK_ADMIN_PASSWORD:
            raise RuntimeError(
                "Provisioning requires either KEYCLOAK_ADMIN_CLIENT_SECRET (client_credentials) "
                "or KEYCLOAK_ADMIN_USERNAME/KEYCLOAK_ADMIN_PASSWORD (password grant)."
            )
        data = {
            "grant_type": "password",
            "client_id": settings.KEYCLOAK_ADMIN_CLIENT_ID or "admin-cli",
            "username": settings.KEYCLOAK_ADMIN_USERNAME,
            "password": settings.KEYCLOAK_ADMIN_PASSWORD,
        }

    async with httpx.AsyncClient(timeout=_timeout(), headers=_headers(), trust_env=False) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        payload = resp.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Failed to obtain Keycloak admin token")
    return token


async def _kc_request(method: str, path: str, *, token: str, json_body: Any | None = None, params: dict[str, Any] | None = None) -> httpx.Response:
    url = f"{_admin_base_url()}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=_timeout(), headers={**_headers(), **headers}, trust_env=False) as client:
        return await client.request(method, url, json=json_body, params=params)


async def ensure_realm(
    realm: str,
    *,
    access_token_lifespan_seconds: int = 900,
    refresh_session_seconds: int = 24 * 60 * 60,
) -> bool:
    """Ensure realm exists with desired token lifespans.

    Note: Keycloak has multiple session/refresh settings; this sets the main access token lifespan and SSO session max.
    """
    token = await _admin_token()

    get_resp = await _kc_request("GET", f"/admin/realms/{realm}", token=token)
    if get_resp.status_code == 200:
        # Patch token config best-effort
        patch = {
            "accessTokenLifespan": access_token_lifespan_seconds,
            "ssoSessionMaxLifespan": refresh_session_seconds,
            "enabled": True,
        }
        await _kc_request("PUT", f"/admin/realms/{realm}", token=token, json_body={**get_resp.json(), **patch})
        return False

    if get_resp.status_code != 404:
        get_resp.raise_for_status()

    payload = {
        "realm": realm,
        "enabled": True,
        "accessTokenLifespan": access_token_lifespan_seconds,
        "ssoSessionMaxLifespan": refresh_session_seconds,
    }
    resp = await _kc_request("POST", "/admin/realms", token=token, json_body=payload)
    if resp.status_code in (201, 204):
        return True
    if resp.status_code == 403:
        raise RuntimeError(
            "Keycloak provisioning account is not allowed to create realms. "
            "In the configured Keycloak admin realm, grant the provisioner service account admin permissions "
            "(for example built-in realm roles in 'master', or admin client roles depending on your Keycloak setup). "
            "Minimum typically includes: 'create-realm' + 'manage-realm' + 'manage-clients' + 'view-realm'."
        )
    resp.raise_for_status()
    return False


async def delete_realm(realm: str) -> None:
    """Best-effort realm deletion (used for compensation on provisioning failures)."""
    token = await _admin_token()
    resp = await _kc_request("DELETE", f"/admin/realms/{quote(realm)}", token=token)
    if resp.status_code in (204, 404):
        return
    # 403 is expected when provisioning account can't delete realms; caller decides whether to treat as fatal.
    resp.raise_for_status()


async def ensure_roles(realm: str, roles: list[str]) -> None:
    token = await _admin_token()
    # list existing
    resp = await _kc_request("GET", f"/admin/realms/{realm}/roles", token=token)
    if resp.status_code == 403:
        raise RuntimeError(
            f"Keycloak provisioning account is not allowed to manage roles in realm '{realm}'. "
            "In the configured Keycloak admin realm, grant the provisioner service account permissions to manage realms/roles. "
            "Minimum typically includes: 'manage-realm' + 'view-realm'."
        )
    resp.raise_for_status()
    existing = {r.get("name") for r in resp.json() if isinstance(r, dict)}
    for role in roles:
        if role in existing:
            continue
        create = await _kc_request(
            "POST", f"/admin/realms/{realm}/roles", token=token, json_body={"name": role}
        )
        if create.status_code == 403:
            raise RuntimeError(
                f"Keycloak provisioning account is not allowed to create roles in realm '{realm}'. "
                "In the configured Keycloak admin realm, grant the provisioner service account permissions to manage realms/roles. "
                "Minimum typically includes: 'manage-realm'."
            )
        if create.status_code not in (201, 204):
            create.raise_for_status()


async def ensure_client_with_mappers(
    realm: str,
    *,
    client_id: str,
    claims_namespace: str,
    tenant_id_value: str,
    confidential: bool = True,
) -> KeycloakClientCredentials:
    """Ensure an OIDC client exists, then attach protocol mappers required by US-201."""
    token = await _admin_token()

    # Find existing client by clientId
    resp = await _kc_request(
        "GET",
        f"/admin/realms/{realm}/clients",
        token=token,
        params={"clientId": client_id},
    )
    resp.raise_for_status()
    items = resp.json()
    internal_id: str | None = None
    if isinstance(items, list) and items:
        internal_id = items[0].get("id")

    if internal_id is None:
        create_payload = {
            "clientId": client_id,
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": not confidential,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": False,
            "serviceAccountsEnabled": False,
        }
        create = await _kc_request(
            "POST", f"/admin/realms/{realm}/clients", token=token, json_body=create_payload
        )
        if create.status_code not in (201, 204):
            create.raise_for_status()
        # Re-fetch
        resp = await _kc_request(
            "GET",
            f"/admin/realms/{realm}/clients",
            token=token,
            params={"clientId": client_id},
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list) or not items:
            raise RuntimeError("Failed to locate created client")
        internal_id = items[0].get("id")

    if not isinstance(internal_id, str) or not internal_id:
        raise RuntimeError("Invalid Keycloak client internal id")

    client_secret: str | None = None
    if confidential:
        secret_resp = await _kc_request(
            "GET", f"/admin/realms/{realm}/clients/{internal_id}/client-secret", token=token
        )
        secret_resp.raise_for_status()
        secret_val = secret_resp.json().get("value")
        if isinstance(secret_val, str) and secret_val:
            client_secret = secret_val

    # Protocol mappers
    mappers_resp = await _kc_request(
        "GET",
        f"/admin/realms/{realm}/clients/{internal_id}/protocol-mappers/models",
        token=token,
    )
    mappers_resp.raise_for_status()
    existing_names = {m.get("name") for m in mappers_resp.json() if isinstance(m, dict)}

    def _add_mapper(name: str, mapper: str, config: dict[str, str]) -> None:
        if name in existing_names:
            return
        payload = {"name": name, "protocol": "openid-connect", "protocolMapper": mapper, "config": config}
        # Fire and forget; errors will be raised by caller if non-2xx
        return payload

    to_create: list[dict[str, Any]] = []

    # allowed_roles
    payload = _add_mapper(
        "allowed_roles",
        "oidc-usermodel-realm-role-mapper",
        {
            "multivalued": "true",
            "claim.name": f"{claims_namespace}.allowed_roles",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    # user_id (Keycloak user UUID)
    payload = _add_mapper(
        "user_id",
        "oidc-usermodel-property-mapper",
        {
            "user.attribute": "id",
            "claim.name": f"{claims_namespace}.user_id",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    # tenant_id (hardcoded per realm)
    payload = _add_mapper(
        "tenant_id",
        "oidc-hardcoded-claim-mapper",
        {
            "claim.name": f"{claims_namespace}.tenant_id",
            "claim.value": tenant_id_value,
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    # national_id (user attribute; used for Ethiopian national ID)
    payload = _add_mapper(
        "national_id",
        "oidc-usermodel-attribute-mapper",
        {
            "user.attribute": "national_id",
            "claim.name": f"{claims_namespace}.national_id",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    # birth_date (user attribute)
    payload = _add_mapper(
        "birth_date",
        "oidc-usermodel-attribute-mapper",
        {
            "user.attribute": "birth_date",
            "claim.name": f"{claims_namespace}.birth_date",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    # phone_number (user attribute)
    payload = _add_mapper(
        "phone_number",
        "oidc-usermodel-attribute-mapper",
        {
            "user.attribute": "phone_number",
            "claim.name": f"{claims_namespace}.phone_number",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    # address (user attribute)
    payload = _add_mapper(
        "address",
        "oidc-usermodel-attribute-mapper",
        {
            "user.attribute": "address",
            "claim.name": f"{claims_namespace}.address",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    )
    if payload:
        to_create.append(payload)

    for p in to_create:
        create_mapper = await _kc_request(
            "POST",
            f"/admin/realms/{realm}/clients/{internal_id}/protocol-mappers/models",
            token=token,
            json_body=p,
        )
        if create_mapper.status_code not in (201, 204):
            create_mapper.raise_for_status()

    return KeycloakClientCredentials(realm=realm, client_id=client_id, client_secret=client_secret)


async def ensure_user(
    realm: str,
    *,
    username: str,
    enabled: bool = True,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    attributes: dict[str, str] | None = None,
    clear_required_actions: bool = True,
) -> str:
    """Ensure a Keycloak user exists and return their user id."""
    token = await _admin_token()

    # Lookup by username
    resp = await _kc_request(
        "GET",
        f"/admin/realms/{realm}/users",
        token=token,
        params={"username": username, "exact": True},
    )
    resp.raise_for_status()
    items = resp.json()
    if isinstance(items, list) and items:
        user_id = items[0].get("id")
        if isinstance(user_id, str) and user_id:
            # Optional: patch user fields/attributes when provided (idempotent).
            if email or first_name or last_name or attributes or clear_required_actions:
                get_u = await _kc_request(
                    "GET",
                    f"/admin/realms/{realm}/users/{quote(user_id)}",
                    token=token,
                )
                if get_u.status_code == 200:
                    current = get_u.json()
                    if isinstance(current, dict):
                        updated: dict[str, Any] = dict(current)
                        if email:
                            updated["email"] = email
                            updated["emailVerified"] = True
                        if first_name:
                            updated["firstName"] = first_name
                        if last_name:
                            updated["lastName"] = last_name
                        if clear_required_actions:
                            updated["requiredActions"] = []
                        if attributes:
                            curr_attrs = updated.get("attributes")
                            merged: dict[str, list[str]] = {}
                            if isinstance(curr_attrs, dict):
                                for k, v in curr_attrs.items():
                                    if isinstance(k, str) and isinstance(v, list) and all(isinstance(i, str) for i in v):
                                        merged[k] = v
                            for k, v in attributes.items():
                                if isinstance(k, str) and k and isinstance(v, str) and v:
                                    merged[k] = [v]
                            if merged:
                                updated["attributes"] = merged
                        await _kc_request(
                            "PUT",
                            f"/admin/realms/{realm}/users/{quote(user_id)}",
                            token=token,
                            json_body=updated,
                        )
            return user_id

    payload: dict[str, Any] = {"username": username, "enabled": enabled}
    if email:
        payload["email"] = email
        payload["emailVerified"] = True
    if first_name:
        payload["firstName"] = first_name
    if last_name:
        payload["lastName"] = last_name
    if clear_required_actions:
        payload["requiredActions"] = []
    if attributes:
        # Keycloak expects attribute values as lists of strings.
        attrs: dict[str, list[str]] = {}
        for k, v in attributes.items():
            if isinstance(k, str) and k and isinstance(v, str) and v:
                attrs[k] = [v]
        if attrs:
            payload["attributes"] = attrs

    create = await _kc_request("POST", f"/admin/realms/{realm}/users", token=token, json_body=payload)
    if create.status_code not in (201, 204):
        create.raise_for_status()

    # Re-fetch
    resp = await _kc_request(
        "GET",
        f"/admin/realms/{realm}/users",
        token=token,
        params={"username": username, "exact": True},
    )
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list) or not items:
        raise RuntimeError("Failed to locate created user")
    user_id = items[0].get("id")
    if not isinstance(user_id, str) or not user_id:
        raise RuntimeError("Invalid Keycloak user id")
    return user_id


async def set_user_password(realm: str, *, user_id: str, password: str, temporary: bool = False) -> None:
    token = await _admin_token()
    payload = {"type": "password", "temporary": temporary, "value": password}
    resp = await _kc_request(
        "PUT",
        f"/admin/realms/{realm}/users/{quote(user_id)}/reset-password",
        token=token,
        json_body=payload,
    )
    if resp.status_code not in (204,):
        resp.raise_for_status()


async def assign_realm_roles(realm: str, *, user_id: str, roles: list[str]) -> None:
    """Assign realm roles to a user (idempotent best-effort)."""
    token = await _admin_token()

    role_reprs: list[dict[str, Any]] = []
    for role in roles:
        r = await _kc_request("GET", f"/admin/realms/{realm}/roles/{quote(role)}", token=token)
        r.raise_for_status()
        rr = r.json()
        if isinstance(rr, dict) and rr.get("name"):
            role_reprs.append(rr)

    if not role_reprs:
        return

    resp = await _kc_request(
        "POST",
        f"/admin/realms/{realm}/users/{quote(user_id)}/role-mappings/realm",
        token=token,
        json_body=role_reprs,
    )
    if resp.status_code not in (204,):
        resp.raise_for_status()
