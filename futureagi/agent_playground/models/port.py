import uuid

from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.choices import (
    RESERVED_NAME_CHARS,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.node import Node
from tfc.utils.base_model import BaseModel


class Port(BaseModel):
    """
    Type contract for data flow.

    Ports define the inputs and outputs of nodes and provide JSON Schema validation
    for data flowing through the graph.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="ports")

    key = models.CharField(
        max_length=100, help_text="Identifier (e.g., 'prompt', 'result')"
    )
    display_name = models.CharField(
        max_length=100,
        help_text="User-facing name for the port",
    )
    direction = models.CharField(max_length=10, choices=PortDirection.choices)
    data_schema = models.JSONField(default=dict, help_text="JSON Schema for validation")
    required = models.BooleanField(default=True)
    default_value = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    ref_port = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referencing_ports",
        help_text="For subgraph node ports: links to the corresponding port in the child graph",
    )

    class Meta:
        db_table = "agent_playground_port"
        constraints = [
            models.UniqueConstraint(
                fields=["node", "key"],
                condition=models.Q(deleted=False) & ~models.Q(key="custom"),
                name="unique_node_port_key",
            ),
            models.UniqueConstraint(
                fields=["node", "display_name"],
                condition=models.Q(deleted=False),
                name="unique_node_port_display_name",
            ),
        ]
        indexes = [models.Index(fields=["node", "direction"])]

    @property
    def routing_key(self) -> str:
        """Key used for data routing.

        Template ports use `key` (matches runner's hardcoded dict keys).
        Custom ports use `display_name` (unique per node, user-defined).
        """
        return self.display_name if self.key == "custom" else self.key

    def _validate_ref_port(self) -> None:
        """Validate ref_port constraints for subgraph node ports.

        Rules:
        - ref_port is only allowed on subgraph node ports (node.type == "subgraph")
        - ref_port must belong to a node in node.ref_graph_version
        - Direction must match (input→input, output→output)
        """
        if not self.ref_port_id:
            return

        if self.node.type != NodeType.SUBGRAPH:
            raise ValidationError("ref_port is only allowed on ports of subgraph nodes")

        if not self.node.ref_graph_version_id:
            raise ValidationError(
                "Subgraph node must have ref_graph_version set to use ref_port"
            )

        if self.ref_port.node.graph_version_id != self.node.ref_graph_version_id:
            raise ValidationError(
                "ref_port must belong to a node in the subgraph's referenced graph version"
            )

        if self.ref_port.direction != self.direction:
            raise ValidationError(
                f"ref_port direction mismatch: this port is '{self.direction}' "
                f"but ref_port is '{self.ref_port.direction}'"
            )

    def _validate_subgraph_port_key(self) -> None:
        """Subgraph nodes must use key='custom' for all ports."""
        if self.node.type == NodeType.SUBGRAPH and self.key != "custom":
            raise ValidationError(
                f"Subgraph node '{self.node.name}' port key must be "
                f"'custom', got '{self.key}'"
            )

    def _validate_key_against_template(self) -> None:
        """Validate port key against the node template's port mode.

        - STRICT: key must be in template definition keys
        - EXTENSIBLE: key must be in template definition keys OR be "custom"
        - DYNAMIC: key must be "custom"
        """
        if self.node.type != NodeType.ATOMIC or not self.node.node_template:
            return

        template = self.node.node_template
        if self.direction == PortDirection.INPUT:
            mode = template.input_mode
            definition = template.input_definition
        else:
            mode = template.output_mode
            definition = template.output_definition

        definition_keys = {d["key"] for d in definition}

        if mode == PortMode.DYNAMIC:
            if self.key != "custom":
                raise ValidationError(
                    f"Port key '{self.key}' is not allowed on "
                    f"dynamic-mode {self.direction} of node '{self.node.name}'. "
                    f"All keys must be 'custom'"
                )
        elif mode == PortMode.STRICT:
            if self.key not in definition_keys:
                raise ValidationError(
                    f"Port key '{self.key}' is not allowed on "
                    f"strict-mode {self.direction} of node '{self.node.name}'. "
                    f"Allowed keys: {sorted(definition_keys)}"
                )
        elif mode == PortMode.EXTENSIBLE:
            if self.key != "custom" and self.key not in definition_keys:
                raise ValidationError(
                    f"Port key '{self.key}' is not allowed on "
                    f"extensible-mode {self.direction} of node '{self.node.name}'. "
                    f"Allowed keys: {sorted(definition_keys)} or 'custom'"
                )

    def __str__(self):
        return f"{self.node.name}.{self.key} ({self.direction})"

    def _validate_display_name_chars(self) -> None:
        """Reject output port display_names containing reserved characters.

        Input ports are allowed to contain these characters because their
        display_name IS the full variable string (e.g. "Node1.response.data").
        """
        if self.direction != PortDirection.OUTPUT:
            return
        bad = RESERVED_NAME_CHARS.intersection(self.display_name)
        if bad:
            raise ValidationError(
                f"Output port display_name cannot contain reserved characters: {sorted(bad)}"
            )

    def clean(self):
        super().clean()
        self._validate_display_name_chars()
        self._validate_ref_port()
        self._validate_subgraph_port_key()
        self._validate_key_against_template()

    def save(self, *args, **kwargs):
        skip_validation = kwargs.pop("skip_validation", False)
        if not skip_validation:
            self.clean()
        super().save(*args, **kwargs)
