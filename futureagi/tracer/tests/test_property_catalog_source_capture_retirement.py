"""Real capture/coordinator journals and locks; source transport alone is fake."""

import fcntl
import hashlib
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    source_capture as capture_module,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    ClickHouseRevisionCoordinator,
    PropertyCatalogCoordinatorError,
    _stream_row,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    FileCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    SourceCaptureRetired,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_reservations import (
    SourceCaptureBackpressure,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_runtime import (
    LifecycleSourceCapture,
    SourceCaptureNeedsReplacement,
)
from tracer.tests.test_property_catalog_durable_lifecycle import (
    HOT_STREAM,
    INITIAL_UNTIL,
    TOKEN_A,
    TOKEN_B,
    TOKEN_C,
    LifecycleRunMode,
    _bounds,
    _Clock,
    _Freezer,
    _lifecycle,
    _scope,
    _State,
)
from tracer.tests.test_property_catalog_hot_drain import _CoordinatorClient, _FenceSink
from tracer.tests.test_property_catalog_source_capture_runtime import (
    INSTALLATION,
    PARTS,
    SERVER,
    SOURCE,
    TABLE_UUID,
    Backend,
)
from tracer.tests.test_property_catalog_supersession import _Harness


@pytest.fixture
def case(tmp_path):
    client = _CoordinatorClient()
    client.catalog_database = "property_catalog_dev"
    coordinator = ClickHouseRevisionCoordinator(
        client,
        database=client.catalog_database,
        serializer=FileCatalogMutationSerializer(str(tmp_path)),
        producer_fence_sink=_FenceSink(),
        hot_producer_stream_id=HOT_STREAM,
        deadline=SharedCatalogDeadline(wall_ms=30_000),
        recovery_journal_directory=str(tmp_path),
    )
    backends = {}

    def service():
        return LifecycleSourceCapture(
            directory=str(tmp_path),
            installation_id=INSTALLATION,
            source_database="default",
            catalog_database=client.catalog_database,
            metadata_loader=lambda: {
                "server_uuid": SERVER,
                "table_uuid": TABLE_UUID,
                "create_table_query": SOURCE,
            },
            live_reader=object(),
            backend_factory=lambda spec, schema: backends[spec.build_token],
            reader_factory=lambda spec: object(),
            can_retire=lambda spec: False,
        )

    def prepare(token=TOKEN_A, *, epoch=3):
        clock = _Clock(INITIAL_UNTIL)
        lifecycle = _lifecycle(
            state=_State(), clock=clock, freezer=_Freezer(clock), tokens=[token]
        )
        prepared = lifecycle.prepare(
            scope=replace(_scope(), catalog_epoch=epoch),
            mode=LifecycleRunMode.INITIAL_BACKFILL,
            configured_bounds=_bounds(),
        )
        lease = prepared.lease
        client.stream_rows.append(
            _stream_row(
                lease=lease,
                source_adapter=SourceAdapter.SYSTEM_MANIFEST,
                producer_stream_id=lease.build_token,
                envelope_version=0,
                status="open",
                now=lease.issued_at,
                drain_deadline=lease.expires_at,
            )
        )
        backends[token] = Backend()
        return prepared

    def capture(prepared):
        bridge = service()
        bridge.bind(prepared, started_parts=PARTS)
        spec, schema, _ = bridge._load(bridge._identity(prepared))
        return spec, schema

    def sweep(prepared):
        bridge = service()
        spec, schema, _ = bridge._load(bridge._identity(prepared))
        return bridge.retire_failed(coordinator=coordinator, spec=spec)

    return SimpleNamespace(
        root=tmp_path,
        client=client,
        coordinator=coordinator,
        backends=backends,
        service=service,
        prepare=prepare,
        capture=capture,
        sweep=sweep,
    )


def veto_key(case, prepared):
    lease = prepared.lease
    return (
        case.coordinator._revision_key_for_lease(lease)
        + ":source-invalid:"
        + lease.build_lease_sha256
    )


def test_exact_veto_reclaims_capture_and_restart_is_idempotent(case, monkeypatch):
    prepared = case.prepare()
    spec, _ = case.capture(prepared)
    case.coordinator.invalidate_source_snapshot(prepared.lease)
    journal = case.coordinator._recovery_journal
    veto = journal.load_record(veto_key(case, prepared))
    # GC must not discover files or query an activation/source head for proof.
    for method in ("glob", "rglob", "iterdir"):
        monkeypatch.setattr(Path, method, lambda *_: pytest.fail("journal scan"))
    monkeypatch.setattr(case.client, "query", lambda *_a, **_k: pytest.fail("GC SQL"))
    assert case.sweep(prepared) == (spec,)
    assert case.sweep(prepared) == ()
    assert case.backends[TOKEN_A].events.count("drop") == 1
    assert journal.load_record(veto_key(case, prepared)) == veto
    with pytest.raises(SourceCaptureRetired):
        case.service().bind(replace(prepared, resumed=True))


@pytest.mark.parametrize("marker", [False, True])
def test_absence_or_possible_active_never_authorizes_cleanup(case, marker):
    prepared = case.prepare()
    spec, schema = case.capture(prepared)
    if marker:
        lease = prepared.lease
        case.coordinator._recovery_journal.save_record(
            case.coordinator._revision_key_for_lease(lease) + ":activation",
            {
                "catalog_revision": lease.catalog_revision,
                "build_token": lease.build_token,
                "build_lease_sha256": lease.build_lease_sha256,
            },
        )
        with pytest.raises(PropertyCatalogCoordinatorError, match="possibly activated"):
            case.coordinator.invalidate_source_snapshot(lease)
    assert case.sweep(prepared) == ()
    assert "drop" not in case.backends[TOKEN_A].events
    assert case.service()._manager(spec, schema)._load(spec)["phase"] == "captured"


@pytest.mark.parametrize("change", ["format", "reason", "build_lease_sha256", "extra"])
def test_malformed_or_mismatched_veto_rejected(case, change):
    prepared = case.prepare()
    case.capture(prepared)
    case.coordinator.invalidate_source_snapshot(prepared.lease)
    journal, key = case.coordinator._recovery_journal, veto_key(case, prepared)
    record = journal.load_record(key)
    record[change] = "f" * 64
    journal.save_record(key, record)
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="invalidation record is corrupt"
    ):
        case.sweep(prepared)
    assert "drop" not in case.backends[TOKEN_A].events


