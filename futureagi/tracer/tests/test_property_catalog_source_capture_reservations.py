"""Real shared journal/locks and explicit fake DDL outcomes; no live database."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    DurableSourceCapture,
    SourceCaptureError,
    SourceCaptureRetired,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_reservations import (
    MAX_LIVE_CAPTURES,
    MAX_UNRESOLVED_CAPTURE_CREATES,
    SourceCaptureBackpressure,
)
from tracer.tests.test_property_catalog_source_capture import Backend, specification


def make_capture(directory, *, spec=None, backend=None):
    spec = spec or specification(build_token=str(uuid4()), workspace_id=str(uuid4()))
    backend = backend or Backend()
    finished = set()
    manager = DurableSourceCapture(
        str(directory), backend, can_retire=lambda value: value.build_token in finished
    )
    backend.manager = manager
    return manager, backend, spec, finished


def absent_create(backend):
    def create(spec, *, before_send):
        backend.events.append("ensure")
        before_send()
        raise OSError("lost CREATE with no visible table")

    backend.create_empty_once = create


def test_absent_creates_are_counted_across_restart_and_workspaces(tmp_path):
    attempts = []
    for _ in range(MAX_UNRESOLVED_CAPTURE_CREATES):
        manager, backend, spec, _ = make_capture(tmp_path)
        absent_create(backend)
        with pytest.raises(OSError):
            manager.acquire(spec)
        assert backend.observation is None
        manager.retire(spec)
        attempts.append(spec)
    manager, backend, spec, _ = make_capture(tmp_path)
    entries = manager.reservation_snapshot(spec)
    assert {e.spec for e in entries} == set(attempts)
    assert {e.phase for e in entries} == {"abandoned_create"}
    with pytest.raises(SourceCaptureBackpressure, match="unresolved CREATE"):
        manager.acquire(spec)
    assert backend.events == []
    assert manager._load(spec) is None


def test_one_unknown_create_does_not_consume_live_capture_slots(tmp_path):
    abandoned, backend, spec, _ = make_capture(tmp_path)
    absent_create(backend)
    with pytest.raises(OSError):
        abandoned.acquire(spec)
    abandoned.retire(spec)
    for _ in range(MAX_LIVE_CAPTURES):
        manager, _, candidate, _ = make_capture(tmp_path)
        manager.acquire(candidate)
    entries = abandoned.reservation_snapshot(spec)
    assert sum(e.phase == "abandoned_create" for e in entries) == 1
    assert sum(e.phase == "captured" for e in entries) == MAX_LIVE_CAPTURES


def test_live_capacity_released_only_after_durable_retirement(tmp_path):
    captures = [make_capture(tmp_path) for _ in range(MAX_LIVE_CAPTURES)]
    for manager, _, spec, _ in captures:
        manager.acquire(spec)
    other, backend, spec, _ = make_capture(tmp_path)
    with pytest.raises(SourceCaptureBackpressure, match="live"):
        other.acquire(spec)
    first, _, first_spec, finished = captures[0]
    with pytest.raises(SourceCaptureError, match="unfinished"):
        first.retire(first_spec)
    with pytest.raises(SourceCaptureBackpressure):
        other.acquire(spec)
    finished.add(first_spec.build_token)
    first.retire(first_spec)
    other.acquire(spec)
    assert "attach" in backend.events
    assert first_spec not in {e.spec for e in other.reservation_snapshot(spec)}


def test_prepared_restart_rechecks_native_capacity_and_keeps_one_reservation(
    tmp_path, monkeypatch
):
    manager, backend, spec, _ = make_capture(tmp_path)
    save = manager._save

    def fail_send(value, phase, observation=None):
        if phase == "creating":
            raise OSError("before_send fsync failed")
        save(value, phase, observation)

    monkeypatch.setattr(manager, "_save", fail_send)
    with pytest.raises(OSError):
        manager.acquire(spec)
    assert manager.reservation_snapshot(spec)[0].phase == "create_prepared"
    restarted, _, _, _ = make_capture(tmp_path, spec=spec, backend=backend)
    backend.capacity_fault = True
    with pytest.raises(SourceCaptureError, match="capacity"):
        restarted.acquire(spec)
    assert backend.events.count("ensure") == 1
    assert len(restarted.reservation_snapshot(spec)) == 1
    backend.capacity_fault = False
    restarted.acquire(spec)
    assert backend.events.count("capacity") == 3
    assert restarted.reservation_snapshot(spec)[0].phase == "captured"


@pytest.mark.parametrize("persist_first", [False, True])
def test_reservation_fsync_failure_cannot_dispatch_create(
    tmp_path, monkeypatch, persist_first
):
    manager, backend, spec, _ = make_capture(tmp_path)
    save = manager._journal.save_record
    index_key = manager._reservations._key(spec)

    def fail_index(key, record):
        if key == index_key:
            if persist_first:
                save(key, record)
            raise OSError("reservation fsync failed")
        save(key, record)

    monkeypatch.setattr(manager._journal, "save_record", fail_index)
    with pytest.raises(OSError):
        manager.acquire(spec)
    assert manager._load(spec)["phase"] == "create_prepared"
    assert "ensure" not in backend.events
    restarted, _, _, _ = make_capture(tmp_path, spec=spec, backend=backend)
    restarted.retire(spec)  # Proven unsent; no DDL, whether the index persisted or not.
    assert restarted.reservation_snapshot(spec) == ()
    assert "drop" not in backend.events


def test_retired_journal_precedes_reservation_release_and_restart_can_prune(
    tmp_path, monkeypatch
):
    manager, backend, spec, finished = make_capture(tmp_path)
    manager.acquire(spec)
    finished.add(spec.build_token)
    save = manager._journal.save_record
    index_key = manager._reservations._key(spec)

    def fail_release(key, record):
        if key == index_key:
            assert manager._load(spec)["phase"] == "retired"
            raise OSError("index release reply lost")
        save(key, record)

    monkeypatch.setattr(manager._journal, "save_record", fail_release)
    with pytest.raises(OSError):
        manager.retire(spec)
    assert backend.observation is None
    restarted, _, _, _ = make_capture(tmp_path, spec=spec, backend=backend)
    restarted.retire(spec)
    assert restarted.reservation_snapshot(spec) == ()
    with pytest.raises(SourceCaptureRetired):
        restarted.acquire(spec)


def test_late_empty_gc_race_is_pending_and_never_releases_reservation(
    tmp_path, monkeypatch
):
    manager, backend, spec, _ = make_capture(tmp_path)
    backend.ensure_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    empty = backend.observation
    backend.observation = None
    drop = backend.drop_owned

    def arrives_after_absent(value, *, require_empty=False):
        assert require_empty
        drop(value, require_empty=require_empty)
        backend.observation = empty

    monkeypatch.setattr(backend, "drop_owned", arrives_after_absent)
    manager.retire(spec)
    assert backend.observation == empty
    assert manager.reservation_snapshot(spec)[0].phase == "abandoned_create"
    monkeypatch.setattr(backend, "drop_owned", drop)
    manager.retire(spec)
    assert backend.observation is None
    assert manager.reservation_snapshot(spec)[0].phase == "abandoned_create"
    with pytest.raises(SourceCaptureError, match="terminal retirement"):
        manager._reservations.release(spec)
    with pytest.raises(SourceCaptureRetired):
        manager.acquire(spec)


@pytest.mark.parametrize("mutation", ["foreign_uuid", "nonempty"])
def test_late_gc_never_deletes_foreign_or_nonempty_capture(tmp_path, mutation):
    manager, backend, spec, _ = make_capture(tmp_path)
    backend.ensure_fault = True
    with pytest.raises(OSError):
        manager.acquire(spec)
    if mutation == "foreign_uuid":
        backend.observation = replace(backend.observation, table_uuid=str(uuid4()))
    else:
        backend.observation = replace(
            backend.observation, parts=1, rows=1, bytes_on_disk=1
        )
    with pytest.raises(SourceCaptureError):
        manager.retire(spec)
    assert "drop" not in backend.events
    assert manager.reservation_snapshot(spec)[0].phase == "abandoned_create"


@pytest.mark.parametrize(
    "field", ["installation_id", "source_server_uuid", "source_database"]
)
def test_reservation_scope_cannot_hide_existing_attempts(tmp_path, field):
    manager, backend, spec, _ = make_capture(tmp_path)
    absent_create(backend)
    with pytest.raises(OSError):
        manager.acquire(spec)
    other = replace(
        spec,
        **{field: "another_source" if field == "source_database" else str(uuid4())},
    )
    with pytest.raises(SourceCaptureError, match="namespace"):
        manager.reservation_snapshot(other)


def test_retired_table_name_cannot_be_reused_in_another_workspace(tmp_path):
    manager, _, spec, finished = make_capture(tmp_path)
    manager.acquire(spec)
    finished.add(spec.build_token)
    manager.retire(spec)
    other = replace(spec, workspace_id=str(uuid4()))
    candidate, backend, _, _ = make_capture(tmp_path, spec=other)
    with pytest.raises(SourceCaptureError, match="table name"):
        candidate.acquire(other)
    assert backend.events == []


@pytest.mark.parametrize(
    "corruption", ["scope", "duplicate", "oversized", "missing_build", "spec"]
)
def test_corrupt_reservation_index_blocks_new_create(tmp_path, corruption, monkeypatch):
    manager, _, spec, _ = make_capture(tmp_path)
    manager.acquire(spec)
    key = manager._reservations._key(spec)
    record = manager._journal.load_record(key)
    if corruption == "scope":
        record["scope"]["source_server_uuid"] = str(uuid4())
    elif corruption == "duplicate":
        record["specs"] *= 2
    elif corruption == "oversized":
        record["specs"] *= MAX_LIVE_CAPTURES + MAX_UNRESOLVED_CAPTURE_CREATES + 1
    elif corruption == "spec":
        record["specs"][0]["source_schema_sha256"] = "not-a-digest"
    else:
        load = manager._reservations._load_capture
        monkeypatch.setattr(
            manager._reservations,
            "_load_capture",
            lambda value: None if value == spec else load(value),
        )
    manager._journal.save_record(key, record)
    other = specification(build_token=str(uuid4()))
    with pytest.raises(SourceCaptureError):
        manager.acquire(other)


def test_concurrent_workspaces_share_the_durable_slot_bound(tmp_path):
    captures = [make_capture(tmp_path) for _ in range(MAX_LIVE_CAPTURES + 2)]
    barrier = Barrier(len(captures))

    def acquire(case):
        manager, _, spec, _ = case
        barrier.wait(timeout=2)
        try:
            manager.acquire(spec)
            return "captured"
        except SourceCaptureBackpressure:
            return "backpressure"

    with ThreadPoolExecutor(max_workers=len(captures)) as pool:
        results = list(pool.map(acquire, captures))
    assert results.count("captured") == MAX_LIVE_CAPTURES
    manager, _, spec, _ = captures[0]
    assert len(manager.reservation_snapshot(spec)) == MAX_LIVE_CAPTURES


def test_snapshot_contains_frozen_exact_specs_not_mutable_index_documents(tmp_path):
    manager, _, spec, _ = make_capture(tmp_path)
    manager.acquire(spec)
    entries = manager.reservation_snapshot(spec)
    assert type(entries) is tuple
    assert asdict(entries[0]) == {"spec": asdict(spec), "phase": "captured"}
    with pytest.raises(AttributeError):
        entries[0].phase = "retired"
