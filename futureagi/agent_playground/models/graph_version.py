import uuid

from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.choices import (
    GraphVersionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.graph import Graph
from tfc.utils.base_model import BaseModel


class GraphVersion(BaseModel):
    """
    Immutable snapshot of the graph structure.

    Status Lifecycle:
    - "draft": Work in progress. Auto-saved. Not executable. Multiple allowed per Graph.
    - "active": Current production version. This version executes when the graph runs. Max 1 per Graph.
    - "inactive": Historical versions. Available for rollback. Multiple allowed per Graph.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph = models.ForeignKey(Graph, on_delete=models.CASCADE, related_name="versions")

    version_number = models.PositiveIntegerField()  # Monotonic (1, 2, 3...)
    status = models.CharField(
        max_length=20,
        choices=GraphVersionStatus.choices,
        default=GraphVersionStatus.DRAFT,
        help_text="Version status (inactive for historical versions)",
    )
    tags = models.JSONField(default=list)
    commit_message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "agent_playground_graph_version"
        constraints = [
            models.UniqueConstraint(
                fields=["graph", "version_number"],
                condition=models.Q(deleted=False),
                name="unique_graph_version_number",
            )
        ]
        indexes = [
            models.Index(fields=["graph", "status"]),
        ]

    def __str__(self):
        return f"{self.graph.name} v{self.version_number} ({self.status})"

    def _validate_unique_exposed_port_display_names(self) -> None:
        """Validate that unconnected output ports have unique display_names across the version."""
        from agent_playground.models.edge import Edge
        from agent_playground.models.port import Port

        output_ports = list(
            Port.no_workspace_objects.filter(
                node__graph_version=self,
                direction=PortDirection.OUTPUT,
            ).select_related("node")
        )
        if not output_ports:
            return

        connected_output_port_ids = set(
            Edge.no_workspace_objects.filter(graph_version=self).values_list(
                "source_port_id", flat=True
            )
        )

        seen: dict[str, str] = {}  # display_name -> node name
        for port in output_ports:
            if port.id not in connected_output_port_ids:
                if port.display_name in seen:
                    raise ValidationError(
                        f"Duplicate exposed output port display name "
                        f"'{port.display_name}' found on nodes "
                        f"'{seen[port.display_name]}' and '{port.node.name}'. "
                        f"All exposed output port display names must be unique "
                    )
                seen[port.display_name] = port.node.name

    def _validate_single_active_version(self) -> None:
        """Validate that there's at most one active version per graph."""
        existing = (
            GraphVersion.no_workspace_objects.filter(graph=self.graph, status="active")
            .exclude(id=self.id)
            .exists()
        )
        if existing:
            raise ValidationError("Only one active version allowed per graph")

    def _validate_template_port_completeness(self) -> None:
        """Validate that STRICT and EXTENSIBLE mode nodes have all required template keys."""
        from agent_playground.models.node import Node
        from agent_playground.models.port import Port

        nodes = Node.no_workspace_objects.filter(
            graph_version=self,
            type=NodeType.ATOMIC,
            node_template__isnull=False,
        ).select_related("node_template")

        for node in nodes:
            template = node.node_template
            for direction, definition_field, mode_field in [
                ("input", "input_definition", "input_mode"),
                ("output", "output_definition", "output_mode"),
            ]:
                mode = getattr(template, mode_field)
                if mode not in (PortMode.STRICT, PortMode.EXTENSIBLE):
                    continue

                definition_keys = {
                    d["key"] for d in getattr(template, definition_field)
                }
                port_keys = set(
                    Port.no_workspace_objects.filter(
                        node=node, direction=direction
                    ).values_list("key", flat=True)
                )
                missing = definition_keys - port_keys
                if missing:
                    raise ValidationError(
                        f"Missing required template port keys "
                        f"{sorted(missing)} on {mode}-mode {direction} "
                        f"of node '{node.name}'"
                    )

    def clean(self):
        super().clean()

        self._validate_unique_exposed_port_display_names()

        if self.status == "active":
            self._validate_single_active_version()
            self._validate_template_port_completeness()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
