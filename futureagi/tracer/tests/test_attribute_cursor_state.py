"""Regression contracts for exhaustive attribute picker continuations."""

from __future__ import annotations

import hashlib
import pickle
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.cache import cache

from tracer.services.clickhouse import attribute_cursor_state as cursor_state
from tracer.services.clickhouse.attribute_cursor_state import (
    ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS,
    AttributeCursorSeenState,
    AttributeCursorStateError,
    load_attribute_cursor_seen_state,
    persist_attribute_cursor_seen_state,
)

RESOURCE = "attribute-cursor-test"
BINDING = {"project_id": "project-a", "query": "final_status"}


def _digest(index: int) -> str:
    return hashlib.md5(f"value-{index}".encode(), usedforsecurity=False).hexdigest()


def _prefixed_digest(prefix: str, index: int) -> str:
    return f"{prefix}{index:031x}"


def _valid(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _persist(
    prior: AttributeCursorSeenState,
    values: tuple[str, ...],
    *,
    binding=BINDING,
):
    return persist_attribute_cursor_seen_state(
        prior,
        values,
        resource=RESOURCE,
        binding=binding,
        validate_digest=_valid,
    )


def _load(reference, *, binding=BINDING) -> AttributeCursorSeenState:
    return load_attribute_cursor_seen_state(
        reference,
        resource=RESOURCE,
        binding=binding,
        validate_digest=_valid,
    )


def _root(reference):
    return cache.get(cursor_state._cache_key(reference[1]))


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


def test_initial_state_roundtrips_as_a_canonical_immutable_root(monkeypatch):
    writes = {}
    original_add = cache.add

    def recording_add(key, value, timeout=None, version=None):
        writes[key] = value
        return original_add(key, value, timeout=timeout, version=version)

    monkeypatch.setattr(cache, "add", recording_add)
    values = tuple(_digest(index) for index in range(149))

    reference = _persist(AttributeCursorSeenState((), None), values)
    root = writes[cursor_state._cache_key(reference[1])]

    assert reference == ("state", root["id"], len(values))
    assert len(reference[1]) == 64
    assert root["format"] == "immutable_blocks"
    assert root["count"] == len(values)
    block_counts = tuple(count for _block_id, count in root["blocks"])
    assert len(block_counts) == len(values).bit_count()
    assert all(count > 0 and not count & (count - 1) for count in block_counts)
    assert all(
        left > right
        for left, right in zip(block_counts, block_counts[1:], strict=False)
    )
    assert sum(block_counts) == len(values)

    for block_id, count in root["blocks"]:
        block = writes[cursor_state._block_cache_key(block_id)]
        assert block["format"] == "digest_block"
        assert block["id"] == block_id
        assert block["count"] == count
        assert len(block["digest_log"]) == count * 16

    loaded = _load(reference)
    assert loaded.digests == values
    assert loaded.state_id == reference[1]
    assert loaded.block_refs == root["blocks"]


def test_identical_retry_is_idempotent_and_old_root_remains_loadable():
    first_values = tuple(_digest(index) for index in range(70))
    first_reference = _persist(AttributeCursorSeenState((), None), first_values)
    first = _load(first_reference)
    appended = tuple(_digest(index) for index in range(70, 91))

    second_reference = _persist(first, appended)
    retry_reference = _persist(first, appended)

    assert retry_reference == second_reference
    assert first_reference != second_reference
    assert _load(first_reference).digests == first_values
    assert _load(second_reference).digests == (*first_values, *appended)


def test_divergent_concurrent_branches_never_overwrite_each_other(monkeypatch):
    first_values = tuple(_digest(index) for index in range(32))
    first_reference = _persist(AttributeCursorSeenState((), None), first_values)
    first = _load(first_reference)
    branch_a = (_digest(100), _digest(101))
    branch_b = (_digest(200), _digest(201))

    root_barrier = Barrier(2)
    original_add = cache.add

    def interleaved_add(key, value, timeout=None, version=None):
        if (
            isinstance(value, dict)
            and value.get("format") == "immutable_blocks"
            and value.get("count") == len(first_values) + 2
        ):
            root_barrier.wait(timeout=5)
        return original_add(key, value, timeout=timeout, version=version)

    monkeypatch.setattr(cache, "add", interleaved_add)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(_persist, first, branch_a)
        future_b = executor.submit(_persist, first, branch_b)
        reference_a = future_a.result(timeout=10)
        reference_b = future_b.result(timeout=10)

    assert reference_a != reference_b
    assert _load(first_reference).digests == first_values
    assert _load(reference_a).digests == (*first_values, *branch_a)
    assert _load(reference_b).digests == (*first_values, *branch_b)


def test_current_cursor_load_uses_one_root_get_and_one_block_mget(monkeypatch):
    values = tuple(_digest(index) for index in range(149))
    reference = _persist(AttributeCursorSeenState((), None), values)
    get_calls = []
    get_many_calls = []
    touch_calls = []
    inside_get_many = False
    original_get = cache.get
    original_get_many = cache.get_many
    original_touch = cache.touch

    def recording_get(key, *args, **kwargs):
        if not inside_get_many:
            get_calls.append(key)
        return original_get(key, *args, **kwargs)

    def recording_get_many(keys, *args, **kwargs):
        nonlocal inside_get_many
        get_many_calls.append(tuple(keys))
        inside_get_many = True
        try:
            return original_get_many(keys, *args, **kwargs)
        finally:
            inside_get_many = False

    def recording_touch(key, *args, **kwargs):
        touch_calls.append(key)
        return original_touch(key, *args, **kwargs)

    monkeypatch.setattr(cache, "get", recording_get)
    monkeypatch.setattr(cache, "get_many", recording_get_many)
    monkeypatch.setattr(cache, "touch", recording_touch)

    loaded = _load(reference)

    assert loaded.digests == values
    assert get_calls == [cursor_state._cache_key(reference[1])]
    assert len(get_many_calls) == 1
    assert set(get_many_calls[0]) == {
        cursor_state._block_cache_key(block_id)
        for block_id, _count in loaded.block_refs
    }
    # TTL renewal is deliberately tracked separately from payload reads.
    assert touch_calls == [
        *(
            cursor_state._block_cache_key(block_id)
            for block_id, _count in loaded.block_refs
        ),
        cursor_state._cache_key(reference[1]),
    ]


def test_page_sized_appends_cross_4096_into_bounded_persistent_radix(monkeypatch):
    payloads = {}
    original_add = cache.add

    def recording_add(key, value, timeout=None, version=None):
        if isinstance(value, dict) and value.get("format") in {
            "immutable_blocks",
            "digest_block",
            "persistent_radix_set",
            "radix_node",
            "radix_leaf",
        }:
            payloads[value["id"]] = value
        return original_add(key, value, timeout=timeout, version=version)

    monkeypatch.setattr(cache, "add", recording_add)
    values = tuple(
        _digest(index) for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )
    state = AttributeCursorSeenState((), None)
    reference = ()
    page_size = 10
    root_ids = set()

    for offset in range(0, len(values), page_size):
        page = values[offset : offset + page_size]
        reference = _persist(state, page)
        root_ids.add(reference[1])
        state = _load(reference)
        assert state.digests == values[: offset + len(page)]

    roots = [
        payload
        for payload in payloads.values()
        if payload["format"] == "immutable_blocks"
    ]
    blocks = [
        payload for payload in payloads.values() if payload["format"] == "digest_block"
    ]
    assert len(root_ids) == (len(values) + page_size - 1) // page_size
    assert len(roots) == len(root_ids)
    for root in roots:
        counts = tuple(count for _block_id, count in root["blocks"])
        assert len(counts) == root["count"].bit_count()
        assert all(
            left > right for left, right in zip(counts, counts[1:], strict=False)
        )
        assert sum(counts) == root["count"]
        assert len(pickle.dumps(root)) < 4 * 1024

    final_root = payloads[reference[1]]
    assert final_root["count"] == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
    assert tuple(count for _block_id, count in final_root["blocks"]) == (
        ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS,
    )
    assert max(len(block["digest_log"]) for block in blocks) == 64 * 1024
    # Every digest participates in at most one immutable block at each level.
    assert sum(len(block["digest_log"]) for block in blocks) <= 13 * 64 * 1024
    assert all(len(pickle.dumps(block)) < 66 * 1024 for block in blocks)
    assert _load(reference).digests == values

    # Isolate the radix-family assertions from the hundreds of deliberately
    # retained legacy roots above. Django's tiny test LocMem cache otherwise
    # culls live radix children; production uses the shared cache backend.
    cache.clear()
    first_radix_digest = _digest(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    radix_reference = _persist(
        AttributeCursorSeenState(values, None),
        (first_radix_digest,),
    )
    radix_root = payloads[radix_reference[1]]
    radix_state = _load(radix_reference)

    assert radix_reference == (
        "state",
        radix_root["id"],
        ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1,
    )
    assert radix_root["format"] == "persistent_radix_set"
    assert len(pickle.dumps(radix_root)) < 1024
    assert radix_state.digests == ()
    assert radix_state.seen_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert radix_state.contains(values[0]) is True
    assert radix_state.contains(values[len(values) // 2]) is True
    assert radix_state.contains(first_radix_digest) is True
    assert radix_state.contains(_digest(100_000)) is False

    radix_payloads = [
        payload
        for payload in payloads.values()
        if payload["format"] in {"radix_node", "radix_leaf"}
    ]
    assert radix_payloads
    # A full 512-digest leaf is the largest object. Keeping it below 32 KiB
    # prevents cache/proxy-size regressions while nibble fanout keeps the
    # initial migration far below Django LocMem's ordinary entry ceiling.
    assert all(len(pickle.dumps(payload)) < 32 * 1024 for payload in radix_payloads)
    assert all(
        len(payload.get("digests", ())) <= cursor_state._RADIX_LEAF_MAX_DIGESTS
        for payload in radix_payloads
    )

    appended = tuple(_digest(index) for index in range(4_097, 4_102))
    continued_reference = _persist(radix_state, appended)
    retry_reference = _persist(radix_state, appended)
    continued_state = _load(continued_reference)

    assert retry_reference == continued_reference
    assert continued_state.seen_count == 4_102
    assert all(continued_state.contains(digest) for digest in appended)
    assert radix_state.contains(appended[-1]) is False


def test_radix_membership_and_append_have_bounded_depth_and_preserve_branches(
    monkeypatch,
):
    values = tuple(
        _digest(index) for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1)
    )
    reference = _persist(AttributeCursorSeenState((), None), values)
    state = _load(reference)
    get_calls = []
    original_get = cache.get

    def recording_get(key, *args, **kwargs):
        if key.startswith(cursor_state._RADIX_CACHE_PREFIX):
            get_calls.append(key)
        return original_get(key, *args, **kwargs)

    monkeypatch.setattr(cache, "get", recording_get)
    assert state.contains(values[-1]) is True
    assert 1 <= len(get_calls) <= cursor_state._RADIX_MAX_DEPTH + 1

    get_calls.clear()
    assert state.contains(_digest(999_999)) is False
    assert 1 <= len(get_calls) <= cursor_state._RADIX_MAX_DEPTH + 1

    branch_a_digest = _digest(200_001)
    branch_b_digest = _digest(200_002)
    branch_a_reference = _persist(state, (branch_a_digest,))
    branch_b_reference = _persist(state, (branch_b_digest,))
    branch_a = _load(branch_a_reference)
    branch_b = _load(branch_b_reference)

    assert branch_a_reference != branch_b_reference
    assert branch_a.contains(branch_a_digest) is True
    assert branch_a.contains(branch_b_digest) is False
    assert branch_b.contains(branch_b_digest) is True
    assert branch_b.contains(branch_a_digest) is False
    assert state.contains(branch_a_digest) is False
    assert state.contains(branch_b_digest) is False


def test_radix_membership_memoizes_each_verified_object_per_request(monkeypatch):
    values = tuple(
        _digest(index) for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1)
    )
    reference = _persist(AttributeCursorSeenState((), None), values)
    state = _load(reference)
    get_calls: Counter[str] = Counter()
    original_get = cache.get

    def recording_get(key, *args, **kwargs):
        if key.startswith(cursor_state._RADIX_CACHE_PREFIX):
            get_calls[key] += 1
        return original_get(key, *args, **kwargs)

    monkeypatch.setattr(cache, "get", recording_get)

    assert all(state.contains(digest) for digest in values)
    assert state.contains(_digest(999_999)) is False
    assert get_calls
    assert max(get_calls.values()) == 1
    assert len(get_calls) <= 16


def test_radix_bulk_pages_do_not_evict_untouched_dependencies(monkeypatch):
    """A full page copies one branch once, not once per appended value."""

    initial = tuple(
        _digest(index) for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1)
    )
    reference = _persist(AttributeCursorSeenState((), None), initial)
    state = _load(reference)
    page_a = tuple(_prefixed_digest("a", 100_000 + index) for index in range(100))
    page_b = tuple(_prefixed_digest("a", 200_000 + index) for index in range(100))
    radix_writes: list[str] = []
    original_add = cache.add

    def recording_add(key, value, timeout=None, version=None):
        if isinstance(value, dict) and value.get("format") in {
            "radix_node",
            "radix_leaf",
        }:
            radix_writes.append(value["id"])
        return original_add(key, value, timeout=timeout, version=version)

    monkeypatch.setattr(cache, "add", recording_add)
    first_continuation = _persist(state, page_a)
    first = _load(first_continuation)
    first_page_writes = len(radix_writes)
    second_continuation = _persist(first, page_b)
    second = _load(second_continuation)
    second_page_writes = len(radix_writes) - first_page_writes

    # All values share one top-level selector and remain in its leaf, so each
    # page needs exactly one leaf and one root-node copy. A per-value loop
    # creates 200 objects here and reproduces LocMem dependency eviction.
    assert first_page_writes == 2
    assert second_page_writes == 2
    assert second.seen_count == len(initial) + len(page_a) + len(page_b)
    assert all(second.contains(digest) for digest in (*initial, *page_a, *page_b))


def test_radix_tamper_and_missing_descendant_fail_closed():
    values = tuple(
        _digest(index) for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1)
    )
    reference = _persist(AttributeCursorSeenState((), None), values)
    state = _load(reference)
    radix_key = cursor_state._radix_cache_key(state.radix_root_id)
    radix_root = cache.get(radix_key)

    cache.set(radix_key, {**radix_root, "count": radix_root["count"] - 1})
    with pytest.raises(AttributeCursorStateError) as tampered:
        _load(reference)
    assert tampered.value.code == "invalid_cursor"

    cache.set(radix_key, radix_root)
    state = _load(reference)
    child_id = radix_root["children"][0][1]
    cache.delete(cursor_state._radix_cache_key(child_id))
    child_selector = radix_root["children"][0][0]
    matching_digest = next(
        digest for digest in values if digest.startswith(child_selector)
    )
    with pytest.raises(AttributeCursorStateError) as missing:
        state.contains(matching_digest)
    assert missing.value.code == "expired_cursor"


