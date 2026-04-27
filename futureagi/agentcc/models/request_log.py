import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccRequestLog(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_request_logs",
        blank=False,
        null=False,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_request_logs",
        null=True,
        blank=True,
    )
    request_id = models.CharField(max_length=255, blank=True, default="")
    model = models.CharField(max_length=255, blank=True, default="")
    provider = models.CharField(max_length=255, blank=True, default="")
    resolved_model = models.CharField(max_length=255, blank=True, default="")
    latency_ms = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    status_code = models.IntegerField(default=0)
    is_stream = models.BooleanField(default=False)
    is_error = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")
    cache_hit = models.BooleanField(default=False)
    fallback_used = models.BooleanField(default=False)
    guardrail_triggered = models.BooleanField(default=False)
    api_key_id = models.CharField(max_length=255, blank=True, default="")
    user_id = models.CharField(max_length=255, blank=True, default="")
    session_id = models.CharField(max_length=255, blank=True, default="")
    routing_strategy = models.CharField(max_length=100, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    # Phase 5.2: Full request/response data for detail view
    request_body = models.JSONField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    request_headers = models.JSONField(null=True, blank=True)
    response_headers = models.JSONField(null=True, blank=True)
    guardrail_results = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_request_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-started_at"]),
            models.Index(fields=["model"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["api_key_id"]),
            models.Index(fields=["status_code"]),
            models.Index(fields=["is_error"]),
            models.Index(fields=["session_id"]),
            models.Index(fields=["user_id"]),
        ]

    def __str__(self):
        return f"{self.request_id} ({self.model})"
