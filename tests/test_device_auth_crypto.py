import os
import unittest
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jose import jwt

from app.core import auth
from app.core.config import get_settings
from app.services.device_auth.crypto import (
    b64u_encode,
    issue_access_token,
    make_signing_input,
    verify_signature,
)


class TestDeviceAuthCrypto(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["AUTH_ENABLED"] = "true"
        os.environ["AUTH_AUDIENCE"] = ""
        os.environ["MOBILE_AUTH_ENABLED"] = "true"
        os.environ["MOBILE_AUTH_ISSUER"] = "ov-mobile"
        os.environ["MOBILE_AUTH_HS256_SECRET"] = "unit-test-mobile-secret"
        get_settings.cache_clear()

    async def test_ed25519_signature_verification(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

        signing_input = make_signing_input(
            challenge_id="ch_123",
            tenant_key="ovp",
            subject="user-123",
            device_id="device-abc",
            nonce="nonce-xyz",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
        signature = private_key.sign(signing_input.encode("utf-8"))

        self.assertTrue(
            verify_signature(
                algorithm="ed25519",
                public_key_b64u=b64u_encode(public_raw),
                signing_input=signing_input,
                signature_b64u=b64u_encode(signature),
            )
        )

    async def test_mobile_access_token_decodes_through_shared_auth_decoder(self) -> None:
        token, _ = issue_access_token(
            subject="user-123",
            tenant_id="ovp",
            device_id="device-abc",
            roles=["maker"],
        )

        payload = await auth.decode_jwt(token)
        self.assertEqual(payload["iss"], "ov-mobile")
        self.assertEqual(payload["tenant_id"], "ovp")
        self.assertEqual(payload["device_id"], "device-abc")
        self.assertEqual(payload["realm_access"]["roles"], ["maker"])

    async def test_mobile_token_wrong_secret_is_rejected(self) -> None:
        token = jwt.encode(
            {
                "iss": "ov-mobile",
                "sub": "user-123",
                "tenant_id": "ovp",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int(datetime.now(timezone.utc).timestamp()) + 300,
            },
            "wrong-secret",
            algorithm="HS256",
        )

        with self.assertRaises(Exception):
            await auth.decode_jwt(token)
