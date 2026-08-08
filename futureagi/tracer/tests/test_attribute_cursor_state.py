"""Regression contracts for exhaustive attribute picker continuations."""

from __future__ import annotations

import hashlib
import pickle
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


def test_page_sized_appends_reach_4096_with_bounded_canonical_storage(monkeypatch):
    payloads = {}
    original_add = cache.add

    def recording_add(key, value, timeout=None, version=None):
        if isinstance(value, dict) and value.get("format") in {
            "immutable_blocks",
            "digest_block",
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

    with pytest.raises(AttributeCursorStateError) as overflow:
        _persist(state, (_digest(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS),))
    assert overflow.value.code == "cursor_limit_reached"


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
