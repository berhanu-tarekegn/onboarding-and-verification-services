import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
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
from app.middleware.tenants import TenantMiddleware
from app.routes.baseline_templates.routes import router as baseline_router
from app.routes.products.routes import router as products_router
from app.routes.submissions.routes import router as submissions_router
from app.routes.transforms.routes import router as transforms_router


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


class _StubPublicSession:
    async def execute(self, *_args, **_kwargs):
        return _StubExecuteResult()


async def _fake_public_session():
    yield _StubPublicSession()


class TestRBACRoutes(unittest.IsolatedAsyncioTestCase):
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
        os.environ["KEYCLOAK_TRUSTED_ISSUER_BASES"] = "https://keycloak.dev"
        os.environ["KEYCLOAK_JWKS_JSON"] = json.dumps(self.jwks)

        get_settings.cache_clear()
        await auth.refresh_jwks_once()

        app = FastAPI()
        app.add_middleware(TenantMiddleware)
        app.add_middleware(auth.JWTAuthMiddleware)
        app.include_router(baseline_router, prefix="/api/v1")
        app.include_router(products_router, prefix="/api/v1")
        app.include_router(submissions_router, prefix="/api/v1")
        app.include_router(transforms_router, prefix="/api/v1")
        app.dependency_overrides[get_public_session] = _fake_public_session
        app.dependency_overrides[authz._get_public_session] = _fake_public_session

        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    def _token(self, *, sub: str = "user-1", roles: list[str]) -> str:
        now = datetime.now(tz=timezone.utc)
        claims = {
            "sub": sub,
            "iss": "https://keycloak.dev/realms/ovp",
            "ovp_claims": {"tenant_id": "ovp"},
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

    async def test_baseline_templates_create_wrong_role_returns_403(self) -> None:
        token = self._token(roles=["maker"])
        resp = await self.client.post(
            "/api/v1/baseline-templates",
            headers={"Authorization": f"Bearer {token}"},
            json={"template_type": "kyc", "level": 1, "name": "Template 1"},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_baseline_templates_publish_schema_author_returns_403(self) -> None:
        token = self._token(roles=["schema_author"])
        resp = await self.client.post(
            "/api/v1/baseline-templates/00000000-0000-0000-0000-000000000000/definitions/00000000-0000-0000-0000-000000000000/publish",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_products_schema_author_returns_403(self) -> None:
        token = self._token(roles=["schema_author"])
        resp = await self.client.get(
            "/api/v1/products",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "ovp"},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_submissions_create_checker_returns_403(self) -> None:
        token = self._token(roles=["checker"])
        resp = await self.client.post(
            "/api/v1/submissions",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "ovp"},
            json={"template_id": "00000000-0000-0000-0000-000000000000", "form_data": {}},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_submissions_transition_maker_returns_403(self) -> None:
        token = self._token(roles=["maker"])
        resp = await self.client.post(
            "/api/v1/submissions/00000000-0000-0000-0000-000000000000/transition",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "ovp"},
            json={"to_status": "approved"},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_transform_rules_create_maker_returns_403(self) -> None:
        token = self._token(roles=["maker"])
        resp = await self.client.post(
            f"/api/v1/templates/{uuid4()}/transform-rules",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "ovp"},
            json={
                "source_version_id": str(uuid4()),
                "target_version_id": str(uuid4()),
                "changelog": "v2 migration",
                "rules": [],
            },
        )
        self.assertEqual(resp.status_code, 403)
