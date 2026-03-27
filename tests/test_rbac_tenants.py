import asyncio
import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jose import jwt

from app.core import auth
from app.core import authz
from app.core.config import get_settings
from app.db.session import get_public_session
from app.routes.tenants.tenant import router as tenant_router
from app.schemas.tenants import TenantUserRead


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(public_numbers.n),
        "e": _b64url_uint(public_numbers.e),
    }
    jwks = {"keys": [jwk]}
    return private_pem, jwks


class _StubExecuteResult:
    def scalars(self):
        return self

    def first(self):
        return None

    def all(self):
        return []


class _StubPublicSession:
    async def execute(self, *_args, **_kwargs):
        return _StubExecuteResult()


async def _fake_public_session():
    yield _StubPublicSession()


class TestTenantsRBAC(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        private_pem, jwks = _make_rsa_keypair()
        cls.private_pem = private_pem
        cls.jwks = jwks

    async def asyncSetUp(self) -> None:
        os.environ["AUTH_ENABLED"] = "true"
        os.environ["AUTH_TENANT_CLAIM"] = "tenant_id,{realm}_claims.tenant_id"
        os.environ["AUTH_AUDIENCE"] = ""
        os.environ["AUTH_ISSUERS"] = ""
        os.environ["KEYCLOAK_ADMIN_REALM"] = "master"
        os.environ["KEYCLOAK_TRUSTED_ISSUER_BASES"] = "https://keycloak.dev"
        os.environ["KEYCLOAK_JWKS_JSON"] = json.dumps(self.jwks)

        get_settings.cache_clear()
        await auth.refresh_jwks_once()

        app = FastAPI()
        app.add_middleware(auth.JWTAuthMiddleware)
        app.include_router(tenant_router, prefix="/api/v1")
        app.dependency_overrides[get_public_session] = _fake_public_session
        app.dependency_overrides[authz._get_public_session] = _fake_public_session

        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    def _token(self, *, roles: list[str], issuer_realm: str = "ovp", tenant_id: str | None = None) -> str:
        now = datetime.now(tz=timezone.utc)
        claims = {
            "sub": "user-1",
            "iss": f"https://keycloak.dev/realms/{issuer_realm}",
            f"{issuer_realm}_claims": {"tenant_id": tenant_id or issuer_realm},
            "realm_access": {"roles": roles},
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        return jwt.encode(
            claims,
            self.private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid"},
        )

    async def test_tenants_missing_jwt_returns_401(self) -> None:
        resp = await self.client.get("/api/v1/tenants")
        self.assertEqual(resp.status_code, 401)

    async def test_tenants_wrong_role_returns_403(self) -> None:
        token = self._token(roles=["maker"])
        resp = await self.client.get(
            "/api/v1/tenants",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_tenant_realm_super_admin_cannot_list_tenants(self) -> None:
        token = self._token(roles=["super_admin"], issuer_realm="ovp")
        resp = await self.client.get(
            "/api/v1/tenants",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "platform_admin_required")

    async def test_master_realm_super_admin_can_list_tenants(self) -> None:
        token = self._token(roles=["super_admin"], issuer_realm="master")
        resp = await self.client.get(
            "/api/v1/tenants",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    async def test_tenant_admin_can_create_user_for_own_tenant(self) -> None:
        tenant_id = uuid4()
        token = self._token(roles=["tenant_admin"], issuer_realm="ovp", tenant_id="ovp")
        tenant = SimpleNamespace(id=tenant_id, tenant_key="ovp", keycloak_realm="ovp")
        created = TenantUserRead(
            realm="ovp",
            user_id="kc-user-1",
            username="ovp-admin",
            national_id="1234",
            roles=["tenant_admin"],
        )

        with (
            patch("app.routes.tenants.tenant.tenant_svc.get_tenant", new=AsyncMock(return_value=tenant)),
            patch("app.routes.tenants.tenant.tenant_svc.create_tenant_user", new=AsyncMock(return_value=created)),
        ):
            resp = await self.client.post(
                f"/api/v1/tenants/{tenant_id}/users",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "national_id": "1234",
                    "username": "ovp-admin",
                    "password": "secret123",
                    "roles": ["tenant_admin"],
                },
            )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["realm"], "ovp")

    async def test_tenant_admin_cannot_manage_other_tenant(self) -> None:
        tenant_id = uuid4()
        token = self._token(roles=["tenant_admin"], issuer_realm="daf", tenant_id="daf")
        tenant = SimpleNamespace(id=tenant_id, tenant_key="ovp", keycloak_realm="ovp")

        with patch("app.routes.tenants.tenant.tenant_svc.get_tenant", new=AsyncMock(return_value=tenant)):
            resp = await self.client.post(
                f"/api/v1/tenants/{tenant_id}/users",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "national_id": "1234",
                    "username": "ovp-admin",
                    "password": "secret123",
                    "roles": ["tenant_admin"],
                },
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "tenant_scope_forbidden")
