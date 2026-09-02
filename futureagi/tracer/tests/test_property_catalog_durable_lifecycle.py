from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    CatalogLifecycleMode,
    ManifestStreamRole,
    RevisionBuildPlan,
    RevisionLease,
    StreamDrainProof,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    ZERO_UUID,
    canonical_json,
    canonical_json_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    _ACTIVATION_COLUMNS,
    _RESERVATION_COLUMNS,
    ActiveStreamEvidence,
    ClickHouseLifecycleStateReader,
    ConfiguredSourceBounds,
    DurableLifecycleError,
    DurableWorkspaceCatalogLifecycle,
    FreshSpanLifecycleCutoffFreezer,
    FrozenLifecycleCutoffs,
    LifecycleCompletionEvidence,
    LifecycleRunMode,
    LineageAnchorEvidence,
    PersistedCheckpointEvidence,
    PersistedReservation,
    PreparedLifecycleRevision,
    PriorActiveEvidence,
    ReservationStatus,
    SourceWindow,
    WorkspaceCatalogScope,
    _activation_state,
    _decode_plan_scope,
    _persisted_active_watermark,
    _uint,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CatalogCheckpoint,
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    CheckpointWrite,
    ReconcileMode,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    SourceKeysetCursor,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import FrozenSpanSource

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT_A = "33333333-3333-4333-8333-333333333333"
PROJECT_B = "44444444-4444-4444-8444-444444444444"
HOT_STREAM = "55555555-5555-4555-8555-555555555555"
TOKEN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TOKEN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TOKEN_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
ZERO_SHA = "0" * 64
INITIAL_SINCE = datetime(2026, 8, 1, tzinfo=UTC)
INITIAL_UNTIL = datetime(2026, 8, 14, 20, tzinfo=UTC)


def _scope() -> WorkspaceCatalogScope:
    return WorkspaceCatalogScope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        projection_version=1,
        project_ids=(PROJECT_B, PROJECT_A),
    )


def _bounds() -> ConfiguredSourceBounds:
    return ConfiguredSourceBounds(INITIAL_SINCE, INITIAL_UNTIL)


def _jsonstrings_activation_row() -> dict[str, object]:
    manifest = canonical_json(
        {
            "lifecycle_mode": "initial_backfill",
            "lineage_anchor_revision": 17,
        }
    )
    return {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "catalog_epoch": 3,
        "catalog_revision": "17",
        "build_token": TOKEN_A,
        "projection_version": 1,
        "lifecycle_mode": "initial_backfill",
        "lineage_anchor_revision": "17",
        "activation_sequence": "1",
        "source_manifest_json": manifest,
        "source_manifest_sha256": canonical_json_sha256(manifest),
        "revision_fence_sha256": SHA_A,
        "activation_sha256": SHA_B,
        "status": "active",
        "live_definition_rows": "231",
        "tombstone_rows": "0",
        "value_rows": "246884",
        "qualified_at": "2026-08-14 20:00:00.000000+00:00",
        "updated_at": "2026-08-14 20:00:00.000000+00:00",
        "_version": "1",
    }


def test_activation_state_accepts_clickhouse_jsonstrings_uint64s() -> None:
    state = _activation_state(_jsonstrings_activation_row(), scope=_scope())

    assert state.catalog_revision == 17
    assert state.lineage_anchor_revision == 17
    assert state.activation_sequence == 1
    assert state.status == "active"


def test_clickhouse_activation_projection_returns_unqualified_mapping_keys() -> None:
    queries: list[str] = []

    class Client:
        catalog_database = "property_catalog_dev_unit"

        def query(
            self,
            sql: str,
            _params: dict[str, object],
            *,
            timeout_ms: int,
        ) -> tuple[dict[str, object], ...]:
            assert timeout_ms == 8_500
            queries.append(sql)
            row = _jsonstrings_activation_row()
            assert set(row) == set(_ACTIVATION_COLUMNS)
            return (row,)

    class Checkpoints:
        def load_checkpoint_write(self, **_kwargs: object) -> None:
            raise AssertionError(
                "activation-state projection must not load checkpoints"
            )

    reader = ClickHouseLifecycleStateReader(
        Client(),  # type: ignore[arg-type]
        database="property_catalog_dev_unit",
        checkpoint_store=Checkpoints(),  # type: ignore[arg-type]
    )

    states = reader._activation_states(_scope())

    projection = ", ".join(f"s.{column} AS {column}" for column in _ACTIVATION_COLUMNS)
    assert len(queries) == 1
    assert f"SELECT DISTINCT {projection}" in queries[0]
    for column in (
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
    ):
        assert f"s.{column} AS {column}" in queries[0]
    assert len(states) == 1
    assert states[0].catalog_revision == 17


