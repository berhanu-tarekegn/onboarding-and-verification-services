"""HTTP client for the User Management Service.

Calls the external service to fetch user profile by ID (e.g. JWT sub).
Expects the service to expose GET /users/{user_id} or GET /users/me;
configure path via USER_MANAGEMENT_USER_PATH if your API differs.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.core.config import get_settings
from app.services.user_management.models import UserProfile

logger = logging.getLogger(__name__)


class UserManagementClient:
    """Client for the external User Management Service.

    When USER_MANAGEMENT_SERVICE_ENABLED is False, all methods return None
    without making HTTP calls.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def _base_url(self) -> str:
        return self._settings.USER_MANAGEMENT_SERVICE_URL.rstrip("/")

    @property
    def enabled(self) -> bool:
        return (
            self._settings.USER_MANAGEMENT_SERVICE_ENABLED
            and bool(self._settings.USER_MANAGEMENT_SERVICE_URL)
        )

    async def get_user_by_id(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> Optional[UserProfile]:
        """Fetch user profile by ID (e.g. Keycloak sub).

        Pass access_token when your User Management Service expects
        the same JWT (e.g. for /users/me or tenant-scoped lookups).

        Returns None if the service is disabled, or on 4xx/5xx/timeout.
        """
        if not self.enabled:
            return None

        path = self._settings.USER_MANAGEMENT_USER_PATH.strip("/")
        url = f"{self._base_url}/{path}/{user_id}"
        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.USER_MANAGEMENT_TIMEOUT_SECONDS
            ) as client:
                resp = await client.get(url, headers=headers or None)
                if resp.status_code == 404:
                    logger.debug("User %s not found in user management service", user_id)
                    return None
                resp.raise_for_status()
                data = resp.json()
                return UserProfile.model_validate(data)
        except httpx.HTTPStatusError as e:
            logger.warning(
                "User management service returned %s for user %s: %s",
                e.response.status_code,
                user_id,
                e.response.text[:200],
            )
            return None
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("User management service unreachable: %s", e)
            return None

    async def get_current_user(
        self,
        access_token: str,
    ) -> Optional[UserProfile]:
        """Fetch the current user profile using GET /users/me and the given JWT.

        Use this when your User Management Service exposes a /users/me
        endpoint that resolves the user from the Bearer token.
        """
        if not self.enabled:
            return None

        path = self._settings.USER_MANAGEMENT_USER_PATH.strip("/")
        url = f"{self._base_url}/{path}/me"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.USER_MANAGEMENT_TIMEOUT_SECONDS
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 401:
                    return None
                resp.raise_for_status()
                data = resp.json()
                return UserProfile.model_validate(data)
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("User management service /users/me failed: %s", e)
            return None


_client: Optional[UserManagementClient] = None


def get_user_management_client() -> UserManagementClient:
    """Return the shared UserManagementClient instance."""
    global _client
    if _client is None:
        _client = UserManagementClient()
    return _client
