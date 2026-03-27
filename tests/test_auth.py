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


class TestJWTAuth(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        private_pem, jwks = _make_rsa_keypair()
        cls.private_pem = private_pem
        cls.jwks = jwks

    async def asyncSetUp(self) -> None:
        os.environ["AUTH_ENABLED"] = "true"
        os.environ["AUTH_TENANT_CLAIM"] = "ovp_claims.tenant_id"
        os.environ["AUTH_AUDIENCE"] = ""
        os.environ["AUTH_ISSUERS"] = ""
        os.environ["KEYCLOAK_TRUSTED_ISSUER_BASES"] = "https://keycloak.dev,https://sso.qena.dev"
        os.environ["KEYCLOAK_JWKS_JSON"] = json.dumps(self.jwks)

        get_settings.cache_clear()
        await auth.refresh_jwks_once()

        app = FastAPI()
        app.add_middleware(auth.JWTAuthMiddleware)

        @app.get("/ovp")
        async def ovp_only(ctx: auth.AuthContext = Depends(auth.require_role("ovp"))):
            return {"ok": True, "tenant_id": ctx.tenant_id}

        @app.get("/me")
        async def me(ctx: auth.AuthContext = Depends(auth.get_current_user)):
            return {"user_id": ctx.user_id, "tenant_id": ctx.tenant_id, "roles": sorted(ctx.roles)}

        @app.get("/tenant/{tenant_id}")
        async def tenant_resource(tenant_id: str, ctx: auth.AuthContext = Depends(auth.get_current_user)):
            auth.enforce_tenant(tenant_id, ctx)
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    def _token(self, *, tenant_id: str, roles: list[str], exp_seconds: int = 300) -> str:
        now = datetime.now(tz=timezone.utc)
        claims = {
            "sub": "user-1",
            "iss": "https://keycloak.dev/realms/ovp",
            "ovp_claims": {"tenant_id": tenant_id},
            "realm_access": {"roles": roles},
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=exp_seconds)).timestamp()),
        }
        return jwt.encode(
            claims,
            self.private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid"},
        )

    async def test_valid_ovp_jwt_allows_access(self) -> None:
        token = self._token(tenant_id="ovp", roles=["ovp"])
        resp = await self.client.get("/ovp", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)

    async def test_wrong_role_for_endpoint_returns_403(self) -> None:
        token = self._token(tenant_id="daf", roles=["daf"])
        resp = await self.client.get("/ovp", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 403)

    async def test_tenant_a_cannot_access_tenant_b_resource(self) -> None:
        token = self._token(tenant_id="ovp", roles=["ovp"])
        resp = await self.client.get("/tenant/daf", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 403)

    async def test_expired_jwt_returns_401(self) -> None:
        token = self._token(tenant_id="ovp", roles=["ovp"], exp_seconds=-10)
        resp = await self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)

    async def test_tampered_jwt_returns_401(self) -> None:
        token = self._token(tenant_id="ovp", roles=["ovp"])
        parts = token.split(".")
        self.assertEqual(len(parts), 3)
        sig = parts[2]
        tampered_sig = ("a" if sig[:1] != "a" else "b") + sig[1:]
        tampered = ".".join([parts[0], parts[1], tampered_sig])
        resp = await self.client.get("/me", headers={"Authorization": f"Bearer {tampered}"})
        self.assertEqual(resp.status_code, 401)

    async def test_missing_jwt_returns_401(self) -> None:
        resp = await self.client.get("/me")
        self.assertEqual(resp.status_code, 401)

    async def test_tenant_claim_template_resolves_from_issuer(self) -> None:
        os.environ["AUTH_TENANT_CLAIM"] = "tenant_id,{realm}_claims.tenant_id"
        get_settings.cache_clear()
        await auth.refresh_jwks_once()

        now = datetime.now(tz=timezone.utc)
        claims = {
            "sub": "user-1",
            "iss": "https://sso.qena.dev/realms/kifiya",
            "kifiya_claims": {"tenant_id": "kifiya"},
            "realm_access": {"roles": ["ovp"]},
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        token = jwt.encode(
            claims,
            self.private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid"},
        )
        resp = await self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tenant_id"], "kifiya")

    async def test_maker_checker_role_conflict_returns_403(self) -> None:
        token = self._token(tenant_id="ovp", roles=["maker", "checker"])
        resp = await self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 403)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "role_conflict")
