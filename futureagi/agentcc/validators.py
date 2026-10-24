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
# metadata into each request as "key_<name>" and reads some of those names
# as the key's authority or identity, so the platform reserves them; it sets
# org_id from the key's organization.
# This is the gateway's list, written once on each side:
# reservedMetadataKeys in agentcc-gateway/internal/auth/keystore.go, and
# test_reserved_key_metadata_parity.py checks the two are equal.
RESERVED_KEY_METADATA = frozenset(
    {
        "org_id",
        "access_groups",
        "type",
        "key_type",
        "role",
        "allow_policy_override",
        "team",
        "id",
        "name",
        "owner",
    }
)


def reserved_key_metadata(metadata) -> list[str]:
    """The reserved entries in a key's metadata, in order."""
    if not isinstance(metadata, dict):
        return []
    return sorted(key for key in metadata if key in RESERVED_KEY_METADATA)
