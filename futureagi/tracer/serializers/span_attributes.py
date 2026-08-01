from rest_framework import serializers


class SpanAttributeProjectQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    # Browsing remains an intentionally bounded sample. Supplying ``q`` asks
    # for one exact key-existence/type probe so a known rare key can still be
    # selected without exploding every Map in the project.
    q = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=512,
        trim_whitespace=True,
    )


class SpanAttributeValuesQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    key = serializers.CharField()
    q = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=500)


class SpanAttributeDetailQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    key = serializers.CharField()
    type = serializers.ChoiceField(
        choices=["string", "number", "boolean"],
        required=False,
    )


class SpanAttributeKeySerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.ChoiceField(choices=["string", "number", "boolean"])
    count = serializers.IntegerField(required=False)


class SpanAttributeKeysResponseSerializer(serializers.Serializer):
    result = SpanAttributeKeySerializer(many=True)
    query_complete = serializers.BooleanField()
    query_status = serializers.ChoiceField(choices=["complete", "sampled", "degraded"])
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField()
    query_window_end = serializers.DateTimeField()


class SpanAttributeValueSerializer(serializers.Serializer):
    value = serializers.JSONField()
    count = serializers.IntegerField()


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
    value = serializers.JSONField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class SpanAttributeDetailResponseSerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.ChoiceField(
        choices=["string", "number", "boolean"],
        required=False,
    )
    count = serializers.IntegerField(required=False)
    unique_values = serializers.IntegerField(required=False)
    top_values = SpanAttributeTopValueSerializer(many=True, required=False)
    min = serializers.FloatField(required=False, allow_null=True)
    max = serializers.FloatField(required=False, allow_null=True)
    avg = serializers.FloatField(required=False, allow_null=True)
    p50 = serializers.FloatField(required=False, allow_null=True)
    p95 = serializers.FloatField(required=False, allow_null=True)
    query_complete = serializers.BooleanField()
    query_status = serializers.ChoiceField(choices=["complete", "sampled", "degraded"])
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField()
    query_window_end = serializers.DateTimeField()
