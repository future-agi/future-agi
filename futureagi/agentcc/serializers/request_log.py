from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from agentcc.models import AgentccRequestAttempt, AgentccRequestLog
from agentcc.serializers.request_queries import GatewayRequestLogQuerySerializer
from agentcc.services.credential_headers import mask_credential_headers
from agentcc.services.request_log_metadata import read_tool_calls

# Landed cost is what a managed request cost FutureAGI, upstream and card fees
# included. It is never returned to customers.
STAFF_ONLY_ATTEMPT_FIELDS = (
    "landed_cost_micros",
    "upstream_fee_bps",
    "processing_fee_bps",
)


def _is_staff(context):
    user = getattr(context.get("request"), "user", None)
    return bool(getattr(user, "is_staff", False))


class AgentccRequestLogSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — excludes large body fields."""

    class Meta:
        model = AgentccRequestLog
        fields = [
            "id",
            "request_id",
            "model",
            "provider",
            "resolved_model",
            "latency_ms",
            "started_at",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cost",
            "status_code",
            "is_stream",
            "is_error",
            "error_message",
            "cache_hit",
            "fallback_used",
            "guardrail_triggered",
            "api_key_id",
            "user_id",
            "session_id",
            "routing_strategy",
            "metadata",
            "organization",
            "workspace",
            "created_at",
            # Log entry v2 columns the request explorer lists and filters on.
            "generation_id",
            "attempt_count",
            "billing_mode",
            "credential_type",
            "cost_upstream",
            "cost_markup",
            "cost_source",
            "usage_estimated",
            "agent_framework",
            "agent_id",
            "agent_run_id",
            "agent_parent_run_id",
            "agent_root_run_id",
            "agent_workflow",
            "agent_step",
            "agent_env",
            "policy_version",
            "policy_variant",
            "auto_routed",
        ]
        read_only_fields = fields


class AgentccRequestAttemptSerializer(serializers.ModelSerializer):
    """One upstream attempt; landed cost is staff-only."""

    class Meta:
        model = AgentccRequestAttempt
        fields = [
            "attempt_id",
            "index",
            "kind",
            "reason",
            "provider",
            "model",
            "endpoint_id",
            "upstream_provider",
            "served_upstream_provider",
            "upstream_key_id",
            "credential_type",
            "status_code",
            "outcome",
            "error_class",
            "error_origin",
            "error_message",
            "started_at",
            "latency_ms",
            "ttfb_ms",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "upstream_cost_micros",
            "billed_cost_micros",
            *STAFF_ONLY_ATTEMPT_FIELDS,
            "cost_source",
            "usage_estimated",
            "billable",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _is_staff(self.context):
            for field in STAFF_ONLY_ATTEMPT_FIELDS:
                data.pop(field)
        return data


class AgentccRequestBillingSerializer(serializers.Serializer):
    """How a request was billed; cost_landed is staff-only."""

    mode = serializers.CharField(source="billing_mode", allow_null=True)
    credential_type = serializers.CharField(allow_null=True)
    cost_upstream = serializers.DecimalField(
        max_digits=16, decimal_places=8, allow_null=True
    )
    cost_markup = serializers.DecimalField(
        max_digits=16, decimal_places=8, allow_null=True
    )
    billed = serializers.DecimalField(source="cost", max_digits=12, decimal_places=6)
    cost_source = serializers.CharField(allow_null=True)
    usage_estimated = serializers.BooleanField(allow_null=True)
    cost_landed = serializers.DecimalField(
        max_digits=16,
        decimal_places=8,
        allow_null=True,
        help_text="Staff only.",
    )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _is_staff(self.context):
            data.pop("cost_landed")
        return data


class AgentccRequestAgentSerializer(serializers.Serializer):
    """The agent context a request carried."""

    framework = serializers.CharField(source="agent_framework", allow_null=True)
    agent_id = serializers.CharField(allow_null=True)
    agent_version = serializers.CharField(allow_null=True)
    run_id = serializers.CharField(source="agent_run_id", allow_null=True)
    parent_run_id = serializers.CharField(source="agent_parent_run_id", allow_null=True)
    root_run_id = serializers.CharField(source="agent_root_run_id", allow_null=True)
    task_id = serializers.CharField(source="agent_task_id", allow_null=True)
    workflow = serializers.CharField(source="agent_workflow", allow_null=True)
    step = serializers.CharField(source="agent_step", allow_null=True)
    step_source = serializers.CharField(source="agent_step_source", allow_null=True)
    turn = serializers.IntegerField(source="agent_turn", allow_null=True)
    env = serializers.CharField(source="agent_env", allow_null=True)
    budget_scope = serializers.CharField(allow_null=True)
    identity_source = serializers.CharField(
        source="agent_identity_source", allow_null=True
    )


class AgentccRequestRoutingSerializer(serializers.Serializer):
    """The routing policy and catalog a request was decided under."""

    policy_version = serializers.CharField(allow_null=True)
    policy_variant = serializers.CharField(allow_null=True)
    catalog_snapshot_id = serializers.CharField(allow_null=True)
    auto_routed = serializers.BooleanField(allow_null=True)


class AgentccToolCallsVerdictSerializer(serializers.Serializer):
    """The tool-call guardrail's verdict on a request, as the gateway sent it."""

    checked = serializers.IntegerField()
    blocked = serializers.ListField(child=serializers.CharField())
    stripped = serializers.ListField(child=serializers.CharField())


class AgentccRequestGuardrailSerializer(serializers.Serializer):
    tool_calls = AgentccToolCallsVerdictSerializer()


