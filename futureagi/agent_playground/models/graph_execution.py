import uuid

from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.choices import GraphExecutionStatus
from agent_playground.models.graph_version import GraphVersion
from tfc.utils.base_model import BaseModel


class GraphExecution(BaseModel):
    """
    An execution instance of a GraphVersion.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph_version = models.ForeignKey(
        GraphVersion, on_delete=models.PROTECT, related_name="executions"
    )

    parent_node_execution = models.ForeignKey(
        "NodeExecution",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="child_graph_executions",
        help_text="If the graph is a module, the parent node calling this",
    )

    status = models.CharField(
        max_length=20,
        choices=GraphExecutionStatus.choices,
        default=GraphExecutionStatus.PENDING,
    )
    input_payload = models.JSONField(default=dict, blank=True, null=True)
    output_payload = models.JSONField(default=dict, blank=True, null=True)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "agent_playground_graph_execution"
        indexes = [
            models.Index(fields=["graph_version", "status"]),
            models.Index(fields=["parent_node_execution"]),
        ]

    def __str__(self):
        return f"Execution of {self.graph_version} ({self.status})"

    def _validate_payload_is_dict(self, field_name: str) -> None:
        """Validate the payload field is a dict (or None)."""
        value = getattr(self, field_name)
        if value is not None and not isinstance(value, dict):
            raise ValidationError(f"{field_name} must be a dict")

    def clean(self):
        """Validate the graph execution."""
        super().clean()

        self._validate_payload_is_dict("input_payload")
        self._validate_payload_is_dict("output_payload")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
