"""Real coordinator/lock/journal with in-memory ClickHouse transport only."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    ActivationHistory,
    ActivationInventory,
    ActivationManifest,
    ActivationRecord,
    ActivationRejected,
    ActivationStatus,
    ManifestStream,
    PropertyCatalogActivator,
    StreamDrainProof,
    make_revision_fence,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    ClickHouseRevisionCoordinator,
    FileSupersessionJournal,
    PropertyCatalogCoordinatorError,
    _row_lease,
    _stream_row,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    LifecycleRunMode,
    PersistedReservation,
    ReservationStatus,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    FileCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    stream_requirement,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    _ACTIVATION_COLUMNS,
    _activation,
    _activation_row,
)
from tracer.tests.test_property_catalog_durable_lifecycle import (
    HOT_STREAM,
    SHA_A,
    TOKEN_C,
    TOKEN_D,
    _bounds,
    _complete_checkpoints,
    _expired_repair_case,
    _Freezer,
    _lifecycle,
    _running_checkpoint,
    _scope,
)
from tracer.tests.test_property_catalog_hot_drain import _CoordinatorClient, _FenceSink


class _Client(_CoordinatorClient):
    def __init__(self, active):
        super().__init__()
        self.active_rows = []
        if active is not None:
            plan = active.build_plan
            checkpoints = tuple(
                value.checkpoint
                for value in _complete_checkpoints(
                    SimpleNamespace(
                        scope=plan,
                        lease=SimpleNamespace(
                            build_plan=plan,
                            catalog_revision=plan.catalog_revision,
                            build_token=plan.build_token,
                        ),
                    )
                )
            )
            roles = {stream.key: stream.role for stream in plan.streams}
            manifest = ActivationManifest(
                organization_id=plan.organization_id,
                workspace_id=plan.workspace_id,
                catalog_epoch=plan.catalog_epoch,
                catalog_revision=active.catalog_revision,
                build_token=active.build_token,
                projection_version=active.projection_version,
                lifecycle_mode=active.lifecycle_mode,
                lineage_anchor_revision=active.lineage_anchor.catalog_revision,
                streams=tuple(
                    ManifestStream(
                        requirement=stream_requirement(checkpoint),
                        role=roles[checkpoint.key],
                    )
                    for checkpoint in checkpoints
                ),
            )
            fence = make_revision_fence(
                manifest=manifest,
                build_plan=plan,
                checkpoints=checkpoints,
                drain_deadline=active.qualified_at,
                fenced_at=active.qualified_at,
            )
            record = ActivationRecord(
                organization_id=plan.organization_id,
                workspace_id=plan.workspace_id,
                catalog_epoch=plan.catalog_epoch,
                catalog_revision=active.catalog_revision,
                build_token=active.build_token,
                projection_version=active.projection_version,
                lifecycle_mode=active.lifecycle_mode,
                lineage_anchor_revision=active.lineage_anchor.catalog_revision,
                activation_sequence=active.activation_sequence,
                source_manifest_json=manifest.canonical_json,
                source_manifest_sha256=manifest.sha256,
                revision_fence_sha256=fence.fence_sha256,
                # Preserve the synthetic prior-active receipt used by the CAS
                # assertions; do not change the expected head to fit this read.
                activation_sha256=active.activation_sha256,
                status=ActivationStatus.ACTIVE,
                live_definition_rows=0,
                tombstone_rows=0,
                value_rows=0,
                qualified_at=active.qualified_at,
                updated_at=active.qualified_at,
                version=active.activation_sequence,
            )
            row = _activation_row(record)
            assert tuple(row) == _ACTIVATION_COLUMNS and _activation(row) == record
            self.active_rows.append(row)
        self.inserts = []
        self.fail_at = None
        self.fail_after = False
        self.data_rows = []

    def query(self, sql, params, *, timeout_ms):
        if "property_catalog_activations" in sql:
            self.queries.append(sql)
            assert timeout_ms > 0
            # Execute the actual latest-state helper (and allocator max query)
            # over the mutable physical rows. Do not prefilter status, discard
            # equal-version variants, or fabricate missing columns in responses.
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute(
                    f"ATTACH DATABASE ':memory:' AS `{self.catalog_database}`"
                )
                table = f"`{self.catalog_database}`.`property_catalog_activations`"
                connection.execute(
                    f"CREATE TABLE {table} ({', '.join(_ACTIVATION_COLUMNS)})"
                )
                connection.executemany(
                    f"INSERT INTO {table} VALUES ({', '.join('?' for _ in _ACTIVATION_COLUMNS)})",
                    [
                        tuple(
                            value.isoformat() if isinstance(value, datetime) else value
                            for value in (row[column] for column in _ACTIVATION_COLUMNS)
                        )
                        for row in self.active_rows
                    ],
                )
                translated = re.sub(r"%\(([a-z_]+)\)s", r":\1", sql)
                return tuple(
                    {
                        key: datetime.fromisoformat(value)
                        if key in {"qualified_at", "updated_at"} and value is not None
                        else value
                        for key, value in dict(row).items()
                    }
                    for row in connection.execute(translated, params)
                )
        if "property_catalog_checkpoints" in sql and "max_revision" in sql:
            self.queries.append(sql)
            return ({"max_revision": 0},)
        return super().query(sql, params, timeout_ms=timeout_ms)

    def insert(self, table, rows, **kwargs):
        fail = self.fail_at == len(self.inserts)
        self.inserts.append(tuple(dict(row) for row in rows))
        if not fail or self.fail_after:
            super().insert(table, rows, **kwargs)
        if fail:
            self.fail_at = None
            raise TimeoutError("ambiguous ClickHouse acknowledgement")


class _Reader:
    def __init__(self, client, active, build):
        self.client, self.active = client, active
        self.checkpoints = {build.lease.build_token: (_running_checkpoint(build),)}

    def load_latest_active(self, scope):
        return self.active

    def load_nonterminal(self, scope):
        latest = {}
        for row in self.client.stream_rows:
            if row["envelope_version"] != 0:
                continue
            key = row["catalog_revision"], row["build_token"]
            if key not in latest or row["_version"] > latest[key]["_version"]:
                latest[key] = row
        rows = [
            row
            for row in latest.values()
            if row["status"] in {"open", "draining", "fenced"}
        ]
        if not rows:
            return None
        row = max(rows, key=lambda row: row["catalog_revision"])
        return PersistedReservation(
            _row_lease(row), ReservationStatus(row["status"]), row["_version"]
        )

    def load_resumes(self, lease):
        return self.checkpoints.get(lease.build_token, ())


class _Harness:
    def __init__(self, directory, *, with_active=True, status="open"):
        _, state, self.clock, _, _, _, self.build = _expired_repair_case(
            with_active=with_active
        )
        self.directory = directory
        self.client, self.sink = _Client(state.active), _FenceSink()
        old = self.build.lease
        self.original = _stream_row(
            lease=old,
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            producer_stream_id=old.build_token,
            envelope_version=0,
            status=status,
            now=old.issued_at,
            drain_deadline=old.expires_at,
        )
        self.client.stream_rows.append(self.original)
        self.reader = _Reader(self.client, state.active, self.build)
        self.freezer = _Freezer(self.clock)
        self.restart()

    def restart(self):
        self.coordinator = ClickHouseRevisionCoordinator(
            self.client,
            database=self.client.catalog_database,
            serializer=FileCatalogMutationSerializer(str(self.directory)),
            producer_fence_sink=self.sink,
            hot_producer_stream_id=HOT_STREAM,
            deadline=SharedCatalogDeadline(wall_ms=30_000),
            now=self.clock,
            recovery_journal_directory=str(self.directory),
        )
        self.lifecycle = _lifecycle(
            state=self.reader,
            clock=self.clock,
            freezer=self.freezer,
            tokens=[TOKEN_C, TOKEN_D],
            coordinator=self.coordinator,
        )

    def prepare(self):
        return self.lifecycle.prepare(
            scope=_scope(),
            configured_bounds=_bounds(),
            mode=LifecycleRunMode.AUTO
            if self.reader.active
            else LifecycleRunMode.INITIAL_BACKFILL,
        )


@pytest.mark.parametrize("with_active", [False, True])
@pytest.mark.parametrize("status", ["open", "draining"])
def test_changed_source_can_supersede_unexpired_build_without_changing_old_data(
    tmp_path, with_active, status
):
    h = _Harness(tmp_path, with_active=with_active, status=status)
    old = h.build.lease
    h.clock.current = old.expires_at - timedelta(seconds=1)
    original_checkpoints = dict(h.reader.checkpoints)
    assert not h.coordinator.source_snapshot_invalid(old)
    h.coordinator.invalidate_source_snapshot(old)
    # A marker itself performs no CH write; it only forbids this attempt from
    # progressing. Replacement remains the same journaled two-record CAS.
    assert h.client.inserts == []
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="source snapshot was invalidated"
    ):
        h.coordinator._assert_reservation(old, expected_status=("open", "draining"))
    h.restart()
    assert h.coordinator.source_snapshot_invalid(old)
    fresh = h.prepare()
    assert fresh.lease.catalog_revision == old.catalog_revision + 1
    assert fresh.lease.build_token != old.build_token and not fresh.resumed
    assert h.reader.checkpoints == original_checkpoints
    assert len(h.client.inserts) == 2
    assert all(stream.resume is None for stream in fresh.streams)
    assert h.client.inserts[0][0]["status"] == "failed"
    with pytest.raises(PropertyCatalogCoordinatorError, match="superseded"):
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=old.build_plan_json),
            operation=lambda: pytest.fail("source-invalidated snapshot was activated"),
        )


def test_live_healthy_build_is_not_superseded_without_audit_failure(tmp_path):
    h = _Harness(tmp_path)
    old = h.build.lease
    h.clock.current = old.expires_at - timedelta(seconds=1)
    fresh = h.prepare()
    assert fresh.resumed and fresh.lease == old and h.client.inserts == []


@pytest.mark.parametrize("possibly_activated", [False, True])
def test_fenced_or_possibly_activated_snapshot_cannot_be_invalidated(
    tmp_path, possibly_activated
):
    h = _Harness(tmp_path, status="open" if possibly_activated else "fenced")
    old = h.build.lease
    if possibly_activated:
        key = h.coordinator._revision_key_for_lease(old)
        h.coordinator._recovery_journal.save_record(
            key + ":activation",
            {
                "catalog_revision": old.catalog_revision,
                "build_token": old.build_token,
                "build_lease_sha256": old.build_lease_sha256,
            },
        )
    with pytest.raises(PropertyCatalogCoordinatorError):
        h.coordinator.invalidate_source_snapshot(old)
    assert not h.coordinator.source_snapshot_invalid(old)
    assert h.client.inserts == []


@pytest.mark.parametrize("fail_at", [0, 1])
@pytest.mark.parametrize("fail_after", [False, True])
def test_invalidated_unexpired_replacement_recovers_uncertain_writes(
    tmp_path, fail_at, fail_after
):
    h = _Harness(tmp_path)
    old = h.build.lease
    h.clock.current = old.expires_at - timedelta(seconds=1)
    h.coordinator.invalidate_source_snapshot(old)
    h.client.fail_at, h.client.fail_after = fail_at, fail_after
    with pytest.raises(TimeoutError):
        h.prepare()
    h.restart()
    fresh = h.prepare()
    assert fresh.lease.catalog_revision == old.catalog_revision + 1
    replacement_rows = [
        row
        for batch in h.client.inserts
        for row in batch
        if row["catalog_revision"] == fresh.lease.catalog_revision
    ]
    assert len({row["build_token"] for row in replacement_rows}) == 1
    assert h.coordinator.source_snapshot_invalid(old)


@pytest.mark.parametrize("status", ["open", "draining"])
@pytest.mark.parametrize("with_active", [False, True])
def test_automatic_supersession_quarantines_old_data_without_replaying_it(
    tmp_path: Path,
    status: str,
    with_active: bool,
):
    h = _Harness(tmp_path, status=status, with_active=with_active)
    old, active = h.build.lease, h.reader.active
    checkpoints = dict(h.reader.checkpoints)
    result = h.prepare()  # Routine default: no operator repair flag.
    assert result.lease.catalog_revision == old.catalog_revision + 1
    assert result.lease.build_token != old.build_token
    assert result.prior_active == active == h.reader.active
    assert result.resumed is False and all(
        stream.resume is None for stream in result.streams
    )
    assert h.reader.checkpoints == checkpoints
    assert len(h.client.inserts) == 2 and h.sink.assignments == []
    revoked = h.client.inserts[0][0]
    assert revoked["status"] == "failed" and revoked["_version"] == (1 << 64) - 1
    assert revoked["build_plan_json"] == old.build_plan_json
    assert revoked["fenced_at"] is None
    # Simulate a data write that was already in flight. The activation lineage
    # still cannot name its revision/token, and nothing copies these bytes.
    h.client.data_rows.append(
        {"catalog_revision": old.catalog_revision, "build_token": old.build_token}
    )
    assert all(
        row["build_token"] != result.lease.build_token for row in h.client.data_rows
    )
    with pytest.raises(PropertyCatalogCoordinatorError, match="superseded"):
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=old.build_plan_json),
            operation=lambda: pytest.fail("quarantined data became active"),
        )


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("landed", [False, True])
def test_restart_resolves_exact_journaled_control_writes(
    tmp_path: Path,
    stage: int,
    landed: bool,
):
    h = _Harness(tmp_path)
    h.client.fail_at, h.client.fail_after = stage, landed
    active = h.reader.active
    with pytest.raises(TimeoutError, match="ambiguous"):
        h.prepare()
    intent = h.coordinator._recovery_journal.load(
        h.coordinator._revision_key_for_lease(h.build.lease)
    )
    assert intent is not None and not intent["complete"]
    expected_plan = intent["replacement"]["build_plan_json"]
    h.restart()  # New serializer/coordinator, same persistent journal and DB log.
    result = h.prepare()
    assert result.resumed
    assert result.lease.build_plan_json == expected_plan
    assert h.reader.active == active
    assert len(h.freezer.calls) == 1  # No new source cutoff or replacement plan.
    assert all(stream.resume is None for stream in result.streams)
    for attempts in h.client.inserts:
        for row in attempts:
            if row["build_token"] == result.lease.build_token:
                assert row["build_plan_json"] == expected_plan


def test_delayed_old_fence_cannot_overwrite_terminal_failure(tmp_path: Path):
    h = _Harness(tmp_path, status="draining")
    old = h.build.lease
    h.prepare()
    h.client.stream_rows.append(
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": old.expires_at - timedelta(seconds=1),
        }
    )
    proofs = tuple(
        StreamDrainProof(
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            last_issued_sequence=1,
            fenced_sequence=1,
            terminal_sequence=1,
            terminal_payload_sha256=SHA_A,
        )
        for stream in old.build_plan.streams
    )
    with pytest.raises(PropertyCatalogCoordinatorError, match="closed"):
        h.coordinator.fence(
            lease=old,
            stream_proofs=proofs,
            checkpoint_state_sha256s=(SHA_A,),
            final_manifest_sha256=SHA_A,
            drain_deadline=old.expires_at,
            now=old.expires_at - timedelta(seconds=1),  # Captured before expiry.
        )
    with pytest.raises(PropertyCatalogCoordinatorError, match="superseded"):
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=old.build_plan_json),
            operation=lambda: None,
        )
    assert len(h.client.inserts) == 2


def test_fenced_reservation_and_active_head_win_supersession_cas(tmp_path: Path):
    h = _Harness(tmp_path)
    freeze = h.lifecycle._cutoff_freezer

    def racing_freeze(**kwargs):
        result = freeze(**kwargs)
        h.client.stream_rows.append(
            {
                **h.original,
                "status": "fenced",
                "_version": 3,
                "fenced_at": h.build.lease.expires_at,
            }
        )
        return result

    h.lifecycle._cutoff_freezer = racing_freeze
    with pytest.raises(PropertyCatalogCoordinatorError, match="closed"):
        h.prepare()
    assert h.client.inserts == []
    assert not list(tmp_path.glob("*.supersession.json"))


def test_supersession_rechecks_active_head_under_lock(tmp_path: Path):
    h = _Harness(tmp_path)
    h.client.active_rows[0] = {**h.client.active_rows[0], "activation_sha256": "f" * 64}
    with pytest.raises(PropertyCatalogCoordinatorError, match="active lineage changed"):
        h.prepare()
    assert h.client.inserts == []


def test_newer_reservation_blocks_stale_supersession_request(tmp_path: Path):
    h = _Harness(tmp_path)
    freeze = h.lifecycle._cutoff_freezer

    def racing_freeze(**kwargs):
        result = freeze(**kwargs)
        h.client.stream_rows.append(
            {
                **h.original,
                "catalog_revision": h.build.lease.catalog_revision + 1,
                "build_token": TOKEN_D,
                "producer_stream_id": TOKEN_D,
            }
        )
        return result

    h.lifecycle._cutoff_freezer = racing_freeze
    with pytest.raises(PropertyCatalogCoordinatorError, match="newer revision exists"):
        h.prepare()
    assert h.client.inserts == []


@pytest.mark.parametrize("archive", [False, True])
def test_corrupt_journal_cannot_reallocate(tmp_path: Path, archive: bool):
    h = _Harness(tmp_path)
    h.client.fail_at = 0
    with pytest.raises(TimeoutError):
        h.prepare()
    key = h.coordinator._revision_key_for_lease(h.build.lease)
    if archive:
        key += ":revoked:" + h.build.lease.build_lease_sha256
    journal = h.coordinator._recovery_journal._path(key)
    journal.write_text("invalid", encoding="utf-8")
    before = list(h.client.inserts)
    h.restart()
    with pytest.raises(PropertyCatalogCoordinatorError, match="journal is corrupt"):
        h.prepare()
    assert h.client.inserts == before


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "fifo", "directory"])
def test_nonregular_journal_fails_before_any_recovery_io(tmp_path: Path, kind: str):
    h = _Harness(tmp_path)
    journal = h.coordinator._recovery_journal
    path = journal._path(h.coordinator._revision_key_for_lease(h.build.lease))
    if kind in {"symlink", "dangling_symlink"}:
        target = tmp_path / "target.json"
        if kind == "symlink":
            target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(PropertyCatalogCoordinatorError, match="journal"):
        h.prepare()
    assert h.client.inserts == [] and h.client.queries == []
    assert h.freezer.calls == []


@pytest.mark.parametrize(
    "fault", ["whitespace", "duplicate_key", "extra_key", "oversized", "truncated"]
)
def test_journal_requires_bounded_canonical_record(tmp_path: Path, fault: str):
    h = _Harness(tmp_path)
    h.client.fail_at = 0
    with pytest.raises(TimeoutError):
        h.prepare()
    key = h.coordinator._revision_key_for_lease(h.build.lease)
    path = h.coordinator._recovery_journal._path(key)
    raw = path.read_bytes()
    if fault == "whitespace":
        raw = b" " + raw
    elif fault == "duplicate_key":
        raw = b'{"key":' + json.dumps(key).encode() + b"," + raw[1:]
    elif fault == "extra_key":
        raw = b'{"extra":true,' + raw[1:]
    elif fault == "oversized":
        raw += b" " * 2_097_152
    else:
        raw = raw[:-2]
    path.write_bytes(raw)
    before = (list(h.client.inserts), list(h.client.queries))
    with pytest.raises(PropertyCatalogCoordinatorError, match="journal"):
        h.prepare()
    assert (h.client.inserts, h.client.queries) == before


def test_journal_container_can_exceed_one_property_definition_limit(tmp_path: Path):
    journal = FileSupersessionJournal(str(tmp_path))
    record = {"payload": "x" * 40_000}
    journal.save_record("workspace", record)
    assert journal.load_record("workspace") == record


def _process_read_fifo_journal(directory, key, finished):
    try:
        FileSupersessionJournal(directory).load_record(key)
    except PropertyCatalogCoordinatorError:
        finished.set()


def test_fifo_journal_read_never_waits_for_a_writer(tmp_path: Path):
    journal = FileSupersessionJournal(str(tmp_path))
    os.mkfifo(journal._path("workspace"))
    context = get_context("spawn")
    finished = context.Event()
    process = context.Process(
        target=_process_read_fifo_journal,
        args=(str(tmp_path), "workspace", finished),
    )
    process.start()
    try:
        assert finished.wait(5), "journal open/read blocked on a FIFO"
    finally:
        process.join(0.1)
        if process.is_alive():
            process.terminate()
            process.join(2)
    assert process.exitcode == 0


def test_activator_requires_and_holds_coordinator_boundary(tmp_path: Path, monkeypatch):
    h = _Harness(tmp_path)
    fence = SimpleNamespace(build_plan_json=h.build.lease.build_plan_json)
    arguments = {"manifest": None, "fence": fence, "inventory": None, "now": h.clock()}
    with pytest.raises(
        ActivationRejected, match="workspace_activation_serialization_required"
    ):
        PropertyCatalogActivator(None).activate(**arguments)
    h.client.stream_rows.append(
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.build.lease.expires_at,
        }
    )
    activator = PropertyCatalogActivator(None, coordinator=h.coordinator)
    monkeypatch.setattr(
        activator, "_activate_serialized", lambda **_: "qualified append"
    )
    assert activator.activate(**arguments) == "qualified append"


def test_replacement_runs_real_activation_qualification_inside_mandatory_guard(
    tmp_path: Path,
):
    h = _Harness(tmp_path, with_active=False)
    prepared = h.prepare()
    lease = prepared.lease
    checkpoints = tuple(item.checkpoint for item in _complete_checkpoints(prepared))
    planned = {stream.key: stream for stream in lease.build_plan.streams}
    manifest = ActivationManifest(
        organization_id=lease.organization_id,
        workspace_id=lease.workspace_id,
        catalog_epoch=lease.catalog_epoch,
        catalog_revision=lease.catalog_revision,
        build_token=lease.build_token,
        projection_version=lease.projection_version,
        lifecycle_mode=prepared.lifecycle_mode,
        lineage_anchor_revision=prepared.lineage_anchor_revision,
        streams=tuple(
            ManifestStream(
                requirement=stream_requirement(checkpoint),
                role=planned[checkpoint.key].role,
            )
            for checkpoint in checkpoints
        ),
    )
    fence = make_revision_fence(
        manifest=manifest,
        build_plan=lease.build_plan,
        checkpoints=checkpoints,
        drain_deadline=lease.expires_at,
        fenced_at=h.clock(),
    )
    records = []
    calls = []

    class Store:
        def audit_build_plan(self, *, build_plan, manifest):
            assert build_plan.matches_manifest(manifest)
            calls.append("audit")

        def load_checkpoints(self, requirement):
            calls.append("checkpoints")
            return checkpoints

        def load_activation_history(self, **_):
            calls.append("activations")
            return ActivationHistory(
                tuple(records),
                max((record.catalog_revision for record in records), default=0),
                max((record.activation_sequence for record in records), default=0),
            )

        def append_active(self, record, **_):
            calls.append("append")
            key = h.coordinator._revision_key_for_lease(lease) + ":activation"
            assert h.coordinator._recovery_journal.load_record(key) == {
                "catalog_revision": lease.catalog_revision,
                "build_token": lease.build_token,
                "build_lease_sha256": lease.build_lease_sha256,
            }
            records.append(record)
            return record

    activator = PropertyCatalogActivator(Store(), coordinator=h.coordinator)
    arguments = {
        "manifest": manifest,
        "fence": fence,
        "inventory": ActivationInventory(0, 0, 0),
        "now": h.clock(),
    }
    with pytest.raises(PropertyCatalogCoordinatorError, match="not fenced"):
        activator.activate(**arguments)
    assert calls == [] and records == []
    h.client.stream_rows.append(
        {
            **h.client.inserts[-1][0],
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.clock(),
        }
    )
    result = activator.activate(**arguments)
    assert result.record == records[0] and result.qualification.qualified
    assert calls == ["audit", "checkpoints", "activations", "append"]
    assert activator.activate(**arguments).idempotent
    assert len(records) == 1


def test_activation_and_supersession_use_the_same_shared_workspace_lock(tmp_path: Path):
    h = _Harness(tmp_path)
    h.client.stream_rows.append(
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.build.lease.expires_at,
        }
    )
    entered, release, competing_entered = Event(), Event(), Event()
    errors = []

    def activation():
        try:
            h.coordinator.serialize_activation(
                fence=SimpleNamespace(build_plan_json=h.build.lease.build_plan_json),
                operation=lambda: (entered.set(), release.wait(5)),
            )
        except Exception as exc:
            errors.append(exc)

    worker = Thread(target=activation)
    worker.start()
    assert entered.wait(2)
    other_serializer = FileCatalogMutationSerializer(str(tmp_path))
    contender = Thread(
        target=lambda: other_serializer.serialize(
            h.coordinator._revision_key_for_lease(h.build.lease),
            competing_entered.set,
        )
    )
    contender.start()
    try:
        assert not competing_entered.wait(0.05)
    finally:
        release.set()
        worker.join(2)
        contender.join(2)
    assert competing_entered.is_set() and errors == []


def test_recorded_revocation_vetoes_stale_replica_after_later_supersession(tmp_path):
    h = _Harness(tmp_path)
    first = h.prepare()
    h.clock.current = first.lease.expires_at
    h.prepare()  # Replaces the pending-operation slot with a second supersession.
    h.client.stream_rows[:] = [
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.build.lease.expires_at,
        }
    ]
    h.restart()
    with pytest.raises(PropertyCatalogCoordinatorError, match="durably superseded"):
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=h.build.lease.build_plan_json),
            operation=lambda: pytest.fail("stale replica revived superseded build"),
        )


def test_ambiguous_activation_cannot_be_superseded_using_stale_rows(tmp_path):
    h = _Harness(tmp_path)
    h.client.stream_rows.append(
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.build.lease.expires_at,
        }
    )

    def ambiguous_activation():
        raise TimeoutError("activation append acknowledgement lost")

    with pytest.raises(TimeoutError):
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=h.build.lease.build_plan_json),
            operation=ambiguous_activation,
        )
    h.client.stream_rows[:] = [
        h.original
    ]  # Stale OPEN; active INSERT may be in flight.
    h.restart()
    with pytest.raises(
        PropertyCatalogCoordinatorError, match="activation outcome is unresolved"
    ):
        h.prepare()
    assert h.client.inserts == []


def test_restart_quarantines_delayed_fence_after_supersession_intent(tmp_path):
    h = _Harness(tmp_path, status="draining")
    h.client.fail_at = 0
    with pytest.raises(TimeoutError):
        h.prepare()
    h.client.stream_rows.append(
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.build.lease.expires_at,
        }
    )
    h.restart()
    result = h.prepare()
    assert result.lease.build_token == TOKEN_C and result.resumed
    with pytest.raises(PropertyCatalogCoordinatorError, match="superseded"):
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=h.build.lease.build_plan_json),
            operation=lambda: None,
        )


def test_same_status_new_version_fails_initial_cas(tmp_path):
    h = _Harness(tmp_path)
    freeze = h.lifecycle._cutoff_freezer

    def raced(**kwargs):
        result = freeze(**kwargs)
        h.client.stream_rows.append({**h.original, "_version": 2})
        return result

    h.lifecycle._cutoff_freezer = raced
    with pytest.raises(PropertyCatalogCoordinatorError, match="version CAS failed"):
        h.prepare()
    assert h.client.inserts == []


def _process_contender(directory, key, ready, entered):
    ready.set()
    FileCatalogMutationSerializer(directory).serialize(key, entered.set)


def test_activation_holds_workspace_lock_against_another_process(tmp_path):
    h = _Harness(tmp_path)
    h.client.stream_rows.append(
        {
            **h.original,
            "status": "fenced",
            "_version": 3,
            "fenced_at": h.build.lease.expires_at,
        }
    )
    context = get_context("spawn")
    ready, entered = context.Event(), context.Event()
    process = context.Process(
        target=_process_contender,
        args=(
            str(tmp_path),
            h.coordinator._revision_key_for_lease(h.build.lease),
            ready,
            entered,
        ),
    )

    def append_while_locked():
        process.start()
        assert ready.wait(5)
        assert not entered.wait(0.05)

    try:
        h.coordinator.serialize_activation(
            fence=SimpleNamespace(build_plan_json=h.build.lease.build_plan_json),
            operation=append_while_locked,
        )
        assert entered.wait(5)
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(2)
    assert process.exitcode == 0