def test_clickhouse_reservation_projections_alias_qualified_columns() -> None:
    queries: list[str] = []

    class Client:
        catalog_database = "property_catalog_dev_unit"

        def query(
            self,
            sql: str,
            _params: dict[str, object],
            *,
            timeout_ms: int,
        ) -> tuple[()]:
            assert timeout_ms == 8_500
            queries.append(sql)
            return ()

    class Checkpoints:
        def load_checkpoint_write(self, **_kwargs: object) -> None:
            raise AssertionError(
                "empty reservation inventory must not load checkpoints"
            )

    reader = ClickHouseLifecycleStateReader(
        Client(),  # type: ignore[arg-type]
        database="property_catalog_dev_unit",
        checkpoint_store=Checkpoints(),  # type: ignore[arg-type]
    )

    assert reader.load_nonterminal(_scope()) is None

    projection = ", ".join(f"s.{column} AS {column}" for column in _RESERVATION_COLUMNS)
    assert len(queries) == 2
    assert all(f"SELECT DISTINCT {projection}" in query for query in queries)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (0, 0),
        ("0", 0),
        ((1 << 64) - 1, (1 << 64) - 1),
        (str((1 << 64) - 1), (1 << 64) - 1),
    ),
)
def test_uint_accepts_exact_uint64_number_or_decimal_string(
    value: object,
    expected: int,
) -> None:
    assert _uint(value, "catalog_revision") == expected


@pytest.mark.parametrize(
    "value",
    (
        True,
        -1,
        1.0,
        "",
        "00",
        "01",
        "+1",
        "-1",
        "1.0",
        "1e0",
        " 1",
        "1 ",
        "١",
        1 << 64,
        str(1 << 64),
        "9" * 10_000,
    ),
)
def test_uint_rejects_noncanonical_or_out_of_range_values(value: object) -> None:
    row = _jsonstrings_activation_row()
    row["catalog_revision"] = value

    with pytest.raises(DurableLifecycleError, match="catalog_revision is not a UInt64"):
        _activation_state(row, scope=_scope())


@dataclass
class _Clock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current


@dataclass
class _Freezer:
    clock: _Clock
    calls: list[dict[str, Any]] = field(default_factory=list)
    next_generation: int = 10_000

    def __call__(
        self,
        *,
        scope: WorkspaceCatalogScope,
        mode: LifecycleRunMode,
        span_since: datetime,
        configured_until: datetime | None,
        prior_active: PriorActiveEvidence | None,
    ) -> FrozenLifecycleCutoffs:
        self.calls.append(
            {
                "configured_until": configured_until,
                "mode": mode,
                "prior_active": prior_active,
                "scope": scope,
                "span_since": span_since,
            }
        )
        until = configured_until or self.clock.current
        self.next_generation += 1
        return FrozenLifecycleCutoffs(
            snapshot_upper=until,
            span_window=SourceWindow(span_since, until),
            span_audit_generation=self.next_generation,
        )


@dataclass
class _State:
    active: PriorActiveEvidence | None = None
    reservation: PersistedReservation | None = None
    resumes: tuple[CheckpointWrite, ...] = ()

    def load_latest_active(
        self, scope: WorkspaceCatalogScope
    ) -> PriorActiveEvidence | None:
        _ = scope
        return self.active

    def load_nonterminal(
        self, scope: WorkspaceCatalogScope
    ) -> PersistedReservation | None:
        _ = scope
        return self.reservation

    def load_resumes(self, lease: RevisionLease) -> tuple[CheckpointWrite, ...]:
        assert self.reservation is not None
        assert self.reservation.lease == lease
        return self.resumes

    def activate(self, prepared: PreparedLifecycleRevision, *, at: datetime) -> None:
        previous_anchor = (
            prepared.prior_active.lineage_anchor
            if prepared.prior_active is not None
            else None
        )
        if prepared.mode in {
            LifecycleRunMode.INITIAL_BACKFILL,
            LifecycleRunMode.FULL_REPAIR,
        }:
            anchor = LineageAnchorEvidence(
                catalog_revision=prepared.lease.catalog_revision,
                build_token=prepared.lease.build_token,
                mode=prepared.mode,
                qualified_at=at,
                activation_sequence=(
                    1
                    if prepared.prior_active is None
                    else prepared.prior_active.activation_sequence + 1
                ),
                activation_sha256=SHA_B,
                active_revisions_since=0,
            )
        else:
            assert previous_anchor is not None
            anchor = LineageAnchorEvidence(
                catalog_revision=previous_anchor.catalog_revision,
                build_token=previous_anchor.build_token,
                mode=previous_anchor.mode,
                qualified_at=previous_anchor.qualified_at,
                activation_sequence=previous_anchor.activation_sequence,
                activation_sha256=previous_anchor.activation_sha256,
                active_revisions_since=previous_anchor.active_revisions_since + 1,
            )
        sequence = (
            1
            if prepared.prior_active is None
            else prepared.prior_active.activation_sequence + 1
        )
        streams = tuple(
            ActiveStreamEvidence(
                source_adapter=value.source_adapter,
                role=value.role,
                producer_stream_id=value.producer_stream_id,
                source_version_fence=value.source_version_fence,
                watermark=(
                    f"watermark:{prepared.lease.catalog_revision}:"
                    f"{value.source_adapter}:{value.role}"
                ),
                checkpoint_state_sha256=SHA_A,
            )
            for value in prepared.lease.build_plan.streams
        )
        self.active = PriorActiveEvidence(
            catalog_revision=prepared.lease.catalog_revision,
            build_token=prepared.lease.build_token,
            projection_version=prepared.lease.projection_version,
            lifecycle_mode=CatalogLifecycleMode(prepared.mode.value),
            activation_sequence=sequence,
            activation_sha256=SHA_B,
            source_manifest_sha256=SHA_A,
            build_plan=prepared.lease.build_plan,
            streams=streams,
            qualified_at=at,
            lineage_anchor=anchor,
        )
        self.reservation = None
        self.resumes = ()


