"""Deliver a completed graph result after the adapter lost its response."""

from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import UUID

from opentelemetry.trace import NoOpTracer

from agent_playground.models import GraphExecution
from agent_playground.models.choices import GraphExecutionStatus
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _canonical,
    _digest,
    _envelope,
    _planned,
    _run,
    _scope,
    _uuid,
    record_run_item_result,
)
from model_hub.services.graph_version_task_adapter import (
    GraphRunIndeterminateError,
    _execution_id,
    _validate_version,
)
from model_hub.services.python_task_adapter import (
    _evaluate_output,
    _evaluator_snapshots,
)
from tfc.routers import primary_required


def _trace_pair(link: Mapping) -> tuple[str | None, str | None]:
    trace_id, root_span_id = link.get("trace_id"), link.get("root_span_id")
    if trace_id is None and root_span_id is None:
        return None, None
    if (
        not isinstance(trace_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", trace_id)
        or int(trace_id, 16) == 0
        or not isinstance(root_span_id, str)
        or not re.fullmatch(r"[0-9a-f]{16}", root_span_id)
        or int(root_span_id, 16) == 0
    ):
        raise RunWriteError("graph claim has invalid original trace context")
    return trace_id, root_span_id


@primary_required
def recover_graph_run_item(
    *,
    item_id: UUID | str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
) -> ExperimentRunItem:
    """Recover only completed persisted work; never dispatch or run evaluators.

    The caller may subsequently resume run_graph_experiment to process unclaimed
    plan entries and finalize. Running or ambiguous graphs still require waiting
    or operator investigation. Recovery does not cancel providers or workers.
    """
    scope = {"organization_id": organization_id, "workspace_id": workspace_id}
    item_id = _uuid(item_id)
    run_id = (
        ExperimentRun.no_workspace_objects.filter(
            **_scope(**scope), items__id=item_id, items__deleted=False
        )
        .values_list("id", flat=True)
        .first()
    )
    if run_id is None:
        raise RunWriteError("run item was not found in this tenant")
    with _run(run_id, **scope) as run:
        if run.task_kind != ExperimentRun.TaskKind.GRAPH:
            raise RunWriteError("recovery requires a graph run")
        if run.status == "cancelled":
            raise RunWriteError("cancelled run cannot recover graph results")
        version = _validate_version(run, organization_id)
        item = (
            ExperimentRunItem.no_workspace_objects.select_for_update()
            .filter(id=item_id, run=run, deleted=False)
            .first()
        )
        if item is None:
            raise RunWriteError("run item was not found in this tenant")
        if (str(item.example_id), item.trial_number) not in _planned(run):
            raise RunWriteError("item identity is not in the pinned run plan")
        membership = DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=run.dataset_version_id,
            example_id=item.example_id,
            deleted=False,
        ).first()
        if membership is None or _canonical([item.input, item.expected]) != _canonical(
            [membership.input, membership.expected]
        ):
            raise RunWriteError(
                "graph claim does not match immutable dataset membership"
            )
        metadata = _envelope(item.metadata)
        write = metadata[_RUN_KEY]
        link, claim = write.get("graph_execution"), write.get("claim_receipt")
        execution_id = _execution_id(run.id, item.example_id, item.trial_number)
        if (
            not isinstance(link, Mapping)
            or link.get("graph_execution_id") != str(execution_id)
            or link.get("workflow_id") != f"graph-run-{execution_id}"
            or not isinstance(claim, Mapping)
            or not isinstance(claim.get("receipt_key"), str)
            or not re.fullmatch(
                rf"graph:[0-9a-f]{{32}}:{item.example_id}:{item.trial_number}",
                claim["receipt_key"],
            )
        ):
            raise RunWriteError("item does not hold its original graph adapter claim")
        trace_id, root_span_id = _trace_pair(link)
        execution = (
            GraphExecution.no_workspace_objects.select_for_update()
            .filter(id=execution_id, deleted=False)
            .first()
        )
        if (
            execution is None
            or execution.graph_version_id != version.id
            or execution.parent_node_execution_id is not None
            or _canonical(execution.input_payload) != _canonical(item.input)
        ):
            raise RunWriteError("graph execution does not match the pinned claim")
        if execution.completed_at is None or execution.status not in {
            GraphExecutionStatus.SUCCESS,
            GraphExecutionStatus.FAILED,
        }:
            raise GraphRunIndeterminateError(
                "graph execution is not a completed result"
            )
        if execution.status == GraphExecutionStatus.SUCCESS and execution.error_message:
            raise RunWriteError(
                "successful graph execution contains conflicting error evidence"
            )
        succeeded = execution.status == GraphExecutionStatus.SUCCESS
        error = (
            None
            if succeeded
            else {
                "type": "graph_execution",
                "message": execution.error_message or "FAILED",
            }
        )
        score_refs = (
            _evaluate_output(
                item=item,
                output=execution.output_payload,
                snapshots=_evaluator_snapshots(run),
                evaluators={},
                tracer=NoOpTracer(),
                root_span_id=root_span_id,
            )
            if succeeded
            else []
        )
        payload = {
            "status": "completed" if succeeded else "failed",
            "output": execution.output_payload,
            "error": error,
            "trace_id": trace_id,
            "root_span_id": root_span_id,
            "score_refs": score_refs,
            "latency_ms": None,
            "cost": None,
            "token_usage": None,
        }
        evidence_hash = _digest(
            {
                "payload": payload,
                "graph_version_id": str(version.id),
                "completed_at": execution.completed_at.isoformat(),
                "claim": claim,
            }
        )
        receipt = f"graph-recovery:{execution_id}:{evidence_hash}"
        if item.status in {"completed", "failed", "skipped"}:
            if _digest({key: getattr(item, key) for key in payload}) != _digest(
                payload
            ):
                raise RunWriteError(
                    "terminal run item conflicts with graph recovery evidence"
                )
        return record_run_item_result(
            item_id=item.id, receipt_key=receipt, **payload, **scope
        )
