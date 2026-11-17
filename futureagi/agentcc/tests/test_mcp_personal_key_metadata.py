"""mcp_personal is reserved key metadata (M8).

It will mark a personal key, whose MCP principal acts as a user on per-user
servers. A key's owner field is a label the tenant sets, not a user id, so no
tenant write may set the flag and no stored value reaches the gateway at key
sync; only M25x sets it, together with a server-owned user id. Until then the
gateway gives no key principal a user. The API refusals run in test_api.py's
reserved-metadata tests, which name it.
"""

from agentcc.services.auth_bridge import bound_key_metadata
from agentcc.validators import RESERVED_KEY_METADATA, reserved_key_metadata


def test_mcp_personal_is_reserved():
    assert "mcp_personal" in RESERVED_KEY_METADATA
    assert reserved_key_metadata({"mcp_personal": "true", "env": "prod"}) == [
        "mcp_personal"
    ]


def test_key_sync_never_sends_a_stored_mcp_personal():
    bound = bound_key_metadata({"mcp_personal": "true", "env": "prod"}, "org-1")

    assert bound == {"env": "prod", "org_id": "org-1"}
