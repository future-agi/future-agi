import uuid
from enum import StrEnum

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class RequestLogTag(StrEnum):
    """Metadata keys a caller sets to name the application and service behind a request."""

    APPLICATION = "application"
    SERVICE = "service"


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

    # Log entry v2 columns. Each is nullable (NULL = not reported) or carries a
    # database default, so a process still
    # running the v1 model can keep inserting while the migration rolls out.
    # Billing
    billing_mode = models.CharField(max_length=16, null=True, blank=True)
    credential_type = models.CharField(max_length=8, null=True, blank=True)
    cost_upstream = models.DecimalField(
        max_digits=16, decimal_places=8, null=True, blank=True
    )
    cost_markup = models.DecimalField(
        max_digits=16, decimal_places=8, null=True, blank=True
    )
    cost_landed = models.DecimalField(
        max_digits=16, decimal_places=8, null=True, blank=True
    )
    cost_source = models.CharField(max_length=32, null=True, blank=True)
    usage_estimated = models.BooleanField(null=True, blank=True)
    served_upstream_provider = models.CharField(max_length=64, null=True, blank=True)
    upstream_key_id = models.CharField(max_length=128, null=True, blank=True)
    # Tokens and attempts
    cached_tokens = models.IntegerField(null=True, blank=True)
    cache_write_tokens = models.IntegerField(null=True, blank=True)
    reasoning_tokens = models.IntegerField(null=True, blank=True)
    attempt_count = models.SmallIntegerField(default=1, db_default=1)
    # Identity
    generation_id = models.CharField(max_length=64, null=True, blank=True)
    # Agent
    agent_framework = models.CharField(max_length=32, null=True, blank=True)
    agent_id = models.CharField(max_length=128, null=True, blank=True)
    agent_version = models.CharField(max_length=128, null=True, blank=True)
    agent_run_id = models.CharField(max_length=128, null=True, blank=True)
    agent_parent_run_id = models.CharField(max_length=128, null=True, blank=True)
    agent_root_run_id = models.CharField(max_length=128, null=True, blank=True)
    agent_task_id = models.CharField(max_length=128, null=True, blank=True)
    agent_workflow = models.CharField(max_length=128, null=True, blank=True)
    agent_step = models.CharField(max_length=32, null=True, blank=True)
    agent_step_source = models.CharField(max_length=32, null=True, blank=True)
    agent_turn = models.IntegerField(null=True, blank=True)
    agent_env = models.CharField(max_length=32, null=True, blank=True)
    agent_identity_source = models.CharField(max_length=32, null=True, blank=True)
    budget_scope = models.CharField(max_length=128, null=True, blank=True)
    # Routing
    policy_version = models.CharField(max_length=64, null=True, blank=True)
    policy_variant = models.CharField(max_length=16, null=True, blank=True)
    catalog_snapshot_id = models.CharField(max_length=32, null=True, blank=True)
    auto_routed = models.BooleanField(null=True, blank=True)
    # App, user and privacy
    app_title = models.CharField(max_length=128, null=True, blank=True)
    app_referer = models.CharField(max_length=256, null=True, blank=True)
    end_user = models.CharField(max_length=256, null=True, blank=True)
    tags = models.JSONField(null=True, blank=True)
    content_mode = models.CharField(max_length=8, null=True, blank=True)
    # The log entry's evidence object, agent.identity and eval_export.
    evidence = models.JSONField(null=True, blank=True)

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
            GinIndex(
                fields=["metadata"],
                name="agentcc_req_metadata_gin",
                opclasses=["jsonb_path_ops"],
            ),
            models.Index(
                fields=["organization", "agent_run_id"],
                name="agentcc_rl_org_run_idx",
            ),
            models.Index(
                fields=["organization", "agent_id", "-started_at"],
                name="agentcc_rl_org_agent_idx",
            ),
            models.Index(
                fields=["organization", "billing_mode", "-started_at"],
                name="agentcc_rl_org_billing_idx",
            ),
            models.Index(
                fields=["organization", "request_id"],
                name="agentcc_rl_org_request_idx",
            ),
            models.Index(
                fields=["organization", "policy_variant", "-started_at"],
                name="agentcc_rl_org_variant_idx",
            ),
        ]

    def __str__(self):
        return f"{self.request_id} ({self.model})"
