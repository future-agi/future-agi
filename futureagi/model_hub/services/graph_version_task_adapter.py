"""Run a pinned GraphVersion through the existing graph workflow."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4, uuid5

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode, Tracer
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from agent_playground.models import GraphExecution, GraphVersion
from agent_playground.models.choices import GraphExecutionStatus, GraphVersionStatus
from agent_playground.services.engine import GraphAnalyzer
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _canonical,
    _planned,
    _uuid,
    claim_run_item,
    finalize_run,
    record_run_item_result,
    transition_run,
)
from model_hub.services.python_task_adapter import (
    Evaluator,
    _evaluate_output,
    _evaluator_snapshots,
    _load_run,
)
from tfc.routers import primary_required
from tfc.telemetry import get_tracer
from tfc.temporal.agent_playground.types import ExecuteGraphInput
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow
from tfc.temporal.common.client import _run_async_in_sync_context, get_client


class GraphRunIndeterminateError(RuntimeError):
    """The graph may still be running; retrying could duplicate side effects."""


@dataclass(frozen=True)
class GraphExecutionResult:
    status: str
    output_payload: Any = None
    error: Any = None


def _execution_id(run_id: UUID, example_id: UUID, trial_number: int) -> UUID:
    return uuid5(run_id, f"{example_id}:{trial_number}")


def _default_executor(
    *,
    graph_execution_id: str,
    graph_version_id: str,
    input_payload: dict[str, Any],
    task_queue: str,
    timeout_seconds: float,
) -> GraphExecutionResult:
    async def execute() -> GraphExecutionResult:
        client = await get_client()
        try:
            handle = await client.start_workflow(
                GraphExecutionWorkflow.run,
                ExecuteGraphInput(
                    graph_execution_id=graph_execution_id,
                    graph_version_id=graph_version_id,
                    input_payload=input_payload,
                    task_queue=task_queue,
                ),
                id=f"graph-run-{graph_execution_id}",
                task_queue=task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
        except Exception as exc:
            raise GraphRunIndeterminateError(
                f"could not dispatch graph execution: {exc}"
            ) from exc
        try:
            result = await asyncio.wait_for(handle.result(), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise GraphRunIndeterminateError("graph execution timed out") from exc
        except Exception as exc:
            raise GraphRunIndeterminateError(
                f"graph execution ended without a result: {exc}"
            ) from exc
        status = result.get("status") if isinstance(result, dict) else result.status
        output = (
            result.get("output_payload")
            if isinstance(result, dict)
            else result.output_payload
        )
        error = result.get("error") if isinstance(result, dict) else result.error
        if status not in {"SUCCESS", "FAILED", "CANCELLED"}:
            raise GraphRunIndeterminateError("graph execution returned unknown status")
        return GraphExecutionResult(status, output, error)

    return _run_async_in_sync_context(execute)


def _validate_version(run: ExperimentRun, organization_id: UUID | str) -> GraphVersion:
    dataset = run.dataset_version.dataset
    workspace_scope = Q(graph__workspace_id=dataset.workspace_id)
    if dataset.workspace_id and dataset.workspace.is_default:
        workspace_scope |= Q(graph__workspace__isnull=True)
    version = (
        GraphVersion.no_workspace_objects.filter(
            workspace_scope,
            id=run.agent_version_id,
            deleted=False,
            status__in=(GraphVersionStatus.ACTIVE, GraphVersionStatus.INACTIVE),
            graph__deleted=False,
            graph__organization_id=organization_id,
        )
        .select_related("graph")
        .first()
    )
    if version is None:
        raise RunWriteError("pinned graph version was not found in this tenant")
    return version


def _validate_input(topology, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("graph task input must be a JSON object")
    ports = topology.unconnected_input_ports
    unknown = set(value) - {port.routing_key for port in ports}
    missing = {
        port.routing_key
        for port in ports
        if port.required
        and port.default_value is None
        and port.routing_key not in value
    }
    if unknown:
        raise ValueError(f"graph task input has unknown keys: {sorted(unknown)}")
    if missing:
        raise ValueError(f"graph task input is missing keys: {sorted(missing)}")
    return value


def _callbacks(
    evaluators: Mapping[UUID | str, Evaluator] | None,
) -> dict[str, Evaluator]:
    if evaluators is not None and not isinstance(evaluators, Mapping):
        raise RunWriteError("evaluators must map pinned metric IDs to callbacks")
    callbacks: dict[str, Evaluator] = {}
    for raw_id, callback in (evaluators or {}).items():
        metric_id = _uuid(raw_id)
        if metric_id in callbacks or not callable(callback):
            raise RunWriteError("evaluator callbacks must be unique and callable")
        callbacks[metric_id] = callback
    return callbacks


@primary_required
def run_graph_experiment(
    *,
    run_id: UUID | str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
    task_queue: str = "tasks_l",
    timeout_seconds: float = 3600,
    tracer: Tracer | None = None,
    evaluators: Mapping[UUID | str, Evaluator] | None = None,
) -> ExperimentRun:
    """Execute sequentially outside a caller transaction.

    A terminal item is not run again. A timeout/dispatch ambiguity or leftover
    running claim raises GraphRunIndeterminateError without restarting work.
    Existing graph-node activity retries still apply; this is not an exactly-once
    side-effect guarantee. Cancellation is observed between and after graph calls.
    """
    if connection.in_atomic_block:
        raise RunWriteError("graph adapter must run outside a database transaction")
    if (
        not isinstance(task_queue, str)
        or not task_queue.strip()
        or len(task_queue) > 255
    ):
        raise ValueError(
            "task_queue must be a nonempty string of at most 255 characters"
        )
    try:
        finite_timeout = math.isfinite(float(timeout_seconds))
    except (ValueError, TypeError, OverflowError):
        finite_timeout = False
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not finite_timeout
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive finite number")
    tracer = get_tracer(__name__) if tracer is None else tracer
    if not isinstance(tracer, Tracer):
        raise TypeError("tracer must be an OpenTelemetry Tracer")
    callbacks = _callbacks(evaluators)
    run_id = UUID(str(run_id))
    run = _load_run(str(run_id), organization_id, workspace_id)
    if run.task_kind != ExperimentRun.TaskKind.GRAPH:
        raise RunWriteError("GraphVersion adapter requires a graph run")
    if run.completed_at is not None or run.status in {
        "completed",
        "failed",
        "cancelled",
    }:
        return run
    version = _validate_version(run, organization_id)
    topology = GraphAnalyzer.analyze(version.id)
    snapshots = _evaluator_snapshots(run)
    pinned_metrics = {str(snapshot["metric_id"]) for snapshot in snapshots}
    if set(callbacks) - pinned_metrics:
        raise RunWriteError("evaluator callback is not pinned on the run")
    scope = {"organization_id": organization_id, "workspace_id": workspace_id}
    if run.status == "draft":
        run = transition_run(run_id=run_id, status="queued", **scope)
    if run.status == "queued":
        run = transition_run(run_id=run_id, status="running", **scope)
    invocation = uuid4().hex
    for example_id, trial_number in sorted(_planned(run)):
        latest = _load_run(str(run_id), **scope)
        if latest.completed_at is not None or latest.status == "cancelled":
            return latest
        receipt = f"graph:{invocation}:{example_id}:{trial_number}"
        execution_id = _execution_id(run_id, UUID(example_id), trial_number)
        # Keep the claim and GraphExecution creation atomic. A different
        # invocation cannot reuse an active receipt or replace this execution.
        with transaction.atomic():
            previous = ExperimentRunItem.all_objects.filter(
                run_id=run_id, example_id=example_id, trial_number=trial_number
            ).first()
            if previous is not None:
                if not previous.deleted and previous.status in {
                    "completed",
                    "failed",
                    "skipped",
                }:
                    continue
                raise GraphRunIndeterminateError(
                    "run item is already claimed; explicit recovery required"
                )
            try:
                item = claim_run_item(
                    run_id=run_id,
                    example_id=example_id,
                    trial_number=trial_number,
                    receipt_key=receipt,
                    **scope,
                )
            except RunWriteError as exc:
                latest = _load_run(str(run_id), **scope)
                if latest.status == "cancelled":
                    return latest
                raise GraphRunIndeterminateError(str(exc)) from exc
            item.metadata[_RUN_KEY]["graph_execution"] = {
                "graph_execution_id": str(execution_id),
                "workflow_id": f"graph-run-{execution_id}",
            }
            item.save(update_fields=["metadata"])
            try:
                input_payload = _validate_input(topology, item.input)
                _canonical(input_payload)
            except ValueError as exc:
                record_run_item_result(
                    item_id=item.id,
                    receipt_key=receipt,
                    status="failed",
                    error={"type": "input_validation", "message": str(exc)},
                    **scope,
                )
                continue
            try:
                with transaction.atomic():
                    GraphExecution.no_workspace_objects.create(
                        id=execution_id,
                        graph_version_id=version.id,
                        status=GraphExecutionStatus.PENDING,
                        input_payload=input_payload,
                    )
            except IntegrityError as exc:
                raise GraphRunIndeterminateError(
                    "stable graph execution ID already exists; explicit recovery required"
                ) from exc
        started = perf_counter()
        with tracer.start_as_current_span(
            "futureagi.experiment.graph_task",
            context=Context(),
            attributes={
                "futureagi.run.id": str(run_id),
                "futureagi.example.id": example_id,
                "futureagi.trial.number": trial_number,
                "futureagi.graph_execution.id": str(execution_id),
            },
        ) as span:
            span_context = span.get_span_context()
            trace_id = (
                f"{span_context.trace_id:032x}" if span_context.is_valid else None
            )
            root_span_id = (
                f"{span_context.span_id:016x}" if span_context.is_valid else None
            )
            try:
                _validate_version(run, organization_id)
                if _load_run(str(run_id), **scope).status == "cancelled":
                    return _load_run(str(run_id), **scope)
                result = _default_executor(
                    graph_execution_id=str(execution_id),
                    graph_version_id=str(version.id),
                    input_payload=input_payload,
                    task_queue=task_queue,
                    timeout_seconds=float(timeout_seconds),
                )
            except GraphRunIndeterminateError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise GraphRunIndeterminateError(str(exc)) from exc
            latest = _load_run(str(run_id), **scope)
            if latest.status == "cancelled":
                return latest
            persisted = (
                GraphExecution.no_workspace_objects.filter(id=execution_id)
                .values_list("status", "output_payload", "error_message")
                .first()
            )
            expected_persisted_status = (
                GraphExecutionStatus.SUCCESS
                if result.status == "SUCCESS"
                else GraphExecutionStatus.FAILED
            )
            if persisted is None or persisted[0] != expected_persisted_status:
                raise GraphRunIndeterminateError(
                    "graph execution result does not match persisted execution"
                )
            if result.status == "SUCCESS" and persisted[1] != result.output_payload:
                raise GraphRunIndeterminateError(
                    "graph execution output does not match persisted execution"
                )
            if result.status != "SUCCESS" and persisted[2] and result.error is None:
                result = GraphExecutionResult(result.status, result.output_payload, persisted[2])
            task_latency = (perf_counter() - started) * 1000
            if result.status == "SUCCESS":
                _canonical(result.output_payload)
                score_refs = _evaluate_output(
                    item=item,
                    output=result.output_payload,
                    snapshots=snapshots,
                    evaluators=callbacks,
                    tracer=tracer,
                    root_span_id=root_span_id,
                )
                payload = {
                    "status": "completed",
                    "output": result.output_payload,
                    "score_refs": score_refs,
                }
            else:
                span.set_status(
                    Status(StatusCode.ERROR, str(result.error or result.status))
                )
                payload = {
                    "status": "failed",
                    "output": result.output_payload,
                    "error": {
                        "type": "graph_execution",
                        "message": str(result.error or result.status),
                    },
                }
            latest = _load_run(str(run_id), **scope)
            if latest.status == "cancelled":
                return latest
            try:
                record_run_item_result(
                    item_id=item.id,
                    receipt_key=receipt,
                    trace_id=trace_id,
                    root_span_id=root_span_id,
                    latency_ms=task_latency,
                    **payload,
                    **scope,
                )
            except RunWriteError:
                latest = _load_run(str(run_id), **scope)
                if latest.status == "cancelled":
                    return latest
                raise
    latest = _load_run(str(run_id), **scope)
    if latest.status == "cancelled":
        return latest
    return finalize_run(run_id=run_id, **scope)
