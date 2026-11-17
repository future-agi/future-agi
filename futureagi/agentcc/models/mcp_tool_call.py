from django.db import models

from accounts.models import Organization
from agentcc.models.mcp_credential import URL_MAX_LENGTH


class AgentccToolCall(models.Model):
    """One MCP ``tools/call``: allowed, denied, failed or errored. Every call is metered.

    One row per ``mcp-tool-call-v1`` record from the gateway, unique on
    ``event_id`` (the ``tool_call_id``). The record's ``agent`` object is
    stored in the same ``agent_*`` columns as the request log. The gateway
    sends ``arguments`` and ``result`` only under content mode ``full``.
    ``evidence`` holds the record's optional ``identity``, ``account_label``,
    ``policy``, ``flow`` and ``shaped`` fields.

    The enum fields below carry no ``choices``: their values come from the
    gateway's record contract, and a new value must not need a migration.
    """

    class Outcome(models.TextChoices):
        SUCCESS = "success"
        IS_ERROR = "is_error"
        DENIED = "denied"
        ERROR = "error"
        TIMEOUT = "timeout"
        CANCELLED = "cancelled"

    class BillingMode(models.TextChoices):
        BYOK = "byok"
        MANAGED = "managed"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_tool_calls",
        db_index=False,  # covered by the (organization, ...) indexes below
    )
    event_id = models.CharField(max_length=64, unique=True)
    tool_call_id = models.CharField(max_length=64)
    request_id = models.CharField(max_length=64, blank=True, default="")
    generation_id = models.CharField(max_length=64, blank=True, default="")
    ts_ms = models.BigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)

    # Principal and target
    api_key_id = models.CharField(max_length=255, blank=True, default="")
    principal_kind = models.CharField(max_length=16, blank=True, default="")
    user_id = models.CharField(max_length=255, blank=True, default="")
    # An OAuth principal's client_id, sized as AgentccMCPOAuthClient.client_id.
    client_id = models.CharField(max_length=URL_MAX_LENGTH, blank=True, default="")
    virtual_server_id = models.CharField(max_length=64, blank=True, default="")
    server_id = models.CharField(max_length=64, blank=True, default="")
    tool = models.CharField(max_length=255, blank=True, default="")
    upstream_tool = models.CharField(max_length=255, blank=True, default="")
    tool_revision = models.CharField(max_length=64, blank=True, default="")
    via = models.CharField(max_length=32, blank=True, default="")
    era = models.CharField(max_length=16, blank=True, default="")
    protocol_version = models.CharField(max_length=16, blank=True, default="")
    session_hash = models.CharField(max_length=64, blank=True, default="")

    # Agent, sized as on the request log
    agent_framework = models.CharField(max_length=32, blank=True, default="")
    agent_id = models.CharField(max_length=128, blank=True, default="")
    agent_version = models.CharField(max_length=128, blank=True, default="")
    agent_run_id = models.CharField(max_length=128, blank=True, default="")
    agent_parent_run_id = models.CharField(max_length=128, blank=True, default="")
    agent_root_run_id = models.CharField(max_length=128, blank=True, default="")
    agent_task_id = models.CharField(max_length=128, blank=True, default="")
    agent_trace_id = models.CharField(max_length=128, blank=True, default="")
    agent_workflow = models.CharField(max_length=128, blank=True, default="")
    agent_step = models.CharField(max_length=32, blank=True, default="")
    agent_turn = models.IntegerField(null=True, blank=True)
    agent_identity_source = models.CharField(max_length=32, blank=True, default="")
    session_id = models.CharField(max_length=255, blank=True, default="")

    # Outcome
    outcome = models.CharField(max_length=16, blank=True, default="")
    is_error = models.BooleanField(default=False)
    error_class = models.CharField(max_length=32, blank=True, default="")
    error_origin = models.CharField(max_length=16, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.CharField(max_length=512, blank=True, default="")
    approval_id = models.CharField(max_length=64, blank=True, default="")
    guardrail = models.JSONField(null=True, blank=True)

    # Timing and size
    latency_ms = models.IntegerField(null=True, blank=True)
    upstream_latency_ms = models.IntegerField(null=True, blank=True)
    gateway_overhead_ms = models.IntegerField(null=True, blank=True)
    bytes_in = models.BigIntegerField(null=True, blank=True)
    bytes_out = models.BigIntegerField(null=True, blank=True)

    # Billing; cost columns are integer micros (USD x 1e6).
    billing_mode = models.CharField(max_length=16, blank=True, default="")
    credential_type = models.CharField(max_length=8, blank=True, default="")
    credential_source = models.CharField(max_length=16, blank=True, default="")
    fallback_from = models.CharField(max_length=16, blank=True, default="")
    upstream_cost_micros = models.BigIntegerField(default=0)
    billed_cost_micros = models.BigIntegerField(default=0)
    markup_micros = models.BigIntegerField(default=0)
    markup_bps = models.IntegerField(default=0)
    cost_source = models.CharField(max_length=32, blank=True, default="")
    usage_estimated = models.BooleanField(default=False)
    dispatched = models.BooleanField(default=False)
    budget_scope = models.CharField(max_length=128, blank=True, default="")

    # Content (content mode "full" only) and evidence
    content_mode = models.CharField(max_length=8, blank=True, default="")
    arguments = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    evidence = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_tool_call"
        ordering = ["-started_at"]
        # Created concurrently in migration 0041, not with the table.
        indexes = [
            models.Index(
                fields=["organization", "-started_at"],
                name="agentcc_tc_org_started_idx",
            ),
            models.Index(
                fields=["organization", "agent_run_id"],
                name="agentcc_tc_org_run_idx",
            ),
            models.Index(
                fields=["organization", "server_id", "tool", "-started_at"],
                name="agentcc_tc_org_server_tool_idx",
            ),
            models.Index(
                fields=["organization", "api_key_id", "-started_at"],
                name="agentcc_tc_org_key_idx",
            ),
            models.Index(
                fields=["organization", "generation_id"],
                name="agentcc_tc_org_generation_idx",
            ),
        ]

    def __str__(self):
        return f"{self.event_id} {self.server_id}/{self.tool} {self.outcome}"
