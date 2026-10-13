"""Execute a caller's Python task sequentially against a pinned external run.

A terminal item is never invoked again. A leftover running claim is indeterminate
and requires explicit recovery; arbitrary callback side effects are not exactly
once across a process crash. No scheduler, leases, or automatic retry is added.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import TypeAlias
from uuid import UUID, uuid4

from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode, Tracer

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import (
    RunWriteError,
    _canonical,
    _planned,
    _scope,
    _uuid,
    claim_run_item,
    finalize_run,
    record_run_item_result,
    transition_run,
)
from tfc.routers import primary_required
from tfc.telemetry import get_tracer

JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)


@dataclass(frozen=True)
class TaskContext:
    run_id: UUID
    example_id: UUID
    trial_number: int
    expected: JSONValue
    metadata: Mapping[str, JSONValue]
    tracer: Tracer
    trace_id: str | None
    root_span_id: str | None


Task: TypeAlias = Callable[[JSONValue, TaskContext], JSONValue]


def _load_run(
    run_id: str, organization_id: UUID | str, workspace_id: UUID | str | None
) -> ExperimentRun:
    try:
        return ExperimentRun.no_workspace_objects.get(
            id=run_id, **_scope(organization_id, workspace_id)
        )
    except ExperimentRun.DoesNotExist as exc:
        raise RunWriteError("experiment run was not found in this tenant") from exc


def _execute(item: ExperimentRunItem, task: Task, tracer: Tracer) -> dict:
    started = perf_counter()
    with tracer.start_as_current_span(
        "futureagi.experiment.python_task",
        context=Context(),
        attributes={
            "futureagi.run.id": str(item.run_id),
            "futureagi.example.id": str(item.example_id),
            "futureagi.trial.number": item.trial_number,
        },
    ) as span:
        span_context = span.get_span_context()
        trace_id = f"{span_context.trace_id:032x}" if span_context.is_valid else None
        span_id = f"{span_context.span_id:016x}" if span_context.is_valid else None
        metadata = {
            key: deepcopy(value)
            for key, value in item.metadata.items()
            if key != "_futureagi_run_write"
        }
        context = TaskContext(
            item.run_id,
            item.example_id,
            item.trial_number,
            deepcopy(item.expected),
            metadata,
            tracer,
            trace_id,
            span_id,
        )
        try:
            output = task(deepcopy(item.input), context)
            _canonical(output)
        except (
            Exception
        ) as exc:  # Arbitrary callback errors are per-case task failures.
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            result = {
                "status": "failed",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        else:
            result = {"status": "completed", "output": output}
    return {
        **result,
        "trace_id": trace_id,
        "root_span_id": span_id,
        "latency_ms": (perf_counter() - started) * 1000,
    }


@primary_required
def run_python_experiment(
    *,
    run_id: UUID | str,
    task: Task,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
    tracer: Tracer | None = None,
) -> ExperimentRun:
    """Execute an existing external run without retrying an owned/terminal item.

    Cancellation is checked before and after each callback. An in-flight
    synchronous callback cannot be preempted; cancellation prevents its result
    write and subsequent callbacks. The caller supplies an authenticated tenant
    scope and runs this function outside a database transaction.
    """
    if not callable(task):
        raise TypeError("task must be callable")
    tracer = get_tracer(__name__) if tracer is None else tracer
    if not isinstance(tracer, Tracer):
        raise TypeError("tracer must be an OpenTelemetry Tracer")
    run_id = _uuid(run_id)
    run = _load_run(run_id, organization_id, workspace_id)
    if run.task_kind != ExperimentRun.TaskKind.EXTERNAL:
        raise RunWriteError("Python task adapter requires an external run")
    if run.completed_at is not None or run.status in {
        "completed",
        "failed",
        "cancelled",
    }:
        return run
    scope = {"organization_id": organization_id, "workspace_id": workspace_id}
    try:
        if run.status == "draft":
            run = transition_run(run_id=run_id, status="queued", **scope)
        if run.status == "queued":
            run = transition_run(run_id=run_id, status="running", **scope)
    except RunWriteError:
        latest = _load_run(run_id, **scope)
        if latest.status == "cancelled":
            return latest
        raise

    invocation = uuid4().hex
    for example_id, trial in sorted(_planned(run)):
        latest = _load_run(run_id, **scope)
        if latest.completed_at is not None or latest.status == "cancelled":
            return latest
        item = ExperimentRunItem.no_workspace_objects.filter(
            run_id=run_id, example_id=example_id, trial_number=trial, deleted=False
        ).first()
        if item is not None and item.status in {"completed", "failed", "skipped"}:
            continue
        if (
            item is None
            and ExperimentRunItem.all_objects.filter(
                run_id=run_id, example_id=example_id, trial_number=trial, deleted=True
            ).exists()
        ):
            raise RunWriteError("deleted run item requires explicit recovery")
        receipt = f"python:{invocation}:{example_id}:{trial}"
        item = claim_run_item(
            run_id=run_id,
            example_id=example_id,
            trial_number=trial,
            receipt_key=receipt,
            **scope,
        )
        result = _execute(item, task, tracer)
        latest = _load_run(run_id, **scope)
        if latest.status == "cancelled":
            return latest
        try:
            record_run_item_result(
                item_id=item.pk, receipt_key=receipt, **result, **scope
            )
        except RunWriteError:
            latest = _load_run(run_id, **scope)
            if latest.status == "cancelled":
                return latest
            raise
    latest = _load_run(run_id, **scope)
    if latest.status == "cancelled":
        return latest
    return finalize_run(run_id=run_id, **scope)
