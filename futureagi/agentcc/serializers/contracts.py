from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField

from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
    EmptyRequestSerializer,
    StrictInputSerializer,
)

API_FORMAT_HELP_TEXT = (
    "Gateway protocol adapter name. This intentionally remains a string because "
    "self-hosted/custom providers may register adapters outside the built-in "
    "openai/anthropic/gemini/google set."
)
PROVIDER_KEY_HELP_TEXT = "Provider key/name used by the gateway, not a database UUID."

# Keep response wrappers explicit even when the envelope is just status/result:
# the generated OpenAPI component names stay stable and each endpoint owns its
# result schema without hiding contracts behind serializer factories.


class AgentccErrorResponseSerializer(ApiErrorResponseSerializer):
    """Named AgentCC error envelope.

    The common base defines type, code, attr, details, and legacy message/error
    fields; this subclass keeps a stable AgentccErrorResponse OpenAPI component.
    """


class AgentccEmptyRequestSerializer(EmptyRequestSerializer):
    """No-body AgentCC action request; rejects any submitted body fields."""


class GatewaySummaryResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    base_url = serializers.URLField()
    status = serializers.CharField()
    provider_count = serializers.IntegerField(required=False)
    model_count = serializers.IntegerField(required=False)


class GatewayDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewaySummaryResultSerializer()


class AgentccListResultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.ListField(child=serializers.JSONField())


class GatewayListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewaySummaryResultSerializer(many=True)


class GatewayConfiguredProviderSerializer(serializers.Serializer):
    name = serializers.CharField()
    display_name = serializers.CharField(required=False, allow_blank=True)
    models = serializers.ListField(child=serializers.JSONField(), required=False)
    status = serializers.CharField(required=False, allow_blank=True)


class GatewayConfiguredProvidersSerializer(serializers.Serializer):
    providers = GatewayConfiguredProviderSerializer(many=True)


class GatewayHealthResultSerializer(serializers.Serializer):
    status = serializers.CharField()
    health = serializers.JSONField(required=False)
    providers = GatewayConfiguredProvidersSerializer()
    provider_count = serializers.IntegerField()
    model_count = serializers.IntegerField()


class GatewayHealthResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayHealthResultSerializer()


class GatewayConfigProviderSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField()
    base_url = serializers.CharField(allow_blank=True, allow_null=True)
    api_format = serializers.CharField(
        allow_blank=True,
        allow_null=True,
        help_text=API_FORMAT_HELP_TEXT,
    )
    models = serializers.ListField(child=serializers.JSONField())
    is_active = serializers.BooleanField()
    default_timeout = serializers.IntegerField(allow_null=True)
    max_concurrent = serializers.IntegerField(allow_null=True)
    conn_pool_size = serializers.IntegerField(allow_null=True)


class GatewayStatusSerializer(serializers.Serializer):
    status = serializers.CharField()


class GatewayConfigResultSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    organization = serializers.UUIDField(required=False)
    version = serializers.IntegerField(required=False)
    guardrails = serializers.JSONField(required=False)
    routing = serializers.JSONField(required=False)
    cache = serializers.JSONField(required=False)
    rate_limiting = serializers.JSONField(required=False)
    budgets = serializers.JSONField(required=False)
    cost_tracking = serializers.JSONField(required=False)
    ip_acl = serializers.JSONField(required=False)
    alerting = serializers.JSONField(required=False)
    privacy = serializers.JSONField(required=False)
    tool_policy = serializers.JSONField(required=False)
    mcp = serializers.JSONField(required=False)
    a2a = serializers.JSONField(required=False)
    audit = serializers.JSONField(required=False)
    model_database = serializers.JSONField(required=False)
    model_map = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(required=False)
    created_by = serializers.UUIDField(required=False, allow_null=True)
    change_description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    created_at = serializers.DateTimeField(required=False)
    updated_at = serializers.DateTimeField(required=False)
    providers = serializers.DictField(child=GatewayConfigProviderSerializer())
    gateway = GatewayStatusSerializer()


class GatewayConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayConfigResultSerializer()


class GatewayBatchSubmitResultSerializer(serializers.Serializer):
    batch_id = serializers.CharField()
    status = serializers.CharField()
    total = serializers.IntegerField()
    max_concurrency = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class GatewayBatchSummarySerializer(serializers.Serializer):
    total_cost = serializers.FloatField()
    total_input_tokens = serializers.IntegerField()
    total_output_tokens = serializers.IntegerField()
    completed = serializers.IntegerField()
    failed = serializers.IntegerField()
    cancelled = serializers.IntegerField()


class GatewayBatchDetailResultSerializer(GatewayBatchSubmitResultSerializer):
    completed_at = serializers.DateTimeField(required=False)
    results = serializers.ListField(child=serializers.JSONField(), required=False)
    summary = GatewayBatchSummarySerializer(required=False)


