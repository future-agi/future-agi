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
"""

import structlog
from django.core.cache import cache

from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.oauth_provider import ACCESS_PREFIX, CLIENT_PREFIX

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
        return None if django_client_active else "client_inactive"

    if cache.get(f"{CLIENT_PREFIX}{client_id}") is None:
        return "client_unknown"

    stored = cache.get(f"{ACCESS_PREFIX}{token}")
    if not isinstance(stored, dict):
        return "token_revoked"
    if stored.get("client_id") != client_id or str(stored.get("user_id")) != str(
        payload.get("user_id")
    ):
        return "token_mismatch"
    return None
