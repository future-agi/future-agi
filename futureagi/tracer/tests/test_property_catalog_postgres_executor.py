from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from tracer.services.clickhouse.v2.property_catalog import postgres_executor
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.postgres_executor import (
    PostgresRevisionReconcileError,
    reconcile_postgres_revision,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresReadBudget,
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    EMPTY_SHA256,
    ZERO_SHA256,
    ReconcileRequest,
    ReconcileResult,
    _checkpoint_write,
    _Progress,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    DatasetColumnSourceAdapter,
    EvalConfigSourceAdapter,
    PropertySourceError,
    SourceKeysetCursor,
    SourceReadBudget,
    SourceSnapshot,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
BUILD = "33333333-3333-4333-8333-333333333333"
EVAL_STREAM = "44444444-4444-4444-8444-444444444444"
DATASET_STREAM = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


class _Adapter:
    read_only = True
    isolation_level = "repeatable_read"

    def __init__(
        self,
        source_adapter: SourceAdapter,
        *,
        requires_postgres_snapshot: bool = True,
    ) -> None:
        self.source_adapter = source_adapter
        self.requires_postgres_snapshot = requires_postgres_snapshot

    def read_snapshot(self, **kwargs: Any) -> SourceSnapshot:
        raise AssertionError("the scripted reconciler owns this test")


def _context(*, revision: int = 7) -> PostgresSnapshotContext:
    return PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=revision,
        projection_version=1,
        snapshot_cutoff=NOW,
    )


def _budget(*, statement_timeout_ms: int = 900) -> SourceReadBudget:
    return SourceReadBudget(
        postgres=PostgresReadBudget(
            statement_timeout_ms=statement_timeout_ms,
            wall_timeout_seconds=1.0,
            max_rows_per_page=10,
            max_total_rows=20,
        )
    )


def _request(
    adapter: _Adapter | EvalConfigSourceAdapter | DatasetColumnSourceAdapter,
    *,
    context: PostgresSnapshotContext,
    budget: SourceReadBudget,
) -> ReconcileRequest:
    stream = {
        SourceAdapter.EVAL_CONFIG: EVAL_STREAM,
        SourceAdapter.DATASET_COLUMN: DATASET_STREAM,
    }[adapter.source_adapter]
    return ReconcileRequest(
        context=context,
        build_token=BUILD,
        producer_stream_id=stream,
        emitted_at=NOW,
        source_budget=budget,
    )


def _result(
    *,
    adapter: _Adapter | EvalConfigSourceAdapter | DatasetColumnSourceAdapter,
    request: ReconcileRequest,
    complete: bool,
    processed_rows: int,
) -> ReconcileResult:
    cursor = ""
    if not complete:
        cursor = SourceKeysetCursor(
            updated_at=NOW,
            source_entity_id=f"{adapter.source_adapter}-{processed_rows}",
        ).encode()
    progress = _Progress(
        source_count=processed_rows,
        definition_count=processed_rows,
        tombstone_count=0,
        delivery_count=1,
        first_sequence=1,
        last_sequence=1,
        source_digest=EMPTY_SHA256,
        emitted_digest=EMPTY_SHA256,
        previous_payload_sha256=ZERO_SHA256,
        watermark=cursor,
        processed_rows=processed_rows,
    )
    checkpoint_write = _checkpoint_write(
        request=request,
        adapter=adapter.source_adapter,
        progress=progress,
        status=(CheckpointStatus.COMPLETE if complete else CheckpointStatus.RUNNING),
        terminal=complete,
        source_cursor=cursor,
        conflict_count=0,
    )
    return ReconcileResult(
        snapshot=SourceSnapshot(
            source_adapter=adapter.source_adapter,
            records=(),
            next_cursor=None if complete else cursor,
            terminal=complete,
            source_count=0,
            source_bytes=0,
            source_digest=EMPTY_SHA256,
            page_count=1,
        ),
        envelopes=(),
        payload_sha256s=(),
        checkpoint_write=checkpoint_write,
    )


