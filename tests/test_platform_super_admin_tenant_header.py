import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from jose import jwt

from app.core import auth
from app.core.config import get_settings
from app.core.context import get_current_tenant
from app.core.dependencies import require_tenant_header
from app.middleware.tenants import TenantMiddleware


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


class TestPlatformSuperAdminTenantHeader(unittest.IsolatedAsyncioTestCase):
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
        app.add_middleware(TenantMiddleware)
        app.add_middleware(auth.JWTAuthMiddleware)

        @app.get("/scoped", dependencies=[Depends(require_tenant_header)])
        async def scoped():
            return {"tenant_id": get_current_tenant()}

        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    def _token(self, *, issuer_realm: str, tenant_id: str, roles: list[str]) -> str:
        now = datetime.now(tz=timezone.utc)
        claims = {
            "sub": "user-1",
            "iss": f"https://keycloak.dev/realms/{issuer_realm}",
            f"{issuer_realm}_claims": {"tenant_id": tenant_id},
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

    async def test_regular_tenant_token_cannot_switch_x_tenant_header(self) -> None:
        token = self._token(issuer_realm="ovp", tenant_id="ovp", roles=["tenant_admin"])
        resp = await self.client.get(
            "/scoped",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "daf"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"]["code"], "tenant_mismatch")

    async def test_master_realm_super_admin_can_target_tenant_with_header(self) -> None:
        token = self._token(issuer_realm="master", tenant_id="master", roles=["super_admin"])
        resp = await self.client.get(
            "/scoped",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "ovp"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tenant_id"], "ovp")

    async def test_master_realm_super_admin_requires_x_tenant_header(self) -> None:
        token = self._token(issuer_realm="master", tenant_id="master", roles=["super_admin"])
        resp = await self.client.get(
            "/scoped",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["code"], "missing_tenant_header")
