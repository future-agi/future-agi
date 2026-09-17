from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField
from tracer.services.clickhouse.attribute_reads import (
    validate_attribute_key,
    validate_attribute_search,
)

SPAN_ATTRIBUTE_TYPES = ("string", "number", "boolean", "array", "map", "json")
SPAN_ATTRIBUTE_KEY_TYPES = SPAN_ATTRIBUTE_TYPES


class SpanAttributeProjectQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=False)
    workspace_scope = serializers.BooleanField(required=False, default=False)
    discovery_mode = serializers.ChoiceField(
        choices=["filter", "eval_mapping"],
        required=False,
        default="filter",
        help_text=(
            "Attribute contract to browse. filter returns only keys supported by "
            "attribute filters; eval_mapping also returns JSON-only keys that an "
            "evaluation mapping can resolve."
        ),
    )
    q = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=512,
    )
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=50)
    cursor = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=8_192,
    )

    def validate(self, attrs):
        workspace_scope = attrs.get("workspace_scope", False)
        has_project_id = attrs.get("project_id") is not None
        if workspace_scope == has_project_id:
            raise serializers.ValidationError(
                "Provide exactly one of project_id or workspace_scope=true."
            )
        if workspace_scope and "page_size" not in attrs:
            raise serializers.ValidationError(
                {"page_size": "page_size is required with workspace_scope"}
            )
        if attrs.get("cursor") and "page_size" not in attrs:
            raise serializers.ValidationError(
                {"page_size": "page_size is required with cursor"}
            )
        # ``q`` without ``page_size`` preserves the legacy bounded lookup.
        # With ``page_size`` it becomes a retained-data cursor search; the
        # signed cursor binds the exact key so continuations cannot be replayed
        # against a different attribute name.
        return attrs

    def validate_q(self, value):
        try:
            return validate_attribute_key(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class SpanAttributeValuesQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    key = serializers.CharField(max_length=512)
    q = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=512,
    )
    limit = serializers.IntegerField(required=False, min_value=1, max_value=500)

    def validate_key(self, value):
        try:
            return validate_attribute_key(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_q(self, value):
        try:
            return validate_attribute_search(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class SpanAttributeDetailQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    key = serializers.CharField(max_length=512)
    refresh = serializers.BooleanField(required=False, default=False)

    def validate_key(self, value):
        try:
            return validate_attribute_key(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class SpanAttributeKeySerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.ChoiceField(choices=SPAN_ATTRIBUTE_KEY_TYPES)
    count = serializers.IntegerField()
    count_exact = serializers.BooleanField(required=False)
    types = serializers.ListField(
        child=serializers.ChoiceField(choices=SPAN_ATTRIBUTE_KEY_TYPES),
        required=False,
    )


class SpanAttributeKeysResponseSerializer(serializers.Serializer):
    result = SpanAttributeKeySerializer(many=True)
    # Present only when the catalog proves the exact distinct-key cardinality
    # for the frozen, unsearched scope. Callers must treat omission as unknown
    # rather than deriving a misleading total from the loaded cursor pages.
    total_count = serializers.IntegerField(required=False, min_value=0)
    query_complete = serializers.BooleanField()
    query_status = serializers.ChoiceField(choices=["complete", "sampled", "degraded"])
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField()
    query_window_end = serializers.DateTimeField()
    query_window_mode = serializers.ChoiceField(
        choices=["frozen_snapshot"],
        required=False,
    )
    query_count = serializers.IntegerField(required=False, min_value=0)
    has_more = serializers.BooleanField(required=False)
    next_cursor = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=False,
        max_length=8_192,
    )
    browse_mode = serializers.ChoiceField(
        choices=["recent_suggestions"],
        required=False,
    )
    browse_status = serializers.ChoiceField(
        choices=["continuation", "exhausted", "limit_reached"],
        required=False,
    )
    browse_limit = serializers.IntegerField(required=False, min_value=1)
    lookup_mode = serializers.ChoiceField(choices=["exact"], required=False)
    exact_match = serializers.BooleanField(required=False)


class SpanAttributeValueSerializer(serializers.Serializer):
    value = JsonValueField(allow_null=True)
    count = serializers.IntegerField()
    type = serializers.ChoiceField(choices=SPAN_ATTRIBUTE_TYPES, required=False)


class SpanAttributeValuesResponseSerializer(serializers.Serializer):
    result = SpanAttributeValueSerializer(many=True)
    query_complete = serializers.BooleanField()
    query_status = serializers.ChoiceField(choices=["complete", "sampled", "degraded"])
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField()
    query_window_end = serializers.DateTimeField()


class SpanAttributeTopValueSerializer(serializers.Serializer):
    value = JsonValueField(allow_null=True)
    type = serializers.ChoiceField(choices=SPAN_ATTRIBUTE_TYPES, required=False)
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class SpanAttributeNumericStatsSerializer(serializers.Serializer):
    min = serializers.FloatField(allow_null=True, required=False)
    max = serializers.FloatField(allow_null=True, required=False)
    avg = serializers.FloatField(allow_null=True, required=False)
    p50 = serializers.FloatField(allow_null=True, required=False)
    p95 = serializers.FloatField(allow_null=True, required=False)


class SpanAttributeTypeSummarySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=SPAN_ATTRIBUTE_TYPES)
    count = serializers.IntegerField(min_value=0)
    unique_values = serializers.IntegerField(min_value=0)


class SpanAttributeDetailResponseSerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.ChoiceField(choices=SPAN_ATTRIBUTE_TYPES, allow_null=True)
    count = serializers.IntegerField()
    unique_values = serializers.IntegerField(required=False)
    types = SpanAttributeTypeSummarySerializer(many=True, required=False)
    top_values = SpanAttributeTopValueSerializer(many=True, required=False)
    min = serializers.FloatField(required=False, allow_null=True)
    max = serializers.FloatField(required=False, allow_null=True)
    avg = serializers.FloatField(required=False, allow_null=True)
    p50 = serializers.FloatField(required=False, allow_null=True)
    p95 = serializers.FloatField(required=False, allow_null=True)
    stats = SpanAttributeNumericStatsSerializer(required=False)
    query_complete = serializers.BooleanField()
    query_status = serializers.ChoiceField(
        choices=["complete", "pending", "sampled", "degraded"]
    )
    query_sampled = serializers.BooleanField()
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField(required=False)
    query_window_end = serializers.DateTimeField(required=False)
    query_count = serializers.IntegerField(required=False, min_value=0)
    query_elapsed_ms = serializers.FloatField(required=False, min_value=0)
    query_completed_at = serializers.DateTimeField(required=False)
    query_cached = serializers.BooleanField(required=False)
    query_refreshing = serializers.BooleanField(required=False)
    query_refresh_failed = serializers.BooleanField(required=False)