@pytest.mark.parametrize("change", ["build_token", "source_table_uuid"])
def test_mismatched_binding_spec_is_not_adopted(case, change):
    prepared = case.prepare()
    spec, schema = case.capture(prepared)
    case.coordinator.invalidate_source_snapshot(prepared.lease)
    bridge = case.service()
    key = bridge._key(bridge._identity(prepared))
    record = bridge._journal.load_record(key)
    record["spec"][change] = TOKEN_B
    bridge._journal.save_record(key, record)
    with pytest.raises(SourceCaptureNeedsReplacement):
        bridge.retire_failed(coordinator=case.coordinator, spec=spec)
    assert "drop" not in case.backends[TOKEN_A].events


def test_original_epoch_used_even_when_next_capture_has_another_epoch(case):
    old, current = case.prepare(epoch=2), case.prepare(TOKEN_B, epoch=3)
    old_spec, _ = case.capture(old)
    case.capture(current)
    case.coordinator.invalidate_source_snapshot(old.lease)
    assert case.sweep(current) == (old_spec,)
    assert "drop" not in case.backends[TOKEN_B].events


def test_veto_for_another_epoch_cannot_authorize_original_capture(case):
    old = case.prepare(epoch=2)
    case.capture(old)
    case.coordinator.invalidate_source_snapshot(old.lease)
    journal = case.coordinator._recovery_journal
    record = journal.load_record(veto_key(case, old))
    bridge = case.service()
    key = bridge._key(bridge._identity(old))
    binding = bridge._journal.load_record(key)
    binding["identity"]["catalog_epoch"] = 3
    bridge._journal.save_record(key, binding)
    spec, schema, _ = bridge._load(binding["identity"])
    assert bridge.retire_failed(coordinator=case.coordinator, spec=spec) == ()
    assert journal.load_record(veto_key(case, old)) == record
    assert "drop" not in case.backends[TOKEN_A].events


