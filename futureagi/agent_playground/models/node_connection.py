import uuid

from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from tfc.utils.base_model import BaseModel


class NodeConnection(BaseModel):
    """
    Directed node-to-node connection within a GraphVersion.

    A NodeConnection declares that two nodes are connected at the node level.
    Edges (port-to-port connections) between nodes require a corresponding
    NodeConnection to exist first.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph_version = models.ForeignKey(
        GraphVersion, on_delete=models.CASCADE, related_name="node_connections"
    )
    source_node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="outgoing_connections"
    )
    target_node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="incoming_connections"
    )

    class Meta:
        db_table = "agent_playground_node_connection"
        constraints = [
            models.UniqueConstraint(
                fields=["source_node", "target_node"],
                condition=models.Q(deleted=False),
                name="unique_node_connection",
            ),
        ]
        indexes = [
            models.Index(fields=["graph_version"]),
            models.Index(fields=["source_node"]),
            models.Index(fields=["target_node"]),
        ]

    def __str__(self):
        return f"{self.source_node.name} → {self.target_node.name}"

    def _validate_no_self_connection(self) -> None:
        if self.source_node_id == self.target_node_id:
            raise ValidationError("A node cannot connect to itself")

    def _validate_source_node_graph_version(self) -> None:
        if self.source_node.graph_version_id != self.graph_version_id:
            raise ValidationError("Source node must belong to this graph version")

    def _validate_target_node_graph_version(self) -> None:
        if self.target_node.graph_version_id != self.graph_version_id:
            raise ValidationError("Target node must belong to this graph version")

    def clean(self):
        super().clean()
        self._validate_no_self_connection()
        self._validate_source_node_graph_version()
        self._validate_target_node_graph_version()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
