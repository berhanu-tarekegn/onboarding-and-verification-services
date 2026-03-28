"""Crypto helpers for mobile device challenge authentication."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jose import jwt

from app.core.config import get_settings


def b64u_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64u_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def validate_public_key(algorithm: str, public_key_b64u: str) -> None:
    if algorithm != "ed25519":
        raise ValueError("Only ed25519 is currently supported.")
    raw = b64u_decode(public_key_b64u)
    if len(raw) != 32:
        raise ValueError("ed25519 public keys must be 32 bytes.")
    Ed25519PublicKey.from_public_bytes(raw)


def verify_signature(*, algorithm: str, public_key_b64u: str, signing_input: str, signature_b64u: str) -> bool:
    if algorithm != "ed25519":
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(b64u_decode(public_key_b64u))
        pub.verify(b64u_decode(signature_b64u), signing_input.encode("utf-8"))
        return True
    except Exception:
        return False


def make_nonce() -> str:
    return secrets.token_urlsafe(24)


def make_signing_input(
    *,
    challenge_id: str,
    tenant_key: str,
    subject: str,
    device_id: str,
    nonce: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    return (
        f"challenge_id={challenge_id}\n"
        f"tenant_id={tenant_key}\n"
        f"subject={subject}\n"
        f"device_id={device_id}\n"
        f"nonce={nonce}\n"
        f"issued_at={int(issued_at.timestamp())}\n"
        f"expires_at={int(expires_at.timestamp())}"
    )


def make_expires_at(ttl_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


def fingerprint_key(public_key_b64u: str) -> str:
    return hashlib.sha256(public_key_b64u.encode("utf-8")).hexdigest()


def issue_access_token(
    *,
    subject: str,
    tenant_id: str,
    device_id: str,
    roles: list[str],
) -> tuple[str, int]:
    settings = get_settings()
    secret = (settings.MOBILE_AUTH_HS256_SECRET or "").strip()
    if not secret:
        raise RuntimeError("MOBILE_AUTH_HS256_SECRET is not configured.")

    now = datetime.now(timezone.utc)
    ttl = int(settings.MOBILE_AUTH_ACCESS_TOKEN_TTL_SECONDS)
    payload: dict[str, Any] = {
        "iss": settings.MOBILE_AUTH_ISSUER,
        "sub": subject,
        "tenant_id": tenant_id,
        "device_id": device_id,
        "auth_provider": "device_challenge",
        "realm_access": {"roles": roles},
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    if settings.AUTH_AUDIENCE:
        payload["aud"] = settings.AUTH_AUDIENCE

    return jwt.encode(payload, secret, algorithm="HS256"), ttl