@dataclass
class _Coordinator:
    state: _State
    allocate_calls: int = 0

    def allocate(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        projection_version: int,
        build_token: str,
        source_scope: BuildPlanSourceScope,
        planned_streams: tuple[Any, ...],
        now: datetime,
        **_kwargs: Any,
    ) -> RevisionLease:
        self.allocate_calls += 1
        if self.state.reservation is not None:
            lease = self.state.reservation.lease
            if lease.build_token == build_token or lease.expires_at > now:
                assert lease.build_token == build_token
                assert lease.build_plan.source_scope == source_scope
                assert lease.build_plan.streams == planned_streams
                return lease
        revision = max(
            (
                self.state.reservation.lease.catalog_revision
                if self.state.reservation is not None
                else 0
            ),
            (
                self.state.active.catalog_revision
                if self.state.active is not None
                else 0
            ),
        )
        revision += 1
        plan = RevisionBuildPlan(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
            catalog_revision=revision,
            build_token=build_token,
            projection_version=projection_version,
            source_scope=source_scope,
            streams=planned_streams,
        )
        lease = RevisionLease(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
            catalog_revision=revision,
            build_token=build_token,
            projection_version=projection_version,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            issued_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        self.state.resumes = ()
        self.state.reservation = PersistedReservation(lease, ReservationStatus.OPEN)
        return lease


def _lifecycle(
    *,
    state: _State,
    clock: _Clock,
    freezer: _Freezer,
    tokens: list[str],
) -> DurableWorkspaceCatalogLifecycle:
    coordinator = _Coordinator(state)
    return DurableWorkspaceCatalogLifecycle(
        state_reader=state,
        coordinator=coordinator,  # type: ignore[arg-type]
        cutoff_freezer=freezer,
        hot_producer_stream_id=HOT_STREAM,
        now=clock,
        new_build_token=lambda: tokens.pop(0),
    )


def _running_checkpoint(prepared: PreparedLifecycleRevision) -> CheckpointWrite:
    stream = next(
        value
        for value in prepared.lease.build_plan.streams
        if value.source_adapter is SourceAdapter.EVAL_CONFIG
    )
    checkpoint = CatalogCheckpoint(
        organization_id=prepared.scope.organization_id,
        workspace_id=prepared.scope.workspace_id,
        catalog_epoch=prepared.scope.catalog_epoch,
        catalog_revision=prepared.lease.catalog_revision,
        build_token=prepared.lease.build_token,
        projection_version=prepared.scope.projection_version,
        source_adapter=stream.source_adapter,
        producer_stream_id=stream.producer_stream_id,
        source_version_fence=stream.source_version_fence,
        status=CheckpointStatus.RUNNING,
        terminal=False,
        source_count=0,
        definition_count=0,
        value_count=0,
        tombstone_count=0,
        gap_count=0,
        poison_count=0,
        conflict_count=0,
        first_sequence=None,
        last_sequence=None,
        last_issued_sequence=0,
        fenced_sequence=0,
        terminal_payload_sha256=ZERO_SHA,
        delivery_count=0,
        source_digest=SHA_A,
        emitted_digest=SHA_B,
    )
    return CheckpointWrite(
        checkpoint=checkpoint,
        source_cursor="persisted-keyset-cursor",
        watermark="persisted-watermark",
        source_version_fence=stream.source_version_fence,
        source_fingerprint=SHA_C,
        previous_payload_sha256=ZERO_SHA,
        processed_rows=0,
    )


def _complete_checkpoints(
    prepared: PreparedLifecycleRevision,
) -> tuple[CheckpointWrite, ...]:
    result = []
    for stream in prepared.lease.build_plan.streams:
        checkpoint = CatalogCheckpoint(
            organization_id=prepared.scope.organization_id,
            workspace_id=prepared.scope.workspace_id,
            catalog_epoch=prepared.scope.catalog_epoch,
            catalog_revision=prepared.lease.catalog_revision,
            build_token=prepared.lease.build_token,
            projection_version=prepared.scope.projection_version,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            source_version_fence=stream.source_version_fence,
            status=CheckpointStatus.COMPLETE,
            terminal=True,
            source_count=0,
            definition_count=0,
            value_count=0,
            tombstone_count=0,
            gap_count=0,
            poison_count=0,
            conflict_count=0,
            first_sequence=1,
            last_sequence=1,
            last_issued_sequence=1,
            fenced_sequence=1,
            terminal_payload_sha256=SHA_A,
            delivery_count=1,
            source_digest=SHA_B,
            emitted_digest=SHA_C,
        )
        result.append(
            CheckpointWrite(
                checkpoint=checkpoint,
                source_cursor="",
                watermark=f"fenced:{stream.source_adapter}:{stream.role}",
                source_version_fence=stream.source_version_fence,
                source_fingerprint=SHA_A,
                previous_payload_sha256=ZERO_SHA,
                processed_rows=0,
            )
        )
    return tuple(result)


def _completion(
    prepared: PreparedLifecycleRevision,
    *,
    absence_pass: bool,
) -> LifecycleCompletionEvidence:
    proofs = tuple(
        StreamDrainProof(
            source_adapter=value.source_adapter,
            producer_stream_id=value.producer_stream_id,
            last_issued_sequence=1,
            fenced_sequence=1,
            terminal_sequence=1,
            terminal_payload_sha256=SHA_A,
        )
        for value in prepared.lease.build_plan.streams
    )
    checkpoints = tuple(
        PersistedCheckpointEvidence(
            source_adapter=value.source_adapter,
            producer_stream_id=value.producer_stream_id,
            state_sha256=SHA_B,
        )
        for value in prepared.lease.build_plan.streams
    )
    lease = prepared.lease
    return LifecycleCompletionEvidence(
        organization_id=lease.organization_id,
        workspace_id=lease.workspace_id,
        catalog_epoch=lease.catalog_epoch,
        catalog_revision=lease.catalog_revision,
        build_token=lease.build_token,
        projection_version=lease.projection_version,
        lifecycle_mode=prepared.lifecycle_mode,
        lineage_anchor_revision=prepared.lineage_anchor_revision,
        opened_streams=tuple(value.key for value in lease.build_plan.streams),
        building_assignment_sha256=SHA_A,
        stream_drain_proofs=proofs,
        hot_drain_proof_sha256=SHA_B,
        checkpoints=checkpoints,
        manifest_sha256=SHA_A,
        fence_sha256=SHA_B,
        qualification_sha256=SHA_C,
        activation_sha256=SHA_A,
        absence_tombstone_pass_completed=absence_pass,
    )


def test_initial_plan_persists_exact_ten_stream_scope() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A],
    )

    prepared = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )

    assert not prepared.resumed
    assert prepared.reservation_status is ReservationStatus.OPEN
    assert prepared.reconcile_mode is ReconcileMode.FULL_REPAIR
    assert prepared.cutoffs.span_window == _bounds().initial_window
    assert prepared.lifecycle_mode is CatalogLifecycleMode.INITIAL_BACKFILL
    assert prepared.lineage_anchor_revision == prepared.lease.catalog_revision
    assert len(prepared.lease.build_plan.streams) == 10
    assert prepared.lease.build_plan.source_scope.project_ids == (
        PROJECT_A,
        PROJECT_B,
    )
    assert {
        (value.source_adapter, value.role)
        for value in prepared.lease.build_plan.streams
    } == {(value.source_adapter, value.role) for value in prepared.streams}
    assert all(value.lower_watermark == "" for value in prepared.streams)
    assert len(freezer.calls) == 1


