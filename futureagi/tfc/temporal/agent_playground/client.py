"""
Temporal client utilities for graph execution.

Domain-specific workflow starters and status checkers.
Uses the centralized client from tfc.temporal.common.
"""

from typing import Any
from uuid import UUID

from channels.db import database_sync_to_async
from django.utils import timezone

from tfc.temporal.common.client import (
    cancel_workflow_async,
    cancel_workflow_sync,
    get_workflow_status_async,
    get_workflow_status_sync,
    start_workflow_async,
    start_workflow_sync,
)

# =============================================================================
# Graph Execution Workflow Helpers
# =============================================================================


class GraphExecutionWorkflowStartError(RuntimeError):
    """Raised when a GraphExecution row exists but Temporal dispatch failed."""

    def __init__(self, graph_execution_id: str, original_error: Exception):
        self.graph_execution_id = graph_execution_id
        self.original_error = original_error
        super().__init__(
            f"Failed to start graph execution workflow for {graph_execution_id}: "
            f"{original_error}"
        )


def _get_graph_execution_workflow_id(graph_execution_id: str) -> str:
    """Generate workflow ID for a graph execution."""
    return f"graph-execution-{graph_execution_id}"


def _create_graph_execution(
    graph_version_id: UUID,
    input_payload: dict[str, Any],
) -> str:
    """
    Create a GraphExecution record in the database.

    Returns the graph_execution_id as string.
    """
    from agent_playground.models import GraphExecution
    from agent_playground.models.choices import GraphExecutionStatus

    graph_execution = GraphExecution.no_workspace_objects.create(
        graph_version_id=graph_version_id,
        status=GraphExecutionStatus.PENDING,
        input_payload=input_payload,
    )

    return str(graph_execution.id)


def _mark_graph_execution_start_failed(
    graph_execution_id: str,
    error: Exception,
) -> None:
    """Persist dispatch failure so execution polling never sees stale pending rows."""
    from agent_playground.models import GraphExecution
    from agent_playground.models.choices import GraphExecutionStatus

    now = timezone.now()
    GraphExecution.no_workspace_objects.filter(id=UUID(graph_execution_id)).update(
        status=GraphExecutionStatus.FAILED,
        started_at=now,
        completed_at=now,
        error_message=f"Failed to start graph execution workflow: {error}",
    )


async def start_graph_execution_async(
    graph_version_id: str,
    input_payload: dict[str, Any],
    max_concurrent_nodes: int = 10,
    task_queue: str = "tasks_l",
) -> str:
    """
    Start a graph execution workflow asynchronously.

    Args:
        graph_version_id: The graph version UUID to execute
        input_payload: Input data for the graph (keys match unconnected input port keys)
        max_concurrent_nodes: Maximum concurrent node execution
        task_queue: Temporal task queue to use

    Returns:
        The graph_execution_id
    """
    from tfc.temporal.agent_playground.workflows import (
        ExecuteGraphInput,
        GraphExecutionWorkflow,
    )

    # Create GraphExecution record
    graph_execution_id = await database_sync_to_async(_create_graph_execution)(
        graph_version_id=UUID(graph_version_id),
        input_payload=input_payload,
    )

    workflow_id = _get_graph_execution_workflow_id(graph_execution_id)

    try:
        await start_workflow_async(
            workflow_class=GraphExecutionWorkflow,
            workflow_input=ExecuteGraphInput(
                graph_execution_id=graph_execution_id,
                graph_version_id=graph_version_id,
                input_payload=input_payload,
                max_concurrent_nodes=max_concurrent_nodes,
                task_queue=task_queue,
                parent_node_execution_id=None,
            ),
            workflow_id=workflow_id,
            task_queue=task_queue,
        )
    except Exception as exc:
        await database_sync_to_async(_mark_graph_execution_start_failed)(
            graph_execution_id, exc
        )
        raise GraphExecutionWorkflowStartError(graph_execution_id, exc) from exc

    return graph_execution_id


def start_graph_execution(
    graph_version_id: str,
    input_payload: dict[str, Any],
    max_concurrent_nodes: int = 10,
    task_queue: str = "tasks_l",
) -> str:
    """
    Start a graph execution workflow synchronously.

    Convenience wrapper for Django views.

    Args:
        graph_version_id: The graph version UUID to execute
        input_payload: Input data for the graph
        max_concurrent_nodes: Maximum concurrent node execution
        task_queue: Temporal task queue to use

    Returns:
        The graph_execution_id
    """
    from tfc.temporal.agent_playground.workflows import (
        ExecuteGraphInput,
        GraphExecutionWorkflow,
    )

    # Create GraphExecution record
    graph_execution_id = _create_graph_execution(
        graph_version_id=UUID(graph_version_id),
        input_payload=input_payload,
    )

    workflow_id = _get_graph_execution_workflow_id(graph_execution_id)

    try:
        start_workflow_sync(
            workflow_class=GraphExecutionWorkflow,
            workflow_input=ExecuteGraphInput(
                graph_execution_id=graph_execution_id,
                graph_version_id=graph_version_id,
                input_payload=input_payload,
                max_concurrent_nodes=max_concurrent_nodes,
                task_queue=task_queue,
                parent_node_execution_id=None,
            ),
            workflow_id=workflow_id,
            task_queue=task_queue,
        )
    except Exception as exc:
        _mark_graph_execution_start_failed(graph_execution_id, exc)
        raise GraphExecutionWorkflowStartError(graph_execution_id, exc) from exc

    return graph_execution_id


async def get_graph_execution_status_async(
    graph_execution_id: str,
) -> dict | None:
    """
    Get the status of a graph execution workflow.

    Args:
        graph_execution_id: The graph execution UUID

    Returns:
        Dict with workflow status info, or None if not found
    """
    workflow_id = _get_graph_execution_workflow_id(graph_execution_id)
    return await get_workflow_status_async(workflow_id)


def get_graph_execution_status(graph_execution_id: str) -> dict | None:
    """
    Get graph execution workflow status synchronously.

    Args:
        graph_execution_id: The graph execution UUID

    Returns:
        Dict with workflow status info, or None if not found
    """
    workflow_id = _get_graph_execution_workflow_id(graph_execution_id)
    return get_workflow_status_sync(workflow_id)


async def cancel_graph_execution_async(graph_execution_id: str) -> bool:
    """
    Cancel a running graph execution workflow.

    Args:
        graph_execution_id: The graph execution UUID

    Returns:
        True if cancellation was requested, False if workflow not found
    """
    workflow_id = _get_graph_execution_workflow_id(graph_execution_id)
    return await cancel_workflow_async(workflow_id)


def cancel_graph_execution(graph_execution_id: str) -> bool:
    """
    Cancel graph execution workflow synchronously.

    Args:
        graph_execution_id: The graph execution UUID

    Returns:
        True if cancellation was requested, False if workflow not found
    """
    workflow_id = _get_graph_execution_workflow_id(graph_execution_id)
    return cancel_workflow_sync(workflow_id)


__all__ = [
    "GraphExecutionWorkflowStartError",
    "start_graph_execution",
    "start_graph_execution_async",
    "get_graph_execution_status",
    "get_graph_execution_status_async",
    "cancel_graph_execution",
    "cancel_graph_execution_async",
]
