"""Token-free outcomes of MCP disconnect and server-removal cleanup."""

import uuid

from django.db import models


class AgentccMCPCleanupReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credential = models.ForeignKey(
        "prism.AgentccMCPCredential",
        on_delete=models.CASCADE,
        related_name="cleanup_receipts",
    )
    trigger = models.CharField(max_length=32)
    step = models.CharField(max_length=32)
    outcome = models.CharField(max_length=16)
    # Fixed reason codes only: no URLs, response bodies, tokens or client ids.
    reason = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_cleanup_receipt"
        ordering = ["created_at"]