def test_fresh_subhour_incremental_span_window_stays_half_open() -> None:
    since = INITIAL_UNTIL + timedelta(minutes=2, microseconds=3)
    until = since + timedelta(minutes=2, microseconds=7)

    frozen = FrozenSpanSource((PROJECT_A,), since, until, audit_generation=9)

    assert frozen.units == ((PROJECT_A, since, until),)


def test_checked_in_freezer_uses_clock_not_static_until_for_schedule() -> None:
    clock = _Clock(INITIAL_UNTIL + timedelta(minutes=2, microseconds=5))

    class _Reader:
        calls: list[dict[str, Any]] = []

        def freeze(self, **kwargs: Any) -> FrozenSpanSource:
            self.calls.append(dict(kwargs))
            return FrozenSpanSource(
                tuple(kwargs["project_ids"]),
                kwargs["since"],
                kwargs["until"],
                audit_generation=123,
            )

    reader = _Reader()
    freezer = FreshSpanLifecycleCutoffFreezer(reader, now=clock)
    frozen = freezer(
        scope=_scope(),
        mode=LifecycleRunMode.INCREMENTAL,
        span_since=INITIAL_UNTIL,
        configured_until=None,
        prior_active=None,
    )

    assert frozen.snapshot_upper == clock.current
    assert frozen.span_window == SourceWindow(INITIAL_UNTIL, clock.current)
    assert frozen.span_audit_generation == 123
    assert reader.calls == [
        {
            "project_ids": (PROJECT_A, PROJECT_B),
            "since": INITIAL_UNTIL,
            "until": clock.current,
        }
    ]


def test_checked_in_freezer_bounds_aged_full_repair_to_366_days() -> None:
    clock = _Clock(INITIAL_UNTIL + timedelta(days=367, hours=3))

    class _Reader:
        calls: list[dict[str, Any]] = []

        def freeze(self, **kwargs: Any) -> FrozenSpanSource:
            self.calls.append(dict(kwargs))
            return FrozenSpanSource(
                tuple(kwargs["project_ids"]),
                kwargs["since"],
                kwargs["until"],
                audit_generation=124,
            )

    reader = _Reader()
    freezer = FreshSpanLifecycleCutoffFreezer(reader, now=clock)
    frozen = freezer(
        scope=_scope(),
        mode=LifecycleRunMode.FULL_REPAIR,
        span_since=INITIAL_SINCE,
        configured_until=None,
        prior_active=None,
    )
    expected_since = clock.current - timedelta(days=366)

    assert frozen.snapshot_upper == clock.current
    assert frozen.span_window == SourceWindow(expected_since, clock.current)
    assert frozen.span_audit_generation == 124
    assert reader.calls == [
        {
            "project_ids": (PROJECT_A, PROJECT_B),
            "since": expected_since,
            "until": clock.current,
        }
    ]


