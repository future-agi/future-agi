from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.columns.uuidcolumn import UUIDColumn

from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    BuildPlanStream,
    ManifestStreamRole,
    StreamDrainProof,
)
from tracer.services.clickhouse.v2.property_catalog.codec import framed_sha256
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    MAX_REVISION_LEASE_SECONDS,
    ClickHouseRevisionCoordinator,
    ProducerRevisionAssignment,
    PropertyCatalogCoordinatorError,
    encode_producer_assignment,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_contract import (
    ProducerDrainProof,
    ProducerDrainProofError,
    derive_hot_delivery_evidence,
    parse_producer_drain_proof,
    select_producer_drain_proof,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
BUILD = "55555555-5555-4555-8555-555555555555"
STREAM = "44444444-4444-4444-8444-444444444444"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
EMPTY = hashlib.sha256(b"").hexdigest()
ZERO = "0" * 64
DATABASE = "property_catalog_dev_hot_drain"
SOURCE_SCOPE = BuildPlanSourceScope(
    project_ids=(PROJECT,),
    span_since_us=1_723_638_000_000_000,
    span_until_us=1_723_641_600_000_000,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _clickhouse_uuid(value: str) -> uuid.UUID:
    """Return the exact stdlib UUID representation produced by our CH driver."""

    column = UUIDColumn(context=SimpleNamespace(client_settings={}))
    (decoded,) = column.after_read_items((uuid.UUID(value).int,))
    assert type(decoded) is uuid.UUID
    return decoded


def _assignment(sequence: int) -> ProducerRevisionAssignment:
    return ProducerRevisionAssignment(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        projection_version=1,
        build_lease_sha256=_sha("plan"),
        build_token=BUILD,
        project_ids=(PROJECT,),
        span_since_us=1_723_638_000_000_000,
        span_until_us=1_723_641_600_000_000,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        drain_deadline=NOW + timedelta(minutes=4),
        fenced_sequence=sequence,
        status="draining",
    )


def _row(sequence: int, previous: str, payload: str, *, terminal: bool) -> dict:
    return {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "catalog_epoch": 1,
        "catalog_revision": 2,
        "build_token": BUILD,
        "projection_version": 1,
        "source_adapter": "span_attribute",
        "producer_stream_id": STREAM,
        "sequence": sequence,
        "terminal": int(terminal),
        "envelope_format": "futureagi.property-catalog-envelope",
        "envelope_version": 1,
        "envelope_id": _sha(f"envelope-{sequence}"),
        "payload_sha256": payload,
        "previous_payload_sha256": previous,
        "source_batch_digest": _sha(f"batch-{sequence}"),
        "outcome": "committed",
        "gap_reasons": [],
        "source_rows": 0 if terminal else 3,
        "definition_rows": 0,
        "value_rows": 0 if terminal else 2,
        "tombstone_rows": 0,
        "transport": "kafka",
        "kafka_partition": 0,
        "kafka_offset": sequence - 1,
        "delivered_at": NOW,
        "_version": sequence,
    }


def _proof(*, ready: bool) -> ProducerDrainProof:
    data_payload, terminal_payload = _sha("payload-1"), _sha("payload-2")
    intent, exact = _assignment(0), _assignment(2)
    source_digest = framed_sha256(
        "futureagi.property-catalog.hot-source-stream.v1",
        EMPTY,
        _sha("batch-1"),
    )
    emitted = framed_sha256(
        "futureagi.property-catalog.emitted-stream.v1", EMPTY, data_payload
    )
    if ready:
        emitted = framed_sha256(
            "futureagi.property-catalog.emitted-stream.v1", emitted, terminal_payload
        )
    return ProducerDrainProof(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD,
        projection_version=1,
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=STREAM,
        build_lease_sha256=_sha("plan"),
        drain_intent_fence_sha256=intent.fence_sha256,
        observed_fence_sha256=(exact if ready else intent).fence_sha256,
        drain_deadline="2026-08-14 12:04:00.000000",
        phase="ready" if ready else "prepared",
        last_data_sequence=1,
        terminal_sequence=2,
        last_issued_sequence=2 if ready else 1,
        last_acknowledged_sequence=2 if ready else 1,
        terminal_issued=ready,
        terminal_acknowledged=ready,
        source_count=3,
        definition_count=0,
        value_count=2,
        tombstone_count=0,
        delivery_count=2 if ready else 1,
        source_digest=source_digest,
        emitted_digest=emitted,
        terminal_payload_sha256=terminal_payload if ready else ZERO,
        gap_issued=False,
        gap_acknowledged=False,
        pending_envelopes=0,
        pending_admissions=0,
        poisoned=False,
        ready=ready,
    )


def _plan_streams() -> tuple[BuildPlanStream, ...]:
    result: list[BuildPlanStream] = []
    index = 0
    for adapter in SourceAdapter:
        roles = (
            (
                ManifestStreamRole.DEFINITIONS,
                ManifestStreamRole.VALUES,
                ManifestStreamRole.HOT_VALUES,
                ManifestStreamRole.SOURCE_AUDIT,
            )
            if adapter is SourceAdapter.SPAN_ATTRIBUTE
            else (ManifestStreamRole.DEFINITIONS,)
        )
        for role in roles:
            result.append(
                BuildPlanStream(
                    source_adapter=adapter,
                    role=role,
                    producer_stream_id=(
                        STREAM
                        if role is ManifestStreamRole.HOT_VALUES
                        else str(uuid.UUID(int=100 + index))
                    ),
                    source_cutoff_label=f"{adapter}_{role}",
                    source_version_fence=1000 + index,
                )
            )
            index += 1
    return tuple(result)


class _FenceSink:
    def __init__(self) -> None:
        self.assignments: list[ProducerRevisionAssignment] = []

    def publish(self, assignment: ProducerRevisionAssignment) -> None:
        self.assignments.append(assignment)


class _CoordinatorClient:
    catalog_database = DATABASE

    def __init__(self) -> None:
        self.stream_rows: list[Mapping[str, object]] = []
        self.delivery_rows: list[Mapping[str, object]] = []
        self.queries: list[str] = []

    def query(
        self, sql: str, params: Mapping[str, object], *, timeout_ms: int
    ) -> Sequence[Mapping[str, object]]:
        assert timeout_ms > 0
        self.queries.append(sql)
        if "property_catalog_deliveries" in sql:
            return tuple(self.delivery_rows)
        if "property_catalog_source_streams" not in sql:
            return ()
        rows = [
            row
            for row in self.stream_rows
            if row["organization_id"] == params["organization_id"]
            and row["workspace_id"] == params["workspace_id"]
            and row["catalog_epoch"] == params["catalog_epoch"]
        ]
        if "AS max_revision" in sql:
            return (
                {
                    "max_revision": max(
                        (int(row["catalog_revision"]) for row in rows), default=0
                    )
                },
            )
        if "reservation_version_rank" in sql:
            reservations = [
                row
                for row in rows
                if row["envelope_version"] == 0
                and row["producer_stream_id"] == row["build_token"]
            ]
            latest: list[Mapping[str, object]] = []
            for key in {
                (row["catalog_revision"], row["build_token"]) for row in reservations
            }:
                candidates = [
                    row
                    for row in reservations
                    if (row["catalog_revision"], row["build_token"]) == key
                ]
                maximum = max(int(row["_version"]) for row in candidates)
                latest.extend(
                    row for row in candidates if int(row["_version"]) == maximum
                )
            rows = latest
        for field in (
            "catalog_revision",
            "build_token",
            "source_adapter",
            "producer_stream_id",
        ):
            if field == "build_token" and "reservation_version_rank" in sql:
                continue
            if field in params:
                rows = [row for row in rows if str(row[field]) == str(params[field])]
        return tuple(rows)

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, object]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        assert table.endswith(".`property_catalog_source_streams`")
        assert timeout_ms > 0 and deduplication_token
        for row in rows:
            assert tuple(row) == tuple(columns)
            self.stream_rows.append(dict(row))


def _coordinator(
    client: _CoordinatorClient,
    sink: _FenceSink | None = None,
) -> ClickHouseRevisionCoordinator:
    return ClickHouseRevisionCoordinator(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
        producer_fence_sink=sink or _FenceSink(),
        hot_producer_stream_id=STREAM,
        deadline=SharedCatalogDeadline(wall_ms=30_000),
        now=lambda: NOW,
    )


def test_coordinator_bounds_extended_initial_revision_lease() -> None:
    client = _CoordinatorClient()
    coordinator = ClickHouseRevisionCoordinator(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
        producer_fence_sink=_FenceSink(),
        hot_producer_stream_id=STREAM,
        deadline=SharedCatalogDeadline(wall_ms=30_000),
        lease_seconds=MAX_REVISION_LEASE_SECONDS,
        now=lambda: NOW,
    )

    lease = coordinator.allocate(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=1,
        build_token=BUILD,
        source_scope=SOURCE_SCOPE,
        planned_streams=_plan_streams(),
        now=NOW,
    )

    assert lease.expires_at - lease.issued_at == timedelta(
        seconds=MAX_REVISION_LEASE_SECONDS
    )
    with pytest.raises(ValueError, match="1800"):
        ClickHouseRevisionCoordinator(
            client,
            database=DATABASE,
            serializer=InProcessCatalogMutationSerializer(),
            producer_fence_sink=_FenceSink(),
            hot_producer_stream_id=STREAM,
            deadline=SharedCatalogDeadline(wall_ms=30_000),
            lease_seconds=MAX_REVISION_LEASE_SECONDS + 1,
            now=lambda: NOW,
        )


def test_prepared_then_ready_proof_binds_raw_physical_ledger() -> None:
    data_payload, terminal_payload = _sha("payload-1"), _sha("payload-2")
    data = _row(1, ZERO, data_payload, terminal=False)
    terminal = _row(2, data_payload, terminal_payload, terminal=True)
    prepared = _proof(ready=False)
    evidence = prepared.validate_prepared(
        assignment=_assignment(0), delivery_rows=(data,)
    )
    assert evidence.delivery_count == 1
    assert evidence.value_count == 2

    ready = _proof(ready=True)
    checkpoint = ready.to_checkpoint(
        assignment=_assignment(2),
        source_version_fence=99,
        delivery_rows=(data, terminal),
    )
    assert checkpoint.terminal
    assert checkpoint.last_sequence == 2
    assert checkpoint.value_count == 2
    assert (
        ready.to_stream_proof(
            assignment=_assignment(2), checkpoint=checkpoint
        ).terminal_sequence
        == 2
    )


def test_physical_identity_conflict_is_rejected_at_any_version() -> None:
    data = _row(1, ZERO, _sha("payload-1"), terminal=False)
    conflict = {**data, "payload_sha256": _sha("forged"), "_version": 999}
    with pytest.raises(ProducerDrainProofError, match="conflicting identities"):
        derive_hot_delivery_evidence(
            proof=_proof(ready=False),
            delivery_rows=(data, conflict),
            terminal_required=False,
        )


def test_physical_identity_accepts_clickhouse_uuid_rows_as_canonical_text() -> None:
    data = _row(1, ZERO, _sha("payload-1"), terminal=False)
    driver_row = {
        **data,
        "organization_id": _clickhouse_uuid(ORG),
        "workspace_id": _clickhouse_uuid(WORKSPACE),
        "build_token": _clickhouse_uuid(BUILD),
        "producer_stream_id": _clickhouse_uuid(STREAM),
    }

    evidence = derive_hot_delivery_evidence(
        proof=_proof(ready=False),
        delivery_rows=(driver_row, data),
        terminal_required=False,
    )

    assert evidence.delivery_count == 1
    assert evidence.value_count == 2


def test_v2_parser_is_canonical_and_rejects_ready_flag_drift() -> None:
    proof = _proof(ready=True)
    document = {
        "format": "futureagi.property-catalog-drain-proof",
        "version": 2,
        "proofs": [asdict(proof)],
    }
    raw = json.dumps(document, separators=(",", ":")).encode() + b"\n"
    assert parse_producer_drain_proof(raw) == (proof,)

    drift = replace(proof, phase="bound", ready=False)
    bad = {**document, "proofs": [asdict(drift)]}
    bad["proofs"][0]["ready"] = True
    with pytest.raises(ProducerDrainProofError):
        parse_producer_drain_proof(
            json.dumps(bad, separators=(",", ":")).encode() + b"\n"
        )


def test_v2_parser_has_no_fixed_workspace_count_cap() -> None:
    proofs = tuple(
        replace(
            _proof(ready=True),
            workspace_id=str(uuid.UUID(int=index, version=4)),
        )
        for index in range(1, 301)
    )
    document = {
        "format": "futureagi.property-catalog-drain-proof",
        "version": 2,
        "proofs": [asdict(proof) for proof in proofs],
    }
    raw = json.dumps(document, separators=(",", ":")).encode() + b"\n"

    assert len(parse_producer_drain_proof(raw)) == 300


def test_v2_assignment_binds_exact_project_and_half_open_source_scope() -> None:
    assignment = _assignment(2)
    raw = encode_producer_assignment(assignment)
    assert raw.startswith(
        b'{"format":"futureagi.property-catalog-revision-fence","version":2,'
    )
    document = json.loads(raw)
    fence = document["fences"][0]
    assert fence["project_ids"] == [PROJECT]
    assert fence["span_since_us"] == SOURCE_SCOPE.span_since_us
    assert fence["span_until_us"] == SOURCE_SCOPE.span_until_us
    with pytest.raises(ValueError, match="digest mismatch"):
        replace(assignment, span_until_us=assignment.span_until_us + 1)


def test_proof_selector_binds_each_phase_to_the_exact_assignment() -> None:
    prepared = _proof(ready=False)
    assert (
        select_producer_drain_proof(
            (prepared,),
            assignment=_assignment(0),
            producer_stream_id=STREAM,
            phase="prepared",
        )
        is prepared
    )

    ready = _proof(ready=True)
    assert (
        select_producer_drain_proof(
            (ready,),
            assignment=_assignment(2),
            producer_stream_id=STREAM,
            phase="ready",
        )
        is ready
    )


@pytest.mark.parametrize(
    "stale",
    (
        replace(_proof(ready=False), catalog_revision=3),
        replace(
            _proof(ready=False),
            build_token="66666666-6666-4666-8666-666666666666",
        ),
        replace(_proof(ready=False), build_lease_sha256=_sha("stale-plan")),
        replace(
            _proof(ready=False),
            workspace_id="77777777-7777-4777-8777-777777777777",
        ),
    ),
)
def test_proof_selector_rejects_stale_build_or_scope(
    stale: ProducerDrainProof,
) -> None:
    with pytest.raises(ProducerDrainProofError, match="stale or conflicting"):
        select_producer_drain_proof(
            (stale,),
            assignment=_assignment(0),
            producer_stream_id=STREAM,
            phase="prepared",
        )


def test_proof_selector_rejects_conflicting_file_and_fence_digest() -> None:
    prepared = _proof(ready=False)
    stale = replace(prepared, drain_intent_fence_sha256=_sha("stale-intent"))
    with pytest.raises(ProducerDrainProofError, match="fence digest is stale"):
        select_producer_drain_proof(
            (stale,),
            assignment=_assignment(0),
            producer_stream_id=STREAM,
            phase="prepared",
        )
    with pytest.raises(ProducerDrainProofError, match="stale or conflicting"):
        select_producer_drain_proof(
            (prepared, replace(prepared, catalog_revision=3)),
            assignment=_assignment(0),
            producer_stream_id=STREAM,
            phase="prepared",
        )
    with pytest.raises(ProducerDrainProofError, match="fence digest is stale"):
        select_producer_drain_proof(
            (_proof(ready=True),),
            assignment=_assignment(2),
            producer_stream_id=STREAM,
            phase="prepared",
        )


def test_coordinator_suppresses_early_building_and_binds_prepared_boundary() -> None:
    client, sink = _CoordinatorClient(), _FenceSink()
    coordinator = ClickHouseRevisionCoordinator(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
        producer_fence_sink=sink,
        hot_producer_stream_id=STREAM,
        deadline=SharedCatalogDeadline(wall_ms=30_000),
        now=lambda: NOW,
    )
    streams = _plan_streams()
    lease = coordinator.allocate(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=1,
        build_token=BUILD,
        source_scope=SOURCE_SCOPE,
        planned_streams=streams,
        now=NOW,
    )
    assert sink.assignments == []
    for stream in streams:
        coordinator.open_stream(
            lease=lease,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
        )
    assert sink.assignments == []
    assert coordinator.publish_building_assignment(lease=lease).status == "building"
    building = sink.assignments[-1]
    assert building.project_ids == (PROJECT,)
    assert building.span_since_us == SOURCE_SCOPE.span_since_us
    assert building.span_until_us == SOURCE_SCOPE.span_until_us
    assert "reservation_version_rank=1" in client.queries[0]
    assert "LIMIT 4096" not in client.queries[0]
    assert any("AS max_revision" in query for query in client.queries)

    non_hot = tuple(
        StreamDrainProof(
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
            last_issued_sequence=1,
            fenced_sequence=1,
            terminal_sequence=1,
            terminal_payload_sha256=_sha(f"terminal-{stream.producer_stream_id}"),
        )
        for stream in streams
        if stream.role is not ManifestStreamRole.HOT_VALUES
    )
    deadline = lease.expires_at
    intent = coordinator.begin_drain_intent(
        lease=lease,
        completed_stream_proofs=non_hot,
        drain_deadline=deadline,
        now=NOW + timedelta(seconds=1),
    )
    assert intent.fenced_sequence == 0
    prepared = ProducerDrainProof(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=lease.catalog_revision,
        build_token=BUILD,
        projection_version=1,
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=STREAM,
        build_lease_sha256=lease.build_lease_sha256,
        drain_intent_fence_sha256=intent.fence_sha256,
        observed_fence_sha256=intent.fence_sha256,
        drain_deadline="2026-08-14 12:10:00.000000",
        phase="prepared",
        last_data_sequence=0,
        terminal_sequence=1,
        last_issued_sequence=0,
        last_acknowledged_sequence=0,
        terminal_issued=False,
        terminal_acknowledged=False,
        source_count=0,
        definition_count=0,
        value_count=0,
        tombstone_count=0,
        delivery_count=0,
        source_digest=EMPTY,
        emitted_digest=EMPTY,
        terminal_payload_sha256=ZERO,
        gap_issued=False,
        gap_acknowledged=False,
        pending_envelopes=0,
        pending_admissions=0,
        poisoned=False,
        ready=False,
    )
    exact = coordinator.bind_hot_drain_boundary(
        lease=lease,
        prepared_proof=prepared,
        drain_deadline=deadline,
        now=NOW + timedelta(seconds=2),
    )
    assert exact.fenced_sequence == 1
    resumed = coordinator.begin_drain_intent(
        lease=lease,
        completed_stream_proofs=non_hot,
        drain_deadline=deadline,
        now=NOW + timedelta(seconds=3),
    )
    assert resumed.fenced_sequence == 1
    assert sink.assignments[-1] == resumed


def test_coordinator_rejects_competing_live_workspace_reservation() -> None:
    client, sink = _CoordinatorClient(), _FenceSink()
    coordinator = ClickHouseRevisionCoordinator(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
        producer_fence_sink=sink,
        hot_producer_stream_id=STREAM,
        deadline=SharedCatalogDeadline(wall_ms=30_000),
        now=lambda: NOW,
    )
    coordinator.allocate(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=1,
        build_token=BUILD,
        source_scope=SOURCE_SCOPE,
        planned_streams=_plan_streams(),
        now=NOW,
    )
    with pytest.raises(PropertyCatalogCoordinatorError, match="another live"):
        coordinator.allocate(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            projection_version=1,
            build_token="66666666-6666-4666-8666-666666666666",
            source_scope=SOURCE_SCOPE,
            planned_streams=_plan_streams(),
            now=NOW + timedelta(seconds=1),
        )
    assert sink.assignments == []


def test_coordinator_reservation_candidates_reject_cap_plus_one_before_reuse() -> None:
    client = _CoordinatorClient()
    coordinator = _coordinator(client)
    coordinator.allocate(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=1,
        build_token=BUILD,
        source_scope=SOURCE_SCOPE,
        planned_streams=_plan_streams(),
        now=NOW,
    )
    reservation = dict(client.stream_rows[0])
    client.stream_rows.extend(dict(reservation) for _ in range(4))

    with pytest.raises(
        PropertyCatalogCoordinatorError,
        match="reservation candidates exceeded its conflict-proof row cap",
    ):
        coordinator.allocate(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            projection_version=1,
            build_token="66666666-6666-4666-8666-666666666666",
            source_scope=SOURCE_SCOPE,
            planned_streams=_plan_streams(),
            now=NOW + timedelta(seconds=1),
        )


def test_coordinator_rejects_immutable_reservation_history_drift() -> None:
    client = _CoordinatorClient()
    coordinator = _coordinator(client)
    lease = coordinator.allocate(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=1,
        build_token=BUILD,
        source_scope=SOURCE_SCOPE,
        planned_streams=_plan_streams(),
        now=NOW,
    )
    forged = dict(client.stream_rows[0])
    forged["_version"] = 0
    forged["build_plan_json"] = "{}"
    client.stream_rows.append(forged)

    first = _plan_streams()[0]
    with pytest.raises(
        PropertyCatalogCoordinatorError,
        match="history changed immutable scope or build plan",
    ):
        coordinator.open_stream(
            lease=lease,
            source_adapter=first.source_adapter,
            producer_stream_id=first.producer_stream_id,
        )


def test_coordinator_inventory_rejects_crowding_before_assignment() -> None:
    client, sink = _CoordinatorClient(), _FenceSink()
    coordinator = _coordinator(client, sink)
    streams = _plan_streams()
    lease = coordinator.allocate(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        projection_version=1,
        build_token=BUILD,
        source_scope=SOURCE_SCOPE,
        planned_streams=streams,
        now=NOW,
    )
    for stream in streams:
        coordinator.open_stream(
            lease=lease,
            source_adapter=stream.source_adapter,
            producer_stream_id=stream.producer_stream_id,
        )
    crowded = dict(client.stream_rows[-1])
    row_cap = (len(streams) + 1) * 32
    client.stream_rows.extend(
        dict(crowded) for _ in range(row_cap + 1 - len(client.stream_rows))
    )

    with pytest.raises(
        PropertyCatalogCoordinatorError,
        match="source stream inventory exceeded its conflict-proof row cap",
    ):
        coordinator.publish_building_assignment(lease=lease)
    assert sink.assignments == []