def test_radix_branches_share_one_absolute_expiry_without_unbounded_refresh(
    monkeypatch,
):
    initial_time = 1_000_000
    monkeypatch.setattr(cursor_state, "_current_time_seconds", lambda: initial_time)
    values = tuple(
        _digest(index) for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1)
    )
    reference = _persist(AttributeCursorSeenState((), None), values)
    state = _load(reference)
    family_expiry = initial_time + cursor_state._ttl_seconds()
    original_radix_root = cache.get(cursor_state._radix_cache_key(state.radix_root_id))
    untouched_child_id = original_radix_root["children"][0][1]

    monkeypatch.setattr(
        cursor_state,
        "_current_time_seconds",
        lambda: family_expiry - 30,
    )
    branch_reference = _persist(state, (_digest(300_000),))
    branch = _load(branch_reference)
    branch_root = _root(branch_reference)
    untouched_child = cache.get(cursor_state._radix_cache_key(untouched_child_id))

    assert branch.expires_at == family_expiry
    assert branch_root["expires_at"] == family_expiry
    assert untouched_child["expires_at"] == family_expiry

    # Even if a backend retains an expired object briefly, application-level
    # validation prevents a later branch root from outliving any dependency.
    monkeypatch.setattr(
        cursor_state,
        "_current_time_seconds",
        lambda: family_expiry,
    )
    with pytest.raises(AttributeCursorStateError) as expired:
        branch.contains(values[0])
    assert expired.value.code == "expired_cursor"


