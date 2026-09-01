from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tracer.services.clickhouse.v2.property_catalog.codec import ZERO_UUID
from tracer.services.clickhouse.v2.property_catalog.models import (
    PropertyCatalogEnvelope,
    PropertyCategory,
    PropertyDefinition,
    PropertyKind,
    PropertyRole,
    SourceAdapter,
    VisibilityBinding,
    VisibilityScope,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresReadBudget,
    PostgresSnapshotContext,
    project_definition,
)
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    ZERO_SHA256,
    CheckpointWrite,
    EnvelopeBudget,
    PropertyCatalogReconciler,
    ReconcileMode,
    ReconcileRequest,
    _project_records,
    _starting_progress,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    DatasetColumnSourceAdapter,
    PropertySourceDeadlineExceeded,
    SourceDefinitionRecord,
    SourceKeysetCursor,
    SourceReadBudget,
    SourceSnapshot,
    _dataset_column_definition,
    _make_source_record,
)
from tracer.services.clickhouse.v2.property_catalog.wire import (
    MAX_CHUNK_BYTES,
    MAX_RECORD_BYTES,
    PropertyCatalogWireError,
    encode_envelope,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
PROJECT_A = "33333333-3333-4333-8333-333333333333"
PROJECT_B = "44444444-4444-4444-8444-444444444444"
STREAM = "55555555-5555-4555-8555-555555555555"
BUILD = "66666666-6666-4666-8666-666666666666"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _definition() -> PropertyDefinition:
    return PropertyDefinition(
        property_kind=PropertyKind.EVAL_CONFIG,
        source_key="77777777-7777-4777-8777-777777777777",
        category=PropertyCategory.EVAL_METRIC,
        category_rank=1,
        source_rank=0,
        definition_source="eval_config",
        primary_source="traces",
        source_tokens=("eval",),
        value_adapter="native_eval",
        name="Quality",
        display_name="Quality",
        value_type="number",
        output_type="SCORE",
        role=PropertyRole.METRIC,
    )


def test_new_stream_uses_wire_v1_zero_hash_genesis() -> None:
    request = ReconcileRequest(
        context=PostgresSnapshotContext(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            project_ids=(PROJECT,),
            catalog_epoch=1,
            catalog_revision=2,
            projection_version=1,
            snapshot_cutoff=NOW,
        ),
        build_token=BUILD,
        producer_stream_id=STREAM,
        emitted_at=NOW,
        source_version=2,
    )

    assert (
        _starting_progress(request, SourceAdapter.EVAL_CONFIG).previous_payload_sha256
        == ZERO_SHA256
        == "0" * 64
    )


def test_incremental_visibility_change_tombstones_stale_binding_immediately() -> None:
    definition = _definition()
    old = project_definition(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=1,
        build_token=BUILD,
        projection_version=1,
        visibility=VisibilityBinding(VisibilityScope.PROJECT, PROJECT_A),
        definition=definition,
        source_adapter=SourceAdapter.EVAL_CONFIG,
        source_entity_id="77777777-7777-4777-8777-777777777777",
        source_version=1,
        source_fingerprint=_sha("v1"),
        producer_stream_id=STREAM,
        producer_sequence=1,
        emitted_at=NOW,
    )
    record = _make_source_record(
        source_adapter=SourceAdapter.EVAL_CONFIG,
        source_entity_id=old.source_entity_id,
        source_updated_at=NOW,
        definition=definition,
        visibilities=(VisibilityBinding(VisibilityScope.PROJECT, PROJECT_B),),
    )
    snapshot = SourceSnapshot(
        source_adapter=SourceAdapter.EVAL_CONFIG,
        records=(record,),
        next_cursor=None,
        terminal=True,
        source_count=1,
        source_bytes=record.encoded_bytes,
        source_digest=_sha("snapshot"),
        page_count=1,
    )
    request = ReconcileRequest(
        context=PostgresSnapshotContext(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            project_ids=(PROJECT,),
            catalog_epoch=1,
            catalog_revision=2,
            projection_version=1,
            snapshot_cutoff=NOW,
        ),
        build_token=BUILD,
        producer_stream_id=STREAM,
        emitted_at=NOW,
        source_version=2,
    )

    projected, conflicts = _project_records(
        snapshot=snapshot,
        current=(old,),
        request=request,
    )

    assert conflicts == 0
    assert {(row.visibility_id, row.is_deleted) for row in projected} == {
        (PROJECT_A, True),
        (PROJECT_B, False),
    }


class _EmptyCurrentBindings:
    def read_current(self, **kwargs: object) -> tuple[()]:
        return ()


class _CheckpointSink:
    def __init__(self) -> None:
        self.writes: list[CheckpointWrite] = []

    def append(self, checkpoint: CheckpointWrite) -> None:
        self.writes.append(checkpoint)


class _ExactWirePublisher:
    def __init__(self) -> None:
        self.envelopes: list[PropertyCatalogEnvelope] = []
        self.raw_sizes: list[int] = []
        self.chunk_sizes: list[tuple[int, ...]] = []

    def publish(self, envelope: PropertyCatalogEnvelope) -> str:
        wire = encode_envelope(envelope)
        self.envelopes.append(envelope)
        self.raw_sizes.append(len(wire.raw))
        self.chunk_sizes.append(
            tuple(
                len(base64.b64decode(chunk["json_each_row"]))
                for chunk in wire.document["payload"]["chunks"]
            )
        )
        return wire.payload_sha256


class _StaticWireFailurePublisher:
    def publish(self, envelope: PropertyCatalogEnvelope) -> str:
        raise PropertyCatalogWireError("encoded envelope exceeds the v1 record limit")


class _CommitThenLoseFirstAcknowledgementPublisher:
    def __init__(self) -> None:
        self.committed: dict[int, tuple[str, str]] = {}
        self.attempts: list[tuple[int, str, str]] = []
        self.fail_first_acknowledgement = True

    def publish(self, envelope: PropertyCatalogEnvelope) -> str:
        wire = encode_envelope(envelope)
        identity = (wire.envelope_id, wire.payload_sha256)
        existing = self.committed.setdefault(envelope.sequence, identity)
        if existing != identity:
            raise AssertionError("delivery sequence replay changed immutable identity")
        self.attempts.append((envelope.sequence, *identity))
        if self.fail_first_acknowledgement:
            self.fail_first_acknowledgement = False
            raise TimeoutError("acknowledgement lost after durable commit")
        return wire.payload_sha256


def _dataset_column_records(count: int) -> tuple[SourceDefinitionRecord, ...]:
    dataset_id = "88888888-8888-4888-8888-888888888888"
    records = []
    for index in range(1, count + 1):
        column_id = str(UUID(int=index, version=4))
        # Quotes, a reverse solidus, and multi-byte text exercise the exact
        # UTF-8 plus JSON escaping cost that the old definition_json estimate
        # omitted.  This is the same shape as the real dataset-column adapter.
        definition = _dataset_column_definition(
            {
                "id": column_id,
                "name": (
                    f"customer “feedback” \\ export column {index:04d} — "
                    "descriptive dataset field"
                ),
                "data_type": "text",
            }
        )
        records.append(
            _make_source_record(
                source_adapter=SourceAdapter.DATASET_COLUMN,
                source_entity_id=column_id,
                source_updated_at=NOW,
                definition=definition,
                visibilities=(VisibilityBinding(VisibilityScope.DATASET, dataset_id),),
            )
        )
    return tuple(records)


def _dataset_request() -> ReconcileRequest:
    return ReconcileRequest(
        context=PostgresSnapshotContext(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            project_ids=(PROJECT,),
            catalog_epoch=1,
            catalog_revision=2,
            projection_version=1,
            snapshot_cutoff=NOW,
        ),
        build_token=BUILD,
        producer_stream_id=STREAM,
        emitted_at=NOW,
        source_version=2,
    )


def test_empty_relational_full_repair_persists_frozen_cutoff_watermark() -> None:
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **_kwargs: ())
    publisher = _ExactWirePublisher()
    checkpoints = _CheckpointSink()
    reconciler = PropertyCatalogReconciler(
        publisher=publisher,  # type: ignore[arg-type]
        checkpoint_writer=checkpoints,  # type: ignore[arg-type]
        current_bindings=_EmptyCurrentBindings(),
    )

    result = reconciler.reconcile(
        adapter,
        replace(_dataset_request(), mode=ReconcileMode.FULL_REPAIR),
    )

    assert result.complete is True
    assert result.checkpoint_write.checkpoint.source_count == 0
    assert result.envelopes[-1].terminal is True
    assert SourceKeysetCursor.decode(result.checkpoint_write.watermark) == (
        SourceKeysetCursor(NOW, ZERO_UUID)
    )


