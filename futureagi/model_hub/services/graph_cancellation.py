"""Fence a graph-backed experiment and request cancellation of live workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _envelope,
    _planned,
    _run,
    _save_run,
    _uuid,
)
from tfc.routers import primary_required
from tfc.temporal.common.client import _run_async_in_sync_context, get_client


@dataclass(frozen=True)
class GraphCancellationResult:
    run_id: str
    status: str
    requested_workflows: tuple[str, ...] = ()
    cancelled_workflows: tuple[str, ...] = ()
    missing_workflows: tuple[str, ...] = ()
    control_errors: tuple[str, ...] = ()


def _workflow_ids(items: QuerySet[ExperimentRunItem]) -> tuple[str, ...]:
    result: set[str] = set()
    for item in items:
        metadata = _envelope(item.metadata)
        link = metadata[_RUN_KEY].get("graph_execution")
        workflow_id = link.get("workflow_id") if isinstance(link, Mapping) else None
        if isinstance(workflow_id, str) and workflow_id.strip():
            result.add(workflow_id)
    return tuple(sorted(result))


async def _request_cancellation(
    workflow_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if not workflow_ids:
        return (), (), ()
    try:
        client = await get_client()
    except Exception as exc:
        return (), (), (f"client:{type(exc).__name__}",)
    cancelled: list[str] = []
    missing: list[str] = []
    errors: list[str] = []
    for workflow_id in workflow_ids:
        try:
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            status = getattr(description, "status", None)
            status_name = getattr(status, "name", str(status).rsplit(".", 1)[-1])
            if status_name != "RUNNING":
                missing.append(workflow_id)
                continue
            await handle.cancel()
            cancelled.append(workflow_id)
        except Exception as exc:
            # Keep control diagnostics bounded and do not persist provider data.
            errors.append(f"{workflow_id}:{type(exc).__name__}")
    return tuple(cancelled), tuple(missing), tuple(errors)


@primary_required
def cancel_graph_experiment(
    *,
    run_id: UUID | str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
) -> GraphCancellationResult:
    """Durably fence a graph run, then request Temporal workflow cancellation.

    The DB transaction never spans a Temporal call. Durable cancellation and the
    existing result-write fence are authoritative if a workflow is already gone
    or an activity reaches an upstream provider before its cancellation boundary.
    """
    scope = {"organization_id": organization_id, "workspace_id": workspace_id}
    run_id = _uuid(run_id)
    with _run(run_id, **scope) as run:
        if run.task_kind != ExperimentRun.TaskKind.GRAPH:
            raise RunWriteError("cancellation requires a graph run")
        if run.status == "cancelled":
            status = run.status
            workflow_ids = ()
        elif run.completed_at is not None or run.status in {"completed", "failed"}:
            return GraphCancellationResult(str(run.id), run.status)
        else:
            _planned(run)
            workflow_ids = _workflow_ids(
                ExperimentRunItem.no_workspace_objects.filter(
                    run=run, deleted=False, status="running"
                )
            )
            run.status = "cancelled"
            run.completed_at = timezone.now()
            _save_run(run, ["status", "completed_at"])
            status = "cancelled"
    cancelled, missing, errors = _run_async_in_sync_context(
        lambda: _request_cancellation(workflow_ids)
    )
    return GraphCancellationResult(
        str(run_id), status, workflow_ids, cancelled, missing, errors
    )