class AgentccRequestLogDetailSerializer(serializers.ModelSerializer):
    """Full serializer for detail views — includes request/response bodies,
    the attempts in order, and the billing, agent and routing blocks."""

    attempts = serializers.SerializerMethodField()
    billing = AgentccRequestBillingSerializer(source="*", read_only=True)
    agent = AgentccRequestAgentSerializer(source="*", read_only=True)
    routing = AgentccRequestRoutingSerializer(source="*", read_only=True)
    guardrail = serializers.SerializerMethodField(
        help_text="The tool-call guardrail verdict, as the gateway reported it; "
        "null when it reported none."
    )

    class Meta:
        model = AgentccRequestLog
        fields = [
            "id",
            "request_id",
            "model",
            "provider",
            "resolved_model",
            "latency_ms",
            "started_at",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cost",
            "status_code",
            "is_stream",
            "is_error",
            "error_message",
            "cache_hit",
            "fallback_used",
            "guardrail_triggered",
            "api_key_id",
            "user_id",
            "session_id",
            "routing_strategy",
            "metadata",
            "request_body",
            "response_body",
            "request_headers",
            "response_headers",
            "guardrail_results",
            "organization",
            "workspace",
            "created_at",
            "generation_id",
            "attempt_count",
            "cached_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "served_upstream_provider",
            "content_mode",
            "app_title",
            "app_referer",
            "end_user",
            "tags",
            "attempts",
            "billing",
            "agent",
            "routing",
            "guardrail",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # The detail masks every credential header value it returns.
        for field in ("request_headers", "response_headers"):
            if field in data:
                data[field] = mask_credential_headers(data[field])
        return data

    @staticmethod
    def get_attempts_queryset(log):
        """The log's attempts, matched on (organization, request_id), by index."""
        if not log.request_id:
            return AgentccRequestAttempt.objects.none()
        return AgentccRequestAttempt.objects.filter(
            organization_id=log.organization_id, request_id=log.request_id
        ).order_by("index", "attempt_id")

    @swagger_serializer_method(
        serializer_or_field=AgentccRequestAttemptSerializer(many=True)
    )
    def get_attempts(self, obj):
        return AgentccRequestAttemptSerializer(
            self.get_attempts_queryset(obj), many=True, context=self.context
        ).data

    @swagger_serializer_method(serializer_or_field=AgentccRequestGuardrailSerializer)
    def get_guardrail(self, obj):
        tool_calls = read_tool_calls(obj.metadata)
        if tool_calls is None:
            return None
        return AgentccRequestGuardrailSerializer({"tool_calls": tool_calls}).data


class AgentccRequestLogQuerySerializer(GatewayRequestLogQuerySerializer):
    """The list query, with the log entry v2 filters. Each takes a
    comma-separated list and matches any of its values."""

    billing_mode = serializers.CharField(
        required=False, help_text="Comma-separated billing modes (managed, byok)."
    )
    credential_type = serializers.CharField(
        required=False, help_text="Comma-separated credential types (managed, byok)."
    )
    agent_id = serializers.CharField(
        required=False, help_text="Comma-separated agent ids."
    )
    run_id = serializers.CharField(
        required=False, help_text="Comma-separated agent run ids."
    )
    step = serializers.CharField(
        required=False, help_text="Comma-separated agent steps."
    )
    policy_variant = serializers.CharField(
        required=False,
        help_text="Comma-separated routing policy variants (stable, candidate).",
    )
    env = serializers.CharField(
        required=False, help_text="Comma-separated agent environments."
    )
    error_class = serializers.CharField(
        required=False,
        help_text=(
            "Comma-separated attempt error classes: requests with at least one "
            "attempt in one of them."
        ),
    )
    application = serializers.CharField(
        required=False,
        help_text="Comma-separated values of the request's application tag.",
    )
    service = serializers.CharField(
        required=False,
        help_text="Comma-separated values of the request's service tag.",
    )
    tags = serializers.CharField(
        required=False,
        help_text=(
            "Comma-separated key:value custom tags; any listed value of a key "
            "matches, and every key must match."
        ),
    )


class AgentccRequestLogExportQuerySerializer(AgentccRequestLogQuerySerializer):
    """The export query: the list's filters, read and validated the same way,
    so a value export cannot read is refused rather than dropped."""

    export_format = serializers.CharField(
        required=False,
        help_text=(
            "csv (the default) or json (one JSON object per line). An export "
            "holds at most 10,000 rows: 0 matching rows answers 400 "
            "export_empty and more than 10,000 answers 400 export_too_large "
            "with the count, unless max_rows is sent. The list endpoint's "
            "count, with the same filters, is the count export uses."
        ),
    )
    max_rows = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=10_000,
        help_text=(
            "Export at most this many rows, the first in `ordering` (newest "
            "started_at first when no ordering is sent), instead of refusing a "
            "filter that matches more than 10,000. Times in the file are UTC."
        ),
    )


class AgentccSessionSerializer(serializers.Serializer):
    """Serializer for session aggregation results."""

    session_id = serializers.CharField()
    request_count = serializers.IntegerField()
    total_cost = serializers.DecimalField(max_digits=12, decimal_places=6)
    total_tokens = serializers.IntegerField()
    avg_latency = serializers.FloatField()
    first_request_at = serializers.DateTimeField()
    last_request_at = serializers.DateTimeField()
    error_count = serializers.IntegerField()
    models = serializers.ListField(child=serializers.CharField())
    providers = serializers.ListField(child=serializers.CharField())


class AgentccRequestLogMetadataValuesSerializer(serializers.Serializer):
    """Options for the Application, Service and Custom Tags filters."""

    application = serializers.ListField(child=serializers.CharField())
    service = serializers.ListField(child=serializers.CharField())
    tags = serializers.ListField(
        child=serializers.CharField(),
        help_text="key:value pairs, for keys declared as custom properties.",
    )


class AgentccRequestLogMetadataValuesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AgentccRequestLogMetadataValuesSerializer()
