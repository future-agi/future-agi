"""Immutable server-side de-duplication state for attribute browse cursors.

Attribute keys and values are discovered from a newest-first physical span
walk.  A continuation therefore needs both a physical checkpoint and the set
of already-published logical values.  Copying that set into every signed URL
eventually exceeds proxy request-line limits; copying the complete set into a
new cache value on every page also has quadratic storage cost.

New cursors use immutable content-addressed digest blocks arranged like a
binary counter. Appending values creates only new singleton/carry blocks and a
small immutable root; it never rewrites state referenced by an older cursor.
Identical retries converge on the same root while divergent branches receive
different roots, so correctness does not depend on a cache lock or lease. A
load reads one root plus at most one block per set bit in the published count.
Legacy append-log, linked, and vector formats remain readable for rolling
deploy compatibility and migrate on their next continuation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache

ATTRIBUTE_CURSOR_STATE_VERSION = 1
ATTRIBUTE_CURSOR_STATE_TTL_SECONDS = 24 * 60 * 60
ATTRIBUTE_CURSOR_STATE_CHUNK_SIZE = 64
# Recent suggestions are deliberately finite. Exact key/value search is the
# unbounded path; retaining millions of high-cardinality URL digests in Redis
# would turn a picker cursor into tenant-controlled cache exhaustion.
ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS = 4_096
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
        if not 1 <= prefix_count <= ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS:
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

    prior_values = _validate_digest_tuple(prior.digests, validate_digest)
    new_digests = _validate_digest_tuple(appended, validate_digest)
    prior_digests = set(prior_values)
    if any(value in prior_digests for value in new_digests):
        raise AttributeCursorStateError(
            "invalid_cursor", "The continuation cursor is invalid."
        )
    all_values = (*prior_values, *new_digests)
    if not all_values:
        return ()
    if len(all_values) > ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS:
        raise AttributeCursorStateError(
            "cursor_limit_reached",
            "The recent suggestion limit was reached. Search for an exact value.",
        )

    binding_digest = attribute_cursor_binding_digest(resource=resource, binding=binding)

    def add_immutable(key: str, stored: dict[str, Any]) -> None:
        """Create or verify one content-addressed cache object."""

        try:
            created = cache.add(key, stored, timeout=_ttl_seconds())
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
        _touch_or_fail(key)

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
