"""Personal keys (M25x): a key its owner marks personal acts, on MCP servers
that connect each member's own account, as that member.

A key's ``owner`` is a label the tenant sets and may change, so it never
names the member. The member is the key's own ``user`` (who created it), set
by the platform, and only that member may mark the key personal or not
(``set_personal``); an org admin may not, since that would let one member's
key act as another. No key is personal by default.

The mark reaches the gateway through the key sync: the payload of a personal
key carries the reserved entry ``mcp_personal`` whose value is the member's
user id (``payload_metadata``), derived from the mark's row, never from the
key's stored metadata, which the sync strips of reserved names. A mark whose
member is no longer the key's user is not sent.
"""

import structlog
from django.db import transaction

from agentcc.models.api_key import AgentccAPIKey
from agentcc.models.mcp_upstream_oauth import AgentccMCPPersonalKey

logger = structlog.get_logger(__name__)

PERSONAL_METADATA = "mcp_personal"


class NotKeyOwner(PermissionError):
    """Only the member who owns a key marks it personal."""


def is_personal(key: AgentccAPIKey) -> bool:
    return AgentccMCPPersonalKey.objects.filter(
        api_key=key, user_id=key.user_id
    ).exists()


def set_personal(key: AgentccAPIKey, actor, personal: bool) -> bool:
    """Mark ``key`` personal or not, as ``actor``; returns the new state.
    Raises ``NotKeyOwner`` unless ``actor`` is the key's own user."""
    if key.user_id is None or actor is None or key.user_id != actor.pk:
        raise NotKeyOwner("only the key's owner can mark it personal")
    with transaction.atomic():
        AgentccMCPPersonalKey.objects.filter(api_key=key).delete()
        if personal:
            AgentccMCPPersonalKey.objects.create(api_key=key, user_id=key.user_id)
    logger.info(
        "mcp_personal_key_set",
        key_id=str(key.pk),
        org_id=str(key.organization_id),
        user_id=str(key.user_id),
        personal=personal,
    )
    return personal


def payload_metadata(keys) -> dict:
    """{key pk: {"mcp_personal": <user id>}} for the personal keys among
    ``keys``, with one query. A key whose mark names another user than its
    own has no entry."""
    by_pk = {key.pk: key for key in keys}
    if not by_pk:
        return {}
    rows = AgentccMCPPersonalKey.objects.filter(api_key_id__in=by_pk).values_list(
        "api_key_id", "user_id"
    )
    return {
        key_id: {PERSONAL_METADATA: str(user_id)}
        for key_id, user_id in rows
        if by_pk[key_id].user_id is not None and by_pk[key_id].user_id == user_id
    }