def test_old_binding_without_epoch_resumes_but_is_not_guessed_for_gc(case):
    old = case.prepare()
    case.capture(old)
    bridge = case.service()
    key = bridge._key(bridge._identity(old))
    record = bridge._journal.load_record(key)
    del record["identity"]["catalog_epoch"]
    bridge._journal.save_record(key, record)
    bridge.bind(replace(old, resumed=True))
    case.coordinator.invalidate_source_snapshot(old.lease)
    assert case.sweep(old) == ()
    assert "drop" not in case.backends[TOKEN_A].events


@pytest.mark.parametrize("phase", ["creating", "abandoned_create"])
def test_uncertain_create_keeps_its_reservation(case, phase):
    old = case.prepare()
    spec, schema = case.capture(old)
    manager = case.service()._manager(spec, schema)
    with manager._source_lock(spec), manager._lock(spec):
        manager._save(spec, phase)
    case.coordinator.invalidate_source_snapshot(old.lease)
    assert case.sweep(old) == ()
    assert manager.reservation_snapshot(spec)[0].phase == phase
    assert "drop" not in case.backends[TOKEN_A].events


def test_failed_capture_slots_reused_before_next_attempt(case):
    old, healthy = case.prepare(), case.prepare(TOKEN_B)
    case.capture(old)
    case.capture(healthy)
    next_build = case.prepare(TOKEN_C)
    with pytest.raises(SourceCaptureBackpressure):
        case.service().bind(next_build, started_parts=PARTS)
    case.coordinator.invalidate_source_snapshot(old.lease)
    bridge = case.service()
    factory, routed = bridge._backend_factory, []

    def backend(spec, schema):
        routed.append(spec.build_token)
        return factory(spec, schema)

    bridge._backend_factory = backend
    binding = bridge.bind(next_build, coordinator=case.coordinator)
    assert routed == [TOKEN_A, TOKEN_C]  # Old exact member first; healthy B untouched.
    assert binding.spec.build_token == TOKEN_C
    assert case.backends[TOKEN_A].observed is None
    assert case.backends[TOKEN_B].observed is not None
    assert case.backends[TOKEN_C].events.count("create") == 1


def test_drop_failure_resumes_existing_retiring_intent(case, monkeypatch):
    old = case.prepare()
    spec, schema = case.capture(old)
    case.coordinator.invalidate_source_snapshot(old.lease)
    backend = case.backends[TOKEN_A]
    drop = backend.drop_owned
    monkeypatch.setattr(
        backend, "drop_owned", lambda *_: (_ for _ in ()).throw(TimeoutError())
    )
    with pytest.raises(TimeoutError):
        case.sweep(old)
    assert case.service()._manager(spec, schema)._load(spec)["phase"] == "retiring"
    monkeypatch.setattr(backend, "drop_owned", drop)
    assert case.sweep(old) == (spec,)


def test_drop_holds_workspace_namespace_build_and_callback_cannot_escape(
    case, monkeypatch
):
    old = case.prepare()
    spec, schema = case.capture(old)
    case.coordinator.invalidate_source_snapshot(old.lease)
    bridge = case.service()
    manager_factory = bridge._manager
    callbacks = []

    def manager(*args, **kwargs):
        proof = kwargs.get("can_retire")
        if proof is not None:
            assert proof(spec) is True
            assert proof(replace(spec, build_token=TOKEN_B)) is False
            callbacks.append(proof)
        return manager_factory(*args, **kwargs)

    def held(path):
        fd = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    key = case.coordinator._revision_key_for_lease(old.lease)
    workspace = case.root / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    original_drop = case.backends[TOKEN_A].drop_owned

    # Probe descriptors nonblocking rather than reentering the ordinary locks.
    original_lock = capture_module.locked_file
    held_paths = []

    @contextmanager
    def traced_lock(path):
        with original_lock(path):
            held_paths.append(path)
            try:
                yield
            finally:
                held_paths.pop()

    def checked_drop(current):
        held(workspace)
        assert len(held_paths) == 2
        assert held_paths[0] == case.root / spec.capture_database
        for path in held_paths:
            held(str(path) + ".lock")
        original_drop(current)

    monkeypatch.setattr(capture_module, "locked_file", traced_lock)
    monkeypatch.setattr(bridge, "_manager", manager)
    monkeypatch.setattr(case.backends[TOKEN_A], "drop_owned", checked_drop)
    assert bridge.retire_failed(coordinator=case.coordinator, spec=spec) == (spec,)
    assert callbacks and callbacks[0](spec) is False


