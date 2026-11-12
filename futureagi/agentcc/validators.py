import re

SAFE_AGENTCC_NAME_REGEX = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_AGENTCC_NAME_ERROR = (
    "Name can only contain letters, numbers, hyphens, and underscores"
)


def validate_safe_agentcc_name(value: str) -> str:
    cleaned = value.strip()
    if not SAFE_AGENTCC_NAME_REGEX.fullmatch(cleaned):
        raise ValueError(SAFE_AGENTCC_NAME_ERROR)
    return cleaned


# Key metadata names a tenant may never set. The gateway copies a key's
# metadata into each request as "key_<name>", so a name is reserved when the
# gateway reads key_<name> as authority, or when key_<name> would stand in for
# one of the key's own fields:
# - authority: org_id picks the org whose config, provider credentials, budgets
#   and guardrails a key uses; access_groups grants model access groups; role
#   is the RBAC role; team picks the RBAC team (its role and model list) and
#   the team budget the key's spend counts against; allow_policy_override lets
#   a request choose its guardrail policy. The platform sets org_id from the
#   key's organization.
# - the key's own fields: "type" would replace key_type, which decides whether
#   a managed key pays and whether a request is logged at all, and key_type
#   goes with it; id, name and owner would read as the key's identity.
# This is the gateway's list, written once on each side:
# reservedMetadataKeys in agentcc-gateway/internal/auth/keystore.go, and
# test_reserved_key_metadata_parity.py checks the two are equal.
RESERVED_KEY_METADATA = frozenset(
    {
        "org_id",
        "user_id",
        "access_groups",
        "type",
        "key_type",
        "role",
        "allow_policy_override",
        "team",
        "id",
        "name",
        "owner",
        # MCP personal key (M8 reserves it): only M25x sets it, with a
        # server-owned user id; no tenant write may.
        "mcp_personal",
        # A platform-minted key's scope and spend budget (CN22c): only the
        # control plane sets them, from the provisioning records
        # (services/ee_agent_key_policy.py), never from a tenant's metadata.
        "scopes",
        "budget_limit_usd",
        "budget_period",
    }
)


def reserved_key_metadata(metadata) -> list[str]:
    """The reserved entries in a key's metadata, in order."""
    if not isinstance(metadata, dict):
        return []
    return sorted(key for key in metadata if key in RESERVED_KEY_METADATA)