def test_legacy_inline_and_append_log_states_migrate_to_immutable_blocks():
    inline = tuple(_digest(index) for index in range(12))
    loaded_inline = _load(inline)
    inline_reference = _persist(loaded_inline, (_digest(12),))

    assert loaded_inline == AttributeCursorSeenState(inline, None)
    assert _root(inline_reference)["format"] == "immutable_blocks"
    assert _load(inline_reference).digests == (*inline, _digest(12))

    append_values = tuple(_digest(index) for index in range(20, 27))
    append_state_id = "a" * 64
    packed = cursor_state._pack_digest_log(append_values)
    binding_digest = cursor_state.attribute_cursor_binding_digest(
        resource=RESOURCE,
        binding=BINDING,
    )
    cache.set(
        cursor_state._cache_key(append_state_id),
        {
            "v": cursor_state.ATTRIBUTE_CURSOR_STATE_VERSION,
            "format": "append_log",
            "resource": RESOURCE,
            "binding": binding_digest,
            "id": append_state_id,
            "count": len(append_values),
            "digest_log": packed,
            "digest_log_sha256": hashlib.sha256(packed).hexdigest(),
        },
    )
    loaded_append = _load(("state", append_state_id, len(append_values)))
    append_reference = _persist(loaded_append, (_digest(27),))

    assert loaded_append.digests == append_values
    assert loaded_append.block_refs == ()
    assert append_reference[1] != append_state_id
    assert _root(append_reference)["format"] == "immutable_blocks"
    assert _load(append_reference).digests == (*append_values, _digest(27))


