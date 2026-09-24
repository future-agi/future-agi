"""Revision-wide orchestration for PostgreSQL definition adapters.

One catalog revision is authoritative only when every relational adapter and
every resumed keyset segment observes the same PostgreSQL snapshot.  This
module owns that boundary so callers cannot accidentally open a transaction
per adapter or per continuation.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Protocol

from .models import SourceAdapter
from .projection import (
    PostgresSnapshotContext,
    validate_postgres_adapter,
)
from .qualification import CheckpointStatus
from .reconciler import (
    CheckpointWrite,
    ReconcileRequest,
    ReconcileResult,
)
from .source_adapters import (
    DefinitionSourceAdapter,
    SourceKeysetCursor,
    SourceReadBudget,
    SourceSnapshot,
    default_postgres_source_adapters,
    postgres_revision_snapshot,
)

_POSTGRES_SOURCE_ADAPTERS = frozenset(
    {
        SourceAdapter.EVAL_TEMPLATE,
        SourceAdapter.EVAL_CONFIG,
        SourceAdapter.SIMULATION_EVAL_CONFIG,
        SourceAdapter.ANNOTATION_LABEL,
        SourceAdapter.DATASET_COLUMN,
    }
)


class PostgresRevisionReconcileError(RuntimeError):
    """A relational catalog revision cannot provide one coherent snapshot."""


class PostgresRevisionReconciler(Protocol):
    def reconcile(
        self,
        adapter: DefinitionSourceAdapter,
        request: ReconcileRequest,
    ) -> ReconcileResult: ...


ReconcileRequestFactory = Callable[[DefinitionSourceAdapter], ReconcileRequest]
PostgresSnapshotGuard = Callable[[], None]


@dataclass(frozen=True, slots=True)
class PostgresAdapterReconcileResult:
    """All segment evidence plus the authoritative terminal result."""

    source_adapter: SourceAdapter
    segment_results: tuple[ReconcileResult, ...]

    @property
    def final_result(self) -> ReconcileResult:
        return self.segment_results[-1]

    @property
    def checkpoint_write(self) -> CheckpointWrite:
        return self.final_result.checkpoint_write


@dataclass(frozen=True, slots=True)
class PostgresRevisionReconcileResult:
    """Terminal evidence for every relational source in one revision."""

    context: PostgresSnapshotContext
    source_budget: SourceReadBudget
    build_token: str
    adapter_results: tuple[PostgresAdapterReconcileResult, ...]
    postgres_snapshot_opened: bool

    @property
    def final_results(self) -> tuple[ReconcileResult, ...]:
        return tuple(result.final_result for result in self.adapter_results)

    @property
    def checkpoint_writes(self) -> tuple[CheckpointWrite, ...]:
        return tuple(result.checkpoint_write for result in self.adapter_results)


def reconcile_postgres_revision(
    *,
    reconciler: PostgresRevisionReconciler,
    request_factory: ReconcileRequestFactory,
    adapters: Sequence[DefinitionSourceAdapter] | None = None,
    snapshot_guard: PostgresSnapshotGuard | None = None,
) -> PostgresRevisionReconcileResult:
    """Reconcile every PG adapter and continuation in one snapshot session.

    Requests are materialized and scope-checked before the transaction opens.
    Default relational adapters then run under one read-only repeatable-read
    snapshot.  A set made entirely of explicitly injected page loaders remains
    pure and does not require a database connection.
    """

    selected_adapters = tuple(
        default_postgres_source_adapters() if adapters is None else adapters
    )
    if not selected_adapters:
        raise PostgresRevisionReconcileError(
            "at least one PostgreSQL property adapter is required"
        )
    if snapshot_guard is not None and not callable(snapshot_guard):
        raise TypeError("snapshot_guard must be callable")

    _validate_adapters(selected_adapters)
    requests = tuple(request_factory(adapter) for adapter in selected_adapters)
    context, source_budget, build_token = _validate_requests(
        selected_adapters,
        requests,
    )
    completed_results = tuple(
        _rehydrate_completed_resume(adapter=adapter, request=request)
        for adapter, request in zip(selected_adapters, requests, strict=True)
    )
    open_snapshot = any(
        completed is None and _requires_postgres_snapshot(adapter)
        for adapter, completed in zip(
            selected_adapters,
            completed_results,
            strict=True,
        )
    )
    snapshot_session = (
        postgres_revision_snapshot(
            context=context,
            budget=source_budget.postgres,
        )
        if open_snapshot
        else nullcontext()
    )

    adapter_results: list[PostgresAdapterReconcileResult] = []
    with snapshot_session:
        if open_snapshot and snapshot_guard is not None:
            snapshot_guard()
        for adapter, request, completed in zip(
            selected_adapters,
            requests,
            completed_results,
            strict=True,
        ):
            if completed is not None:
                adapter_results.append(
                    PostgresAdapterReconcileResult(
                        source_adapter=adapter.source_adapter,
                        segment_results=(completed,),
                    )
                )
                continue
            adapter_results.append(
                _reconcile_all_segments(
                    reconciler=reconciler,
                    adapter=adapter,
                    request=request,
                    snapshot_guard=(snapshot_guard if open_snapshot else None),
                )
            )

    return PostgresRevisionReconcileResult(
        context=context,
        source_budget=source_budget,
        build_token=build_token,
        adapter_results=tuple(adapter_results),
        postgres_snapshot_opened=open_snapshot,
    )


def _validate_adapters(adapters: tuple[DefinitionSourceAdapter, ...]) -> None:
    seen: set[SourceAdapter] = set()
    for adapter in adapters:
        try:
            validate_postgres_adapter(adapter)
        except (TypeError, ValueError, AttributeError) as exc:
            raise PostgresRevisionReconcileError(str(exc)) from exc
        if adapter.source_adapter not in _POSTGRES_SOURCE_ADAPTERS:
            raise PostgresRevisionReconcileError(
                f"unsupported PostgreSQL property adapter: {adapter.source_adapter}"
            )
        if adapter.source_adapter in seen:
            raise PostgresRevisionReconcileError(
                f"duplicate PostgreSQL property adapter: {adapter.source_adapter}"
            )
        seen.add(adapter.source_adapter)
        _requires_postgres_snapshot(adapter)


def _requires_postgres_snapshot(adapter: DefinitionSourceAdapter) -> bool:
    # Older/injected protocol doubles without the capability marker are
    # treated as PostgreSQL-backed.  The safe default is to open the snapshot,
    # never to silently run a production loader outside one.
    required = getattr(adapter, "requires_postgres_snapshot", True)
    if type(required) is not bool:
        raise PostgresRevisionReconcileError(
            "requires_postgres_snapshot must be a bool"
        )
    return required


def _validate_requests(
    adapters: tuple[DefinitionSourceAdapter, ...],
    requests: tuple[ReconcileRequest, ...],
) -> tuple[PostgresSnapshotContext, SourceReadBudget, str]:
    if len(requests) != len(adapters):
        raise PostgresRevisionReconcileError("request count does not match adapters")
    if any(not isinstance(request, ReconcileRequest) for request in requests):
        raise PostgresRevisionReconcileError(
            "request_factory must return ReconcileRequest"
        )

    authority = requests[0]
    for adapter, request in zip(adapters, requests, strict=True):
        if request.context != authority.context:
            raise PostgresRevisionReconcileError(
                "PostgreSQL revision request context mismatch"
            )
        if request.source_budget != authority.source_budget:
            raise PostgresRevisionReconcileError(
                "PostgreSQL revision request source budget mismatch"
            )
        if request.build_token != authority.build_token:
            raise PostgresRevisionReconcileError(
                "PostgreSQL revision request build token mismatch"
            )
        if request.mode is not authority.mode:
            raise PostgresRevisionReconcileError(
                "PostgreSQL revision request reconcile mode mismatch"
            )
        if request.source_version != authority.source_version:
            raise PostgresRevisionReconcileError(
                "PostgreSQL revision request source version mismatch"
            )
        if (
            request.resume is not None
            and request.resume.checkpoint.source_adapter is not adapter.source_adapter
        ):
            raise PostgresRevisionReconcileError(
                "PostgreSQL revision resume adapter mismatch"
            )

    return authority.context, authority.source_budget, authority.build_token


def _reconcile_all_segments(
    *,
    reconciler: PostgresRevisionReconciler,
    adapter: DefinitionSourceAdapter,
    request: ReconcileRequest,
    snapshot_guard: PostgresSnapshotGuard | None = None,
) -> PostgresAdapterReconcileResult:
    segment_results: list[ReconcileResult] = []
    seen_cursors: set[str] = set()
    if request.resume is not None and request.resume.source_cursor:
        seen_cursors.add(request.resume.source_cursor)
    active_request = (
        replace(request, postgres_snapshot_guard=snapshot_guard)
        if snapshot_guard is not None
        else request
    )

    while True:
        result = reconciler.reconcile(adapter, active_request)
        if not isinstance(result, ReconcileResult):
            raise PostgresRevisionReconcileError(
                "reconciler must return ReconcileResult"
            )
        _validate_result_scope(
            adapter=adapter,
            request=active_request,
            result=result,
        )
        segment_results.append(result)

        if result.error is not None:
            raise PostgresRevisionReconcileError(
                f"{adapter.source_adapter} reconciliation failed: {result.error}"
            )
        if result.complete:
            return PostgresAdapterReconcileResult(
                source_adapter=adapter.source_adapter,
                segment_results=tuple(segment_results),
            )

        checkpoint_write = result.checkpoint_write
        cursor = checkpoint_write.source_cursor
        previous_rows = (
            active_request.resume.processed_rows
            if active_request.resume is not None
            else 0
        )
        if not cursor:
            raise PostgresRevisionReconcileError(
                f"{adapter.source_adapter} non-terminal segment has no cursor"
            )
        SourceKeysetCursor.decode(cursor)
        if cursor in seen_cursors or checkpoint_write.processed_rows <= previous_rows:
            raise PostgresRevisionReconcileError(
                f"{adapter.source_adapter} reconciliation made no source progress"
            )
        seen_cursors.add(cursor)
        active_request = replace(
            active_request,
            lower_watermark="",
            resume=checkpoint_write,
        )


def _rehydrate_completed_resume(
    *,
    adapter: DefinitionSourceAdapter,
    request: ReconcileRequest,
) -> ReconcileResult | None:
    """Return persisted terminal evidence without reopening a completed stream."""

    resume = request.resume
    if resume is None or resume.checkpoint.status is not CheckpointStatus.COMPLETE:
        return None
    checkpoint = resume.checkpoint
    result = ReconcileResult(
        # No source page is read during recovery.  The zero-page snapshot makes
        # that explicit while retaining the adapter and persisted source digest
        # needed to validate and carry the terminal evidence forward.
        snapshot=SourceSnapshot(
            source_adapter=adapter.source_adapter,
            records=(),
            next_cursor=None,
            terminal=True,
            source_count=0,
            source_bytes=0,
            source_digest=checkpoint.source_digest,
            page_count=0,
        ),
        envelopes=(),
        payload_sha256s=(),
        checkpoint_write=resume,
    )
    _validate_result_scope(adapter=adapter, request=request, result=result)
    return result


def _validate_result_scope(
    *,
    adapter: DefinitionSourceAdapter,
    request: ReconcileRequest,
    result: ReconcileResult,
) -> None:
    checkpoint_write = result.checkpoint_write
    checkpoint = checkpoint_write.checkpoint
    context = request.context
    if result.snapshot.source_adapter is not adapter.source_adapter:
        raise PostgresRevisionReconcileError("source snapshot adapter mismatch")
    if (
        checkpoint.source_adapter is not adapter.source_adapter
        or checkpoint.organization_id != context.organization_id
        or checkpoint.workspace_id != context.workspace_id
        or checkpoint.catalog_epoch != context.catalog_epoch
        or checkpoint.catalog_revision != context.catalog_revision
        or checkpoint.projection_version != context.projection_version
        or checkpoint.build_token != request.build_token
        or checkpoint.producer_stream_id != request.producer_stream_id
        or checkpoint.source_version_fence != request.source_version
        or checkpoint_write.source_version_fence != request.source_version
    ):
        raise PostgresRevisionReconcileError(
            "PostgreSQL revision result scope mismatch"
        )
    if checkpoint.status is CheckpointStatus.COMPLETE:
        if not checkpoint.terminal:
            raise PostgresRevisionReconcileError(
                "complete PostgreSQL revision checkpoint is not terminal"
            )
    elif checkpoint.status is not CheckpointStatus.RUNNING and result.error is None:
        raise PostgresRevisionReconcileError(
            "non-terminal PostgreSQL revision checkpoint is not running"
        )
    elif checkpoint.terminal:
        raise PostgresRevisionReconcileError(
            "running PostgreSQL revision checkpoint cannot be terminal"
        )


__all__ = [
    "PostgresAdapterReconcileResult",
    "PostgresRevisionReconcileError",
    "PostgresRevisionReconcileResult",
    "PostgresRevisionReconciler",
    "PostgresSnapshotGuard",
    "ReconcileRequestFactory",
    "reconcile_postgres_revision",
]
