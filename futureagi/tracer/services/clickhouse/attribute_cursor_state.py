"""Immutable server-side de-duplication state for attribute browse cursors.

Attribute keys and values are discovered from a newest-first physical span
walk.  A continuation therefore needs both a physical checkpoint and the set
of already-published logical values.  Copying that set into every signed URL
eventually exceeds proxy request-line limits; copying the complete set into a
new cache value on every page also has quadratic storage cost.

Small and rolling-deploy cursors use immutable content-addressed digest blocks.
Once that legacy representation reaches 4,096 values, it migrates to an
immutable persistent radix set. A page append copies each affected radix
branch once, never once per value, and never rewrites state referenced by an
older cursor. Identical retries converge on the same root while divergent
branches receive different roots, so correctness does not depend on a cache
lock or lease. Legacy append-log, linked, vector, and immutable-block formats
remain readable for rolling deploy compatibility and migrate as needed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.cache import cache

ATTRIBUTE_CURSOR_STATE_VERSION = 1
ATTRIBUTE_CURSOR_STATE_TTL_SECONDS = 24 * 60 * 60
ATTRIBUTE_CURSOR_STATE_CHUNK_SIZE = 64
# Legacy immutable-block states materialize at most this many digests.  Once a
# continuation grows beyond the threshold it is migrated to the persistent
# radix set below: the cursor remains constant-size and each membership/append
# touches a bounded number of small cache objects, so this is no longer a
# user-visible browse ceiling.
ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS = 4_096
ATTRIBUTE_CURSOR_STATE_MAX_COUNT = (1 << 63) - 1
_PACKED_DIGEST_BYTES = 16
_PACKED_DIGEST_VECTOR_BYTES = ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS * _PACKED_DIGEST_BYTES
# Existing deployed cursors embed a tuple of digests.  Keep accepting those
# during a rolling deploy; the next continuation migrates them into immutable
# server-side chunks.
ATTRIBUTE_CURSOR_LEGACY_INLINE_LIMIT = 224
_CACHE_PREFIX = "attribute-cursor-state"
_APPEND_LOG_FORMAT = "append_log"
_IMMUTABLE_BLOCKS_FORMAT = "immutable_blocks"
_IMMUTABLE_BLOCK_FORMAT = "digest_block"
_BLOCK_CACHE_PREFIX = "attribute-cursor-block"
_RADIX_ROOT_FORMAT = "persistent_radix_set"
_RADIX_NODE_FORMAT = "radix_node"
_RADIX_LEAF_FORMAT = "radix_leaf"
_RADIX_CACHE_PREFIX = "attribute-cursor-radix"
_RADIX_LEAF_MAX_DIGESTS = 512
_RADIX_DIGEST_BYTES = 16
_RADIX_MAX_DEPTH = _RADIX_DIGEST_BYTES * 2


class AttributeCursorStateError(ValueError):
    """A continuation's required server-side state is invalid or unavailable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AttributeCursorSeenState:
    """Fully resolved exact de-duplication state for one continuation."""

    digests: tuple[str, ...]
    state_id: str | None
    block_refs: tuple[tuple[str, int], ...] = ()
    count: int | None = None
    radix_root_id: str | None = None
    resource: str | None = None
    binding_digest: str | None = None
    expires_at: int | None = None
    # One state instance lives for one API request. Cache only payloads that
    # were fully content-verified by this request so thousands of candidate
    # membership probes do not turn into thousands of sequential Redis GETs.
    radix_object_cache: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    @property
    def seen_count(self) -> int:
        return len(self.digests) if self.count is None else int(self.count)

    def contains(self, digest: str) -> bool:
        """Return exact membership without materializing a large continuation."""

        if digest in self.digests:
            return True
        if self.radix_root_id is None:
            return False
        if (
            self.resource is None
            or self.binding_digest is None
            or self.expires_at is None
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        return _radix_contains(
            self.radix_root_id,
            digest,
            resource=self.resource,
            binding_digest=self.binding_digest,
            expires_at=self.expires_at,
            object_cache=self.radix_object_cache,
        )


def _ttl_seconds() -> int:
    return max(
        60,
        int(
            getattr(
                settings,
                "ATTRIBUTE_CURSOR_STATE_TTL_SECONDS",
                ATTRIBUTE_CURSOR_STATE_TTL_SECONDS,
            )
        ),
    )


def _current_time_seconds() -> int:
    """Return an integer clock so a radix family shares one exact deadline."""

    return int(time.time())


def _remaining_ttl(expires_at: int) -> int:
    remaining = int(expires_at) - _current_time_seconds()
    if remaining <= 0:
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        )
    return remaining


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def attribute_cursor_binding_digest(*, resource: str, binding: Any) -> str:
    """Return the tenant/query binding persisted in every immutable node."""

    if not resource:
        raise ValueError("attribute cursor resource is required")
    return hashlib.sha256(
        _canonical({"resource": resource, "binding": binding}).encode("utf-8")
    ).hexdigest()