def test_empty_relational_incremental_advances_to_frozen_cutoff() -> None:
    prior = SourceKeysetCursor(NOW - timedelta(minutes=2), ZERO_UUID).encode()
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **_kwargs: ())
    reconciler = PropertyCatalogReconciler(
        publisher=_ExactWirePublisher(),  # type: ignore[arg-type]
        checkpoint_writer=_CheckpointSink(),  # type: ignore[arg-type]
        current_bindings=_EmptyCurrentBindings(),
    )

    result = reconciler.reconcile(
        adapter,
        replace(
            _dataset_request(),
            lower_watermark=prior,
            incremental_overlap_seconds=0,
        ),
    )

    assert result.complete is True
    assert SourceKeysetCursor.decode(result.checkpoint_write.watermark) == (
        SourceKeysetCursor(NOW, ZERO_UUID)
    )


def test_many_dataset_columns_split_before_first_wire_publish() -> None:
    records = _dataset_column_records(360)
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **kwargs: records)
    publisher = _ExactWirePublisher()
    checkpoints = _CheckpointSink()
    reconciler = PropertyCatalogReconciler(
        publisher=publisher,  # type: ignore[arg-type]
        checkpoint_writer=checkpoints,  # type: ignore[arg-type]
        current_bindings=_EmptyCurrentBindings(),
    )

    result = reconciler.reconcile(adapter, _dataset_request())

    assert result.complete is True
    assert result.error is None
    assert len(checkpoints.writes) == 1
    assert sum(len(envelope.definitions) for envelope in result.envelopes) == 360
    assert len(result.envelopes) >= 3  # two or more data records plus terminal
    assert [envelope.sequence for envelope in result.envelopes] == list(
        range(1, len(result.envelopes) + 1)
    )
    assert result.envelopes[0].counts.source_count == 360
    assert all(envelope.counts.source_count == 0 for envelope in result.envelopes[1:])
    assert result.envelopes[-1].terminal is True
    assert all(size <= MAX_RECORD_BYTES for size in publisher.raw_sizes)
    assert all(
        len(chunks) == 1 and chunks[0] <= MAX_CHUNK_BYTES
        for chunks in publisher.chunk_sizes[:-1]
    )
    assert publisher.chunk_sizes[-1] == ()


