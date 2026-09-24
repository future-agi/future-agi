"""Completed physical backfills are history, never executable resume plans."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from tracer.services.clickhouse.v2.property_catalog import durable_lifecycle as dl
from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    BuildPlanStream,
    ManifestStreamRole,
    RevisionBuildPlan,
    RevisionLease,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_json_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    ReconcileRequest,
    _source_cursor,
)
from tracer.tests.test_property_catalog_durable_lifecycle import (
    INITIAL_SINCE,
    INITIAL_UNTIL,
    SHA_A,
    TOKEN_A,
    TOKEN_B,
    _bounds,
    _Clock,
    _complete_checkpoints,
    _Freezer,
    _jsonstrings_activation_row,
    _lifecycle,
    _scope,
    _State,
)

# A production-shaped opaque generation, intentionally too large for a UTC
# microsecond timestamp. It must survive unchanged in persisted evidence.
FENCE = 1788179167838495941


class _PhysicalSnapshot:
    catalog_database = "property_catalog_dev_unit"

    def __init__(self):
        self.scope = replace(_scope(), catalog_epoch=1, projection_version=3)
        self.plan = RevisionBuildPlan(
            organization_id=self.scope.organization_id,
            workspace_id=self.scope.workspace_id,
            catalog_epoch=1,
            catalog_revision=3,
            projection_version=3,
            build_token=TOKEN_A,
            source_scope=BuildPlanSourceScope(
                project_ids=self.scope.project_ids,
                span_since_us=dl._datetime_to_micros(INITIAL_SINCE),
                span_until_us=dl._datetime_to_micros(INITIAL_UNTIL),
            ),
            streams=tuple(
                BuildPlanStream(
                    source_adapter=adapter,
                    role=role,
                    producer_stream_id=str(uuid5(NAMESPACE_URL, f"{adapter}/{role}")),
                    source_cutoff_label="physical_snapshot_r3",
                    source_version_fence=FENCE,
                )
                for adapter, role in sorted(dl._EXPECTED_ROLE_INVENTORY)
            ),
        )
        self.lease = RevisionLease(
            organization_id=self.scope.organization_id,
            workspace_id=self.scope.workspace_id,
            catalog_epoch=1,
            catalog_revision=3,
            projection_version=3,
            build_token=TOKEN_A,
            build_plan_json=self.plan.canonical_json,
            build_lease_sha256=self.plan.sha256,
            issued_at=INITIAL_UNTIL,
            expires_at=INITIAL_UNTIL + timedelta(days=7),
        )
        self.reservation = {
            "organization_id": self.scope.organization_id,
            "workspace_id": self.scope.workspace_id,
            "catalog_epoch": 1,
            "catalog_revision": 3,
            "build_token": TOKEN_A,
            "projection_version": 3,
            "producer_stream_id": TOKEN_A,
            "envelope_version": 0,
            "build_plan_json": self.plan.canonical_json,
            "build_lease_sha256": self.plan.sha256,
            "status": "fenced",
            "started_at": self.lease.issued_at,
            "drain_deadline": self.lease.expires_at,
            "fenced_at": INITIAL_UNTIL,
            "_version": 1,
        }
        manifest = canonical_json(
            {
                "lifecycle_mode": "initial_backfill",
                "lineage_anchor_revision": 3,
                "streams": [
                    {
                        "source_adapter": s.source_adapter,
                        "role": s.role,
                        "producer_stream_id": s.producer_stream_id,
                        "source_version_fence": s.source_version_fence,
                    }
                    for s in self.plan.streams
                ],
            }
        )
        self.activation = dict(
            _jsonstrings_activation_row(),
            catalog_epoch=1,
            catalog_revision=3,
            projection_version=3,
            lineage_anchor_revision=3,
            activation_sequence=1,
            source_manifest_json=manifest,
            source_manifest_sha256=canonical_json_sha256(manifest),
            qualified_at=INITIAL_UNTIL,
            updated_at=INITIAL_UNTIL,
        )
        self.checkpoints = {
            value.checkpoint.key: replace(value, watermark=str(FENCE))
            for value in _complete_checkpoints(
                SimpleNamespace(scope=self.scope, lease=self.lease)
            )
        }
        self.queries = []

    def query(self, sql, params, *, timeout_ms):
        assert sql.startswith(("SELECT", "WITH"))
        assert params["workspace_id"] == self.scope.workspace_id
        assert timeout_ms <= 8500
        self.queries.append(sql)
        if "property_catalog_activations" in sql:
            return (self.activation,)
        assert "property_catalog_source_streams" in sql
        return (self.reservation,)

    def load_checkpoint_write(self, **params):
        assert params["catalog_revision"] == 3
        assert params["build_token"] == TOKEN_A
        return self.checkpoints.get(
            (params["source_adapter"], params["producer_stream_id"])
        )

    def reader(self):
        return dl.ClickHouseLifecycleStateReader(
            self, database=self.catalog_database, checkpoint_store=self
        )


def test_physical_snapshot_active_read_keeps_plan_and_checkpoint_hashes():
    stored = _PhysicalSnapshot()
    before = (
        dict(stored.reservation),
        dict(stored.activation),
        dict(stored.checkpoints),
    )

    active = stored.reader().load_latest_active(stored.scope)

    assert active.build_plan.canonical_json == stored.plan.canonical_json
    assert active.build_plan.sha256 == stored.lease.build_lease_sha256
    assert len(active.streams) == 10
    assert {s.watermark for s in active.streams} == {str(FENCE)}
    assert (stored.reservation, stored.activation, stored.checkpoints) == before
    decoded = dl._decode_plan_scope(active.build_plan, allow_physical_snapshot=True)
    assert decoded.cutoffs.span_window == dl.SourceWindow(INITIAL_SINCE, INITIAL_UNTIL)
    assert decoded.cutoffs.span_audit_generation == FENCE
    assert len(stored.queries) == 2


@pytest.mark.parametrize(
    "mode", [dl.LifecycleRunMode.INCREMENTAL, dl.LifecycleRunMode.FULL_REPAIR]
)
def test_next_run_uses_standard_plan_same_projection_and_safe_definition_watermarks(
    mode,
):
    stored = _PhysicalSnapshot()
    active = stored.reader().load_latest_active(stored.scope)
    state = _State(active=active)
    clock = _Clock(INITIAL_UNTIL + timedelta(minutes=15))
    lifecycle = _lifecycle(
        state=state, clock=clock, freezer=_Freezer(clock), tokens=[TOKEN_B]
    )

    prepared = lifecycle.prepare(
        scope=stored.scope, mode=mode, configured_bounds=_bounds()
    )

    assert prepared.lease.catalog_revision == 4
    assert prepared.lease.projection_version == 3
    assert prepared.prior_active is active
    assert dl._decode_plan_scope(prepared.lease.build_plan).mode is mode
    assert prepared.cutoffs.span_window.since == (
        INITIAL_UNTIL if mode is dl.LifecycleRunMode.INCREMENTAL else INITIAL_SINCE
    )
    assert all(
        s.lower_watermark == ""
        for s in prepared.streams
        if s.role is ManifestStreamRole.DEFINITIONS
    )
    assert all(
        s.source_cutoff_label != "physical_snapshot_r3"
        for s in prepared.lease.build_plan.streams
    )
    assert active.build_plan.sha256 == stored.plan.sha256
    context = PostgresSnapshotContext(
        organization_id=stored.scope.organization_id,
        workspace_id=stored.scope.workspace_id,
        project_ids=stored.scope.project_ids,
        catalog_epoch=stored.scope.catalog_epoch,
        catalog_revision=prepared.lease.catalog_revision,
        projection_version=prepared.lease.projection_version,
        snapshot_cutoff=prepared.cutoffs.snapshot_upper,
    )
    # Exercise the downstream request/cursor contract for every definition
    # adapter, not only the lifecycle's string-valued StreamStart container.
    for stream in prepared.lease.build_plan.streams:
        if stream.role is ManifestStreamRole.DEFINITIONS:
            request = ReconcileRequest(
                context=context,
                build_token=prepared.lease.build_token,
                producer_stream_id=stream.producer_stream_id,
                emitted_at=clock.current,
                mode=mode.reconcile_mode,
                source_version=stream.source_version_fence,
                lower_watermark=prepared.stream(
                    stream.source_adapter, stream.role
                ).lower_watermark,
            )
            assert _source_cursor(request) == ""
    # Crash recovery uses the *new* executable plan, retaining the same token.
    resumed = lifecycle.prepare(
        scope=stored.scope, mode=mode, configured_bounds=_bounds()
    )
    assert resumed.lease == prepared.lease
    assert resumed.resumed


@pytest.mark.parametrize("status", list(dl.ReservationStatus))
def test_physical_snapshot_without_activation_is_not_resumable(status):
    stored = _PhysicalSnapshot()
    state = _State(reservation=dl.PersistedReservation(stored.lease, status))
    clock = _Clock(INITIAL_UNTIL + timedelta(minutes=15))
    lifecycle = _lifecycle(
        state=state, clock=clock, freezer=_Freezer(clock), tokens=[TOKEN_B]
    )
    with pytest.raises(dl.DurableLifecycleError, match="cannot resume"):
        lifecycle.prepare(
            scope=stored.scope,
            mode=dl.LifecycleRunMode.AUTO,
            configured_bounds=_bounds(),
        )


@pytest.mark.parametrize(
    "field,value",
    [("catalog_epoch", 2), ("catalog_revision", 4), ("projection_version", 1)],
)
def test_physical_snapshot_rejects_other_contracts(field, value):
    plan = replace(_PhysicalSnapshot().plan, **{field: value})
    with pytest.raises(dl.DurableLifecycleError, match="contract changed"):
        dl._decode_plan_scope(plan, allow_physical_snapshot=True)


@pytest.mark.parametrize("change", ["label", "fence", "inventory"])
def test_physical_snapshot_rejects_mixed_or_missing_streams(change):
    plan = _PhysicalSnapshot().plan
    streams = list(plan.streams)
    if change == "inventory":
        streams.pop()
    elif change == "label":
        streams[0] = replace(
            streams[0], source_cutoff_label="initial_backfill_postgres_until_us"
        )
    else:
        streams[0] = replace(streams[0], source_version_fence=FENCE + 1)
    with pytest.raises((dl.DurableLifecycleError, ValueError)):
        plan = replace(plan, streams=tuple(streams))
        dl._decode_plan_scope(plan, allow_physical_snapshot=True)


@pytest.mark.parametrize(
    "change",
    ["missing", "running", "poison", "conflict", "gap", "projection", "watermark"],
)
def test_active_physical_snapshot_still_requires_ten_safe_checkpoints(change):
    stored = _PhysicalSnapshot()
    key = next(iter(stored.checkpoints))
    value = stored.checkpoints[key]
    if change == "missing":
        del stored.checkpoints[key]
    elif change == "watermark":
        stored.checkpoints[key] = replace(value, watermark="unproven-cursor")
    else:
        kwargs = {
            "running": {"status": CheckpointStatus.RUNNING, "terminal": False},
            "poison": {"poison_count": 1},
            "conflict": {"conflict_count": 1},
            "gap": {"gap_count": 1},
            "projection": {"projection_version": 1},
        }[change]
        stored.checkpoints[key] = replace(
            value,
            checkpoint=replace(value.checkpoint, **kwargs),
            gap_reasons=("gap",) if change == "gap" else (),
        )
    with pytest.raises(dl.DurableLifecycleError):
        stored.reader().load_latest_active(stored.scope)


@pytest.mark.parametrize(
    "change", ["open", "plan_hash", "manifest_hash", "manifest_stream", "projection"]
)
def test_active_physical_snapshot_rejects_invalid_reservation_or_activation(change):
    stored = _PhysicalSnapshot()
    if change == "open":
        stored.reservation["status"] = "open"
    elif change == "plan_hash":
        stored.reservation["build_lease_sha256"] = SHA_A
    elif change == "manifest_hash":
        stored.activation["source_manifest_sha256"] = SHA_A
    elif change == "projection":
        stored.activation["projection_version"] = 1
    else:
        manifest = json.loads(stored.activation["source_manifest_json"])
        manifest["streams"][0]["source_version_fence"] += 1
        payload = canonical_json(manifest)
        stored.activation.update(
            source_manifest_json=payload,
            source_manifest_sha256=canonical_json_sha256(payload),
        )
    with pytest.raises((dl.DurableLifecycleError, ValueError)):
        stored.reader().load_latest_active(stored.scope)