def test_restart_rejects_project_inventory_drift_before_coordinator_replay() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    tokens = [TOKEN_A, TOKEN_B]
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    )
    lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    changed_scope = WorkspaceCatalogScope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        projection_version=1,
        project_ids=(PROJECT_A,),
    )

    with pytest.raises(DurableLifecycleError, match="project inventory"):
        lifecycle.prepare(
            scope=changed_scope,
            mode=LifecycleRunMode.INITIAL_BACKFILL,
            configured_bounds=_bounds(),
        )

    assert len(freezer.calls) == 1


def test_expired_project_inventory_drift_requires_and_honors_explicit_repair() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B, TOKEN_C],
    )
    original_scope = replace(_scope(), project_ids=(PROJECT_A,))
    initial = lifecycle.prepare(
        scope=original_scope,
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)
    incomplete = lifecycle.prepare(
        scope=original_scope,
        mode=LifecycleRunMode.FULL_REPAIR,
        configured_bounds=_bounds(),
    )
    clock.current = incomplete.lease.expires_at + timedelta(seconds=1)

    with pytest.raises(DurableLifecycleError, match="explicit repair"):
        lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.FULL_REPAIR,
            configured_bounds=_bounds(),
        )

    repaired = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.FULL_REPAIR,
        configured_bounds=_bounds(),
        allow_expired_repair=True,
    )

    assert repaired.resumed is False
    assert repaired.mode is LifecycleRunMode.FULL_REPAIR
    assert repaired.scope.project_ids == (PROJECT_A, PROJECT_B)
    assert repaired.lease.catalog_revision == incomplete.lease.catalog_revision + 1
    assert repaired.lease.build_token == TOKEN_C
    assert all(value.resume is None for value in repaired.streams)


def test_crash_restart_reuses_plan_cutoff_mode_token_and_checkpoint() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    tokens = [TOKEN_A, TOKEN_B]
    first_lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    )
    first = first_lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.reservation = PersistedReservation(
        lease=first.lease,
        status=ReservationStatus.DRAINING,
    )
    state.resumes = (_running_checkpoint(first),)
    clock.current += timedelta(minutes=1)

    restarted = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    ).prepare(
        scope=_scope(),
        # A generic scheduled tick cannot reinterpret persisted initial/full mode.
        mode=LifecycleRunMode.INCREMENTAL,
        # Configuration drift cannot widen/recompute the immutable open plan.
        configured_bounds=ConfiguredSourceBounds(
            INITIAL_SINCE - timedelta(days=1),
            INITIAL_UNTIL + timedelta(hours=1),
        ),
    )

    assert restarted.resumed
    assert restarted.reservation_status is ReservationStatus.DRAINING
    assert restarted.mode is LifecycleRunMode.INITIAL_BACKFILL
    assert restarted.lease == first.lease
    assert restarted.cutoffs == first.cutoffs
    assert restarted.lease.build_token == TOKEN_A
    assert len(freezer.calls) == 1
    assert tokens == [TOKEN_B]
    assert (
        restarted.stream(
            SourceAdapter.EVAL_CONFIG,
            ManifestStreamRole.DEFINITIONS,
        ).resume
        == state.resumes[0]
    )


def test_incremental_resume_uses_checkpoint_instead_of_prior_lower_watermark() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    tokens = [TOKEN_A, TOKEN_B, TOKEN_C]
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)
    incremental = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INCREMENTAL,
        configured_bounds=_bounds(),
    )
    resume = _running_checkpoint(incremental)
    state.reservation = PersistedReservation(
        lease=incremental.lease,
        status=ReservationStatus.DRAINING,
    )
    state.resumes = (resume,)
    clock.current += timedelta(minutes=1)

    restarted = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
    )

    resumed = restarted.stream(
        SourceAdapter.EVAL_CONFIG,
        ManifestStreamRole.DEFINITIONS,
    )
    untouched = restarted.stream(
        SourceAdapter.EVAL_TEMPLATE,
        ManifestStreamRole.DEFINITIONS,
    )
    assert resumed.resume == resume
    assert resumed.lower_watermark == ""
    assert untouched.resume is None
    assert untouched.lower_watermark


def test_fenced_crash_recovers_terminal_revision_without_reopening_sources() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    tokens = [TOKEN_A, TOKEN_B]
    first = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    ).prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.reservation = PersistedReservation(
        lease=first.lease,
        status=ReservationStatus.FENCED,
    )
    state.resumes = (_running_checkpoint(first),)
    clock.current = first.lease.expires_at + timedelta(hours=1)

    with pytest.raises(DurableLifecycleError, match="exact ten"):
        _lifecycle(
            state=state,
            clock=clock,
            freezer=freezer,
            tokens=tokens,
        ).prepare(
            scope=_scope(),
            mode=LifecycleRunMode.AUTO,
            configured_bounds=_bounds(),
        )

    state.resumes = _complete_checkpoints(first)
    restarted = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    ).prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
    )

    assert restarted.resumed
    assert restarted.reservation_status is ReservationStatus.FENCED
    assert restarted.lease == first.lease
    assert restarted.mode is LifecycleRunMode.INITIAL_BACKFILL
    assert all(value.resume is not None for value in restarted.streams)
    assert len(freezer.calls) == 1
    assert tokens == [TOKEN_B]


