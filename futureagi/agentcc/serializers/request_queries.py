"""Public filters for gateway request logs and overview analytics."""

from rest_framework import serializers

from agentcc.models.request_log import AgentccRequestLog

# `AgentccRequestLog.api_key_id` holds the gateway's own key id — the value the
# proxy stamps on each log and the one log ingestion matches against
# `AgentccAPIKey.gateway_key_id`. Both are CharFields of opaque gateway ids
# (`gw-key-7f3a`), never the DB's UUID primary key, so a filter on this column
# is a string filter. Typing it as a UUID rejected every real key id while
# accepting none, and bounding it by the column's own width keeps a reference
# the column could not possibly hold an error rather than a silent empty result.
API_KEY_ID_MAX_LENGTH = AgentccRequestLog._meta.get_field("api_key_id").max_length


def _api_key_id_field():
    # Blank means "no key filter" to both consumers, exactly as for the
    # user_id/session_id filters alongside it.
    return serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=API_KEY_ID_MAX_LENGTH,
        help_text="Gateway key id (not the API key's UUID primary key).",
    )


class GatewayOverviewQuerySerializer(serializers.Serializer):
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)
    granularity = serializers.CharField(required=False)
    api_key_id = _api_key_id_field()


class GatewayRequestLogQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1)
    # The list never capped `limit`; documenting it must not start rejecting
    # callers that already passed values above 100.
    limit = serializers.IntegerField(required=False, min_value=1)
    user_id = serializers.CharField(required=False, allow_blank=True)
    session_id = serializers.CharField(required=False, allow_blank=True)
    api_key_id = _api_key_id_field()
    request_id = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(
        required=False, help_text="Comma-separated model names."
    )
    provider = serializers.CharField(
        required=False, help_text="Comma-separated provider names."
    )
    status_code = serializers.CharField(
        required=False, help_text="Comma-separated HTTP status codes."
    )
    min_status_code = serializers.IntegerField(required=False)
    max_status_code = serializers.IntegerField(required=False)
    is_error = serializers.BooleanField(required=False)
    cache_hit = serializers.BooleanField(required=False)
    fallback_used = serializers.BooleanField(required=False)
    guardrail_triggered = serializers.BooleanField(required=False)
    is_stream = serializers.BooleanField(required=False)
    started_after = serializers.DateTimeField(required=False)
    started_before = serializers.DateTimeField(required=False)
    min_latency = serializers.IntegerField(required=False)
    max_latency = serializers.IntegerField(required=False)
    min_cost = serializers.FloatField(required=False)
    max_cost = serializers.FloatField(required=False)
    min_tokens = serializers.IntegerField(required=False)
    max_tokens = serializers.IntegerField(required=False)
    q = serializers.CharField(required=False, allow_blank=True)
    search = serializers.CharField(required=False, allow_blank=True)
    ordering = serializers.CharField(required=False)