def _cache_key(state_id: str) -> str:
    return f"{_CACHE_PREFIX}:v{ATTRIBUTE_CURSOR_STATE_VERSION}:{state_id}"


def _block_cache_key(block_id: str) -> str:
    return f"{_BLOCK_CACHE_PREFIX}:v{ATTRIBUTE_CURSOR_STATE_VERSION}:{block_id}"


def _radix_cache_key(object_id: str) -> str:
    return f"{_RADIX_CACHE_PREFIX}:v{ATTRIBUTE_CURSOR_STATE_VERSION}:{object_id}"


def _radix_object_id(
    *,
    resource: str,
    binding_digest: str,
    payload: dict[str, Any],
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "resource": resource,
                "binding": binding_digest,
                **payload,
            }
        ).encode("utf-8")
    ).hexdigest()


def _radix_root_id(
    *,
    resource: str,
    binding_digest: str,
    count: int,
    radix_root_id: str,
    expires_at: int,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "format": _RADIX_ROOT_FORMAT,
                "resource": resource,
                "binding": binding_digest,
                "count": count,
                "radix_root": radix_root_id,
                "expires_at": expires_at,
            }
        ).encode("utf-8")
    ).hexdigest()


def _immutable_block_id(
    *,
    resource: str,
    binding_digest: str,
    count: int,
    packed: bytes,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"attribute-cursor-block\0")
    digest.update(resource.encode("utf-8"))
    digest.update(b"\0")
    digest.update(binding_digest.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(count).encode("ascii"))
    digest.update(b"\0")
    digest.update(packed)
    return digest.hexdigest()


def _immutable_root_id(
    *,
    resource: str,
    binding_digest: str,
    count: int,
    blocks: tuple[tuple[str, int], ...],
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "format": _IMMUTABLE_BLOCKS_FORMAT,
                "resource": resource,
                "binding": binding_digest,
                "count": count,
                "blocks": blocks,
            }
        ).encode("utf-8")
    ).hexdigest()


def _validate_digest_tuple(
    values: Iterable[Any], validate_digest: Callable[[str], bool]
) -> tuple[str, ...]:
    normalized = tuple(str(value) for value in values)
    if len(set(normalized)) != len(normalized) or any(
        not validate_digest(value) for value in normalized
    ):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    return normalized


def _radix_add_object(
    payload: dict[str, Any],
    *,
    resource: str,
    binding_digest: str,
    expires_at: int,
) -> str:
    """Content-address and persist one immutable radix object."""

    if payload.get("expires_at") != expires_at:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    object_id = _radix_object_id(
        resource=resource,
        binding_digest=binding_digest,
        payload=payload,
    )
    stored = {
        "v": ATTRIBUTE_CURSOR_STATE_VERSION,
        "resource": resource,
        "binding": binding_digest,
        "id": object_id,
        **payload,
    }
    key = _radix_cache_key(object_id)
    timeout = _remaining_ttl(expires_at)
    try:
        created = cache.add(key, stored, timeout=timeout)
        if created:
            return object_id
        existing = cache.get(key)
    except Exception as exc:
        raise AttributeCursorStateError(
            "cursor_state_unavailable",
            "A continuation could not be created. Please retry.",
        ) from exc
    if existing != stored:
        raise AttributeCursorStateError(
            "cursor_state_unavailable",
            "A continuation could not be created. Please retry.",
        )
    return object_id