def test_binding_state_loss_and_each_ttl_renewal_failure_fail_closed(monkeypatch):
    values = tuple(_digest(index) for index in range(7))
    reference = _persist(AttributeCursorSeenState((), None), values)
    loaded = _load(reference)
    root_key = cursor_state._cache_key(reference[1])
    block_keys = [
        cursor_state._block_cache_key(block_id)
        for block_id, _count in loaded.block_refs
    ]

    with pytest.raises(AttributeCursorStateError) as mismatch:
        _load(reference, binding={**BINDING, "project_id": "project-b"})
    assert mismatch.value.code == "invalid_cursor"

    original_touch = cache.touch
    with monkeypatch.context() as context:
        context.setattr(
            cache,
            "touch",
            lambda key, *args, **kwargs: (
                False if key == root_key else original_touch(key, *args, **kwargs)
            ),
        )
        with pytest.raises(AttributeCursorStateError) as root_renewal:
            _load(reference)
    assert root_renewal.value.code == "expired_cursor"

    with monkeypatch.context() as context:
        context.setattr(
            cache,
            "touch",
            lambda key, *args, **kwargs: (
                False if key == block_keys[-1] else original_touch(key, *args, **kwargs)
            ),
        )
        with pytest.raises(AttributeCursorStateError) as block_renewal:
            _load(reference)
    assert block_renewal.value.code == "expired_cursor"

    cache.delete(block_keys[0])
    with pytest.raises(AttributeCursorStateError) as missing_block:
        _load(reference)
    assert missing_block.value.code == "expired_cursor"

    cache.delete(root_key)
    with pytest.raises(AttributeCursorStateError) as missing_root:
        _load(reference)
    assert missing_root.value.code == "expired_cursor"