def test_revision_coordinator_shares_one_snapshot_across_adapters_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = (
        _Adapter(SourceAdapter.EVAL_CONFIG),
        _Adapter(SourceAdapter.DATASET_COLUMN),
    )
    context = _context()
    budget = _budget()
    events: list[str] = []
    snapshot_arguments: list[dict[str, Any]] = []
    inside_snapshot = False

    @contextmanager
    def fake_snapshot(**kwargs: Any) -> Iterator[None]:
        nonlocal inside_snapshot
        snapshot_arguments.append(kwargs)
        events.append("snapshot-enter")
        inside_snapshot = True
        try:
            yield
        finally:
            inside_snapshot = False
            events.append("snapshot-exit")

    monkeypatch.setattr(
        postgres_executor,
        "postgres_revision_snapshot",
        fake_snapshot,
    )

    calls: dict[SourceAdapter, int] = {}
    first_checkpoint: list[Any] = []

    class ScriptedReconciler:
        def reconcile(
            self,
            adapter: _Adapter,
            request: ReconcileRequest,
        ) -> ReconcileResult:
            assert inside_snapshot is True
            assert request.postgres_snapshot_guard is snapshot_guard
            calls[adapter.source_adapter] = calls.get(adapter.source_adapter, 0) + 1
            events.append(f"reconcile-{adapter.source_adapter}")
            if (
                adapter.source_adapter is SourceAdapter.EVAL_CONFIG
                and calls[adapter.source_adapter] == 1
            ):
                result = _result(
                    adapter=adapter,
                    request=request,
                    complete=False,
                    processed_rows=10,
                )
                first_checkpoint.append(result.checkpoint_write)
                return result
            if adapter.source_adapter is SourceAdapter.EVAL_CONFIG:
                assert request.resume is first_checkpoint[0]
                assert request.lower_watermark == ""
                return _result(
                    adapter=adapter,
                    request=request,
                    complete=True,
                    processed_rows=20,
                )
            return _result(
                adapter=adapter,
                request=request,
                complete=True,
                processed_rows=4,
            )

    def request_factory(adapter: _Adapter) -> ReconcileRequest:
        events.append(f"request-{adapter.source_adapter}")
        return _request(adapter, context=context, budget=budget)

    def snapshot_guard() -> None:
        events.append("snapshot-guard")

    result = reconcile_postgres_revision(
        reconciler=ScriptedReconciler(),
        request_factory=request_factory,
        adapters=adapters,
        snapshot_guard=snapshot_guard,
    )

    assert snapshot_arguments == [{"context": context, "budget": budget.postgres}]
    assert events == [
        "request-eval_config",
        "request-dataset_column",
        "snapshot-enter",
        "snapshot-guard",
        "reconcile-eval_config",
        "reconcile-eval_config",
        "reconcile-dataset_column",
        "snapshot-exit",
    ]
    assert result.postgres_snapshot_opened is True
    assert [len(item.segment_results) for item in result.adapter_results] == [2, 1]
    assert result.final_results == tuple(
        item.final_result for item in result.adapter_results
    )
    assert result.checkpoint_writes == tuple(
        item.checkpoint_write for item in result.adapter_results
    )
    assert all(item.checkpoint.terminal for item in result.checkpoint_writes)


def test_revision_coordinator_rehydrates_completed_adapter_and_only_runs_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = (
        _Adapter(SourceAdapter.EVAL_CONFIG),
        _Adapter(SourceAdapter.DATASET_COLUMN),
    )
    context = _context()
    budget = _budget()
    eval_request = _request(adapters[0], context=context, budget=budget)
    completed = _result(
        adapter=adapters[0],
        request=eval_request,
        complete=True,
        processed_rows=7,
    ).checkpoint_write
    requests = {
        SourceAdapter.EVAL_CONFIG: replace(eval_request, resume=completed),
        SourceAdapter.DATASET_COLUMN: _request(
            adapters[1],
            context=context,
            budget=budget,
        ),
    }
    snapshot_arguments: list[dict[str, Any]] = []
    inside_snapshot = False

    @contextmanager
    def fake_snapshot(**kwargs: Any) -> Iterator[None]:
        nonlocal inside_snapshot
        snapshot_arguments.append(kwargs)
        inside_snapshot = True
        try:
            yield
        finally:
            inside_snapshot = False

    monkeypatch.setattr(
        postgres_executor,
        "postgres_revision_snapshot",
        fake_snapshot,
    )
    reconcile_calls: list[SourceAdapter] = []

    class RemainingOnlyReconciler:
        def reconcile(
            self,
            adapter: _Adapter,
            request: ReconcileRequest,
        ) -> ReconcileResult:
            assert inside_snapshot is True
            assert adapter.source_adapter is SourceAdapter.DATASET_COLUMN
            assert request == requests[SourceAdapter.DATASET_COLUMN]
            assert request.postgres_snapshot_guard is None
            reconcile_calls.append(adapter.source_adapter)
            return _result(
                adapter=adapter,
                request=request,
                complete=True,
                processed_rows=3,
            )

    result = reconcile_postgres_revision(
        reconciler=RemainingOnlyReconciler(),
        request_factory=lambda adapter: requests[adapter.source_adapter],
        adapters=adapters,
    )

    assert result.context == context
    assert result.build_token == BUILD
    assert result.postgres_snapshot_opened is True
    assert snapshot_arguments == [{"context": context, "budget": budget.postgres}]
    assert reconcile_calls == [SourceAdapter.DATASET_COLUMN]
    rehydrated = result.adapter_results[0].final_result
    assert rehydrated.checkpoint_write is completed
    assert rehydrated.complete is True
    assert rehydrated.error is None
    assert rehydrated.envelopes == ()
    assert rehydrated.payload_sha256s == ()
    assert rehydrated.snapshot.source_adapter is SourceAdapter.EVAL_CONFIG
    assert rehydrated.snapshot.source_digest == completed.checkpoint.source_digest
    assert rehydrated.snapshot.terminal is True
    assert rehydrated.snapshot.page_count == 0
    assert result.adapter_results[1].final_result.complete is True