def test_expired_incomplete_revision_requires_explicit_fresh_revision() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    tokens = [TOKEN_A, TOKEN_B]
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    )
    first = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    expired_lease = first.lease
    state.resumes = (_running_checkpoint(first),)
    clock.current = expired_lease.expires_at + timedelta(seconds=1)

    with pytest.raises(DurableLifecycleError, match="explicit repair"):
        lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.INITIAL_BACKFILL,
            configured_bounds=_bounds(),
        )

    repaired = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
        allow_expired_repair=True,
    )

    assert repaired.resumed is False
    assert repaired.lease.catalog_revision == expired_lease.catalog_revision + 1
    assert repaired.lease.build_token == TOKEN_B
    assert repaired.lease != expired_lease
    assert repaired.streams and all(value.resume is None for value in repaired.streams)
    assert expired_lease.build_token == TOKEN_A
    assert expired_lease.expires_at < repaired.lease.issued_at
    assert tokens == []

    class ReservationReader(ClickHouseLifecycleStateReader):
        def __init__(self, stale: PersistedReservation) -> None:
            self.stale = stale

        def _reservations(
            self, _scope: WorkspaceCatalogScope
        ) -> tuple[PersistedReservation, ...]:
            return (self.stale,)

        def _latest_fenced(self, _scope: WorkspaceCatalogScope) -> PersistedReservation:
            return PersistedReservation(
                repaired.lease,
                ReservationStatus.FENCED,
            )

    stale = PersistedReservation(expired_lease, ReservationStatus.OPEN)
    selected = ReservationReader(stale).load_nonterminal(_scope())
    assert selected is not None and selected.lease == repaired.lease

    overlapping = PersistedReservation(
        replace(
            expired_lease,
            expires_at=repaired.lease.issued_at + timedelta(seconds=1),
        ),
        ReservationStatus.OPEN,
    )
    with pytest.raises(DurableLifecycleError, match="overlapping"):
        ReservationReader(overlapping).load_nonterminal(_scope())


def test_expired_no_active_repair_rejects_implicit_auto_mode() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A, TOKEN_B],
    )
    first = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    clock.current = first.lease.expires_at + timedelta(seconds=1)

    with pytest.raises(DurableLifecycleError, match="explicit initial-backfill"):
        lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.AUTO,
            configured_bounds=_bounds(),
            allow_expired_repair=True,
        )


def test_expired_full_repair_can_fall_back_to_fresh_incremental() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B, TOKEN_C],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)
    failed_repair = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.FULL_REPAIR,
        configured_bounds=_bounds(),
    )
    clock.current = failed_repair.lease.expires_at + timedelta(seconds=1)

    recovered = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
        allow_expired_repair=True,
    )

    assert recovered.mode is LifecycleRunMode.INCREMENTAL
    assert recovered.resumed is False
    assert recovered.lease.catalog_revision == (
        failed_repair.lease.catalog_revision + 1
    )
    assert recovered.cutoffs.span_window.since == initial.cutoffs.span_window.until
    assert recovered.prior_active == state.active


def test_auto_schedule_selects_incremental_then_due_full_repair() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B, TOKEN_C],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    initial_system = next(
        value
        for value in initial.lease.build_plan.streams
        if value.source_adapter is SourceAdapter.SYSTEM_MANIFEST
    )
    assert initial_system.source_cutoff_label == "initial_backfill_no_prior_active"
    assert initial_system.source_version_fence == initial.scope.catalog_epoch
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)

    incremental = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
    )
    assert incremental.mode is LifecycleRunMode.INCREMENTAL
    incremental_system = next(
        value
        for value in incremental.lease.build_plan.streams
        if value.source_adapter is SourceAdapter.SYSTEM_MANIFEST
    )
    assert incremental_system.source_cutoff_label == (
        "incremental_prior_active_revision_plus_epoch"
    )
    assert incremental_system.source_version_fence == (
        incremental.scope.catalog_epoch + initial.lease.catalog_revision
    )
    state.activate(incremental, at=clock.current)

    clock.current = INITIAL_UNTIL + timedelta(hours=24)
    repair = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
    )

    assert repair.mode is LifecycleRunMode.FULL_REPAIR
    assert repair.lifecycle_mode is CatalogLifecycleMode.FULL_REPAIR
    assert repair.lineage_anchor_revision == repair.lease.catalog_revision
    assert all(value.lower_watermark == "" for value in repair.streams)
    repair_system = next(
        value
        for value in repair.lease.build_plan.streams
        if value.source_adapter is SourceAdapter.SYSTEM_MANIFEST
    )
    assert repair_system.source_version_fence == (
        repair.scope.catalog_epoch + incremental.lease.catalog_revision
    )