def test_missing_old_binding_is_retained_without_routing_its_backend(case, monkeypatch):
    old, healthy = case.prepare(), case.prepare(TOKEN_B)
    case.capture(old)
    spec, _ = case.capture(healthy)
    case.coordinator.invalidate_source_snapshot(old.lease)
    bridge = case.service()
    missing_key = bridge._key(bridge._identity(old))
    load = bridge._journal.load_record
    monkeypatch.setattr(
        bridge._journal,
        "load_record",
        lambda key: None if key == missing_key else load(key),
    )
    monkeypatch.setattr(
        bridge, "_backend_factory", lambda *_: pytest.fail("no cleanup authority")
    )
    assert bridge.retire_failed(coordinator=case.coordinator, spec=spec) == ()
    assert "drop" not in case.backends[TOKEN_A].events


@pytest.mark.parametrize("epoch", [True, 0, 65536])
def test_malformed_stored_epoch_cannot_authorize_cleanup(case, epoch):
    old = case.prepare()
    spec, _ = case.capture(old)
    case.coordinator.invalidate_source_snapshot(old.lease)
    bridge = case.service()
    key = bridge._key(bridge._identity(old))
    binding = bridge._journal.load_record(key)
    binding["identity"]["catalog_epoch"] = epoch
    bridge._journal.save_record(key, binding)
    with pytest.raises(ValueError, match="positive UInt16"):
        bridge.retire_failed(coordinator=case.coordinator, spec=spec)
    assert "drop" not in case.backends[TOKEN_A].events


def test_uncertain_attach_can_retire_after_exact_source_invalidation(case):
    old = case.prepare()
    case.backends[TOKEN_A].lose_attach = True
    with pytest.raises(TimeoutError):
        case.service().bind(old, started_parts=PARTS)
    case.coordinator.invalidate_source_snapshot(old.lease)
    assert len(case.sweep(old)) == 1
    assert case.backends[TOKEN_A].events.count("attach") == 1
    assert case.backends[TOKEN_A].events.count("drop") == 1


@pytest.fixture
def expired_capture(case):
    h = _Harness(case.root, with_active=False)
    case.client.catalog_database = h.client.catalog_database
    case.backends[h.build.lease.build_token] = Backend()
    spec, schema = case.capture(h.build)
    return SimpleNamespace(
        h=h, bridge=case.service(), spec=spec, schema=schema, case=case
    )


def test_expiry_alone_does_not_retire_but_real_supersession_archive_does(
    expired_capture,
):
    f = expired_capture
    assert f.h.clock() >= f.h.build.lease.expires_at
    assert f.bridge.retire_failed(coordinator=f.h.coordinator, spec=f.spec) == ()
    successor = (
        f.h.prepare()
    )  # Real coordinator creates and archives failed reservation.
    assert successor.lease.build_token != f.spec.build_token
    assert not f.h.coordinator.source_snapshot_invalid(f.h.build.lease)
    assert f.bridge.retire_failed(coordinator=f.h.coordinator, spec=f.spec) == (f.spec,)
    assert f.bridge.retire_failed(coordinator=f.h.coordinator, spec=f.spec) == ()


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "fenced"),
        ("_version", 1),
        ("source_adapter", "span_attribute"),
        ("envelope_version", 1),
        ("producer_stream_id", TOKEN_B),
        ("build_token", TOKEN_B),
        ("organization_id", TOKEN_A),
        ("catalog_epoch", 4),
        ("build_lease_sha256", "f" * 64),
        ("build_plan_json", "{}"),
    ],
)
def test_mutated_revoked_reservation_cannot_authorize_capture_retirement(
    expired_capture, field, value
):
    f = expired_capture
    f.h.prepare()
    lease = f.h.build.lease
    journal = f.h.coordinator._recovery_journal
    key = (
        f.h.coordinator._revision_key_for_lease(lease)
        + ":revoked:"
        + lease.build_lease_sha256
    )
    archive = journal.load_record(key)
    assert archive["revoked"][field] != value
    archive["revoked"][field] = value
    journal.save_record(key, archive)
    with pytest.raises(PropertyCatalogCoordinatorError):
        f.bridge.retire_failed(coordinator=f.h.coordinator, spec=f.spec)
    assert "drop" not in f.case.backends[lease.build_token].events
