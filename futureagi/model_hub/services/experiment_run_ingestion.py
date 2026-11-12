"""Atomic ingestion of externally executed experiment-run results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from django.db import transaction

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.dataset_version_service import JsonValue
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _canonical,
    _envelope,
    _planned,
    _receipt,
    _scope,
    _uuid,
    claim_run_item,
    finalize_run,
    record_run_item_result,
    transition_run,
)
from tfc.routers import force_primary

_MAX_RESULTS = 100
_TERMINAL = frozenset({"completed", "failed", "skipped"})


@dataclass(frozen=True)
class ExternalRunResultInput:
    example_id: UUID
    trial_number: int
    receipt_key: str
    status: Literal["completed", "failed", "skipped"]
    output: JsonValue = None
    error: JsonValue = None
    trace_id: str | None = None
    root_span_id: str | None = None
    latency_ms: float | None = None
    token_usage: JsonValue = None
    cost: float | None = None
    score_refs: Sequence[Mapping[str, JsonValue]] = ()


@dataclass(frozen=True)
class ExternalRunIngestion:
    run: ExperimentRun
    items: tuple[ExperimentRunItem, ...]


def _load_run(
    *, run_id: UUID | str, organization_id: UUID | str, workspace_id: UUID | str | None
) -> ExperimentRun:
    try:
        return (
            ExperimentRun.no_workspace_objects.select_for_update(of=("self",))
            .select_related("dataset_version", "dataset_version__dataset")
            .filter(id=_uuid(run_id), **_scope(organization_id, workspace_id))
            .get()
        )
    except ExperimentRun.DoesNotExist as exc:
        raise RunWriteError("experiment run was not found in this tenant") from exc


def _normalise_results(
    results: Sequence[ExternalRunResultInput],
) -> list[ExternalRunResultInput]:
    if not isinstance(results, (list, tuple)) or not 1 <= len(results) <= _MAX_RESULTS:
        raise RunWriteError(f"result batch must contain 1 to {_MAX_RESULTS} items")
    normalised: list[ExternalRunResultInput] = []
    seen: set[tuple[str, int]] = set()
    total_bytes = 0
    for result in results:
        if not isinstance(result, ExternalRunResultInput):
            raise RunWriteError("each result must be an ExternalRunResultInput")
        example_id = _uuid(result.example_id)
        if type(result.trial_number) is not int or result.trial_number < 1:
            raise RunWriteError("trial_number must be a positive integer")
        identity = (example_id, result.trial_number)
        if identity in seen:
            raise RunWriteError("result identities must be unique within a batch")
        seen.add(identity)
        receipt_key = _receipt(result.receipt_key)
        if len(receipt_key) > 255:
            raise RunWriteError("receipt_key must not exceed 255 characters")
        checked = replace(result, example_id=UUID(example_id), receipt_key=receipt_key)
        if not isinstance(checked.score_refs, (list, tuple)):
            raise RunWriteError("score_refs must be a list")
        encoded = _canonical(
            {
                **vars(checked),
                "example_id": example_id,
                "score_refs": list(checked.score_refs),
            }
        )
        size = len(encoded.encode("utf-8"))
        total_bytes += size
        if size > 1048576:
            raise RunWriteError("result item must not exceed 1048576 bytes")
        normalised.append(checked)
    if total_bytes > 2097152:
        raise RunWriteError("result batch must not exceed 2097152 bytes")
    return normalised


def _claim_matches(item: ExperimentRunItem, receipt_key: str) -> bool:
    metadata = item.metadata if isinstance(item.metadata, Mapping) else {}
    envelope = metadata.get(_RUN_KEY)
    claim = envelope.get("claim_receipt") if isinstance(envelope, Mapping) else None
    return isinstance(claim, Mapping) and claim.get("receipt_key") == receipt_key


@force_primary()
@transaction.atomic
def ingest_external_run_results(
    *,
    run_id: UUID | str,
    organization_id: UUID | str,
    results: Sequence[ExternalRunResultInput],
    workspace_id: UUID | str | None = None,
    now: datetime | None = None,
) -> ExternalRunIngestion:
    """Atomically claim/write a bounded external result batch.

    A result retry is safe when its receipt and payload match the stored
    terminal item. A different receipt for an existing running/terminal item is
    refused. The outer transaction rolls back every item if one result fails.
    A run is finalized only when every planned identity has a terminal item.
    """
    normalised = _normalise_results(results)
    run = _load_run(
        run_id=run_id, organization_id=organization_id, workspace_id=workspace_id
    )
    if run.task_kind != ExperimentRun.TaskKind.EXTERNAL:
        raise RunWriteError("external result ingestion requires an external run")
    plan = _envelope(run.metadata)[_RUN_KEY].get("plan", {})
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("identities"), list)
        or len(plan["identities"]) > 10000
    ):
        raise RunWriteError("run plan must contain at most 10000 identities")
    expected = _planned(run)
    # Public evidence must identify a modern metric/version pin, never a legacy
    # version-only envelope that cannot prove which metric produced the score.
    bindings = {}
    if not isinstance(run.evaluator_versions, list):
        raise RunWriteError("evaluator bindings must be a list")
    for binding in run.evaluator_versions:
        if not isinstance(binding, Mapping):
            raise RunWriteError("evaluator binding must identify a metric and version")
        metric_id = _uuid(binding.get("metric_id"))
        if metric_id in bindings:
            raise RunWriteError("evaluator metric bindings must be unique")
        bindings[metric_id] = _uuid(binding.get("evaluator_version_id"))

    # Completed partial runs are sealed too; replays preserve their lifecycle.
    if run.completed_at is None and run.status == "draft":
        run = transition_run(
            run_id=run.id,
            status="queued",
            organization_id=organization_id,
            workspace_id=workspace_id,
            now=now,
        )
    if run.completed_at is None and run.status in {"queued", "partial"}:
        run = transition_run(
            run_id=run.id,
            status="running",
            organization_id=organization_id,
            workspace_id=workspace_id,
            now=now,
        )

    written: list[ExperimentRunItem] = []
    for result in normalised:
        for ref in result.score_refs:
            if not isinstance(ref, Mapping) or bindings.get(
                _uuid(ref.get("metric_id"))
            ) != _uuid(ref.get("evaluator_version_id")):
                raise RunWriteError(
                    "score reference metric/evaluator pair is not pinned"
                )
        item = (
            ExperimentRunItem.all_objects.select_for_update()
            .filter(
                run_id=run.id,
                example_id=result.example_id,
                trial_number=result.trial_number,
            )
            .first()
        )
        if item is not None and item.deleted:
            raise RunWriteError("deleted run item requires explicit recovery")
        if item is None:
            if run.completed_at is not None or run.status in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise RunWriteError("terminal run cannot accept a new result identity")
            item = claim_run_item(
                run_id=run.id,
                example_id=result.example_id,
                trial_number=result.trial_number,
                receipt_key=result.receipt_key,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        elif item.status == "running" and not _claim_matches(item, result.receipt_key):
            raise RunWriteError("result receipt does not own the running item")
        elif item.status not in _TERMINAL and item.status != "running":
            raise RunWriteError("run item is not ready for an external result")

        item = record_run_item_result(
            item_id=item.id,
            status=result.status,
            receipt_key=result.receipt_key,
            organization_id=organization_id,
            workspace_id=workspace_id,
            output=result.output,
            error=result.error,
            trace_id=result.trace_id,
            root_span_id=result.root_span_id,
            latency_ms=result.latency_ms,
            token_usage=result.token_usage,
            cost=result.cost,
            score_refs=result.score_refs,
        )
        written.append(item)

    latest = ExperimentRun.no_workspace_objects.get(id=run.id)
    if latest.completed_at is None and latest.status in {"running", "partial"}:
        items = list(
            ExperimentRunItem.no_workspace_objects.filter(
                run_id=latest.id, deleted=False
            )
            .only("example_id", "trial_number", "status")
            .order_by("id")[:10001]
        )
        if len(items) > 10000:
            raise RunWriteError("run has more than 10000 recorded items")
        actual = {(str(item.example_id), item.trial_number): item for item in items}
        if expected == actual.keys() and all(
            item.status in _TERMINAL for item in actual.values()
        ):
            latest = finalize_run(
                run_id=latest.id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                now=now,
            )
    return ExternalRunIngestion(run=latest, items=tuple(written))