def test_span_definition_version_stays_monotonic_when_repair_window_widens() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A, TOKEN_B],
    )
    narrow_bounds = ConfiguredSourceBounds(
        INITIAL_UNTIL - timedelta(hours=1),
        INITIAL_UNTIL,
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=narrow_bounds,
    )
    initial_plan = initial.lease.build_plan
    initial_definitions = next(
        stream
        for stream in initial_plan.streams
        if stream.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
        and stream.role is ManifestStreamRole.DEFINITIONS
    )
    state.activate(initial, at=clock.current)

    clock.current += timedelta(minutes=2)
    repair = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.FULL_REPAIR,
        configured_bounds=_bounds(),
    )
    repair_plan = repair.lease.build_plan
    repair_definitions = next(
        stream
        for stream in repair_plan.streams
        if stream.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
        and stream.role is ManifestStreamRole.DEFINITIONS
    )

    assert (
        repair_plan.source_scope.span_since_us < initial_plan.source_scope.span_since_us
    )
    assert repair_definitions.source_cutoff_label == (
        "full_repair_span_definition_version_us"
    )
    assert (
        repair_definitions.source_version_fence
        == repair_plan.source_scope.span_until_us
    )
    assert (
        repair_definitions.source_version_fence
        > initial_definitions.source_version_fence
    )


def test_auto_schedule_promotes_project_scope_change_to_full_repair() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A, TOKEN_B],
    )
    initial_scope = replace(_scope(), project_ids=(PROJECT_A,))
    initial = lifecycle.prepare(
        scope=initial_scope,
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)

    repaired = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=_bounds(),
    )

    assert repaired.mode is LifecycleRunMode.FULL_REPAIR
    assert repaired.lifecycle_mode is CatalogLifecycleMode.FULL_REPAIR
    assert repaired.cutoffs.span_window.since == INITIAL_SINCE
    assert repaired.scope.project_ids == (PROJECT_A, PROJECT_B)
    assert all(value.lower_watermark == "" for value in repaired.streams)


def test_decoder_accepts_legacy_incremental_system_revision_marker() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A, TOKEN_B],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)
    incremental = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INCREMENTAL,
        configured_bounds=_bounds(),
    )
    legacy_streams = tuple(
        replace(
            stream,
            source_cutoff_label="incremental_prior_active_revision",
            source_version_fence=initial.lease.catalog_revision,
        )
        if stream.source_adapter is SourceAdapter.SYSTEM_MANIFEST
        else stream
        for stream in incremental.lease.build_plan.streams
    )
    legacy_plan = replace(incremental.lease.build_plan, streams=legacy_streams)

    decoded = _decode_plan_scope(legacy_plan)

    assert decoded.mode is LifecycleRunMode.INCREMENTAL
    assert decoded.prior_active_revision == initial.lease.catalog_revision
    assert decoded.cutoffs == incremental.cutoffs


def test_decoder_accepts_legacy_span_definition_lower_bound_marker() -> None:
    clock = _Clock(INITIAL_UNTIL)
    lifecycle = _lifecycle(
        state=_State(),
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    plan = initial.lease.build_plan
    legacy_streams = tuple(
        replace(
            stream,
            source_cutoff_label="initial_backfill_span_since_us",
            source_version_fence=plan.source_scope.span_since_us,
        )
        if stream.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
        and stream.role is ManifestStreamRole.DEFINITIONS
        else stream
        for stream in plan.streams
    )

    decoded = _decode_plan_scope(replace(plan, streams=legacy_streams))

    assert decoded.cutoffs == initial.cutoffs


def test_two_successive_incremental_revisions_advance_frozen_cutoff() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    tokens = [TOKEN_A, TOKEN_B, TOKEN_C]
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=tokens,
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)

    clock.current += timedelta(minutes=2)
    first = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INCREMENTAL,
        configured_bounds=_bounds(),
    )
    prior_watermarks = {
        value.role_key: value.watermark
        for value in state.active.streams  # type: ignore[union-attr]
    }
    assert first.cutoffs.snapshot_upper == clock.current
    assert first.cutoffs.span_window.since == INITIAL_UNTIL
    assert {
        value.role_key: value.lower_watermark for value in first.streams
    } == prior_watermarks
    state.activate(first, at=clock.current)

    clock.current += timedelta(minutes=2)
    second = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INCREMENTAL,
        configured_bounds=_bounds(),
    )

    assert second.cutoffs.snapshot_upper > first.cutoffs.snapshot_upper
    assert second.cutoffs.span_window.since == first.cutoffs.span_window.until
    assert second.lease.build_token != first.lease.build_token
    assert len(freezer.calls) == 3  # initial plus two independently frozen revisions
    assert second.prior_active is not None
    assert second.prior_active.lineage_anchor.active_revisions_since == 1
    assert second.lineage_anchor_revision == initial.lease.catalog_revision


def test_incremental_rejects_arbitrary_blank_active_stream_evidence() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A, TOKEN_B],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    assert state.active is not None
    state.active = replace(
        state.active,
        streams=tuple(
            replace(stream, watermark="")
            if stream.source_adapter is SourceAdapter.SIMULATION_EVAL_CONFIG
            and stream.role is ManifestStreamRole.DEFINITIONS
            else stream
            for stream in state.active.streams
        ),
    )

    clock.current += timedelta(minutes=2)
    with pytest.raises(DurableLifecycleError, match="no incremental watermark"):
        lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.INCREMENTAL,
            configured_bounds=_bounds(),
        )