def test_large_postgres_publish_keeps_revision_snapshot_active() -> None:
    records = _dataset_column_records(360)
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **kwargs: records)
    publisher = _ExactWirePublisher()
    checkpoints = _CheckpointSink()
    guard_observations: list[int] = []
    reconciler = PropertyCatalogReconciler(
        publisher=publisher,  # type: ignore[arg-type]
        checkpoint_writer=checkpoints,  # type: ignore[arg-type]
        current_bindings=_EmptyCurrentBindings(),
    )
    request = replace(
        _dataset_request(),
        postgres_snapshot_guard=lambda: guard_observations.append(
            len(publisher.envelopes)
        ),
    )

    result = reconciler.reconcile(adapter, request)

    assert result.complete is True
    assert len(result.envelopes) >= 3
    assert len(guard_observations) >= len(result.envelopes) + 3
    assert set(range(len(result.envelopes))).issubset(guard_observations)


def test_scheduled_relational_wall_reaches_terminal_after_standard_wall_expires() -> None:
    records = _dataset_column_records(360)

    def reconcile(*, scheduled: bool):
        elapsed = [0.0]
        page_reads = [0]
        wall_seconds = (
            RUNTIME_LIMITS.scheduled_reconcile_source_adapter_wall_seconds
            if scheduled
            else RUNTIME_LIMITS.source_adapter_wall_seconds
        )

        def monotonic() -> float:
            return elapsed[0]

        def page_loader(*, cursor, limit, **_kwargs):  # type: ignore[no-untyped-def]
            page_reads[0] += 1
            elapsed[0] += 0.5
            remaining = tuple(
                record for record in records if cursor is None or record.cursor > cursor
            )
            return remaining[:limit]

        class AdvancingPublisher(_ExactWirePublisher):
            def publish(self, envelope: PropertyCatalogEnvelope) -> str:
                elapsed[0] += 1.0
                return super().publish(envelope)

        def snapshot_guard() -> None:
            if monotonic() >= wall_seconds:
                raise PropertySourceDeadlineExceeded(
                    "property source deadline exceeded"
                )

        adapter = DatasetColumnSourceAdapter(page_loader=page_loader)
        publisher = AdvancingPublisher()
        checkpoints = _CheckpointSink()
        reconciler = PropertyCatalogReconciler(
            publisher=publisher,  # type: ignore[arg-type]
            checkpoint_writer=checkpoints,  # type: ignore[arg-type]
            current_bindings=_EmptyCurrentBindings(),
        )
        source_budget = SourceReadBudget(
            postgres=PostgresReadBudget(
                wall_timeout_seconds=wall_seconds,
                max_rows_per_page=100,
                max_total_rows=1_000,
                scheduled_reconcile=scheduled,
            ),
            adapter_wall_timeout_seconds=wall_seconds,
        )
        request = replace(
            _dataset_request(),
            source_budget=source_budget,
            envelope_budget=EnvelopeBudget(max_definition_rows=40),
            postgres_snapshot_guard=snapshot_guard,
        )
        result = reconciler.reconcile(adapter, request)
        return result, publisher, elapsed[0], page_reads[0]

    standard, standard_publisher, standard_elapsed, standard_page_reads = reconcile(
        scheduled=False
    )
    scheduled, scheduled_publisher, scheduled_elapsed, scheduled_page_reads = (
        reconcile(scheduled=True)
    )

    assert standard.complete is False
    assert standard.error == (
        "envelope publish failed: PropertySourceDeadlineExceeded"
    )
    assert all(not envelope.terminal for envelope in standard_publisher.envelopes)
    assert standard_elapsed > RUNTIME_LIMITS.source_adapter_wall_seconds
    assert standard_page_reads == 4

    assert scheduled.complete is True
    assert scheduled.error is None
    assert scheduled_publisher.envelopes[-1].terminal is True
    assert RUNTIME_LIMITS.source_adapter_wall_seconds < scheduled_elapsed < (
        RUNTIME_LIMITS.scheduled_reconcile_source_adapter_wall_seconds
    )
    assert scheduled_page_reads == 4


