"""User identity for v1 private resources, separate from workspace authorization."""

from __future__ import annotations

from accounts.models.user import OrgApiKey, User
from model_hub.services.skills_hub.errors import SkillsHubError


def private_user(
    *, user: User, api_key: OrgApiKey | None, required: bool = False
) -> User | None:
    """Never treat APIKeyAuthentication's system-key fallback as a draft owner.

    Authentication and SkillsPermission still establish tenant/workspace access.
    Here only JWT users and keys bound to that exact user have a private identity;
    unknown key types fail closed, including in force-authenticated adapters.
    """
    if api_key is None or (
        api_key.type in {"user", "mcp"}
        and api_key.user_id
        and api_key.user_id == user.pk
    ):
        return user
    if required:
        raise SkillsHubError(
            "principal_not_supported",
            "A real user or a user-bound key is required for private Skills resources.",
            status=403,
        )
    return None
