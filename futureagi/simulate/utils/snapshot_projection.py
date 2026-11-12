"""Read-side projection of historical agent snapshots; never use for persistence."""

from copy import deepcopy

# AgentDefinition/ProviderCredentials and AgentVersion.restore_from_snapshot.
# public_api_key is ProviderCredentials.config_json's Vapi credential. The old
# edit form also reads token/password, although current models do not store them.
SNAPSHOT_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "encrypted_api_key",
        "encrypted_api_secret",
        "livekit_api_key",
        "livekit_api_secret",
        "public_api_key",
        "token",
        "password",
        "credentials",
        "credentials_legacy",
    }
)

# frontend/src/sections/agents/common.js reads these four root fields. Keep only
# display masks there; nested occurrences have no client contract and are removed.
_SNAPSHOT_DISPLAY_KEYS = frozenset(
    {"livekit_api_key", "livekit_api_secret", "token", "password"}
)


def scrub_snapshot_secrets(snapshot):
    """Return an independent JSON-shaped copy with historical secrets removed.

    Preserve all other keys, order and values. Never decrypt stored ciphertext or
    consult credentials/settings/database. The existing mask helper is pure; its
    import is lazy so secret-free projections need no credential module startup.
    Current ProviderCredentials display overlays remain the serializer's concern.
    """

    def project(value, *, root=False):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in SNAPSHOT_SECRET_KEYS:
                    if root and key in _SNAPSHOT_DISPLAY_KEYS:
                        from agentcc.services.credential_manager import mask_key

                        # Historical JSON may contain malformed credential values.
                        # Never stringify a container (which could expose its data).
                        result[key] = mask_key(
                            item if isinstance(item, str) else "****" if item else ""
                        )
                    continue
                result[key] = project(item)
            return result
        if isinstance(value, list):
            return [project(item) for item in value]
        return deepcopy(value)

    return project(snapshot, root=True)
