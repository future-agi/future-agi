"""An org's semantic cache settings (docs-agentcc-711, item 4).

The gateway reads an org's cache.enabled, default_ttl and backend, never its
cache.semantic. The semantic tier exists only when the gateway's own config
file turns it on (cache.semantic in config.yaml), for every org on that
gateway, so an org setting saved here, answered with no warning and pushed,
changed nothing.

A new or changed ``cache.semantic`` value is refused with 400
``semantic_cache_not_supported`` (``param`` ``cache.semantic``), as
routing_key_rules refuses routing.mirror. A value the org's active version
already holds is saved unchanged and stays readable, removing it or clearing
it to ``null`` or ``{}`` is never refused, and activating a stored version
writes no new value, so it is never refused either.

The writer runs this after it stored the save's secrets encrypted
(org_config_secrets), and the two values are compared as the gateway would
get them, secrets decrypted: a vector-store key sent back as the mask a
reader got, typed in again, or still stored in clear by a release before
migration 0047 is the same value, so a client that saves back the cache
section it read is not refused.
"""

import copy
from collections.abc import Mapping
from typing import Any

from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.org_config_secrets import decrypt_payload_secrets

SEMANTIC_CACHE_NOT_SUPPORTED = "semantic_cache_not_supported"
PARAM = "cache.semantic"


class SemanticCacheNotSupported(OrgConfigRejected):
    """A save would store a new or changed org semantic cache setting."""

    code = SEMANTIC_CACHE_NOT_SUPPORTED
    param = PARAM

    def __init__(self):
        super().__init__(
            f"{PARAM} is not supported for an organization: the gateway's "
            "semantic cache is set in its own config file, for every "
            "organization at once, so a new or changed value cannot be saved. "
            "Remove it, or keep the value the active version already holds."
        )


def check_semantic_cache(active: Any, config: Any) -> None:
    """Refuse a new or changed ``cache.semantic``.

    ``active`` is the org's active version before the save (``None`` when it
    has none) and ``config`` the version about to be saved.
    """
    new = _value(config)
    if new is not None and new != _value(active):
        raise SemanticCacheNotSupported()


def _value(config: Any) -> Any:
    """``cache.semantic`` with its secrets decrypted; ``None`` when absent or
    empty."""
    cache = getattr(config, "cache", None) if config is not None else None
    if not isinstance(cache, Mapping):
        return None
    value = cache.get("semantic")
    if value is None or value in ({}, [], ""):
        return None
    payload = {"cache": {"semantic": copy.deepcopy(value)}}
    return decrypt_payload_secrets(payload)["cache"]["semantic"]
