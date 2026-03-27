import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.core.errors import add_exception_handlers
from app.core.config import get_settings
from app.routes.auth.routes import router as auth_router


class TestAuthProxy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["DEBUG"] = "false"
        os.environ["KEYCLOAK_BASE_URL"] = "https://sso.qena.dev"
        os.environ["KEYCLOAK_CLIENT_ID"] = "test-client"
        os.environ["KEYCLOAK_CLIENT_SECRET"] = ""
        # Avoid DB fallback in auth proxy during unit tests.
        os.environ["KEYCLOAK_CLIENTS_JSON"] = json.dumps(
            {"kifiya": {"client_id": "test-client", "client_secret": ""}}
        )
        # Leave allow-list empty; realm validation uses Keycloak discovery (mocked in tests).
        os.environ["KEYCLOAK_REALMS"] = ""

        get_settings.cache_clear()

        app = FastAPI()
        add_exception_handlers(app)
        app.include_router(auth_router)

        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_login_proxies_to_keycloak(self) -> None:
        class _Resp:
            status_code = 200

            def json(self):
                return {"access_token": "t", "refresh_token": "r", "expires_in": 300}

        with patch(
            "app.routes.auth.routes._discover_realm_exists_with_attempts",
            new=AsyncMock(return_value=(True, [])),
        ), patch(
            "app.routes.auth.routes._post_to_keycloak_realm_with_fallback",
            new=AsyncMock(return_value=_Resp()),
        ) as mocked:
            resp = await self.client.post(
                "/api/auth/login/kifiya",
                headers={"Content-Type": "application/json"},
                content=json.dumps({"username": "u", "password": "p"}),
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["access_token"], "t")
            mocked.assert_awaited()

    async def test_login_invalid_credentials_returns_401(self) -> None:
        class _Resp:
            status_code = 401

            def json(self):
                return {"error": "invalid_grant"}

        with patch(
            "app.routes.auth.routes._discover_realm_exists_with_attempts",
            new=AsyncMock(return_value=(True, [])),
        ), patch(
            "app.routes.auth.routes._post_to_keycloak_realm_with_fallback",
            new=AsyncMock(return_value=_Resp()),
        ):
            resp = await self.client.post(
                "/api/auth/login/kifiya",
                headers={"Content-Type": "application/json"},
                content=json.dumps({"username": "bad", "password": "bad"}),
            )
            self.assertEqual(resp.status_code, 401)
            payload = resp.json()
            self.assertEqual(payload["error"]["code"], "invalid_credentials")

    async def test_unknown_realm_returns_404(self) -> None:
        with patch(
            "app.routes.auth.routes._discover_realm_exists_with_attempts",
            new=AsyncMock(return_value=(False, [])),
        ):
            resp = await self.client.post(
                "/api/auth/login/unknown-realm",
                headers={"Content-Type": "application/json"},
                content=json.dumps({"username": "u", "password": "p"}),
            )
            self.assertEqual(resp.status_code, 404)

    async def test_service_token_proxies_client_credentials_to_keycloak(self) -> None:
        class _Resp:
            status_code = 200

            def json(self):
                return {"access_token": "svc", "expires_in": 300}

        with patch(
            "app.routes.auth.routes._discover_realm_exists_with_attempts",
            new=AsyncMock(return_value=(True, [])),
        ), patch(
            "app.routes.auth.routes._post_to_keycloak_realm_with_fallback",
            new=AsyncMock(return_value=_Resp()),
        ) as mocked:
            resp = await self.client.post(
                "/api/auth/service-token/kifiya",
                headers={"Content-Type": "application/json"},
                content=json.dumps({"scope": "submissions.read"}),
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["access_token"], "svc")
            _, kwargs = mocked.await_args
            self.assertEqual(kwargs["data"]["grant_type"], "client_credentials")
            self.assertEqual(kwargs["data"]["client_id"], "test-client")
            self.assertEqual(kwargs["data"]["scope"], "submissions.read")

    async def test_service_token_invalid_client_returns_401(self) -> None:
        class _Resp:
            status_code = 401

            def json(self):
                return {"error": "invalid_client"}

        with patch(
            "app.routes.auth.routes._discover_realm_exists_with_attempts",
            new=AsyncMock(return_value=(True, [])),
        ), patch(
            "app.routes.auth.routes._post_to_keycloak_realm_with_fallback",
            new=AsyncMock(return_value=_Resp()),
        ):
            resp = await self.client.post(
                "/api/auth/service-token/kifiya",
                headers={"Content-Type": "application/json"},
                content=json.dumps({}),
            )
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(resp.json()["error"]["code"], "invalid_client_credentials")
