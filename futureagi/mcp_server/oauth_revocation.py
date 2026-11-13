"""Whether an MCP OAuth access token has been revoked since it was issued.

Access tokens are Fernet-encrypted payloads, so decrypting one only proves that
this server minted it and that it has not expired. Revocation lives elsewhere,
and depends on which token endpoint issued the token:

* ``/mcp/oauth/token/`` (Django) issues tokens to clients registered as
  ``MCPOAuthClient`` rows. Deleting or deactivating the row revokes them.
* The MCP SDK routes (root ``/register``, ``/token``, ``/revoke``) keep their
  clients and access tokens in the cache. ``/revoke`` deletes the token's
  entry, and a client whose entry is gone can no longer use its tokens.

A token naming a client that neither store knows is refused: no endpoint
issues tokens to unregistered clients, so its client must have been removed.

Access tokens also name the refresh-token family issued with them
(``refresh_family_id``). Both token endpoints revoke a family when one of its
rotated refresh tokens is replayed, and the SDK ``/revoke`` revokes the family
of a refresh token it is given; the family's access tokens end at the same
time. A family that was only superseded by a new authorization keeps its
access tokens. Tokens minted before families were named carry no family and
keep their remaining (at most one hour) lifetime.
"""

import structlog
from django.core.cache import cache
from django.core.exceptions import ValidationError

from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_refresh_token import MCPOAuthRefreshFamily
from mcp_server.oauth_provider import (
    ACCESS_PREFIX,
    CLIENT_PREFIX,
    REFRESH_FAMILY_REVOKED_PREFIX,
)
from mcp_server.refresh_tokens import family_revocation_ends_access_tokens

logger = structlog.get_logger(__name__)


def access_token_revocation_reason(token: str, payload: dict) -> str | None:
    """Why the decrypted access token ``token`` is no longer valid, or None."""
    client_id = payload.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        return "missing_client_id"

    django_client_active = (
        MCPOAuthClient.objects.filter(client_id=client_id)
        .values_list("is_active", flat=True)
        .first()
    )
    if django_client_active is not None:
        if not django_client_active:
            return "client_inactive"
        return _django_family_revocation_reason(payload)

    if cache.get(f"{CLIENT_PREFIX}{client_id}") is None:
        return "client_unknown"

    stored = cache.get(f"{ACCESS_PREFIX}{token}")
    if not isinstance(stored, dict):
        return "token_revoked"
    if stored.get("client_id") != client_id or str(stored.get("user_id")) != str(
        payload.get("user_id")
    ):
        return "token_mismatch"
    family_id = payload.get("refresh_family_id")
    if family_id and cache.get(f"{REFRESH_FAMILY_REVOKED_PREFIX}{family_id}"):
        return "refresh_family_revoked"
    return None


def _django_family_revocation_reason(payload: dict) -> str | None:
    family_id = payload.get("refresh_family_id")
    if not family_id:
        return None
    try:
        family = (
            MCPOAuthRefreshFamily.objects.filter(pk=family_id)
            .values("revoked_at", "revoked_reason")
            .first()
        )
    except (ValueError, ValidationError):
        family = None
    if family is None:
        # Families are removed only with their connection.
        return "refresh_family_unknown"
    if family["revoked_at"] is not None and family_revocation_ends_access_tokens(
        family["revoked_reason"]
    ):
        return "refresh_family_revoked"
    return None
