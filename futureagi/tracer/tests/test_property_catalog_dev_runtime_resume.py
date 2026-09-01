from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    ManifestStreamRole,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CheckedInPropertyCatalogDevRuntime,
    PropertyCatalogDevRuntimeError,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import StreamStart
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CatalogCheckpoint,
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    CheckpointWrite,
    ReconcileMode,
    ReconcileRequest,
    ReconcileResult,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    SourceReadBudget,
    SourceSnapshot,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER_WORKSPACE = "99999999-9999-4999-8999-999999999999"
PROJECT = "33333333-3333-4333-8333-333333333333"
BUILD = "44444444-4444-4444-8444-444444444444"
SYSTEM_STREAM = "55555555-5555-4555-8555-555555555555"
SPAN_STREAM = "66666666-6666-4666-8666-666666666666"
NOW = datetime(2026, 8, 15, 12, tzinfo=UTC)
SOURCE_DIGEST = "a" * 64
EMITTED_DIGEST = "b" * 64
TERMINAL_PAYLOAD = "c" * 64


def _context() -> PostgresSnapshotContext:
    return PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=1,
        projection_version=1,
        snapshot_cutoff=NOW,
    )


def _completed_resume(
    *,
    source_adapter: SourceAdapter = SourceAdapter.SYSTEM_MANIFEST,
    producer_stream_id: str = SYSTEM_STREAM,
    source_version: int = 11,
) -> CheckpointWrite:
    checkpoint = CatalogCheckpoint(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=1,
        build_token=BUILD,
        projection_version=1,
        source_adapter=source_adapter,
        producer_stream_id=producer_stream_id,
        source_version_fence=source_version,
        status=CheckpointStatus.COMPLETE,
        terminal=True,
        source_count=3,
        definition_count=3,
        value_count=0,
        tombstone_count=0,
        gap_count=0,
        poison_count=0,
        conflict_count=0,
        first_sequence=1,
        last_sequence=2,
        last_issued_sequence=2,
        fenced_sequence=2,
        terminal_payload_sha256=TERMINAL_PAYLOAD,
        delivery_count=2,
        source_digest=SOURCE_DIGEST,
        emitted_digest=EMITTED_DIGEST,
    )
    return CheckpointWrite(
        checkpoint=checkpoint,
        source_cursor="",
        watermark="persisted-terminal-watermark",
        source_version_fence=source_version,
        source_fingerprint=SOURCE_DIGEST,
        previous_payload_sha256=TERMINAL_PAYLOAD,
        processed_rows=3,
    )


def _terminal_result(
    *,
    adapter: SourceAdapter,
    request: ReconcileRequest,
) -> ReconcileResult:
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
        terminal_payload_sha256=TERMINAL_PAYLOAD,
        delivery_count=1,
        source_digest=SOURCE_DIGEST,
        emitted_digest=EMITTED_DIGEST,
    )
    write = CheckpointWrite(
        checkpoint=checkpoint,
        source_cursor="",
        watermark="",
        source_version_fence=request.source_version,
        source_fingerprint=SOURCE_DIGEST,
        previous_payload_sha256=TERMINAL_PAYLOAD,
        processed_rows=0,
    )
    return ReconcileResult(
        snapshot=SourceSnapshot(
            source_adapter=adapter,
            records=(),
            next_cursor=None,
            terminal=True,
            source_count=0,
            source_bytes=0,
            source_digest=SOURCE_DIGEST,
            page_count=1,
        ),
        envelopes=(),
        payload_sha256s=(TERMINAL_PAYLOAD,),
        checkpoint_write=write,
    )


class _PreparedRestart:
    resumed = True

    def __init__(self, starts: dict[SourceAdapter, StreamStart]) -> None:
        self._starts = starts

    def stream(
        self,
        source_adapter: SourceAdapter,
        role: ManifestStreamRole,
    ) -> StreamStart:
        assert role is ManifestStreamRole.DEFINITIONS
        return self._starts[source_adapter]


class _Execution:
    def __init__(
        self,
        *,
        system_resume: CheckpointWrite,
        reconciler: Any,
    ) -> None:
        self.context = _context()
        self.lease = SimpleNamespace(build_token=BUILD)
        self.emitted_at = NOW
        self.mode = ReconcileMode.FULL_REPAIR
        self.source_budget = SourceReadBudget()
        self.definition_results: dict[SourceAdapter, ReconcileResult] = {}
        self.checkpoints: dict[tuple[SourceAdapter, str], CatalogCheckpoint] = {}
        self.reconciler = reconciler
        self._streams = {
            SourceAdapter.SYSTEM_MANIFEST: SimpleNamespace(
                producer_stream_id=SYSTEM_STREAM,
                source_version_fence=11,
            ),
            SourceAdapter.SPAN_ATTRIBUTE: SimpleNamespace(
                producer_stream_id=SPAN_STREAM,
                source_version_fence=17,
            ),
        }
        self.prepared = _PreparedRestart(
            {
                SourceAdapter.SYSTEM_MANIFEST: StreamStart(
                    source_adapter=SourceAdapter.SYSTEM_MANIFEST,
                    role=ManifestStreamRole.DEFINITIONS,
                    producer_stream_id=SYSTEM_STREAM,
                    lower_watermark="",
                    resume=system_resume,
                ),
                SourceAdapter.SPAN_ATTRIBUTE: StreamStart(
                    source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                    role=ManifestStreamRole.DEFINITIONS,
                    producer_stream_id=SPAN_STREAM,
                    lower_watermark="",
                    resume=None,
                ),
            }
        )

    def stream(
        self,
        source_adapter: SourceAdapter,
        role: ManifestStreamRole,
    ) -> Any:
        assert role is ManifestStreamRole.DEFINITIONS
        return self._streams[source_adapter]


