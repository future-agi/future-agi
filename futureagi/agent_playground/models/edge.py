import uuid

from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.choices import PortDirection
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.port import Port
from agent_playground.utils.graph_validation import would_create_cycle
from tfc.utils.base_model import BaseModel


class Edge(BaseModel):
    """
    Data flow connection within a GraphVersion.

    Edge Connection Rules:
    - Fan-Out (Broadcast): ✅ ALLOWED - One output port can connect to multiple input ports
    - Fan-In (Merge): ❌ BLOCKED - One input port can only receive from ONE source
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph_version = models.ForeignKey(
        GraphVersion, on_delete=models.CASCADE, related_name="edges"
    )
    source_port = models.ForeignKey(
        Port, on_delete=models.CASCADE, related_name="outgoing_edges"
    )
    target_port = models.ForeignKey(
        Port, on_delete=models.CASCADE, related_name="incoming_edges"
    )

    class Meta:
        db_table = "agent_playground_edge"
        constraints = [
            models.UniqueConstraint(
                fields=["source_port", "target_port"],
                condition=models.Q(deleted=False),
                name="unique_edge_connection",
            ),
            models.UniqueConstraint(
                fields=["target_port"],
                condition=models.Q(deleted=False),
                name="unique_input_connection",
            ),
        ]
        indexes = [
            models.Index(fields=["graph_version"]),
            models.Index(fields=["source_port"]),
            models.Index(fields=["target_port"]),
        ]

    def __str__(self):
        return f"{self.source_port.node.name}.{self.source_port.key} → {self.target_port.node.name}.{self.target_port.key}"

    def _validate_source_port_direction(self) -> None:
        """
        Validate if the source port is an output port.
        """
        if self.source_port.direction != PortDirection.OUTPUT:
            raise ValidationError("Source port must be an output port")

    def _validate_target_port_direction(self) -> None:
        """
        Validate if the target port is an input port.
        """
        if self.target_port.direction != PortDirection.INPUT:
            raise ValidationError("Target port must be an input port")

    def _validate_source_port_graph_version(self) -> None:
        """
        Validate if the source port's node belongs to the edge's graph version.
        """
        if self.source_port.node.graph_version != self.graph_version:
            raise ValidationError("Source port node must belong to this graph version")

    def _validate_target_port_graph_version(self) -> None:
        """
        Validate if the target port's node belongs to the edge's graph version.
        """
        if self.target_port.node.graph_version != self.graph_version:
            raise ValidationError("Target port node must belong to this graph version")

    def _validate_no_cycle(self) -> None:
        """
        Validate that this edge would not create a cycle in the graph.
        """
        if would_create_cycle(
            source_node_id=self.source_port.node_id,
            target_node_id=self.target_port.node_id,
            graph_version_id=self.graph_version_id,
            exclude_edge_id=self.pk,
        ):
            raise ValidationError("This edge would create a cycle in the graph")

    def _validate_node_connection_exists(self) -> None:
        """
        Validate that a NodeConnection exists between the source and target nodes.
        """
        from agent_playground.models.node_connection import NodeConnection

        exists = NodeConnection.no_workspace_objects.filter(
            graph_version=self.graph_version,
            source_node_id=self.source_port.node_id,
            target_node_id=self.target_port.node_id,
        ).exists()
        if not exists:
            raise ValidationError(
                "A NodeConnection between the source and target nodes must exist before creating an edge"
            )

    def clean(self):
        """
        Validate the edge connections.
        """
        super().clean()

        self._validate_source_port_direction()
        self._validate_target_port_direction()
        self._validate_source_port_graph_version()
        self._validate_target_port_graph_version()
        self._validate_node_connection_exists()
        self._validate_no_cycle()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
