"""Execute a caller's Python task sequentially against a pinned external run.

A terminal item is never invoked again. A leftover running claim is indeterminate
and requires explicit recovery; arbitrary callback side effects are not exactly
once across a process crash. No scheduler, leases, or automatic retry is added.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from types import MappingProxyType
from typing import Literal, TypeAlias, TypedDict
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
FrozenJSON: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["FrozenJSON", ...]
    | Mapping[str, "FrozenJSON"]
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

EvaluatorStatus = Literal["assessed", "error", "unknown", "not_assessed", "unsupported"]


@dataclass(frozen=True)
class EvaluatorResult:
    status: EvaluatorStatus
    value: float | None = None
    explanation: str | None = None


@dataclass(frozen=True)
class EvaluatorContext:
    run_id: UUID
    example_id: UUID
    trial_number: int
    input: JSONValue
    output: JSONValue
    expected: JSONValue
    metric_id: UUID
    evaluator_version_id: UUID
    snapshot: Mapping[str, FrozenJSON]
    tracer: Tracer
    trace_id: str | None
    root_span_id: str | None


Evaluator: TypeAlias = Callable[[JSONValue, EvaluatorContext], EvaluatorResult]


def _immutable_json(value: JSONValue) -> FrozenJSON:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _immutable_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_immutable_json(item) for item in value)
    return value


def _evaluator_snapshots(run: ExperimentRun) -> list[Mapping[str, FrozenJSON]]:
    if not isinstance(run.evaluator_versions, list):
        raise RunWriteError("run evaluator_versions must be a list")
    _canonical(run.evaluator_versions)
    result: list[Mapping[str, FrozenJSON]] = []
    seen: set[str] = set()
    for raw in run.evaluator_versions:
        if not isinstance(raw, Mapping):
            raise RunWriteError("evaluator snapshot must be a mapping")
        metric_id = _uuid(raw.get("metric_id"))
        evaluator_version_id = _uuid(raw.get("evaluator_version_id"))
        if metric_id in seen:
            raise RunWriteError("run contains duplicate evaluator metric bindings")
        seen.add(metric_id)
        snapshot = dict(
            raw, metric_id=metric_id, evaluator_version_id=evaluator_version_id
        )
        result.append(
            MappingProxyType(
                {key: _immutable_json(value) for key, value in snapshot.items()}
            )
        )
    return result


def _evaluate_output(
    *,
    item: ExperimentRunItem,
    output: JSONValue,
    snapshots: list[Mapping[str, FrozenJSON]],
    evaluators: Mapping[str, Evaluator],
    tracer: Tracer,
    root_span_id: str | None,
) -> list[dict[str, JSONValue]]:
    refs: list[dict[str, JSONValue]] = []
    for snapshot in snapshots:
        metric_id = str(snapshot["metric_id"])
        version_id = str(snapshot["evaluator_version_id"])
        evaluator = evaluators.get(metric_id)
        if evaluator is None:
            refs.append(
                {
                    "metric_id": metric_id,
                    "evaluator_version_id": version_id,
                    "status": "not_assessed",
                    "explanation": "No callback supplied",
                }
            )
            continue
        with tracer.start_as_current_span(
            "futureagi.experiment.evaluator",
            attributes={
                "futureagi.metric.id": metric_id,
                "futureagi.evaluator.version_id": version_id,
            },
        ) as span:
            context = span.get_span_context()
            trace_id = f"{context.trace_id:032x}" if context.is_valid else None
            span_id = f"{context.span_id:016x}" if context.is_valid else None
            ref: dict[str, JSONValue] = {
                "metric_id": metric_id,
                "evaluator_version_id": version_id,
            }
            try:
                result = evaluator(
                    deepcopy(output),
                    EvaluatorContext(
                        item.run_id,
                        item.example_id,
                        item.trial_number,
                        deepcopy(item.input),
                        deepcopy(output),
                        deepcopy(item.expected),
                        UUID(metric_id),
                        UUID(version_id),
                        snapshot,
                        tracer,
                        trace_id,
                        root_span_id,
                    ),
                )
                if (
                    not isinstance(result, EvaluatorResult)
                    or not isinstance(result.status, str)
                    or result.status
                    not in {
                        "assessed",
                        "error",
                        "unknown",
                        "not_assessed",
                        "unsupported",
                    }
                ):
                    raise ValueError("evaluator callback returned an invalid result")
                if result.status == "assessed":
                    if (
                        isinstance(result.value, bool)
                        or not isinstance(result.value, (int, float))
                        or not math.isfinite(float(result.value))
                    ):
                        raise ValueError(
                            "assessed evaluator result must be finite numeric"
                        )
                    ref["value"] = float(result.value)
                ref["status"] = result.status
                if result.explanation is not None:
                    if not isinstance(result.explanation, str):
                        raise ValueError("evaluator explanation must be a string")
                    ref["explanation"] = result.explanation
                _canonical(ref)
                if result.status == "error":
                    span.set_status(Status(StatusCode.ERROR, result.explanation))
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                ref.pop("value", None)
                ref["status"] = "error"
                ref["explanation"] = (
                    str(error)
                    .encode("utf-8", "backslashreplace")
                    .decode("utf-8")
                    .replace("\x00", "\\u0000")
                )
            ref["trace_id"], ref["span_id"] = trace_id, span_id
            refs.append(ref)
    return refs


def _load_run(
    run_id: str, organization_id: UUID | str, workspace_id: UUID | str | None
) -> ExperimentRun:
    try:
        return ExperimentRun.no_workspace_objects.get(
            id=run_id, **_scope(organization_id, workspace_id)
        )
    except ExperimentRun.DoesNotExist as exc:
        raise RunWriteError("experiment run was not found in this tenant") from exc


class TaskResult(TypedDict, total=False):
    status: Literal["completed", "failed"]
    output: JSONValue
    error: dict[str, str]
    score_refs: list[dict[str, JSONValue]]
    latency_ms: float
    trace_id: str | None
    root_span_id: str | None


def _execute(
    item: ExperimentRunItem,
    task: Task,
    tracer: Tracer,
    snapshots: list[Mapping[str, FrozenJSON]],
    evaluators: Mapping[str, Evaluator],
) -> TaskResult:
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
            task_elapsed_ms = (perf_counter() - started) * 1000
            result: TaskResult = {
                "status": "failed",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        else:
            task_elapsed_ms = (perf_counter() - started) * 1000
            result = {"status": "completed", "output": output}
            result["score_refs"] = _evaluate_output(
                item=item,
                output=output,
                snapshots=snapshots,
                evaluators=evaluators,
                tracer=tracer,
                root_span_id=span_id,
            )
    return {
        **result,
        "trace_id": trace_id,
        "root_span_id": span_id,
        "latency_ms": task_elapsed_ms,
    }


@primary_required
def run_python_experiment(
    *,
    run_id: UUID | str,
    task: Task,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
    tracer: Tracer | None = None,
    evaluators: Mapping[UUID | str, Evaluator] | None = None,
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
    snapshots = _evaluator_snapshots(run)
    if evaluators is not None and not isinstance(evaluators, Mapping):
        raise RunWriteError("evaluators must map pinned metric IDs to callbacks")
    callbacks: dict[str, Evaluator] = {}
    for key, evaluator in (evaluators or {}).items():
        identifier = _uuid(key)
        if identifier in callbacks or not callable(evaluator):
            raise RunWriteError("evaluator callbacks must be unique and callable")
        callbacks[identifier] = evaluator
    pinned_metric_ids = {str(snapshot["metric_id"]) for snapshot in snapshots}
    unknown_evaluators = set(callbacks) - pinned_metric_ids
    if unknown_evaluators:
        raise RunWriteError("evaluator callback is not pinned on the run")
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
        result = _execute(item, task, tracer, snapshots, callbacks)
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
