import uuid

from django.core.exceptions import ValidationError
from django.db import models

from tfc.utils.base_model import BaseModel


class PromptTemplateNode(BaseModel):
    """
    Links a Node to a PromptTemplate and PromptVersion from model_hub.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    node = models.OneToOneField(
        "agent_playground.Node",
        on_delete=models.CASCADE,
        related_name="prompt_template_node",
    )

    prompt_template = models.ForeignKey(
        "model_hub.PromptTemplate",
        on_delete=models.CASCADE,
        related_name="prompt_template_nodes",
    )

    prompt_version = models.ForeignKey(
        "model_hub.PromptVersion",
        on_delete=models.CASCADE,
        related_name="prompt_template_nodes",
    )

    class Meta:
        db_table = "agent_playground_prompt_template_node"

    def __str__(self):
        return (
            f"PromptTemplateNode(node={self.node_id}, "
            f"template={self.prompt_template_id}, "
            f"version={self.prompt_version_id})"
        )

    def clean(self):
        super().clean()
        if (
            self.prompt_template_id
            and self.prompt_version_id
            and self.prompt_version.original_template_id != self.prompt_template_id
        ):
            raise ValidationError(
                "prompt_version must belong to the specified prompt_template."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
        self._update_output_port_schema()

    def _update_output_port_schema(self):
        """Update the node's output port data_schema based on the linked PromptVersion's response_format."""
        from agent_playground.models.port import Port
        from agent_playground.services.engine.utils.json_path import (
            get_output_schema_for_response_format,
        )

        config = self.prompt_version.prompt_config_snapshot or {}
        response_format = config.get("configuration", {}).get("response_format")
        data_schema = get_output_schema_for_response_format(response_format)

        Port.no_workspace_objects.filter(
            node=self.node,
            key="response",
            direction="output",
        ).update(data_schema=data_schema)
