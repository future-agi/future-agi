"""Bounded, resumable control-plane reconciliation for property definitions.

Every source read is immutable and read-only.  Non-terminal keyset segments are
published and checkpointed, then resumed from the exact persisted cursor.  A
full-repair absence tombstone is computed only after the terminal segment and
only after all earlier segment bindings are durably readable in the catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .codec import ZERO_UUID, canonical_uuid, framed_sha256, require_sha256
from .models import (
    EnvelopeCounts,
    EnvelopeOutcome,
    PropertyBindingRow,
    PropertyCatalogEnvelope,
    SourceAdapter,
    make_state_sha256,
)
from .projection import PostgresSnapshotContext, project_definition
from .qualification import CatalogCheckpoint, CheckpointStatus
from .runtime_limits import (
    MAX_RECONCILE_INCREMENTAL_OVERLAP_SECONDS,
    RUNTIME_LIMITS,
)
from .source_adapters import (
    DefinitionSourceAdapter,
    SourceKeysetCursor,
    SourceReadBudget,
    SourceSnapshot,
)
from .wire import (
    MAX_CHUNK_BYTES,
    PropertyCatalogWireError,
    definition_json_each_row_size,
)

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ZERO_SHA256 = "0" * 64
DEFAULT_INCREMENTAL_OVERLAP_SECONDS = (
    RUNTIME_LIMITS.reconcile_incremental_overlap_seconds
)
DEFAULT_MAX_ENVELOPE_ROWS = RUNTIME_LIMITS.reconcile_default_envelope_rows
MAX_ENVELOPE_ROWS = RUNTIME_LIMITS.reconcile_max_envelope_rows
DEFAULT_MAX_ENVELOPE_BYTES = RUNTIME_LIMITS.reconcile_default_max_envelope_bytes
MAX_ENVELOPE_BYTES = RUNTIME_LIMITS.reconcile_max_envelope_bytes
_MAX_UINT64 = (1 << 64) - 1
_RELATIONAL_ADAPTERS = frozenset(
    {
        SourceAdapter.EVAL_TEMPLATE,
        SourceAdapter.EVAL_CONFIG,
        SourceAdapter.SIMULATION_EVAL_CONFIG,
        SourceAdapter.ANNOTATION_LABEL,
        SourceAdapter.DATASET_COLUMN,
    }
)


class ReconcileMode(StrEnum):
    INCREMENTAL = "incremental"
    FULL_REPAIR = "full_repair"


class PropertyCatalogReconcileError(RuntimeError):
    """A revision cannot advance without violating its immutable contract."""


@dataclass(frozen=True, slots=True)
class EnvelopeBudget:
    max_definition_rows: int = DEFAULT_MAX_ENVELOPE_ROWS
    max_payload_bytes: int = DEFAULT_MAX_ENVELOPE_BYTES

    def __post_init__(self) -> None:
        if not 1 <= self.max_definition_rows <= MAX_ENVELOPE_ROWS:
            raise ValueError(
                f"max_definition_rows must be between 1 and {MAX_ENVELOPE_ROWS}"
            )
        if not 1 <= self.max_payload_bytes <= MAX_ENVELOPE_BYTES:
            raise ValueError(
                f"max_payload_bytes must be at most {MAX_ENVELOPE_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True)
class CheckpointWrite:
    """Qualification checkpoint plus persisted resume/watermark evidence."""

    checkpoint: CatalogCheckpoint
    source_cursor: str
    watermark: str
    source_version_fence: int
    source_fingerprint: str
    previous_payload_sha256: str
    processed_rows: int
    gap_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.source_version_fence) is not int or not (
            1 <= self.source_version_fence < (1 << 64)
        ):
            raise ValueError("source_version_fence must be a positive UInt64")
        if type(self.processed_rows) is not int or not 0 <= self.processed_rows < (
            1 << 64
        ):
            raise ValueError("processed_rows must be a UInt64")
        require_sha256(self.source_fingerprint, field="source_fingerprint")
        require_sha256(
            self.previous_payload_sha256,
            field="previous_payload_sha256",
        )
        if len(self.gap_reasons) != self.checkpoint.gap_count:
            raise ValueError("gap_reasons do not match checkpoint gap_count")
        if self.checkpoint.terminal and self.source_cursor:
            raise ValueError("terminal checkpoints cannot have a resume cursor")


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    context: PostgresSnapshotContext
    build_token: str
    producer_stream_id: str
    emitted_at: datetime
    mode: ReconcileMode = ReconcileMode.INCREMENTAL
    source_version: int | None = None
    lower_watermark: str = ""
    resume: CheckpointWrite | None = None
    incremental_overlap_seconds: int = DEFAULT_INCREMENTAL_OVERLAP_SECONDS
    source_budget: SourceReadBudget = SourceReadBudget()
    envelope_budget: EnvelopeBudget = EnvelopeBudget()
    postgres_snapshot_guard: Callable[[], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.context, PostgresSnapshotContext):
            raise TypeError("context must be a PostgresSnapshotContext")
        if not isinstance(self.mode, ReconcileMode):
            raise TypeError("mode must be a ReconcileMode")
        if self.postgres_snapshot_guard is not None and not callable(
            self.postgres_snapshot_guard
        ):
            raise TypeError("postgres_snapshot_guard must be callable")
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        _require_utc(self.emitted_at, "emitted_at")
        _require_utc(self.context.snapshot_cutoff, "snapshot_cutoff")
        source_version = (
            self.context.catalog_revision
            if self.source_version is None
            else self.source_version
        )
        if type(source_version) is not int or not 1 <= source_version < (1 << 64):
            raise ValueError("source_version must be a positive UInt64")
        object.__setattr__(self, "source_version", source_version)
        if type(self.incremental_overlap_seconds) is not int or not (
            0
            <= self.incremental_overlap_seconds
            <= MAX_RECONCILE_INCREMENTAL_OVERLAP_SECONDS
        ):
            raise ValueError(
                "incremental_overlap_seconds must be between 0 and "
                f"{MAX_RECONCILE_INCREMENTAL_OVERLAP_SECONDS}"
            )
        if self.mode is ReconcileMode.FULL_REPAIR and self.lower_watermark:
            raise ValueError("full repair must begin at the source origin")
        if self.resume is not None and self.lower_watermark:
            raise ValueError("resume and lower_watermark are mutually exclusive")
        if self.lower_watermark:
            SourceKeysetCursor.decode(self.lower_watermark)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    snapshot: SourceSnapshot
    envelopes: tuple[PropertyCatalogEnvelope, ...]
    payload_sha256s: tuple[str, ...]
    checkpoint_write: CheckpointWrite
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.checkpoint_write.checkpoint.status is CheckpointStatus.COMPLETE


class EnvelopePublisher(Protocol):
    """Publish durably and idempotently, rejecting active/fenced revisions."""

    def publish(self, envelope: PropertyCatalogEnvelope) -> str:
        """Return canonical payload SHA after the data and ledger are durable."""


class CheckpointWriter(Protocol):
    def append(self, checkpoint: CheckpointWrite) -> None: ...


class CurrentBindingReader(Protocol):
    """Return one resolved row per binding, visible immediately after publish."""

    def read_current(
        self,
        *,
        context: PostgresSnapshotContext,
        source_adapter: SourceAdapter,
        at_revision: int,
        build_token: str,
    ) -> Sequence[PropertyBindingRow]: ...


@dataclass(slots=True)
class _Progress:
    source_count: int
    definition_count: int
    tombstone_count: int
    delivery_count: int
    first_sequence: int | None
    last_sequence: int | None
    source_digest: str
    emitted_digest: str
    previous_payload_sha256: str
    watermark: str
    processed_rows: int


class PropertyCatalogReconciler:
    def __init__(
        self,
        *,
        publisher: EnvelopePublisher,
        checkpoint_writer: CheckpointWriter,
        current_bindings: CurrentBindingReader,
    ) -> None:
        self._publisher = publisher
        self._checkpoint_writer = checkpoint_writer
        self._current_bindings = current_bindings

    def reconcile(
        self,
        adapter: DefinitionSourceAdapter,
        request: ReconcileRequest,
    ) -> ReconcileResult:
        progress = _starting_progress(request, adapter.source_adapter)
        source_cursor = _source_cursor(request)
        snapshot = adapter.read_snapshot(
            context=request.context,
            budget=request.source_budget,
            cursor=source_cursor or None,
        )
        if snapshot.source_adapter is not adapter.source_adapter:
            raise PropertyCatalogReconcileError("source snapshot adapter mismatch")
        _guard_postgres_snapshot(request)

        current = tuple(
            self._current_bindings.read_current(
                context=request.context,
                source_adapter=adapter.source_adapter,
                at_revision=request.context.catalog_revision,
                build_token=request.build_token,
            )
        )
        _validate_catalog_rows(
            current,
            request=request,
            adapter=adapter.source_adapter,
            at_revision=request.context.catalog_revision,
        )
        projected, conflicts = _project_records(
            snapshot=snapshot,
            current=current,
            request=request,
        )
        _guard_postgres_snapshot(request)

        segment_source_digest = progress.source_digest
        for record in snapshot.records:
            segment_source_digest = framed_sha256(
                "futureagi.property-catalog.source-snapshot.v1",
                segment_source_digest,
                record.source_fingerprint,
            )
        candidate_watermark = (
            snapshot.records[-1].cursor.encode()
            if snapshot.records
            else (
                # A terminal empty page still proves that this immutable
                # snapshot contains no source rows through its frozen cutoff.
                # Persist that cutoff as a valid keyset cursor so a newly
                # activated empty relational stream can seed the next
                # incremental revision, and an unchanged stream advances
                # instead of rescanning its old overlap forever.
                SourceKeysetCursor(
                    request.context.snapshot_cutoff,
                    ZERO_UUID,
                ).encode()
                if snapshot.terminal
                and adapter.source_adapter in _RELATIONAL_ADAPTERS
                else progress.watermark or request.lower_watermark
            )
        )
        segment_watermark = _max_cursor(progress.watermark, candidate_watermark)

        if snapshot.terminal and request.mode is ReconcileMode.FULL_REPAIR:
            baseline: tuple[PropertyBindingRow, ...] = ()
            if request.context.catalog_revision > 1:
                baseline = tuple(
                    self._current_bindings.read_current(
                        context=request.context,
                        source_adapter=adapter.source_adapter,
                        at_revision=request.context.catalog_revision - 1,
                        build_token=request.build_token,
                    )
                )
                _validate_catalog_rows(
                    baseline,
                    request=request,
                    adapter=adapter.source_adapter,
                    at_revision=request.context.catalog_revision - 1,
                )
            repair_rows, repair_conflicts = _repair_tombstones(
                baseline=baseline,
                current_revision=current,
                current_segment=projected,
                source_digest=segment_source_digest,
                request=request,
            )
            projected = tuple(
                sorted((*projected, *repair_rows), key=lambda row: row.binding_id)
            )
            conflicts += repair_conflicts

        if conflicts:
            return self._failed_without_delivery(
                request=request,
                adapter=adapter.source_adapter,
                snapshot=snapshot,
                progress=progress,
                source_cursor=source_cursor,
                conflict_count=conflicts,
                error="definition binding conflict",
            )

        batches = list(_chunk_rows(projected, request.envelope_budget))
        if not batches and snapshot.source_count:
            # Preserve independent source accounting even when projection
            # produces no definition rows.
            batches.append(())
        plans = [
            (batch, snapshot.source_count if index == 0 else 0, False)
            for index, batch in enumerate(batches)
        ]
        if snapshot.terminal:
            # Cross-language wire contract: the only terminal envelope is a
            # dedicated empty committed record at the exact fence sequence.
            plans.append(((), 0, True))
        envelopes: list[PropertyCatalogEnvelope] = []
        payloads: list[str] = []
        next_sequence = (progress.last_sequence or 0) + 1
        previous_payload = progress.previous_payload_sha256
        emitted_digest = progress.emitted_digest

        for index, (batch, envelope_source_count, terminal) in enumerate(plans):
            sequence = next_sequence + index
            sequenced = tuple(replace(row, producer_sequence=sequence) for row in batch)
            envelope = PropertyCatalogEnvelope(
                organization_id=request.context.organization_id,
                workspace_id=request.context.workspace_id,
                catalog_epoch=request.context.catalog_epoch,
                catalog_revision=request.context.catalog_revision,
                build_token=request.build_token,
                projection_version=request.context.projection_version,
                source_adapter=adapter.source_adapter,
                producer_stream_id=request.producer_stream_id,
                sequence=sequence,
                previous_payload_sha256=previous_payload,
                source_version=request.source_version,
                source_fingerprint=segment_source_digest,
                source_batch_digest=framed_sha256(
                    "futureagi.property-catalog.source-batch.v1",
                    segment_source_digest,
                    sequence,
                    terminal,
                    *(row.state_sha256 for row in sequenced),
                ),
                outcome=EnvelopeOutcome.COMMITTED,
                counts=EnvelopeCounts(
                    source_count=envelope_source_count,
                    definition_count=len(sequenced),
                    value_count=0,
                    tombstone_count=sum(row.is_deleted for row in sequenced),
                    gap_count=0,
                ),
                definitions=sequenced,
                gap_reasons=(),
                terminal=terminal,
            )
            envelopes.append(envelope)
            try:
                # A large relational snapshot can produce many catalog
                # envelopes. Keep the same read-only REPEATABLE READ session
                # active between ClickHouse publishes so PostgreSQL's
                # idle-in-transaction protection cannot sever it at COMMIT.
                _guard_postgres_snapshot(request)
                payload_sha256 = self._publisher.publish(envelope)
                require_sha256(payload_sha256, field="payload_sha256")
            except Exception as exc:  # retain the pre-segment cursor for replay
                # Wire errors are a closed set of static contract messages, so
                # retaining their detail is safe and makes rollout evidence
                # actionable.  Other publisher exceptions may contain client
                # or database details and remain type-only.
                error_detail = (
                    f": {exc}" if isinstance(exc, PropertyCatalogWireError) else ""
                )
                return self._publish_failed(
                    request=request,
                    adapter=adapter.source_adapter,
                    snapshot=snapshot,
                    progress=progress,
                    source_cursor=source_cursor,
                    envelopes=tuple(envelopes),
                    error=(
                        f"envelope publish failed: {type(exc).__name__}{error_detail}"
                    ),
                )
            payloads.append(payload_sha256)
            previous_payload = payload_sha256
            emitted_digest = framed_sha256(
                "futureagi.property-catalog.emitted-stream.v1",
                emitted_digest,
                payload_sha256,
            )

        completed_deliveries = len(payloads)
        final_progress = _Progress(
            source_count=progress.source_count + snapshot.source_count,
            definition_count=progress.definition_count + len(projected),
            tombstone_count=(
                progress.tombstone_count + sum(row.is_deleted for row in projected)
            ),
            delivery_count=progress.delivery_count + completed_deliveries,
            first_sequence=(
                progress.first_sequence
                or (next_sequence if completed_deliveries else None)
            ),
            last_sequence=(
                next_sequence + completed_deliveries - 1
                if completed_deliveries
                else progress.last_sequence
            ),
            source_digest=segment_source_digest,
            emitted_digest=emitted_digest,
            previous_payload_sha256=previous_payload,
            watermark=segment_watermark,
            processed_rows=progress.processed_rows + snapshot.source_count,
        )
        checkpoint_write = _checkpoint_write(
            request=request,
            adapter=adapter.source_adapter,
            progress=final_progress,
            status=(
                CheckpointStatus.COMPLETE
                if snapshot.terminal
                else CheckpointStatus.RUNNING
            ),
            terminal=snapshot.terminal,
            source_cursor=snapshot.next_cursor or "",
            conflict_count=0,
        )
        _guard_postgres_snapshot(request)
        self._checkpoint_writer.append(checkpoint_write)
        return ReconcileResult(
            snapshot=snapshot,
            envelopes=tuple(envelopes),
            payload_sha256s=tuple(payloads),
            checkpoint_write=checkpoint_write,
        )

    def _failed_without_delivery(
        self,
        *,
        request: ReconcileRequest,
        adapter: SourceAdapter,
        snapshot: SourceSnapshot,
        progress: _Progress,
        source_cursor: str,
        conflict_count: int,
        error: str,
    ) -> ReconcileResult:
        checkpoint_write = _checkpoint_write(
            request=request,
            adapter=adapter,
            progress=progress,
            status=CheckpointStatus.FAILED,
            terminal=False,
            source_cursor=source_cursor,
            conflict_count=conflict_count,
        )
        self._checkpoint_writer.append(checkpoint_write)
        return ReconcileResult(
            snapshot=snapshot,
            envelopes=(),
            payload_sha256s=(),
            checkpoint_write=checkpoint_write,
            error=error,
        )

    def _publish_failed(
        self,
        *,
        request: ReconcileRequest,
        adapter: SourceAdapter,
        snapshot: SourceSnapshot,
        progress: _Progress,
        source_cursor: str,
        envelopes: tuple[PropertyCatalogEnvelope, ...],
        error: str,
    ) -> ReconcileResult:
        # The publisher may have committed before its acknowledgement failed.
        # Do not advance source progress; replay the identical sequence and rely
        # on the delivery ledger's envelope identity to make it a no-op.
        checkpoint_write = _checkpoint_write(
            request=request,
            adapter=adapter,
            progress=progress,
            status=CheckpointStatus.FAILED,
            terminal=False,
            source_cursor=source_cursor,
            conflict_count=0,
        )
        self._checkpoint_writer.append(checkpoint_write)
        return ReconcileResult(
            snapshot=snapshot,
            envelopes=envelopes,
            payload_sha256s=(),
            checkpoint_write=checkpoint_write,
            error=error,
        )


def _guard_postgres_snapshot(request: ReconcileRequest) -> None:
    guard = request.postgres_snapshot_guard
    if guard is not None:
        guard()


def _starting_progress(
    request: ReconcileRequest,
    adapter: SourceAdapter,
) -> _Progress:
    if request.resume is None:
        return _Progress(
            source_count=0,
            definition_count=0,
            tombstone_count=0,
            delivery_count=0,
            first_sequence=None,
            last_sequence=None,
            source_digest=EMPTY_SHA256,
            emitted_digest=EMPTY_SHA256,
            # Wire v1 defines sequence-one genesis as the all-zero hash.  The
            # SHA-256 of empty bytes remains reserved for empty source/emitted
            # digest accumulators and is not a chain predecessor.
            previous_payload_sha256=ZERO_SHA256,
            watermark=request.lower_watermark,
            processed_rows=0,
        )
    resume = request.resume
    checkpoint = resume.checkpoint
    if (
        checkpoint.organization_id != request.context.organization_id
        or checkpoint.workspace_id != request.context.workspace_id
        or checkpoint.catalog_epoch != request.context.catalog_epoch
        or checkpoint.catalog_revision != request.context.catalog_revision
        or checkpoint.build_token != request.build_token
        or checkpoint.projection_version != request.context.projection_version
        or checkpoint.source_adapter is not adapter
        or checkpoint.producer_stream_id != request.producer_stream_id
        or resume.source_version_fence != request.source_version
        or checkpoint.terminal
        or checkpoint.status not in {CheckpointStatus.RUNNING, CheckpointStatus.FAILED}
    ):
        raise PropertyCatalogReconcileError("resume checkpoint scope mismatch")
    if resume.source_cursor:
        SourceKeysetCursor.decode(resume.source_cursor)
    elif not _is_first_segment_publish_retry(resume):
        raise PropertyCatalogReconcileError("resume checkpoint has no source cursor")
    return _Progress(
        source_count=checkpoint.source_count,
        definition_count=checkpoint.definition_count,
        tombstone_count=checkpoint.tombstone_count,
        delivery_count=checkpoint.delivery_count,
        first_sequence=checkpoint.first_sequence,
        last_sequence=checkpoint.last_sequence,
        source_digest=checkpoint.source_digest,
        emitted_digest=checkpoint.emitted_digest,
        previous_payload_sha256=resume.previous_payload_sha256,
        watermark=resume.watermark,
        processed_rows=resume.processed_rows,
    )


def _is_first_segment_publish_retry(resume: CheckpointWrite) -> bool:
    """Accept only the exact persisted genesis state for an ambiguous publish.

    A publisher can durably commit sequence one and then lose its acknowledgement.
    The failed checkpoint intentionally retains the pre-segment state, including an
    empty origin cursor, so recovery must replay sequence one byte-for-byte.  These
    invariants distinguish that safe replay from an empty/corrupt continuation; the
    immutable delivery ledger then accepts only the identical envelope identity.
    """

    checkpoint = resume.checkpoint
    return (
        checkpoint.status is CheckpointStatus.FAILED
        and resume.processed_rows == 0
        and not resume.source_cursor
        and not resume.watermark
        and checkpoint.source_count == 0
        and checkpoint.definition_count == 0
        and checkpoint.value_count == 0
        and checkpoint.tombstone_count == 0
        and checkpoint.gap_count == 0
        and checkpoint.poison_count == 0
        and checkpoint.conflict_count == 0
        and checkpoint.delivery_count == 0
        and checkpoint.first_sequence is None
        and checkpoint.last_sequence is None
        and checkpoint.last_issued_sequence == 0
        and checkpoint.fenced_sequence == 0
        and checkpoint.source_digest == EMPTY_SHA256
        and checkpoint.emitted_digest == EMPTY_SHA256
        and checkpoint.terminal_payload_sha256 == EMPTY_SHA256
        and resume.source_fingerprint == EMPTY_SHA256
        and resume.previous_payload_sha256 == ZERO_SHA256
    )


def _source_cursor(request: ReconcileRequest) -> str:
    if request.resume is not None:
        return request.resume.source_cursor
    if request.mode is ReconcileMode.FULL_REPAIR or not request.lower_watermark:
        return ""
    cursor = SourceKeysetCursor.decode(request.lower_watermark)
    assert cursor is not None
    return SourceKeysetCursor(
        updated_at=cursor.updated_at
        - timedelta(seconds=request.incremental_overlap_seconds),
        source_entity_id=ZERO_UUID,
    ).encode()


def _project_records(
    *,
    snapshot: SourceSnapshot,
    current: tuple[PropertyBindingRow, ...],
    request: ReconcileRequest,
) -> tuple[tuple[PropertyBindingRow, ...], int]:
    by_binding: dict[str, PropertyBindingRow] = {}
    records_by_entity = {record.source_entity_id: record for record in snapshot.records}
    conflicts: set[str] = set()
    for record in snapshot.records:
        for visibility in sorted(
            record.visibilities,
            key=lambda item: (item.scope, item.visibility_id),
        ):
            row = project_definition(
                organization_id=request.context.organization_id,
                workspace_id=request.context.workspace_id,
                catalog_epoch=request.context.catalog_epoch,
                catalog_revision=request.context.catalog_revision,
                build_token=request.build_token,
                projection_version=request.context.projection_version,
                visibility=visibility,
                definition=record.definition,
                source_adapter=record.source_adapter,
                source_entity_id=record.source_entity_id,
                source_version=request.source_version,
                source_fingerprint=record.source_fingerprint,
                producer_stream_id=request.producer_stream_id,
                producer_sequence=1,
                emitted_at=request.emitted_at,
                first_seen=record.first_seen,
                last_seen=record.last_seen,
                is_deleted=record.is_deleted,
                deleted_at=record.deleted_at,
            )
            existing = by_binding.get(row.binding_id)
            if existing is not None and existing.state_sha256 != row.state_sha256:
                conflicts.add(row.binding_id)
            else:
                by_binding[row.binding_id] = row

    current_by_binding = {row.binding_id: row for row in current}
    # Every returned source entity carries its complete current visibility set.
    # Tombstone only lost bindings for those touched entities here; absence of
    # an entire entity remains a terminal full-repair concern.
    for old in current:
        record = records_by_entity.get(old.source_entity_id)
        if record is None or old.is_deleted or old.binding_id in by_binding:
            continue
        if old.source_version >= request.source_version:
            conflicts.add(old.binding_id)
            continue
        source_fingerprint = framed_sha256(
            "futureagi.property-catalog.visibility-tombstone.v1",
            record.source_fingerprint,
            old.binding_id,
        )
        state_sha256 = make_state_sha256(
            binding_id=old.binding_id,
            definition_sha256=old.definition.definition_sha256,
            source_entity_id=old.source_entity_id,
            source_version=request.source_version,
            source_fingerprint=source_fingerprint,
            is_deleted=True,
            deleted_at=request.context.snapshot_cutoff,
            first_seen=old.first_seen,
            last_seen=old.last_seen,
        )
        by_binding[old.binding_id] = replace(
            old,
            catalog_revision=request.context.catalog_revision,
            build_token=request.build_token,
            projection_version=request.context.projection_version,
            source_version=request.source_version,
            source_fingerprint=source_fingerprint,
            is_deleted=True,
            deleted_at=request.context.snapshot_cutoff,
            state_sha256=state_sha256,
            producer_stream_id=request.producer_stream_id,
            producer_sequence=1,
            emitted_at=request.emitted_at,
        )
    for binding_id, row in by_binding.items():
        old = current_by_binding.get(binding_id)
        if old is None:
            continue
        if old.source_version > request.source_version or (
            old.source_version == request.source_version
            and old.state_sha256 != row.state_sha256
        ):
            conflicts.add(binding_id)
    return tuple(sorted(by_binding.values(), key=lambda row: row.binding_id)), len(
        conflicts
    )


def _repair_tombstones(
    *,
    baseline: tuple[PropertyBindingRow, ...],
    current_revision: tuple[PropertyBindingRow, ...],
    current_segment: tuple[PropertyBindingRow, ...],
    source_digest: str,
    request: ReconcileRequest,
) -> tuple[tuple[PropertyBindingRow, ...], int]:
    seen = {
        row.binding_id
        for row in current_revision
        if row.catalog_revision == request.context.catalog_revision
        and row.source_version == request.source_version
    }
    seen.update(row.binding_id for row in current_segment)
    tombstones: list[PropertyBindingRow] = []
    conflicts = 0
    for old in baseline:
        if old.is_deleted or old.binding_id in seen:
            continue
        if old.source_version >= request.source_version:
            conflicts += 1
            continue
        source_fingerprint = framed_sha256(
            "futureagi.property-catalog.repair-tombstone.v1",
            old.source_fingerprint,
            source_digest,
        )
        state_sha256 = make_state_sha256(
            binding_id=old.binding_id,
            definition_sha256=old.definition.definition_sha256,
            source_entity_id=old.source_entity_id,
            source_version=request.source_version,
            source_fingerprint=source_fingerprint,
            is_deleted=True,
            deleted_at=request.context.snapshot_cutoff,
            first_seen=old.first_seen,
            last_seen=old.last_seen,
        )
        tombstones.append(
            replace(
                old,
                catalog_revision=request.context.catalog_revision,
                build_token=request.build_token,
                projection_version=request.context.projection_version,
                source_version=request.source_version,
                source_fingerprint=source_fingerprint,
                is_deleted=True,
                deleted_at=request.context.snapshot_cutoff,
                state_sha256=state_sha256,
                producer_stream_id=request.producer_stream_id,
                producer_sequence=1,
                emitted_at=request.emitted_at,
            )
        )
    return tuple(tombstones), conflicts


def _max_cursor(first: str, second: str) -> str:
    first_cursor = SourceKeysetCursor.decode(first) if first else None
    second_cursor = SourceKeysetCursor.decode(second) if second else None
    if first_cursor is None:
        return second
    if second_cursor is None:
        return first
    return max(first_cursor, second_cursor).encode()


def _chunk_rows(
    rows: tuple[PropertyBindingRow, ...],
    budget: EnvelopeBudget,
) -> tuple[tuple[PropertyBindingRow, ...], ...]:
    if not rows:
        return ()
    batches: list[tuple[PropertyBindingRow, ...]] = []
    current: list[PropertyBindingRow] = []
    current_bytes = 0
    # A definition envelope that stays within one exact v1 JSONEachRow chunk
    # also stays below the smaller 768 KiB Base64-wrapped record ceiling.  Size
    # at the widest possible sequence so a later multi-envelope stream cannot
    # cross the chunk boundary when its sequence gains digits.
    wire_payload_bytes = min(budget.max_payload_bytes, MAX_CHUNK_BYTES)
    for row in rows:
        row_bytes = definition_json_each_row_size(
            row,
            producer_sequence=_MAX_UINT64,
        )
        if row_bytes > wire_payload_bytes:
            raise PropertyCatalogReconcileError(
                "one definition exceeds the bounded envelope byte budget"
            )
        if current and (
            len(current) >= budget.max_definition_rows
            or current_bytes + row_bytes > wire_payload_bytes
        ):
            batches.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(row)
        current_bytes += row_bytes
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _validate_catalog_rows(
    rows: tuple[PropertyBindingRow, ...],
    *,
    request: ReconcileRequest,
    adapter: SourceAdapter,
    at_revision: int,
) -> None:
    seen: set[str] = set()
    for row in rows:
        if row.binding_id in seen:
            raise PropertyCatalogReconcileError(
                "binding reader returned duplicate resolved bindings"
            )
        seen.add(row.binding_id)
        if (
            row.organization_id != request.context.organization_id
            or row.workspace_id != request.context.workspace_id
            or row.catalog_epoch != request.context.catalog_epoch
            or row.catalog_revision > at_revision
            or row.source_adapter is not adapter
        ):
            raise PropertyCatalogReconcileError(
                "binding reader returned an out-of-scope row"
            )


def _checkpoint_write(
    *,
    request: ReconcileRequest,
    adapter: SourceAdapter,
    progress: _Progress,
    status: CheckpointStatus,
    terminal: bool,
    source_cursor: str,
    conflict_count: int,
) -> CheckpointWrite:
    checkpoint = CatalogCheckpoint(
        organization_id=request.context.organization_id,
        workspace_id=request.context.workspace_id,
        catalog_epoch=request.context.catalog_epoch,
        catalog_revision=request.context.catalog_revision,
        build_token=request.build_token,
        projection_version=request.context.projection_version,
        source_adapter=adapter,
        producer_stream_id=request.producer_stream_id,
        source_version_fence=request.source_version,
        status=status,
        terminal=terminal,
        source_count=progress.source_count,
        definition_count=progress.definition_count,
        value_count=0,
        tombstone_count=progress.tombstone_count,
        gap_count=0,
        poison_count=0,
        conflict_count=conflict_count,
        first_sequence=progress.first_sequence,
        last_sequence=progress.last_sequence,
        last_issued_sequence=progress.last_sequence or 0,
        fenced_sequence=(progress.last_sequence or 0) if terminal else 0,
        terminal_payload_sha256=(
            progress.previous_payload_sha256 if terminal else EMPTY_SHA256
        ),
        delivery_count=progress.delivery_count,
        source_digest=progress.source_digest,
        emitted_digest=progress.emitted_digest,
    )
    return CheckpointWrite(
        checkpoint=checkpoint,
        source_cursor=source_cursor,
        watermark=progress.watermark,
        source_version_fence=request.source_version,
        source_fingerprint=progress.source_digest,
        previous_payload_sha256=progress.previous_payload_sha256,
        processed_rows=progress.processed_rows,
    )


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


__all__ = [
    "CheckpointWrite",
    "CheckpointWriter",
    "CurrentBindingReader",
    "EnvelopeBudget",
    "EnvelopePublisher",
    "PropertyCatalogReconcileError",
    "PropertyCatalogReconciler",
    "ReconcileMode",
    "ReconcileRequest",
    "ReconcileResult",
]