def test_persisted_empty_relational_checkpoint_uses_frozen_cutoff_watermark() -> None:
    clock = _Clock(INITIAL_UNTIL)
    prepared = _lifecycle(
        state=_State(),
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A],
    ).prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    checkpoint = next(
        value
        for value in _complete_checkpoints(prepared)
        if value.checkpoint.source_adapter
        is SourceAdapter.SIMULATION_EVAL_CONFIG
    )

    normalized = _persisted_active_watermark(
        replace(checkpoint, watermark=""),
        snapshot_cutoff=prepared.cutoffs.snapshot_upper,
    )

    assert SourceKeysetCursor.decode(normalized) == SourceKeysetCursor(
        prepared.cutoffs.snapshot_upper,
        ZERO_UUID,
    )


def test_persisted_nonempty_relational_checkpoint_with_blank_watermark_fails() -> None:
    clock = _Clock(INITIAL_UNTIL)
    prepared = _lifecycle(
        state=_State(),
        clock=clock,
        freezer=_Freezer(clock),
        tokens=[TOKEN_A],
    ).prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    checkpoint = next(
        value
        for value in _complete_checkpoints(prepared)
        if value.checkpoint.source_adapter
        is SourceAdapter.SIMULATION_EVAL_CONFIG
    )
    corrupt = replace(
        checkpoint,
        checkpoint=replace(checkpoint.checkpoint, source_count=1),
        watermark="",
        processed_rows=1,
    )

    with pytest.raises(DurableLifecycleError, match="no incremental watermark"):
        _persisted_active_watermark(
            corrupt,
            snapshot_cutoff=prepared.cutoffs.snapshot_upper,
        )


def test_incremental_fails_before_bounded_lineage_loses_daily_anchor() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(hours=27)

    with pytest.raises(DurableLifecycleError, match="daily full-repair"):
        lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.INCREMENTAL,
            configured_bounds=_bounds(),
        )

    assert len(freezer.calls) == 1


def test_incremental_fails_at_explicit_full_repair_anchor_depth() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B, TOKEN_C],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    anchor = state.active
    assert anchor is not None
    clock.current += timedelta(minutes=2)
    incremental = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INCREMENTAL,
        configured_bounds=_bounds(),
    )
    state.reservation = None
    deep_plan = RevisionBuildPlan(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        catalog_revision=2_049,
        build_token=TOKEN_C,
        projection_version=1,
        source_scope=incremental.lease.build_plan.source_scope,
        streams=incremental.lease.build_plan.streams,
    )
    deep_streams = tuple(
        ActiveStreamEvidence(
            source_adapter=value.source_adapter,
            role=value.role,
            producer_stream_id=value.producer_stream_id,
            source_version_fence=value.source_version_fence,
            watermark=f"deep:{value.source_adapter}:{value.role}",
            checkpoint_state_sha256=SHA_A,
        )
        for value in deep_plan.streams
    )
    state.active = PriorActiveEvidence(
        catalog_revision=2_049,
        build_token=TOKEN_C,
        projection_version=1,
        lifecycle_mode=CatalogLifecycleMode.INCREMENTAL,
        activation_sequence=2_049,
        activation_sha256=SHA_B,
        source_manifest_sha256=SHA_A,
        build_plan=deep_plan,
        streams=deep_streams,
        qualified_at=clock.current,
        lineage_anchor=replace(
            anchor.lineage_anchor,
            active_revisions_since=2_048,
        ),
    )

    with pytest.raises(DurableLifecycleError, match="bounded full-repair"):
        lifecycle.prepare(
            scope=_scope(),
            mode=LifecycleRunMode.INCREMENTAL,
            configured_bounds=_bounds(),
        )


def test_full_repair_requires_persisted_absence_tombstone_pass() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(hours=1)
    repair = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.FULL_REPAIR,
        configured_bounds=_bounds(),
    )

    assert repair.reconcile_mode is ReconcileMode.FULL_REPAIR
    assert repair.lineage_anchor_revision == repair.lease.catalog_revision
    assert all(value.lower_watermark == "" for value in repair.streams)
    with pytest.raises(DurableLifecycleError, match="absence-tombstone"):
        _completion(repair, absence_pass=False).validate_for(repair)
    _completion(repair, absence_pass=True).validate_for(repair)


def test_scheduled_freezer_cannot_reuse_static_configured_upper() -> None:
    clock = _Clock(INITIAL_UNTIL)
    state = _State()
    freezer = _Freezer(clock)
    lifecycle = _lifecycle(
        state=state,
        clock=clock,
        freezer=freezer,
        tokens=[TOKEN_A, TOKEN_B],
    )
    initial = lifecycle.prepare(
        scope=_scope(),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=_bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += timedelta(minutes=2)

    class _StaticFreezer(_Freezer):
        def __call__(self, **kwargs: Any) -> FrozenLifecycleCutoffs:
            self.calls.append(dict(kwargs))
            stale_upper = INITIAL_UNTIL + timedelta(minutes=1)
            return FrozenLifecycleCutoffs(
                snapshot_upper=stale_upper,
                span_window=SourceWindow(kwargs["span_since"], stale_upper),
                span_audit_generation=99,
            )

    static = _StaticFreezer(clock)
    with pytest.raises(DurableLifecycleError, match="freshly frozen|strictly advance"):
        _lifecycle(
            state=state,
            clock=clock,
            freezer=static,
            tokens=[TOKEN_B],
        ).prepare(
            scope=_scope(),
            mode=LifecycleRunMode.INCREMENTAL,
            configured_bounds=_bounds(),
        )