def test_tampered_root_and_block_content_are_rejected(monkeypatch):
    values = tuple(_digest(index) for index in range(7))
    reference = _persist(AttributeCursorSeenState((), None), values)
    root_key = cursor_state._cache_key(reference[1])
    root = cache.get(root_key)

    cache.set(root_key, {**root, "count": root["count"] - 1})
    with pytest.raises(AttributeCursorStateError) as tampered_root:
        _load(reference)
    assert tampered_root.value.code == "invalid_cursor"
    cache.set(root_key, root)

    block_id, _count = root["blocks"][0]
    block_key = cursor_state._block_cache_key(block_id)
    block = cache.get(block_key)
    corrupted_log = bytes([block["digest_log"][0] ^ 1]) + block["digest_log"][1:]
    cache.set(
        block_key,
        {
            **block,
            "digest_log": corrupted_log,
            "digest_log_sha256": hashlib.sha256(corrupted_log).hexdigest(),
        },
    )
    with pytest.raises(AttributeCursorStateError) as tampered_block:
        _load(reference)
    assert tampered_block.value.code == "invalid_cursor"
    cache.set(block_key, block)

    monkeypatch.setattr(cache, "get_many", lambda *_args, **_kwargs: None)
    with pytest.raises(AttributeCursorStateError) as unavailable_bulk_read:
        _load(reference)
    assert unavailable_bulk_read.value.code == "expired_cursor"


def test_persist_rejects_block_refs_that_do_not_describe_prior_digests():
    first_reference = _persist(
        AttributeCursorSeenState((), None),
        tuple(_digest(index) for index in range(4)),
    )
    other_reference = _persist(
        AttributeCursorSeenState((), None),
        tuple(_digest(index) for index in range(10, 14)),
    )
    first = _load(first_reference)
    other = _load(other_reference)
    mismatched = AttributeCursorSeenState(
        first.digests,
        other.state_id,
        other.block_refs,
    )

    with pytest.raises(AttributeCursorStateError) as invalid:
        _persist(mismatched, (_digest(99),))
    assert invalid.value.code == "invalid_cursor"