def _radix_load_object(
    object_id: str,
    *,
    resource: str,
    binding_digest: str,
    expires_at: int,
    object_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load and content-verify one immutable radix object."""

    _remaining_ttl(expires_at)
    if not isinstance(object_id, str) or len(object_id) != 64:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if object_cache is not None and object_id in object_cache:
        return object_cache[object_id]
    try:
        stored = cache.get(_radix_cache_key(object_id))
    except Exception as exc:
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        ) from exc
    if not isinstance(stored, dict):
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        )
    if (
        stored.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
        or stored.get("resource") != resource
        or stored.get("binding") != binding_digest
        or stored.get("id") != object_id
        or stored.get("expires_at") != expires_at
    ):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    object_format = stored.get("format")
    try:
        depth = int(stored["depth"])
        count = int(stored["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        ) from exc
    if (
        not 0 <= depth <= _RADIX_MAX_DEPTH
        or not 1 <= count <= ATTRIBUTE_CURSOR_STATE_MAX_COUNT
    ):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if object_format == _RADIX_LEAF_FORMAT:
        raw_digests = stored.get("digests")
        if not isinstance(raw_digests, (tuple, list)):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        digests = tuple(str(value) for value in raw_digests)
        if (
            count != len(digests)
            or not 1 <= len(digests) <= _RADIX_LEAF_MAX_DIGESTS
            or tuple(sorted(set(digests))) != digests
            or any(
                len(value) != _RADIX_DIGEST_BYTES * 2
                or any(char not in "0123456789abcdef" for char in value)
                for value in digests
            )
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        payload = {
            "format": _RADIX_LEAF_FORMAT,
            "depth": depth,
            "count": count,
            "digests": digests,
            "expires_at": expires_at,
        }
    elif object_format == _RADIX_NODE_FORMAT:
        raw_children = stored.get("children")
        if not isinstance(raw_children, (tuple, list)) or depth >= _RADIX_MAX_DEPTH:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        children: list[tuple[str, str, int]] = []
        for raw_child in raw_children:
            if not isinstance(raw_child, (tuple, list)) or len(raw_child) != 3:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            selector, child_id, raw_count = raw_child
            try:
                child_count = int(raw_count)
            except (TypeError, ValueError) as exc:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                ) from exc
            if (
                not isinstance(selector, str)
                or len(selector) != 1
                or any(char not in "0123456789abcdef" for char in selector)
                or not isinstance(child_id, str)
                or len(child_id) != 64
                or not 1 <= child_count <= ATTRIBUTE_CURSOR_STATE_MAX_COUNT
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            children.append((selector, child_id, child_count))
        normalized_children = tuple(children)
        if (
            not 1 <= len(normalized_children) <= 16
            or tuple(sorted(normalized_children)) != normalized_children
            or len({selector for selector, _child, _count in normalized_children})
            != len(normalized_children)
            or sum(
                child_count for _selector, _child, child_count in normalized_children
            )
            != count
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        payload = {
            "format": _RADIX_NODE_FORMAT,
            "depth": depth,
            "count": count,
            "children": normalized_children,
            "expires_at": expires_at,
        }
    else:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if object_id != _radix_object_id(
        resource=resource,
        binding_digest=binding_digest,
        payload=payload,
    ):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if object_cache is not None:
        object_cache[object_id] = payload
    return payload


def _radix_build(
    digests: Iterable[str],
    *,
    depth: int,
    resource: str,
    binding_digest: str,
    expires_at: int,
) -> tuple[str, int]:
    values = tuple(sorted(set(digests)))
    if not values:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if len(values) <= _RADIX_LEAF_MAX_DIGESTS:
        payload = {
            "format": _RADIX_LEAF_FORMAT,
            "depth": depth,
            "count": len(values),
            "digests": values,
            "expires_at": expires_at,
        }
        return (
            _radix_add_object(
                payload,
                resource=resource,
                binding_digest=binding_digest,
                expires_at=expires_at,
            ),
            len(values),
        )
    if depth >= _RADIX_MAX_DEPTH:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    groups: dict[str, list[str]] = {}
    for digest in values:
        groups.setdefault(digest[depth : depth + 1], []).append(digest)
    children = []
    for selector, group in sorted(groups.items()):
        child_id, child_count = _radix_build(
            group,
            depth=depth + 1,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        )
        children.append((selector, child_id, child_count))
    payload = {
        "format": _RADIX_NODE_FORMAT,
        "depth": depth,
        "count": len(values),
        "children": tuple(children),
        "expires_at": expires_at,
    }
    return (
        _radix_add_object(
            payload,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        ),
        len(values),
    )


def _radix_contains(
    root_id: str,
    digest: str,
    *,
    resource: str,
    binding_digest: str,
    expires_at: int,
    object_cache: dict[str, dict[str, Any]] | None = None,
) -> bool:
    _remaining_ttl(expires_at)
    if (
        not isinstance(digest, str)
        or len(digest) != _RADIX_DIGEST_BYTES * 2
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    current_id = root_id
    while True:
        payload = _radix_load_object(
            current_id,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
            object_cache=object_cache,
        )
        if payload["format"] == _RADIX_LEAF_FORMAT:
            return digest in payload["digests"]
        depth = payload["depth"]
        selector = digest[depth : depth + 1]
        child = next(
            (
                child_id
                for child_selector, child_id, _count in payload["children"]
                if child_selector == selector
            ),
            None,
        )
        if child is None:
            return False
        current_id = child


def _radix_insert_many(
    root_id: str,
    digests: Iterable[str],
    *,
    resource: str,
    binding_digest: str,
    expires_at: int,
) -> tuple[str, int, int]:
    """Copy each affected radix branch once for one exact page append.

    Inserting values one at a time creates a new leaf and every ancestor for
    every value. Besides Redis write amplification, those obsolete immutable
    versions can evict untouched children that the newly published root still
    references in bounded caches. Grouping the page by selector produces one
    new object per affected branch level while retaining the same immutable,
    content-addressed retry and branching semantics.
    """

    values = tuple(sorted(set(digests)))
    if not values:
        payload = _radix_load_object(
            root_id,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        )
        return root_id, payload["count"], 0
    payload = _radix_load_object(
        root_id,
        resource=resource,
        binding_digest=binding_digest,
        expires_at=expires_at,
    )
    if payload["format"] == _RADIX_LEAF_FORMAT:
        prior_values = set(payload["digests"])
        if any(digest in prior_values for digest in values):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        return (
            *_radix_build(
                (*payload["digests"], *values),
                depth=payload["depth"],
                resource=resource,
                binding_digest=binding_digest,
                expires_at=expires_at,
            ),
            len(values),
        )

    depth = payload["depth"]
    children = {
        child_selector: (child_id, child_count)
        for child_selector, child_id, child_count in payload["children"]
    }
    grouped: dict[str, list[str]] = {}
    for digest in values:
        grouped.setdefault(digest[depth : depth + 1], []).append(digest)

    added_count = 0
    for selector, group in sorted(grouped.items()):
        prior_child = children.get(selector)
        if prior_child is None:
            child_id, child_count = _radix_build(
                group,
                depth=depth + 1,
                resource=resource,
                binding_digest=binding_digest,
                expires_at=expires_at,
            )
            child_added_count = len(group)
        else:
            child_id, child_count, child_added_count = _radix_insert_many(
                prior_child[0],
                group,
                resource=resource,
                binding_digest=binding_digest,
                expires_at=expires_at,
            )
        if child_added_count != len(group):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        added_count += child_added_count
        children[selector] = (child_id, child_count)
    normalized_children = tuple(
        (child_selector, stored_id, stored_count)
        for child_selector, (stored_id, stored_count) in sorted(children.items())
    )
    count = payload["count"] + added_count
    new_payload = {
        "format": _RADIX_NODE_FORMAT,
        "depth": depth,
        "count": count,
        "children": normalized_children,
        "expires_at": expires_at,
    }
    return (
        _radix_add_object(
            new_payload,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        ),
        count,
        added_count,
    )


def _pack_digest_vector(values: tuple[str, ...]) -> bytes:
    """Encode ordered 128-bit digests into one fixed-capacity exact vector."""

    try:
        packed = b"".join(bytes.fromhex(value) for value in values)
    except ValueError as exc:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        ) from exc
    if len(packed) != len(values) * _PACKED_DIGEST_BYTES:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    return packed.ljust(_PACKED_DIGEST_VECTOR_BYTES, b"\0")


def _pack_digest_log(values: tuple[str, ...]) -> bytes:
    """Encode only the published prefix; the 4,096-value maximum is 64 KiB."""

    try:
        packed = b"".join(bytes.fromhex(value) for value in values)
    except ValueError as exc:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        ) from exc
    if len(packed) != len(values) * _PACKED_DIGEST_BYTES:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    return packed


def _unpack_digest_log(
    packed: Any,
    *,
    count: int,
    validate_digest: Callable[[str], bool],
) -> tuple[str, ...]:
    if (
        not isinstance(packed, bytes)
        or not 1 <= count <= ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
        or len(packed) != count * _PACKED_DIGEST_BYTES
    ):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    return _validate_digest_tuple(
        (
            packed[offset : offset + _PACKED_DIGEST_BYTES].hex()
            for offset in range(0, len(packed), _PACKED_DIGEST_BYTES)
        ),
        validate_digest,
    )


def _unpack_digest_vector(
    packed: Any,
    *,
    count: int,
    validate_digest: Callable[[str], bool],
) -> tuple[str, ...]:
    if not isinstance(packed, bytes) or len(packed) != _PACKED_DIGEST_VECTOR_BYTES:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if not 1 <= count <= ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    used_bytes = count * _PACKED_DIGEST_BYTES
    if any(packed[used_bytes:]):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    return _validate_digest_tuple(
        (
            packed[offset : offset + _PACKED_DIGEST_BYTES].hex()
            for offset in range(0, used_bytes, _PACKED_DIGEST_BYTES)
        ),
        validate_digest,
    )


def _touch_or_fail(key: str) -> None:
    try:
        touched = cache.touch(key, timeout=_ttl_seconds())
    except Exception as exc:
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        ) from exc
    if touched is not True:
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        )


def load_attribute_cursor_seen_state(
    reference: Any,
    *,
    resource: str,
    binding: Any,
    validate_digest: Callable[[str], bool],
) -> AttributeCursorSeenState:
    """Resolve and validate exact de-duplication state for one continuation.

    New roots and every referenced block are content-verified before use. All
    required blocks are renewed before the root, so a renewed root never
    advertises a dependency that this load already found expired.
    """

    if reference in (None, (), []):
        return AttributeCursorSeenState((), None)
    prefix_count: int | None = None
    if isinstance(reference, tuple):
        if len(reference) == 3 and reference[0] == "state":
            state_id = reference[1]
            prefix_count = reference[2]
        elif len(reference) == 2 and reference[0] == "state":
            state_id = reference[1]
        else:
            # Legacy inline digest tuple from the previous release.
            if len(reference) > ATTRIBUTE_CURSOR_LEGACY_INLINE_LIMIT:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            return AttributeCursorSeenState(
                _validate_digest_tuple(reference, validate_digest), None
            )
    elif isinstance(reference, list):
        if len(reference) == 3 and reference[0] == "state":
            state_id = reference[1]
            prefix_count = reference[2]
        elif len(reference) == 2 and reference[0] == "state":
            state_id = reference[1]
        else:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
    else:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if not isinstance(state_id, str) or len(state_id) != 64:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if prefix_count is not None:
        try:
            prefix_count = int(prefix_count)
        except (TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        if not 1 <= prefix_count <= ATTRIBUTE_CURSOR_STATE_MAX_COUNT:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )

    binding_digest = attribute_cursor_binding_digest(resource=resource, binding=binding)
    # Accept immutable blocks plus the older formats emitted by immediately
    # preceding builds during a rolling deploy.
    leaf_key = _cache_key(state_id)
    try:
        leaf = cache.get(leaf_key)
    except Exception as exc:
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        ) from exc
    if leaf is None:
        raise AttributeCursorStateError(
            "expired_cursor",
            "The continuation cursor has expired. Please restart the search.",
        )
    if isinstance(leaf, dict) and leaf.get("format") == _RADIX_ROOT_FORMAT:
        if (
            leaf.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
            or leaf.get("resource") != resource
            or leaf.get("binding") != binding_digest
            or leaf.get("id") != state_id
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        try:
            stored_count = int(leaf["count"])
            expires_at = int(leaf["expires_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        radix_root_id = leaf.get("radix_root")
        if (
            not 1 <= stored_count <= ATTRIBUTE_CURSOR_STATE_MAX_COUNT
            or isinstance(leaf.get("expires_at"), bool)
            or leaf.get("expires_at") != expires_at
            or prefix_count not in (None, stored_count)
            or not isinstance(radix_root_id, str)
            or len(radix_root_id) != 64
            or state_id
            != _radix_root_id(
                resource=resource,
                binding_digest=binding_digest,
                count=stored_count,
                radix_root_id=radix_root_id,
                expires_at=expires_at,
            )
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        radix_payload = _radix_load_object(
            radix_root_id,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        )
        if radix_payload["count"] != stored_count:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        # Radix continuations have one absolute 24-hour lifetime. Do not renew
        # only the tiny root while untouched descendants approach expiry; a
        # partially renewed exact set could otherwise fail open on a duplicate.
        return AttributeCursorSeenState(
            (),
            state_id,
            (),
            stored_count,
            radix_root_id,
            resource,
            binding_digest,
            expires_at,
            {radix_root_id: radix_payload},
        )
    if isinstance(leaf, dict) and leaf.get("format") == _IMMUTABLE_BLOCKS_FORMAT:
        if (
            leaf.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
            or leaf.get("resource") != resource
            or leaf.get("binding") != binding_digest
            or leaf.get("id") != state_id
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        try:
            stored_count = int(leaf["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        raw_blocks = leaf.get("blocks")
        if not isinstance(raw_blocks, (tuple, list)) or not raw_blocks:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        block_refs: list[tuple[str, int]] = []
        for raw_ref in raw_blocks:
            if not isinstance(raw_ref, (tuple, list)) or len(raw_ref) != 2:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            block_id, raw_count = raw_ref
            try:
                block_count = int(raw_count)
            except (TypeError, ValueError) as exc:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                ) from exc
            if (
                not isinstance(block_id, str)
                or len(block_id) != 64
                or block_count < 1
                or block_count > ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
                or block_count & (block_count - 1)
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            block_refs.append((block_id, block_count))
        normalized_blocks = tuple(block_refs)
        if (
            stored_count < 1
            or stored_count > ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
            or sum(count for _block_id, count in normalized_blocks) != stored_count
            or any(
                left_count <= right_count
                for (_left_id, left_count), (_right_id, right_count) in zip(
                    normalized_blocks, normalized_blocks[1:], strict=False
                )
            )
            or prefix_count not in (None, stored_count)
            or state_id
            != _immutable_root_id(
                resource=resource,
                binding_digest=binding_digest,
                count=stored_count,
                blocks=normalized_blocks,
            )
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )

        block_keys = {
            _block_cache_key(block_id): (block_id, block_count)
            for block_id, block_count in normalized_blocks
        }
        try:
            stored_blocks = cache.get_many(tuple(block_keys))
        except Exception as exc:
            raise AttributeCursorStateError(
                "expired_cursor",
                "The continuation cursor has expired. Please restart the search.",
            ) from exc
        if not isinstance(stored_blocks, Mapping) or set(stored_blocks) != set(
            block_keys
        ):
            raise AttributeCursorStateError(
                "expired_cursor",
                "The continuation cursor has expired. Please restart the search.",
            )
        digest_parts: list[str] = []
        for block_id, block_count in normalized_blocks:
            block_key = _block_cache_key(block_id)
            block = stored_blocks[block_key]
            if (
                not isinstance(block, dict)
                or block.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
                or block.get("format") != _IMMUTABLE_BLOCK_FORMAT
                or block.get("resource") != resource
                or block.get("binding") != binding_digest
                or block.get("id") != block_id
                or block.get("count") != block_count
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            packed = block.get("digest_log")
            if (
                not isinstance(packed, bytes)
                or block.get("digest_log_sha256") != hashlib.sha256(packed).hexdigest()
                or block_id
                != _immutable_block_id(
                    resource=resource,
                    binding_digest=binding_digest,
                    count=block_count,
                    packed=packed,
                )
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            digest_parts.extend(
                _unpack_digest_log(
                    packed,
                    count=block_count,
                    validate_digest=validate_digest,
                )
            )
        digests = tuple(digest_parts)
        if len(digests) != stored_count or len(set(digests)) != stored_count:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        for block_key in block_keys:
            _touch_or_fail(block_key)
        _touch_or_fail(leaf_key)
        return AttributeCursorSeenState(digests, state_id, normalized_blocks)
    if isinstance(leaf, dict) and leaf.get("format") == _APPEND_LOG_FORMAT:
        if (
            prefix_count is None
            or leaf.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
            or leaf.get("resource") != resource
            or leaf.get("binding") != binding_digest
            or leaf.get("id") != state_id
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        try:
            stored_count = int(leaf["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        packed = leaf.get("digest_log")
        if (
            not isinstance(packed, bytes)
            or leaf.get("digest_log_sha256") != hashlib.sha256(packed).hexdigest()
            or prefix_count > stored_count
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        all_digests = _unpack_digest_log(
            packed,
            count=stored_count,
            validate_digest=validate_digest,
        )
        _touch_or_fail(leaf_key)
        return AttributeCursorSeenState(all_digests[:prefix_count], state_id)
    if prefix_count is not None:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    if isinstance(leaf, dict) and "digest_vector" in leaf:
        if (
            leaf.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
            or leaf.get("resource") != resource
            or leaf.get("binding") != binding_digest
            or leaf.get("id") != state_id
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        try:
            count = int(leaf["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        packed = leaf["digest_vector"]
        if (
            not isinstance(packed, bytes)
            or leaf.get("digest_vector_sha256") != hashlib.sha256(packed).hexdigest()
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        digests = _unpack_digest_vector(
            packed,
            count=count,
            validate_digest=validate_digest,
        )
        _touch_or_fail(leaf_key)
        return AttributeCursorSeenState(digests, state_id)

    # Accept variable snapshots emitted by an earlier intermediate build.
    if isinstance(leaf, dict) and "digests" in leaf:
        if (
            leaf.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
            or leaf.get("resource") != resource
            or leaf.get("binding") != binding_digest
            or leaf.get("id") != state_id
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        digests = _validate_digest_tuple(leaf.get("digests") or (), validate_digest)
        try:
            count = int(leaf["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        if count != len(digests) or not digests:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        _touch_or_fail(leaf_key)
        return AttributeCursorSeenState(digests, state_id)

    nodes: list[tuple[str, tuple[str, ...]]] = []
    base_digests: tuple[str, ...] = ()
    base_key: str | None = None
    visited: set[str] = set()
    leaf_count: int | None = None
    remaining_count: int | None = None
    current: str | None = state_id
    prefetched: dict[str, Any] = {state_id: leaf}
    while current is not None:
        if current in visited:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        visited.add(current)
        key = _cache_key(current)
        if current in prefetched:
            stored = prefetched.pop(current)
        else:
            try:
                stored = cache.get(key)
            except Exception as exc:
                raise AttributeCursorStateError(
                    "expired_cursor",
                    "The continuation cursor has expired. Please restart the search.",
                ) from exc
        if not isinstance(stored, dict):
            raise AttributeCursorStateError(
                "expired_cursor",
                "The continuation cursor has expired. Please restart the search.",
            )
        if "digest_vector" in stored or "digests" in stored:
            if (
                stored.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
                or stored.get("resource") != resource
                or stored.get("binding") != binding_digest
                or stored.get("id") != current
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            try:
                snapshot_count = int(stored["count"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                ) from exc
            if "digest_vector" in stored:
                packed = stored["digest_vector"]
                if (
                    not isinstance(packed, bytes)
                    or stored.get("digest_vector_sha256")
                    != hashlib.sha256(packed).hexdigest()
                ):
                    raise AttributeCursorStateError(
                        "invalid_cursor", "The continuation cursor is invalid."
                    )
                base_digests = _unpack_digest_vector(
                    packed,
                    count=snapshot_count,
                    validate_digest=validate_digest,
                )
            else:
                base_digests = _validate_digest_tuple(
                    stored.get("digests") or (), validate_digest
                )
                if snapshot_count != len(base_digests) or not base_digests:
                    raise AttributeCursorStateError(
                        "invalid_cursor", "The continuation cursor is invalid."
                    )
            if remaining_count is None or snapshot_count != remaining_count:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            remaining_count = 0
            base_key = key
            break
        if (
            stored.get("v") != ATTRIBUTE_CURSOR_STATE_VERSION
            or stored.get("resource") != resource
            or stored.get("binding") != binding_digest
            or stored.get("id") != current
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        chunk = _validate_digest_tuple(stored.get("chunk") or (), validate_digest)
        if not 1 <= len(chunk) <= ATTRIBUTE_CURSOR_STATE_CHUNK_SIZE:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        try:
            count = int(stored["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
        if not 1 <= count <= ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        if leaf_count is None:
            leaf_count = count
            remaining_count = count
        if remaining_count is None or count != remaining_count:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        remaining_count -= len(chunk)
        parent = stored.get("parent")
        if parent is not None and (not isinstance(parent, str) or len(parent) != 64):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        nodes.append((key, chunk))
        if len(nodes) > ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        current = parent

    assert leaf_count is not None
    if remaining_count != 0:
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    digests = (
        *base_digests,
        *(digest for _key, chunk in reversed(nodes) for digest in chunk),
    )
    if len(digests) != leaf_count or len(set(digests)) != len(digests):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    # Renew only after the entire chain has been proven internally consistent.
    for key, _chunk in nodes:
        _touch_or_fail(key)
    if base_key is not None:
        _touch_or_fail(base_key)
    return AttributeCursorSeenState(digests, state_id)


def persist_attribute_cursor_seen_state(
    prior: AttributeCursorSeenState,
    appended: Iterable[Any],
    *,
    resource: str,
    binding: Any,
    validate_digest: Callable[[str], bool],
) -> tuple[str, str, int] | tuple[str, str] | tuple[()]:
    """Persist an immutable exact continuation root for ``prior + appended``.

    Blocks and roots are addressed by their complete content and written with
    cache ``add``. No published object is ever overwritten, so concurrent
    retries and branches cannot corrupt one another even if a worker stalls.
    """

    binding_digest = attribute_cursor_binding_digest(resource=resource, binding=binding)

    def add_immutable(
        key: str,
        stored: dict[str, Any],
        *,
        timeout: int | None = None,
        renew: bool = True,
    ) -> None:
        """Create or verify one content-addressed cache object."""

        resolved_timeout = _ttl_seconds() if timeout is None else timeout
        try:
            created = cache.add(key, stored, timeout=resolved_timeout)
            if created:
                return
            existing = cache.get(key)
        except Exception as exc:
            raise AttributeCursorStateError(
                "cursor_state_unavailable",
                "A continuation could not be created. Please retry.",
            ) from exc
        if existing != stored:
            raise AttributeCursorStateError(
                "cursor_state_unavailable",
                "A continuation could not be created. Please retry.",
            )
        if renew:
            _touch_or_fail(key)

    prior_values = _validate_digest_tuple(prior.digests, validate_digest)
    new_digests = _validate_digest_tuple(appended, validate_digest)
    if prior.radix_root_id is not None:
        if (
            prior_values
            or prior.block_refs
            or prior.state_id is None
            or prior.resource != resource
            or prior.binding_digest != binding_digest
            or prior.expires_at is None
            or not 1 <= prior.seen_count <= ATTRIBUTE_CURSOR_STATE_MAX_COUNT
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        radix_root_id = prior.radix_root_id
        count = prior.seen_count
        expires_at = prior.expires_at
        remaining_ttl = _remaining_ttl(expires_at)
        if not new_digests:
            return ("state", prior.state_id, count)
        radix_root_id, next_count, added_count = _radix_insert_many(
            radix_root_id,
            new_digests,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        )
        if added_count != len(new_digests) or next_count != count + added_count:
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )
        count = next_count
        root_id = _radix_root_id(
            resource=resource,
            binding_digest=binding_digest,
            count=count,
            radix_root_id=radix_root_id,
            expires_at=expires_at,
        )
        add_immutable(
            _cache_key(root_id),
            {
                "v": ATTRIBUTE_CURSOR_STATE_VERSION,
                "format": _RADIX_ROOT_FORMAT,
                "resource": resource,
                "binding": binding_digest,
                "id": root_id,
                "count": count,
                "radix_root": radix_root_id,
                "expires_at": expires_at,
            },
            timeout=remaining_ttl,
            renew=False,
        )
        return ("state", root_id, count)

    prior_digests = set(prior_values)
    if any(value in prior_digests for value in new_digests):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    all_values = (*prior_values, *new_digests)
    if not all_values:
        return ()
    if len(all_values) > ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS:
        expires_at = _current_time_seconds() + _ttl_seconds()
        radix_root_id, count = _radix_build(
            all_values,
            depth=0,
            resource=resource,
            binding_digest=binding_digest,
            expires_at=expires_at,
        )
        root_id = _radix_root_id(
            resource=resource,
            binding_digest=binding_digest,
            count=count,
            radix_root_id=radix_root_id,
            expires_at=expires_at,
        )
        add_immutable(
            _cache_key(root_id),
            {
                "v": ATTRIBUTE_CURSOR_STATE_VERSION,
                "format": _RADIX_ROOT_FORMAT,
                "resource": resource,
                "binding": binding_digest,
                "id": root_id,
                "count": count,
                "radix_root": radix_root_id,
                "expires_at": expires_at,
            },
            timeout=_remaining_ttl(expires_at),
            renew=False,
        )
        return ("state", root_id, count)

    def add_block(values: tuple[str, ...]) -> tuple[str, int]:
        packed = _pack_digest_log(values)
        count = len(values)
        block_id = _immutable_block_id(
            resource=resource,
            binding_digest=binding_digest,
            count=count,
            packed=packed,
        )
        add_immutable(
            _block_cache_key(block_id),
            {
                "v": ATTRIBUTE_CURSOR_STATE_VERSION,
                "format": _IMMUTABLE_BLOCK_FORMAT,
                "resource": resource,
                "binding": binding_digest,
                "id": block_id,
                "count": count,
                "digest_log": packed,
                "digest_log_sha256": hashlib.sha256(packed).hexdigest(),
            },
        )
        return (block_id, count)

    block_refs: list[tuple[str, int]] = []
    if prior.block_refs:
        for raw_ref in prior.block_refs:
            if not isinstance(raw_ref, (tuple, list)) or len(raw_ref) != 2:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            block_id, raw_count = raw_ref
            try:
                block_count = int(raw_count)
            except (TypeError, ValueError) as exc:
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                ) from exc
            if (
                not isinstance(block_id, str)
                or len(block_id) != 64
                or block_count < 1
                or block_count > ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
                or block_count & (block_count - 1)
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            block_refs.append((block_id, block_count))

        normalized_prior_refs = tuple(block_refs)
        if (
            prior.state_id is None
            or sum(count for _block_id, count in normalized_prior_refs)
            != len(prior_values)
            or any(
                left_count <= right_count
                for (_left_id, left_count), (_right_id, right_count) in zip(
                    normalized_prior_refs,
                    normalized_prior_refs[1:],
                    strict=False,
                )
            )
            or prior.state_id
            != _immutable_root_id(
                resource=resource,
                binding_digest=binding_digest,
                count=len(prior_values),
                blocks=normalized_prior_refs,
            )
        ):
            raise AttributeCursorStateError(
                "invalid_cursor", "The continuation cursor is invalid."
            )

        offset = 0
        for block_id, block_count in normalized_prior_refs:
            packed = _pack_digest_log(
                tuple(prior_values[offset : offset + block_count])
            )
            if block_id != _immutable_block_id(
                resource=resource,
                binding_digest=binding_digest,
                count=block_count,
                packed=packed,
            ):
                raise AttributeCursorStateError(
                    "invalid_cursor", "The continuation cursor is invalid."
                )
            offset += block_count
    elif prior_values:
        # Inline, append-log, vector, and linked cursors migrate once by
        # rebuilding their already-validated logical prefix into blocks.
        block_refs = []

    processed = list(prior_values)
    values_to_add = new_digests
    if prior_values and not prior.block_refs:
        processed = []
        values_to_add = all_values

    for digest in values_to_add:
        processed.append(digest)
        current = add_block((digest,))
        while block_refs and block_refs[-1][1] == current[1]:
            merged_count = current[1] * 2
            block_refs.pop()
            current = add_block(tuple(processed[-merged_count:]))
        block_refs.append(current)

    normalized_blocks = tuple(block_refs)
    if not normalized_blocks or sum(count for _id, count in normalized_blocks) != len(
        all_values
    ):
        raise AttributeCursorStateError(
            "cursor_state_unavailable",
            "A continuation could not be created. Please retry.",
        )

    root_id = _immutable_root_id(
        resource=resource,
        binding_digest=binding_digest,
        count=len(all_values),
        blocks=normalized_blocks,
    )
    add_immutable(
        _cache_key(root_id),
        {
            "v": ATTRIBUTE_CURSOR_STATE_VERSION,
            "format": _IMMUTABLE_BLOCKS_FORMAT,
            "resource": resource,
            "binding": binding_digest,
            "id": root_id,
            "count": len(all_values),
            "blocks": normalized_blocks,
        },
    )
    return ("state", root_id, len(all_values))


__all__ = [
    "ATTRIBUTE_CURSOR_LEGACY_INLINE_LIMIT",
    "ATTRIBUTE_CURSOR_STATE_CHUNK_SIZE",
    "ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS",
    "ATTRIBUTE_CURSOR_STATE_TTL_SECONDS",
    "AttributeCursorSeenState",
    "AttributeCursorStateError",
    "attribute_cursor_binding_digest",
    "load_attribute_cursor_seen_state",
    "persist_attribute_cursor_seen_state",
]
