"""Models for user profile data returned by the User Management Service.

Adjust fields to match your service's API response; extra fields are preserved in
a catch-all so unknown keys are not dropped.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """User profile as returned by the User Management Service.

    Minimal fields expected by CoS; extend or relax as needed to match your API.
    """

    id: str = Field(description="User ID (typically same as Keycloak sub)")
    username: Optional[str] = None
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    realm: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    enabled: Optional[bool] = None

    # Catch-all for any extra fields your service returns
    model_config = {"extra": "allow"}

    def model_dump_extra(self) -> Dict[str, Any]:
        """Return only the extra (non-defined) attributes for forwarding."""
        data = self.model_dump()
        known = set(self.model_fields)
        return {k: v for k, v in data.items() if k not in known}