def test_wire_failure_retains_static_contract_detail_without_advancing_source() -> None:
    records = _dataset_column_records(1)
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **kwargs: records)
    checkpoints = _CheckpointSink()
    reconciler = PropertyCatalogReconciler(
        publisher=_StaticWireFailurePublisher(),
        checkpoint_writer=checkpoints,  # type: ignore[arg-type]
        current_bindings=_EmptyCurrentBindings(),
    )

    result = reconciler.reconcile(adapter, _dataset_request())

    assert result.complete is False
    assert result.error == (
        "envelope publish failed: PropertyCatalogWireError: "
        "encoded envelope exceeds the v1 record limit"
    )
    assert result.checkpoint_write.processed_rows == 0
    assert result.checkpoint_write.source_cursor == ""


def test_first_publish_failure_replays_identical_genesis_segment_on_restart() -> None:
    records = _dataset_column_records(1)
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **kwargs: records)
    publisher = _CommitThenLoseFirstAcknowledgementPublisher()
    checkpoints = _CheckpointSink()
    reconciler = PropertyCatalogReconciler(
        publisher=publisher,
        checkpoint_writer=checkpoints,
        current_bindings=_EmptyCurrentBindings(),
    )
    request = _dataset_request()

    failed = reconciler.reconcile(adapter, request)

    assert failed.error == "envelope publish failed: TimeoutError"
    assert failed.checkpoint_write.checkpoint.status is CheckpointStatus.FAILED
    assert failed.checkpoint_write.source_cursor == ""
    assert failed.checkpoint_write.processed_rows == 0
    assert [attempt[0] for attempt in publisher.attempts] == [1]

    recovered = reconciler.reconcile(
        adapter,
        replace(request, resume=failed.checkpoint_write),
    )

    assert recovered.complete is True
    assert recovered.error is None
    assert [attempt[0] for attempt in publisher.attempts] == [1, 1, 2]
    assert publisher.attempts[0] == publisher.attempts[1]
    assert tuple(publisher.committed) == (1, 2)
    assert recovered.checkpoint_write.processed_rows == 1
    assert recovered.checkpoint_write.checkpoint.source_count == 1
    assert recovered.checkpoint_write.checkpoint.definition_count == 1
    assert recovered.checkpoint_write.checkpoint.delivery_count == 2
    assert recovered.checkpoint_write.checkpoint.first_sequence == 1
    assert recovered.checkpoint_write.checkpoint.last_sequence == 2
    assert len(checkpoints.writes) == 2
