import uuid

import jsonschema
from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.choices import (
    RESERVED_NAME_CHARS,
    GraphVersionStatus,
    NodeType,
)
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node_template import NodeTemplate
from tfc.utils.base_model import BaseModel


class Node(BaseModel):
    """
    Atomic unit of execution or Subgraph reference.

    Node Categories:
    - Atomic Nodes: type="atomic". ref_graph_version MUST be None.
                   node_template MUST be set. Execute a single operation.
    - Subgraph Nodes: type="subgraph". ref_graph_version MUST be set (active or inactive versions only).
                   node_template MUST be None. Execute another Graph.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph_version = models.ForeignKey(
        GraphVersion, on_delete=models.CASCADE, related_name="nodes"
    )
    node_template = models.ForeignKey(
        NodeTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="nodes",
        help_text="If set: References a node template (for atomic nodes). Null for subgraph nodes.",
    )
    ref_graph_version = models.ForeignKey(
        GraphVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="referenced_by_nodes",
        help_text="If set: This node references a reusable subgraph",
    )

    type = models.CharField(
        max_length=20,
        choices=NodeType.choices,
        help_text="'subgraph' for subgraph nodes, 'atomic' for nodes using a NodeTemplate",
    )
    name = models.CharField(max_length=255, help_text="Display name")
    config = models.JSONField(
        default=dict,
        help_text="Node-specific configuration (validated against node_template.config_schema for atomic nodes)",
    )

    # TODO: Need to add a Validation for this structure
    position = models.JSONField(
        default=dict, help_text='UI coordinates {"x": 0, "y": 0}'
    )

    class Meta:
        db_table = "agent_playground_node"
        constraints = [
            models.UniqueConstraint(
                fields=["graph_version", "name"],
                condition=models.Q(deleted=False),
                name="unique_node_name_per_graph_version",
            )
        ]
        indexes = [
            models.Index(fields=["graph_version", "type"]),
            models.Index(fields=["ref_graph_version"]),
            models.Index(fields=["node_template"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.type})"

    def _validate_subgraph_node_fields(self) -> None:
        """
        Validate subgraph node fields.

        Subgraph nodes must have ref_graph_version set and NO node_template.
        """
        if self.type != NodeType.SUBGRAPH:
            return
        if not self.ref_graph_version:
            raise ValidationError("Subgraph nodes must have ref_graph_version set")
        if self.node_template:
            raise ValidationError("Subgraph nodes cannot have node_template")

    def _validate_atomic_node_fields(self) -> None:
        """
        Validate atomic node fields.

        Atomic nodes must have node_template set and NO ref_graph_version.
        """
        if self.type != NodeType.ATOMIC:
            return
        if not self.node_template:
            raise ValidationError("Atomic nodes must have node_template set")
        if self.ref_graph_version:
            raise ValidationError("Atomic nodes cannot have ref_graph_version")

    def _validate_config_schema(self) -> None:
        """
        Validate config against node_template's config_schema for atomic nodes.

        Atomic nodes must have a valid config schema.
        Skipped when a PromptTemplateNode exists (config lives in PTV snapshot).
        """
        if self.type != NodeType.ATOMIC or not self.node_template:
            return
        # Skip validation if config is managed by PromptTemplateNode
        if self.pk:
            from agent_playground.models.prompt_template_node import PromptTemplateNode

            if PromptTemplateNode.no_workspace_objects.filter(node_id=self.pk).exists():
                return
        try:
            jsonschema.validate(
                instance=self.config, schema=self.node_template.config_schema
            )
        except jsonschema.ValidationError as e:
            raise ValidationError(f"Invalid config: {e.message}")

    def _validate_subgraph_node_config(self) -> None:
        """
        Validate subgraph nodes have empty config.

        Subgraph nodes don't have a config_schema (no node_template),
        so config must be empty.
        """
        if self.type != NodeType.SUBGRAPH:
            return
        if self.config:
            raise ValidationError("Subgraph nodes must have empty config")

    def _validate_no_self_reference(self) -> None:
        """
        Validate no self-reference.

        ref_graph_version must belong to a different graph (no self-reference).
        """
        if not self.ref_graph_version:
            return
        if self.ref_graph_version.graph_id == self.graph_version.graph_id:
            raise ValidationError(
                "Subgraph nodes cannot reference versions of the same graph"
            )

    def _validate_ref_is_validated_version(self) -> None:
        """
        Validate ref_graph_version is a validated version (active or inactive).

        ref_graph_version must be either active or inactive. Draft versions are
        excluded because they haven't been validated yet.
        """
        if not self.ref_graph_version:
            return

        if self.ref_graph_version.status not in (
            GraphVersionStatus.ACTIVE,
            GraphVersionStatus.INACTIVE,
        ):
            raise ValidationError(
                "Subgraph nodes can only reference active or inactive versions. "
                "Draft versions cannot be referenced."
            )

    def _validate_single_version_per_graph(self) -> None:
        """
        Validate only one version of each external graph can be referenced per graph_version.

        Only one version of each external graph can be referenced per graph_version.
        """
        if not self.ref_graph_version:
            return
        existing_ref = (
            Node.no_workspace_objects.filter(
                graph_version=self.graph_version,
                ref_graph_version__graph=self.ref_graph_version.graph,
                type=NodeType.SUBGRAPH,
            )
            .exclude(id=self.id)
            .first()
        )

        if (
            existing_ref
            and existing_ref.ref_graph_version_id != self.ref_graph_version_id
        ):
            raise ValidationError(
                f"This graph already references a different version of '{self.ref_graph_version.graph.name}'"
            )

    def _validate_no_graph_reference_cycle(self) -> None:
        """
        Validate that adding this subgraph reference would not create a
        circular dependency between graphs.
        """
        if self.type != NodeType.SUBGRAPH or not self.ref_graph_version:
            return
        from agent_playground.utils.graph_validation import (
            would_create_graph_reference_cycle,
        )

        if would_create_graph_reference_cycle(
            source_graph_id=self.graph_version.graph_id,
            target_graph_id=self.ref_graph_version.graph_id,
        ):
            raise ValidationError(
                "This reference would create a circular dependency between graphs"
            )

    def _validate_name_chars(self) -> None:
        """Reject node names containing reserved characters used in dot-notation."""
        bad = RESERVED_NAME_CHARS.intersection(self.name)
        if bad:
            raise ValidationError(
                f"Node name cannot contain reserved characters: {sorted(bad)}"
            )

    def clean(self):
        """
        Validate node type, template, and reference consistency.
        """
        super().clean()

        self._validate_name_chars()
        self._validate_subgraph_node_fields()
        self._validate_atomic_node_fields()
        self._validate_config_schema()
        self._validate_subgraph_node_config()
        self._validate_no_self_reference()
        self._validate_ref_is_validated_version()
        self._validate_single_version_per_graph()
        self._validate_no_graph_reference_cycle()

    def save(self, *args, **kwargs):
        skip_validation = kwargs.pop("skip_validation", False)
        if not skip_validation:
            self.clean()
        super().save(*args, **kwargs)