def test_revision_coordinator_rejects_mismatched_scope_before_opening_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = (
        _Adapter(SourceAdapter.EVAL_CONFIG),
        _Adapter(SourceAdapter.DATASET_COLUMN),
    )
    opened = False

    @contextmanager
    def forbidden_snapshot(**kwargs: Any) -> Iterator[None]:
        nonlocal opened
        opened = True
        yield

    monkeypatch.setattr(
        postgres_executor,
        "postgres_revision_snapshot",
        forbidden_snapshot,
    )

    with pytest.raises(PostgresRevisionReconcileError, match="context mismatch"):
        reconcile_postgres_revision(
            reconciler=object(),  # type: ignore[arg-type]
            request_factory=lambda adapter: _request(
                adapter,
                context=_context(
                    revision=(
                        7 if adapter.source_adapter is SourceAdapter.EVAL_CONFIG else 8
                    )
                ),
                budget=_budget(),
            ),
            adapters=adapters,
        )

    assert opened is False


def test_snapshot_guard_fails_before_any_adapter_or_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = (_Adapter(SourceAdapter.EVAL_CONFIG),)
    context = _context()
    budget = _budget()
    events: list[str] = []

    @contextmanager
    def fake_snapshot(**_kwargs: Any) -> Iterator[None]:
        events.append("snapshot-enter")
        try:
            yield
        finally:
            events.append("snapshot-exit")

    monkeypatch.setattr(
        postgres_executor,
        "postgres_revision_snapshot",
        fake_snapshot,
    )

    class ForbiddenReconciler:
        def reconcile(self, *_args: Any, **_kwargs: Any) -> ReconcileResult:
            events.append("reconcile")
            raise AssertionError("ownership failure must precede source/publish work")

    def reject_foreign_owner() -> None:
        events.append("snapshot-guard")
        raise RuntimeError("foreign project owner")

    with pytest.raises(RuntimeError, match="foreign project owner"):
        reconcile_postgres_revision(
            reconciler=ForbiddenReconciler(),
            request_factory=lambda adapter: _request(
                adapter,
                context=context,
                budget=budget,
            ),
            adapters=adapters,
            snapshot_guard=reject_foreign_owner,
        )

    assert events == ["snapshot-enter", "snapshot-guard", "snapshot-exit"]


def test_injected_adapters_remain_pure_in_revision_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = (
        EvalConfigSourceAdapter(page_loader=lambda **kwargs: ()),
        DatasetColumnSourceAdapter(page_loader=lambda **kwargs: ()),
    )
    context = _context()
    budget = _budget()

    def forbidden_snapshot(**kwargs: Any) -> None:
        raise AssertionError("pure injected adapters must not open PostgreSQL")

    monkeypatch.setattr(
        postgres_executor,
        "postgres_revision_snapshot",
        forbidden_snapshot,
    )

    class TerminalReconciler:
        def reconcile(
            self,
            adapter: EvalConfigSourceAdapter | DatasetColumnSourceAdapter,
            request: ReconcileRequest,
        ) -> ReconcileResult:
            return _result(
                adapter=adapter,
                request=request,
                complete=True,
                processed_rows=1,
            )

    result = reconcile_postgres_revision(
        reconciler=TerminalReconciler(),
        request_factory=lambda adapter: _request(
            adapter,  # type: ignore[arg-type]
            context=context,
            budget=budget,
        ),
        adapters=adapters,
    )

    assert result.postgres_snapshot_opened is False
    assert len(result.checkpoint_writes) == 2


def test_default_adapter_still_fails_closed_outside_revision_coordinator() -> None:
    adapter = DatasetColumnSourceAdapter()

    assert adapter.requires_postgres_snapshot is True
    with pytest.raises(PropertySourceError, match="revision snapshot session"):
        adapter.read_snapshot(context=_context(), budget=_budget())
