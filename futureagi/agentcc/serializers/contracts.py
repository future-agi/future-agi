from rest_framework import serializers

from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
    EmptyRequestSerializer,
    StrictInputSerializer,
)
from tfc.utils.serializer_fields import JsonValueField

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


LAST_HEALTH_CHECK_HELP_TEXT = (
    "When a member of the org last ran Check Health (ISO 8601), or null when "
    "no check is recorded."
)
LAST_HEALTH_CHECK_STATUS_HELP_TEXT = (
    "What the org's last health check found: healthy or unreachable."
)


class GatewaySummaryResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    base_url = serializers.URLField()
    status = serializers.CharField()
    provider_count = serializers.IntegerField(required=False)
    model_count = serializers.IntegerField(required=False)
    last_health_check = serializers.DateTimeField(
        required=False, allow_null=True, help_text=LAST_HEALTH_CHECK_HELP_TEXT
    )
    last_health_check_status = serializers.CharField(
        required=False, allow_null=True, help_text=LAST_HEALTH_CHECK_STATUS_HELP_TEXT
    )


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
    org_configs_pushed = serializers.BooleanField(
        required=False,
        help_text=(
            "Whether this check re-pushed every org's config to the gateway. "
            "Only an Owner or Admin's check does, at most once per 5 minutes."
        ),
    )
    last_health_check = serializers.DateTimeField(
        required=False, allow_null=True, help_text=LAST_HEALTH_CHECK_HELP_TEXT
    )
    last_health_check_status = serializers.CharField(
        required=False, allow_null=True, help_text=LAST_HEALTH_CHECK_STATUS_HELP_TEXT
    )


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
    api_path_prefix = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    models = serializers.ListField(child=serializers.JSONField())
    is_active = serializers.BooleanField()
    default_timeout = serializers.IntegerField(allow_null=True)
    max_concurrent = serializers.IntegerField(allow_null=True)
    conn_pool_size = serializers.IntegerField(allow_null=True)


class GatewayStatusSerializer(serializers.Serializer):
    status = serializers.CharField()


# The blocks of the gateway's GET /-/config the config action passes through
# unchanged. A block the gateway does not report is absent from the result.
class GatewayServerSettingsSerializer(serializers.Serializer):
    host = serializers.CharField(required=False, allow_blank=True)
    port = serializers.IntegerField(required=False)
    read_timeout_ms = serializers.IntegerField(required=False)
    write_timeout_ms = serializers.IntegerField(required=False)
    idle_timeout_ms = serializers.IntegerField(required=False)
    max_request_timeout_ms = serializers.IntegerField(required=False)


class GatewayLoggingSettingsSerializer(serializers.Serializer):
    level = serializers.CharField(required=False, allow_blank=True)
    request_logging = serializers.BooleanField(required=False)
    include_bodies = serializers.BooleanField(required=False)
    content_mode = serializers.CharField(required=False, allow_blank=True)


class GatewayGuardrailPipelineSerializer(serializers.Serializer):
    """How the gateway node runs an organization's guardrail checks (GET
    /-/config guardrail_pipeline). An org's own fail_open and pipeline_mode
    are stored, not read; the console shows these values in their place."""

    enabled = serializers.BooleanField(
        help_text="guardrails.enabled on the node: without it no org check runs."
    )
    fail_open = serializers.BooleanField(
        help_text=(
            "The node's guardrails.fail_open: an org check that errors or "
            "times out lets the request through when true."
        )
    )
    mode = serializers.CharField(
        help_text=(
            "sequential: org checks run in turn and the first block stops the rest."
        )
    )


class GatewayBatchSettingsSerializer(serializers.Serializer):
    storage = serializers.CharField(
        required=False, help_text="Where batches live: memory (the node's)."
    )
    default_max_concurrency = serializers.IntegerField(
        required=False,
        help_text="The concurrency a submission that sets none runs at.",
    )
    max_body_bytes = serializers.IntegerField(
        required=False, help_text="The largest submit body the gateway reads."
    )


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
    managed_models = serializers.JSONField(required=False)
    tools = serializers.JSONField(required=False)
    identity = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(required=False)
    created_by = serializers.UUIDField(required=False, allow_null=True)
    change_description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    created_at = serializers.DateTimeField(required=False)
    updated_at = serializers.DateTimeField(required=False)
    secrets_set = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "JSON pointers of the infrastructure secrets stored in the active "
            "org config. Each one reads as a mask, never its value."
        ),
    )
    providers = serializers.DictField(child=GatewayConfigProviderSerializer())
    gateway = GatewayStatusSerializer()
    server = GatewayServerSettingsSerializer(required=False)
    logging = GatewayLoggingSettingsSerializer(required=False)
    batch = GatewayBatchSettingsSerializer(required=False)
    org_sections_enforced = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "The org config sections this gateway node enforces, from alerting, "
            "budgets, cache, ip_acl, rate_limiting and tool_policy. Absent: the "
            "gateway did not say."
        ),
    )
    guardrail_pipeline = GatewayGuardrailPipelineSerializer(required=False)
    org_sections_not_enforced = serializers.DictField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "Org config section -> the reason this gateway node gives for not "
            "enforcing it. Absent: no such section, or the gateway did not say."
        ),
    )
    last_health_check = serializers.DateTimeField(
        required=False, allow_null=True, help_text=LAST_HEALTH_CHECK_HELP_TEXT
    )
    last_health_check_status = serializers.CharField(
        required=False, allow_null=True, help_text=LAST_HEALTH_CHECK_STATUS_HELP_TEXT
    )


class GatewayConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayConfigResultSerializer()


_BATCH_ORG_HELP_TEXT = (
    "The organization the batch runs as: every item is a request "
    "authenticated by a key minted for it."
)


class GatewayBatchSubmitResultSerializer(serializers.Serializer):
    batch_id = serializers.CharField()
    org_id = serializers.CharField(help_text=_BATCH_ORG_HELP_TEXT)
    key_id = serializers.CharField(
        required=False,
        help_text="The gateway id of the key the batch's items run as.",
    )
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
    max_concurrency = serializers.IntegerField(
        required=False,
        help_text="Absent when the gateway does not hold the batch (see error).",
    )
    completed_at = serializers.DateTimeField(required=False)
    results = serializers.ListField(child=serializers.JSONField(), required=False)
    summary = GatewayBatchSummarySerializer(required=False)
    outcome = serializers.CharField(
        required=False,
        help_text=(
            "unknown: the gateway lost the batch before it was seen to end "
            "(status failed)."
        ),
    )
    error = serializers.CharField(
        required=False,
        help_text=(
            "Set when the gateway does not hold the batch: the answer is the "
            "status recorded when it finished, without results or summary."
        ),
    )


class GatewayBatchCancelResultSerializer(serializers.Serializer):
    batch_id = serializers.CharField()
    org_id = serializers.CharField(help_text=_BATCH_ORG_HELP_TEXT)
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
    status_reason = serializers.CharField(
        required=False,
        help_text=(
            "Why the status is what it is: the org's own error rate over the "
            "last 24 h (unhealthy at 50% or more, degraded at 10% or more, "
            "both from 5 requests), or why the provider is not pushed."
        ),
    )
    pushed = serializers.BooleanField(
        required=False,
        help_text=(
            "Whether the config push sends this provider to the gateway. "
            "False for a stored credential the push cannot use."
        ),
    )


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
            "The gateway's MCP server statuses (mcp-admin-status-v1, keyed by "
            "server_id), limited to the org's own servers."
        ),
    )
    # Why MCP reads as disabled when the gateway's status call failed.
    reason = serializers.ChoiceField(
        choices=("gateway_mcp_disabled", "gateway_unreachable"), required=False
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
    blocked = serializers.BooleanField(
        help_text="HTTP 403 with error.type guardrail_error: a check blocked."
    )
    warned = serializers.BooleanField(
        help_text=(
            "A 2xx with x-agentcc-guardrail-triggered: true: a check warned or "
            "logged and the request went through."
        )
    )
    guardrail_action = serializers.ChoiceField(
        choices=("warn", "log"),
        required=False,
        allow_null=True,
        help_text="x-agentcc-guardrail-action on a warned answer, when sent.",
    )
    triggered_checks = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "The check names in x-agentcc-guardrails-triggered on a warned "
            "answer; empty when the gateway does not send the header."
        ),
    )


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
    # update-config: the writer's warnings, e.g. {code, param, message} for a
    # routing key the gateway does not run.
    warnings = serializers.ListField(child=serializers.DictField(), required=False)


class GatewayMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GatewayMutationResultSerializer()


class GatewayConfigPatchRequestSerializer(serializers.Serializer):
    """update-config takes each section as an RFC 7386 merge patch: a null
    value deletes that key from the stored section (see
    agentcc.services.org_config_writer.deep_merge), so a value directly under a
    section may be null. A whole section may not."""

    guardrails = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    routing = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    cache = serializers.DictField(child=JsonValueField(allow_null=True), required=False)
    rate_limiting = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    budgets = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    cost_tracking = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    ip_acl = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    alerting = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    privacy = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    tool_policy = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    mcp = serializers.DictField(child=JsonValueField(allow_null=True), required=False)
    a2a = serializers.DictField(child=JsonValueField(allow_null=True), required=False)
    audit = serializers.DictField(child=JsonValueField(allow_null=True), required=False)
    model_database = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    model_map = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    managed_models = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    tools = serializers.DictField(child=JsonValueField(allow_null=True), required=False)
    identity = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )


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
    key_prefix = serializers.CharField(allow_blank=True)
    models = serializers.ListField(child=serializers.CharField())
    providers = serializers.ListField(child=serializers.CharField())
    metadata = serializers.DictField()
    expires_at = serializers.DateTimeField(allow_null=True)


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
    managed_models = serializers.JSONField(required=False)
    tools = serializers.JSONField(required=False)
    identity = serializers.JSONField(required=False)


class OrgConfigBulkResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(child=OrgConfigBulkItemSerializer())
    unbuilt_org_ids = serializers.ListField(
        child=serializers.CharField(),
        help_text="Orgs with an active config that could not be built. The "
        "gateway keeps their last good config instead of dropping them.",
    )


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
