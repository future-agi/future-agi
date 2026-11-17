from rest_framework import serializers

from agentcc.models import AgentccToolCall
from agentcc.services.mcp_tool_call_outcomes import outcomes as tool_call_outcomes

_TOOL_CALL_FIELDS = [
    "id",
    "organization",
    "event_id",
    "tool_call_id",
    "request_id",
    "generation_id",
    "ts_ms",
    "started_at",
    "api_key_id",
    "principal_kind",
    "user_id",
    "client_id",
    "virtual_server_id",
    "server_id",
    "tool",
    "upstream_tool",
    "tool_revision",
    "via",
    "era",
    "protocol_version",
    "session_hash",
    "agent_framework",
    "agent_id",
    "agent_version",
    "agent_run_id",
    "agent_parent_run_id",
    "agent_root_run_id",
    "agent_task_id",
    "agent_trace_id",
    "agent_workflow",
    "agent_step",
    "agent_turn",
    "agent_identity_source",
    "session_id",
    "outcome",
    "is_error",
    "error_class",
    "error_origin",
    "error_code",
    "error_message",
    "approval_id",
    "guardrail",
    "latency_ms",
    "upstream_latency_ms",
    "gateway_overhead_ms",
    "bytes_in",
    "bytes_out",
    "billing_mode",
    "credential_type",
    "credential_source",
    "fallback_from",
    "upstream_cost_micros",
    "billed_cost_micros",
    "markup_micros",
    "markup_bps",
    "cost_source",
    "usage_estimated",
    "dispatched",
    "budget_scope",
    "content_mode",
    "evidence",
]


class AgentccToolCallSerializer(serializers.ModelSerializer):
    # How the call went through the gateway: attempts, approval and limits
    # (agentcc/services/mcp_tool_call_outcomes.py). The view passes the
    # page's approvals, read from the caller's organization, as context.
    outcomes = serializers.SerializerMethodField()

    class Meta:
        model = AgentccToolCall
        fields = [*_TOOL_CALL_FIELDS, "outcomes"]
        read_only_fields = fields

    def get_outcomes(self, instance):
        return tool_call_outcomes(instance, self.context.get("approvals") or {})


class AgentccToolCallDetailSerializer(AgentccToolCallSerializer):
    class Meta(AgentccToolCallSerializer.Meta):
        fields = [*_TOOL_CALL_FIELDS, "outcomes", "arguments", "result"]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # The API contract says content fields are added only when the gateway
        # stored content (content mode ``full``); metadata/none calls omit them.
        if instance.arguments is None:
            data.pop("arguments", None)
        if instance.result is None:
            data.pop("result", None)
        return data
