import uuid

from django.db import models

from agent_playground.models.choices import NodeExecutionStatus
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.node import Node
from tfc.utils.base_model import BaseModel


class NodeExecution(BaseModel):
    """
    Execution of a single node within a GraphExecution.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph_execution = models.ForeignKey(
        GraphExecution, on_delete=models.CASCADE, related_name="node_executions"
    )
    node = models.ForeignKey(Node, on_delete=models.PROTECT, related_name="executions")

    status = models.CharField(
        max_length=20,
        choices=NodeExecutionStatus.choices,
        default=NodeExecutionStatus.PENDING,
    )

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "agent_playground_node_execution"
        constraints = [
            models.UniqueConstraint(
                fields=["graph_execution", "node"],
                condition=models.Q(deleted=False),
                name="unique_graph_execution_node",
            )
        ]
        indexes = [
            models.Index(fields=["graph_execution", "status"]),
            models.Index(fields=["node"]),
        ]

    def __str__(self):
        return f"{self.node.name} execution ({self.status})"