class GatewayBatchCancelResultSerializer(serializers.Serializer):
    batch_id = serializers.CharField()
    status = serializers.CharField()


class GatewayBatchSubmitResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayBatchSubmitResultSerializer()


class GatewayBatchDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayBatchDetailResultSerializer()


class GatewayBatchCancelResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayBatchCancelResultSerializer()


class GatewayProviderStatusSerializer(serializers.Serializer):
    id = serializers.CharField(help_text=PROVIDER_KEY_HELP_TEXT)
    name = serializers.CharField()
    status = serializers.CharField()
    healthy = serializers.BooleanField()
    circuit_state = serializers.CharField()
    display_name = serializers.CharField(required=False, allow_blank=True)
    base_url = serializers.CharField(required=False, allow_blank=True)
    api_format = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=API_FORMAT_HELP_TEXT,
    )
    models = serializers.ListField(child=serializers.JSONField(), required=False)
    request_count = serializers.IntegerField(required=False)
    avg_latency = serializers.FloatField(required=False)
    error_rate = serializers.FloatField(required=False)


class GatewayProvidersResultSerializer(serializers.Serializer):
    providers = GatewayProviderStatusSerializer(many=True)


class GatewayProvidersResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayProvidersResultSerializer()


class GatewayMCPStatusResultSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    sessions = serializers.IntegerField()
    tools = serializers.IntegerField()
    resources = serializers.IntegerField()
    prompts = serializers.IntegerField()
    servers = serializers.ListField(
        child=serializers.JSONField(),
        help_text=(
            "Gateway MCP server statuses are adapter-specific objects; the "
            "Django fallback normalizes configured servers to objects with id "
            "and status."
        ),
    )


class GatewayMCPStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayMCPStatusResultSerializer()


class GatewayMCPToolTestContentSerializer(serializers.Serializer):
    type = serializers.CharField()
    text = serializers.CharField(required=False, allow_blank=True)
    data = serializers.CharField(required=False, allow_blank=True)
    mimeType = serializers.CharField(required=False, allow_blank=True)


class GatewayMCPToolTestResultSerializer(serializers.Serializer):
    content = GatewayMCPToolTestContentSerializer(many=True, required=False)
    is_error = serializers.BooleanField(required=False)
    duration_ms = serializers.FloatField(required=False)
    guardrail_pre = serializers.ChoiceField(
        choices=("pass", "blocked", "skipped"), required=False
    )
    guardrail_post = serializers.ChoiceField(
        choices=("pass", "blocked", "skipped"), required=False
    )
    error = serializers.CharField(required=False, allow_blank=True)
    server = serializers.CharField(required=False, allow_blank=True)


class GatewayMCPToolTestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayMCPToolTestResultSerializer()


class GatewayPlaygroundTestResultSerializer(serializers.Serializer):
    status_code = serializers.IntegerField()
    body = serializers.JSONField()
    guardrail_headers = serializers.DictField(child=serializers.CharField())
    model = serializers.CharField()
    blocked = serializers.BooleanField()
    warned = serializers.BooleanField()


class GatewayPlaygroundTestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayPlaygroundTestResultSerializer()


class GatewayMutationResultSerializer(serializers.Serializer):
    status = serializers.BooleanField(required=False)
    version = serializers.IntegerField(required=False)
    gateway_synced = serializers.BooleanField(required=False)
    gateway_warning = serializers.CharField(required=False, allow_blank=True)
    action = serializers.CharField(required=False, allow_blank=True)
    provider = serializers.CharField(required=False, allow_blank=True)
    guardrail = serializers.CharField(required=False, allow_blank=True)
    budget = serializers.CharField(required=False, allow_blank=True)
    server = serializers.CharField(required=False, allow_blank=True)
    enabled = serializers.BooleanField(required=False)


class GatewayMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayMutationResultSerializer()


class GatewayConfigPatchRequestSerializer(serializers.Serializer):
    guardrails = serializers.DictField(
        child=serializers.JSONField(), required=False
    )
    routing = serializers.DictField(child=serializers.JSONField(), required=False)
    cache = serializers.DictField(child=serializers.JSONField(), required=False)
    rate_limiting = serializers.DictField(
        child=serializers.JSONField(), required=False
    )
    budgets = serializers.DictField(child=serializers.JSONField(), required=False)
    cost_tracking = serializers.DictField(
        child=serializers.JSONField(), required=False
    )
    ip_acl = serializers.DictField(child=serializers.JSONField(), required=False)
    alerting = serializers.DictField(child=serializers.JSONField(), required=False)
    privacy = serializers.DictField(child=serializers.JSONField(), required=False)
    tool_policy = serializers.DictField(
        child=serializers.JSONField(), required=False
    )
    mcp = serializers.DictField(child=serializers.JSONField(), required=False)
    a2a = serializers.DictField(child=serializers.JSONField(), required=False)
    audit = serializers.DictField(child=serializers.JSONField(), required=False)
    model_database = serializers.DictField(
        child=serializers.JSONField(), required=False
    )
    model_map = serializers.DictField(child=serializers.JSONField(), required=False)


class GatewayProviderUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    config = serializers.DictField(child=JsonValueField())


class GatewayNameRequestSerializer(serializers.Serializer):
    name = serializers.CharField()


class GatewayToggleGuardrailRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    enabled = serializers.BooleanField()


class GatewayNamedConfigRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    config = serializers.DictField(child=JsonValueField())


class GatewayPlaygroundTestRequestSerializer(serializers.Serializer):
    prompt = serializers.CharField()
    model = serializers.CharField(required=False, allow_blank=True)
    system_prompt = serializers.CharField(required=False, allow_blank=True)


class GatewayBudgetSetRequestSerializer(serializers.Serializer):
    level = serializers.CharField()
    config = serializers.DictField(child=serializers.JSONField())


class GatewayBudgetRemoveRequestSerializer(serializers.Serializer):
    level = serializers.CharField()


class GatewayBatchSubmitRequestSerializer(serializers.Serializer):
    requests = serializers.ListField(
        child=serializers.DictField(child=serializers.JSONField())
    )
    max_concurrency = serializers.IntegerField(required=False, min_value=1, default=5)


class GatewayBatchRequestSerializer(serializers.Serializer):
    batch_id = serializers.CharField()


class GatewayMCPServerUpdateRequestSerializer(serializers.Serializer):
    server_id = serializers.CharField()
    config = serializers.DictField(child=serializers.JSONField())


class GatewayMCPServerRemoveRequestSerializer(serializers.Serializer):
    server_id = serializers.CharField()


class GatewayMCPGuardrailsUpdateRequestSerializer(serializers.Serializer):
    config = serializers.DictField(child=serializers.JSONField())


class GatewayMCPToolTestRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    arguments = serializers.DictField(
        child=serializers.JSONField(), required=False, default=dict
    )


class PIIEntitySerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    category = serializers.CharField()


class PIIEntitiesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PIIEntitySerializer(many=True)


class TopicCategorySerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    subcategories = serializers.ListField(child=serializers.CharField())


class TopicCategoriesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TopicCategorySerializer(many=True)


class ValidateCELRequestSerializer(serializers.Serializer):
    expression = serializers.CharField()


class ValidateCELResultSerializer(serializers.Serializer):
    expression = serializers.CharField()
    valid = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class ValidateCELResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ValidateCELResultSerializer()


class WebhookLogsRequestSerializer(StrictInputSerializer):
    gateway_id = serializers.CharField(required=False, allow_blank=True)
    logs = serializers.ListField(child=serializers.DictField(), required=False)


class ShadowResultsWebhookRequestSerializer(StrictInputSerializer):
    results = serializers.ListField(child=serializers.DictField(), required=False)


class WebhookIngestResultSerializer(serializers.Serializer):
    ingested = serializers.IntegerField()


class WebhookIngestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WebhookIngestResultSerializer()


class APIKeyBulkItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    owner = serializers.CharField(allow_blank=True)
    key_hash = serializers.CharField()
    models = serializers.ListField(child=serializers.CharField())
    providers = serializers.ListField(child=serializers.CharField())
    metadata = serializers.DictField()


class APIKeyBulkResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = APIKeyBulkItemSerializer(many=True)


class OrgConfigBulkItemSerializer(serializers.Serializer):
    providers = serializers.DictField(child=serializers.JSONField())
    guardrails = serializers.JSONField()
    routing = serializers.JSONField()
    cache = serializers.JSONField()
    rate_limiting = serializers.JSONField()
    budgets = serializers.JSONField()
    cost_tracking = serializers.JSONField()
    ip_acl = serializers.JSONField()
    alerting = serializers.JSONField()
    privacy = serializers.JSONField()
    tool_policy = serializers.JSONField()
    mcp = serializers.JSONField()
    a2a = serializers.JSONField()
    audit = serializers.JSONField()
    model_database = serializers.JSONField()
    model_map = serializers.JSONField()


class OrgConfigBulkResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(child=OrgConfigBulkItemSerializer())


class SpendSummaryQuerySerializer(serializers.Serializer):
    period = serializers.ChoiceField(
        choices=("daily", "weekly", "monthly", "total"), required=False
    )


class SpendSummaryOrgSerializer(serializers.Serializer):
    total_spend = serializers.FloatField()
    per_key = serializers.DictField(child=serializers.FloatField())
    per_user = serializers.DictField(child=serializers.FloatField())
    per_model = serializers.DictField(child=serializers.FloatField())


class SpendSummaryResultSerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=("daily", "weekly", "monthly", "total"))
    period_start = serializers.DateTimeField()
    orgs = serializers.DictField(child=SpendSummaryOrgSerializer())


class SpendSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SpendSummaryResultSerializer()