class _ForbiddenSourceAdapter:
    def __init__(self, source_adapter: SourceAdapter) -> None:
        self.source_adapter = source_adapter

    def read_snapshot(self, **_kwargs: Any) -> SourceSnapshot:
        raise AssertionError("a completed source must not be read during recovery")


def _runtime() -> CheckedInPropertyCatalogDevRuntime:
    return object.__new__(CheckedInPropertyCatalogDevRuntime)


def test_span_definition_request_drops_prior_revision_watermark_but_keeps_resume() -> (
    None
):
    persisted = _completed_resume(
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=SPAN_STREAM,
        source_version=17,
    )
    execution = _Execution(
        system_resume=_completed_resume(),
        reconciler=object(),
    )
    execution.prepared._starts[SourceAdapter.SPAN_ATTRIBUTE] = StreamStart(
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        role=ManifestStreamRole.DEFINITIONS,
        producer_stream_id=SPAN_STREAM,
        lower_watermark="prior-revision-watermark",
        resume=persisted,
    )

    request = _runtime()._definition_request(
        execution,  # type: ignore[arg-type]
        SourceAdapter.SPAN_ATTRIBUTE,
    )

    assert request.lower_watermark == ""
    assert request.resume is persisted


def test_restart_rehydrates_complete_system_then_runs_missing_span_definition() -> None:
    calls: list[tuple[SourceAdapter, CheckpointWrite | None]] = []

    class RemainingOnlyReconciler:
        def reconcile(
            self,
            adapter: _ForbiddenSourceAdapter,
            request: ReconcileRequest,
        ) -> ReconcileResult:
            if adapter.source_adapter is SourceAdapter.SYSTEM_MANIFEST:
                raise AssertionError(
                    "completed system definition must not be read or republished"
                )
            calls.append((adapter.source_adapter, request.resume))
            return _terminal_result(
                adapter=adapter.source_adapter,
                request=request,
            )

    persisted = _completed_resume()
    execution = _Execution(
        system_resume=persisted,
        reconciler=RemainingOnlyReconciler(),
    )
    runtime = _runtime()

    system = runtime._run_definition_adapter(
        execution,  # type: ignore[arg-type]
        _ForbiddenSourceAdapter(SourceAdapter.SYSTEM_MANIFEST),
    )
    span = runtime._run_definition_adapter(
        execution,  # type: ignore[arg-type]
        _ForbiddenSourceAdapter(SourceAdapter.SPAN_ATTRIBUTE),
    )

    assert system.checkpoint_write is persisted
    assert system.snapshot.source_adapter is SourceAdapter.SYSTEM_MANIFEST
    assert system.snapshot.records == ()
    assert system.snapshot.page_count == 0
    assert system.envelopes == ()
    assert system.payload_sha256s == ()
    assert calls == [(SourceAdapter.SPAN_ATTRIBUTE, None)]
    assert span.complete is True
    assert set(execution.definition_results) == {
        SourceAdapter.SYSTEM_MANIFEST,
        SourceAdapter.SPAN_ATTRIBUTE,
    }
    assert set(execution.checkpoints) == {
        persisted.checkpoint.key,
        span.checkpoint_write.checkpoint.key,
    }


@pytest.mark.parametrize(
    ("variant", "message"),
    (
        ("scope", "changed scope"),
        ("counts", "inconsistent evidence"),
        ("chain", "inconsistent evidence"),
        ("terminal", "unsafe terminal evidence"),
    ),
)
def test_completed_non_postgres_resume_fails_closed_before_reconcile(
    variant: str,
    message: str,
) -> None:
    persisted = _completed_resume()
    if variant == "scope":
        persisted = replace(
            persisted,
            checkpoint=replace(
                persisted.checkpoint,
                workspace_id=OTHER_WORKSPACE,
            ),
        )
    elif variant == "counts":
        persisted = replace(
            persisted,
            checkpoint=replace(persisted.checkpoint, value_count=1),
        )
    elif variant == "chain":
        persisted = replace(persisted, previous_payload_sha256="d" * 64)
    else:
        persisted = replace(
            persisted,
            checkpoint=replace(persisted.checkpoint, terminal=False),
        )

    class ForbiddenReconciler:
        def reconcile(self, *_args: Any, **_kwargs: Any) -> ReconcileResult:
            raise AssertionError(
                "invalid completed evidence must fail before reconcile"
            )

    execution = _Execution(
        system_resume=persisted,
        reconciler=ForbiddenReconciler(),
    )

    with pytest.raises(PropertyCatalogDevRuntimeError, match=message):
        _runtime()._run_definition_adapter(
            execution,  # type: ignore[arg-type]
            _ForbiddenSourceAdapter(SourceAdapter.SYSTEM_MANIFEST),
        )
