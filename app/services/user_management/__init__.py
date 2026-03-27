"""User Management Service client — fetch user profiles from an external service.

When USER_MANAGEMENT_SERVICE_ENABLED is True, CoS calls this service to get
user details (e.g. by JWT sub). Authentication remains via Keycloak; this
service is the source of user profile data only.
"""

from app.services.user_management.client import (
    UserManagementClient,
    get_user_management_client,
)
from app.services.user_management.models import UserProfile

__all__ = [
    "UserManagementClient",
    "UserProfile",
    "get_user_management_client",
]
