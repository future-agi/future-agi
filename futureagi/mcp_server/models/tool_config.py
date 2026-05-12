import uuid

from django.db import models

from mcp_server.constants import DEFAULT_TOOL_GROUPS
from tfc.utils.base_model import BaseModel


class MCPToolGroupConfig(BaseModel):
    """Stores which tool groups are enabled for a connection."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.OneToOneField(
        "mcp_server.MCPConnection",
        on_delete=models.CASCADE,
        related_name="tool_config",
    )
    enabled_groups = models.JSONField(default=list)
    disabled_tools = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.enabled_groups:
            self.enabled_groups = list(DEFAULT_TOOL_GROUPS)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"MCPToolGroupConfig({self.connection})"
