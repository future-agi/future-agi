import uuid

import jsonschema
from django.db import models

from agent_playground.models.node import Node
from agent_playground.models.node_execution import NodeExecution
from agent_playground.models.port import Port
from tfc.utils.base_model import BaseModel


class ExecutionData(BaseModel):
    """
    Validated data payload linked to Port contract.

    This model connects runtime data to design-time contracts for lineage and validation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    node_execution = models.ForeignKey(
        NodeExecution, on_delete=models.CASCADE, related_name="execution_data"
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.PROTECT,
        related_name="execution_data",
    )
    port = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="execution_data",
    )

    payload = models.JSONField(help_text="The actual data")
    validation_errors = models.JSONField(
        null=True,
        blank=True,
    )
    is_valid = models.BooleanField(
        default=True,
    )

    class Meta:
        db_table = "agent_playground_execution_data"
        constraints = [
            models.UniqueConstraint(
                fields=["node_execution", "port"],
                condition=models.Q(deleted=False),
                name="unique_node_execution_port",
            )
        ]
        indexes = [
            models.Index(fields=["node_execution"]),
            models.Index(fields=["node"]),  # Query by design-time node
            models.Index(fields=["port"]),  # Critical for lineage queries
            models.Index(
                fields=["is_valid", "port"]
            ),  # Find validation failures by port
        ]

    def __str__(self):
        return f"Data for {self.port} ({'valid' if self.is_valid else 'invalid'})"

    def validate_payload(self):
        """
        Validate payload against port's data_schema.
        Returns (is_valid, errors)
        """
        if not self.port.data_schema:
            return True, None

        try:
            jsonschema.validate(instance=self.payload, schema=self.port.data_schema)
            return True, None
        except jsonschema.ValidationError as e:
            return False, {
                "message": e.message,
                "path": list(e.path),
                "schema_path": list(e.schema_path),
            }

    def save(self, *args, **kwargs):
        # Auto-validate payload on save
        is_valid, errors = self.validate_payload()
        self.is_valid = is_valid
        self.validation_errors = errors

        # Denormalize node from node_execution
        if not self.node_id:
            self.node = self.node_execution.node

        super().save(*args, **kwargs)
