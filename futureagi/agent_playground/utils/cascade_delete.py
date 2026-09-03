from __future__ import annotations

import structlog
from django.utils import timezone

from agent_playground.models import (
    Edge,
    ExecutionData,
    Graph,
    GraphDataset,
    GraphExecution,
    GraphVersion,
    Node,
    NodeConnection,
    NodeExecution,
    Port,
    PromptTemplateNode,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.temporal.agent_playground.client import cancel_graph_execution

logger = structlog.get_logger(__name__)


def cascade_soft_delete_graph(graph: Graph) -> None:
    """
    Cascade soft-delete a graph and all related objects.

    Soft-deletes in order:
    1. Execution data for each node execution
    2. Node executions for each graph execution
    3. Graph executions for each version
    4. Ports for each node
    5. Nodes for each version
    6. Edges for each version
    7. Prompt-template node links
    8. Graph versions
    9. Linked graph dataset, dataset columns, rows, and cells
    10. The graph itself

    Args:
        graph: The Graph instance to soft-delete.
    """
    versions = GraphVersion.no_workspace_objects.filter(graph=graph)
    for version in versions:
        cascade_soft_delete_version_content(version)

    cascade_soft_delete_graph_dataset(graph)

    graph.delete()


def _cancel_executions(version: GraphVersion) -> None:
    """
    Cancel all Temporal workflows for a graph version's executions.

    Args:
        version: The GraphVersion whose executions should be cancelled.
    """
    executions = GraphExecution.no_workspace_objects.filter(graph_version=version)

    for execution in executions:
        try:
            cancel_graph_execution(str(execution.id))
        except Exception:
            logger.error(
                "Failed to cancel Temporal workflow for execution",
                execution_id=str(execution.id),
            )


def cascade_soft_delete_version_content(version: GraphVersion) -> None:
    """
    Soft-delete a graph version and all its content
    (executions, node executions, execution data, nodes, ports, edges).

    Cancels any active Temporal workflows before soft-deleting.

    Args:
        version: The GraphVersion instance to soft-delete.
    """
    _cancel_executions(version)

    now = timezone.now()

    executions = GraphExecution.no_workspace_objects.filter(graph_version=version)
    node_executions = NodeExecution.no_workspace_objects.filter(
        graph_execution__in=executions
    )

    ExecutionData.no_workspace_objects.filter(
        node_execution__in=node_executions
    ).update(deleted=True, deleted_at=now)

    node_executions.update(deleted=True, deleted_at=now)
    executions.update(deleted=True, deleted_at=now)

    Edge.no_workspace_objects.filter(graph_version=version).update(
        deleted=True, deleted_at=now
    )
    NodeConnection.no_workspace_objects.filter(graph_version=version).update(
        deleted=True, deleted_at=now
    )

    nodes = Node.no_workspace_objects.filter(graph_version=version)

    PromptTemplateNode.no_workspace_objects.filter(node__in=nodes).update(
        deleted=True, deleted_at=now
    )

    Port.no_workspace_objects.filter(node__in=nodes).update(
        deleted=True, deleted_at=now
    )
    nodes.update(deleted=True, deleted_at=now)

    version.delete()


def cascade_soft_delete_graph_dataset(graph: Graph) -> None:
    """
    Soft-delete the graph's linked dataset and table data.
    """
    now = timezone.now()
    graph_datasets = GraphDataset.no_workspace_objects.filter(graph=graph)
    dataset_ids = list(graph_datasets.values_list("dataset_id", flat=True))
    if not dataset_ids:
        return

    Cell.no_workspace_objects.filter(dataset_id__in=dataset_ids).update(
        deleted=True, deleted_at=now
    )
    Row.no_workspace_objects.filter(dataset_id__in=dataset_ids).update(
        deleted=True, deleted_at=now
    )
    Column.no_workspace_objects.filter(dataset_id__in=dataset_ids).update(
        deleted=True, deleted_at=now
    )
    Dataset.no_workspace_objects.filter(id__in=dataset_ids).update(
        deleted=True, deleted_at=now
    )
    graph_datasets.update(deleted=True, deleted_at=now)
