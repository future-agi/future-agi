"""Actual reservation/journal binding; fake DDL is not live runtime evidence."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    SourceCaptureError,
    SourceCaptureObservation,
    SourceCaptureRetired,
    SourceCaptureUncertain,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_runtime import (
    CaptureAwareCutoffFreezer,
    LifecycleSourceCapture,
    SourceCaptureNeedsReplacement,
)
from tracer.tests.test_property_catalog_durable_lifecycle import (
    INITIAL_UNTIL,
    TOKEN_A,
    LifecycleRunMode,
    ReservationStatus,
    _bounds,
    _Clock,
    _Freezer,
    _lifecycle,
    _scope,
    _State,
)

INSTALLATION = "227be047-0ec0-49f0-ab11-c303b179cfa1"
SERVER = "ce5cae0f-6880-42bf-93b6-176e25898611"
TABLE_UUID = "5a207528-aeb8-4c15-93b7-61554560da40"
SOURCE = (
    f"CREATE TABLE default.spans UUID '{TABLE_UUID}' ("
    "project_id UUID, observation_type String, service_name String, trace_id String, "
    "id String, start_time DateTime64(6, 'UTC'), _version UInt64, is_deleted UInt8, "
    "attrs_string Map(String,String), attrs_number Map(String,Float64), "
    "attrs_bool Map(String,UInt8), attributes_extra String, model String) "
    "ENGINE=ReplacingMergeTree(_version) PARTITION BY toDate(start_time) "
    "ORDER BY (project_id, id)"
)
PARTS = (("20260901_1_1_0", "a" * 32),)


class Backend:
    def __init__(self):
        self.observed = None
        self.events = []
        self.lose_attach = False

    def check_capacity(self, spec):
        self.events.append("capacity")

    def verify_source(self, spec):
        self.events.append("source")

    def create_empty_once(self, spec, *, before_send):
        before_send()
        self.events.append("create")
        self.observed = SourceCaptureObservation(
            SERVER,
            spec.capture_database,
            spec.capture_table,
            spec.capture_table_uuid,
            spec.capture_schema_sha256,
            "b" * 64,
            0,
            0,
            0,
        )

    def inspect(self, spec):
        return self.observed

    def attach_once(self, spec, *, query_id):
        self.events.append("attach")
        self.observed = replace(self.observed, parts=1, rows=2, bytes_on_disk=16)
        if self.lose_attach:
            raise TimeoutError("lost reply")

    def drop_owned(self, spec, *, require_empty=False):
        self.events.append("drop")
        self.observed = None


@pytest.fixture
def fixture(tmp_path):
    clock = _Clock(INITIAL_UNTIL)
    lifecycle = _lifecycle(
        state=_State(), clock=clock, freezer=_Freezer(clock), tokens=[TOKEN_A]
    )
    prepared = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    backend = Backend()
    metadata = {
        "server_uuid": SERVER,
        "table_uuid": TABLE_UUID,
        "create_table_query": SOURCE,
    }
    reads, bound = [], []
    retire = [False]

    def load():
        reads.append(True)
        return metadata.copy()

    def reader(spec):
        bound.append(spec)
        return SimpleNamespace(source_table=spec.capture_table)

    def service():
        return LifecycleSourceCapture(
            directory=str(tmp_path),
            installation_id=INSTALLATION,
            source_database="default",
            catalog_database="property_catalog_dev",
            metadata_loader=load,
            live_reader=SimpleNamespace(),
            backend_factory=lambda s, schema: backend,
            reader_factory=reader,
            can_retire=lambda s: retire[0],
        )

    return SimpleNamespace(
        service=service,
        prepared=prepared,
        backend=backend,
        metadata=metadata,
        reads=reads,
        bound=bound,
        retire=retire,
    )


def test_binds_existing_reserved_token_and_original_live_baseline(fixture):
    f = fixture
    binding = f.service().bind(f.prepared, started_parts=PARTS)
    assert binding.spec.build_token == f.prepared.lease.build_token
    assert binding.spec.organization_id == f.prepared.scope.organization_id
    assert binding.started_parts == PARTS
    assert binding.reader.source_table == binding.spec.capture_table
    assert f.backend.events.count("create") == f.backend.events.count("attach") == 1


def test_process_restart_never_recaptures_changed_live_source(fixture):
    f = fixture
    original = f.service().bind(f.prepared, started_parts=PARTS)
    f.metadata["create_table_query"] = "invalid new live DDL"
    resumed = f.service().bind(replace(f.prepared, resumed=True), started_parts=())
    assert original.spec == resumed.spec
    assert resumed.started_parts == PARTS
    assert len(f.reads) == 1
    assert f.backend.events.count("attach") == 1


def test_missing_binding_on_resumed_reservation_requires_replacement(fixture):
    f = fixture
    with pytest.raises(SourceCaptureNeedsReplacement, match="no original"):
        f.service().bind(replace(f.prepared, resumed=True), started_parts=PARTS)
    assert not f.reads and not f.backend.events


def test_fenced_recovery_never_reopens_sources(fixture):
    f = fixture
    with pytest.raises(SourceCaptureError, match="fenced completion"):
        f.service().bind(
            replace(
                f.prepared, resumed=True, reservation_status=ReservationStatus.FENCED
            )
        )
    assert not f.reads and not f.backend.events


def test_changed_scope_under_same_token_rejected(fixture):
    f = fixture
    service = f.service()
    service.bind(f.prepared, started_parts=PARTS)
    identity = service._identity(f.prepared)
    record = service._journal.load_record(service._key(identity))
    record["identity"]["project_ids"] = []
    service._journal.save_record(service._key(identity), record)
    with pytest.raises(SourceCaptureNeedsReplacement, match="differs from its lease"):
        f.service().bind(replace(f.prepared, resumed=True))
    assert f.backend.events.count("attach") == 1


def test_missing_pre_discovery_baseline_never_dispatches(fixture):
    with pytest.raises(SourceCaptureError, match="pre-discovery"):
        fixture.service().bind(fixture.prepared)
    assert not fixture.backend.events


def test_uncertain_attach_is_not_replayed_on_restart(fixture):
    f = fixture
    f.backend.lose_attach = True
    with pytest.raises(TimeoutError):
        f.service().bind(f.prepared, started_parts=PARTS)
    with pytest.raises(SourceCaptureUncertain):
        f.service().bind(replace(f.prepared, resumed=True))
    assert f.backend.events.count("attach") == 1


def test_finished_build_only_can_retire_and_never_reuse(fixture):
    f = fixture
    service = f.service()
    service.bind(f.prepared, started_parts=PARTS)
    with pytest.raises(SourceCaptureError, match="unfinished"):
        service.retire(f.prepared)
    f.retire[0] = True
    service.retire(f.prepared)
    assert f.backend.observed is None
    with pytest.raises(SourceCaptureRetired):
        f.service().bind(replace(f.prepared, resumed=True))


def test_changed_captured_contents_rejected_before_final_audit(fixture):
    f = fixture
    service = f.service()
    service.bind(f.prepared, started_parts=PARTS)
    f.backend.observed = replace(f.backend.observed, rows=3)
    with pytest.raises(SourceCaptureError, match="contents changed"):
        service.verify(f.prepared)


def test_live_baseline_precedes_discovery_and_fresh_plan_freeze(fixture):
    events = []

    def parts():
        events.append("baseline")
        return PARTS

    def freeze(**kwargs):
        events.append("discovery_and_freeze")
        return fixture.prepared.cutoffs

    freezer = CaptureAwareCutoffFreezer(freeze, SimpleNamespace(parts_snapshot=parts))
    assert freezer(scope=fixture.prepared.scope) == fixture.prepared.cutoffs
    assert events == ["baseline", "discovery_and_freeze"]
    assert freezer.started_parts == PARTS


def test_failed_discovery_does_not_reuse_previous_baseline(fixture):
    def fail(**kwargs):
        raise TimeoutError("discovery unavailable")

    freezer = CaptureAwareCutoffFreezer(
        fail, SimpleNamespace(parts_snapshot=lambda: PARTS)
    )
    freezer.started_parts = ()
    with pytest.raises(TimeoutError):
        freezer(scope=fixture.prepared.scope)
    assert freezer.started_parts is None


def test_empty_workspace_freeze_does_not_read_spans():
    def fail():
        raise AssertionError("empty workspace read canonical spans")

    freezer = CaptureAwareCutoffFreezer(
        lambda **kwargs: "frozen", SimpleNamespace(parts_snapshot=fail)
    )
    assert freezer(scope=SimpleNamespace(project_ids=())) == "frozen"
    assert freezer.started_parts == ()
