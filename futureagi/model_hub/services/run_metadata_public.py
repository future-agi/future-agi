"""Root-only reservation for public run, run-item, and experiment metadata.

Only the metadata root is a server identity/write namespace. The same closed
reserved-key set drives input rejection and output projection. Matching keys
nested under caller-owned fields (including lists) remain caller data; neither
boundary traverses them or filters arbitrary example, config, or output payloads.
"""

from collections.abc import Mapping

from rest_framework.exceptions import ValidationError

# Above the maximum number of keys admitted by the 262144-byte external-run
# JSON contract (and the 50-entry experiment contract). Bound corrupt/legacy
# objects too; never traverse or stringify the potentially large snapshot.
MAX_METADATA_KEYS = 65536
RUN_WRITE_METADATA_KEY = "_futureagi_run_write"
RESERVED_METADATA_KEYS = frozenset({"native_snapshot", RUN_WRITE_METADATA_KEY})
RESERVED_METADATA_CODE = "reserved_metadata_key"


def public_run_metadata(stored):
    """Copy only public top-level entries; do not mutate or deep-copy storage.

    Non-object/oversized historical values fail closed. Nested caller JSON is
    deliberately untouched: reserved keys are private only at the root of
    the metadata object, not in arbitrary example, config or output payloads.
    """
    if not isinstance(stored, dict) or len(stored) > MAX_METADATA_KEYS:
        return {}
    return {
        key: value for key, value in stored.items() if key not in RESERVED_METADATA_KEYS
    }


def reject_reserved_metadata_keys(payload):
    """Reject the raw request before field coercion or unknown-field handling.

    Like public_run_metadata, inspect only metadata root keys, never nested
    caller data or reserved values. Use non_field_errors so strict adapters do
    not overwrite this typed error
    with the generic unknown 'metadata' error on OFF or immutable update APIs.
    """
    metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
    if isinstance(metadata, Mapping) and any(
        key in metadata for key in RESERVED_METADATA_KEYS
    ):
        raise ValidationError(
            {"non_field_errors": ["Reserved run metadata is not writable."]},
            code=RESERVED_METADATA_CODE,
        )
