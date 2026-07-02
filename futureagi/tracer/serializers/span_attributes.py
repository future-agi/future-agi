from rest_framework import serializers


class SpanAttributeProjectQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()


class SpanAttributeValuesQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    key = serializers.CharField()
    q = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=500)


class SpanAttributeDetailQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    key = serializers.CharField()


class SpanAttributeKeySerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.ChoiceField(choices=["string", "number", "boolean"])
    count = serializers.IntegerField()


class SpanAttributeKeysResponseSerializer(serializers.Serializer):
    result = SpanAttributeKeySerializer(many=True)


class SpanAttributeValueSerializer(serializers.Serializer):
    value = serializers.JSONField()
    count = serializers.IntegerField()


class SpanAttributeValuesResponseSerializer(serializers.Serializer):
    result = SpanAttributeValueSerializer(many=True)


class SpanAttributeTopValueSerializer(serializers.Serializer):
    value = serializers.JSONField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class SpanAttributeDetailResponseSerializer(serializers.Serializer):
    key = serializers.CharField()
    type = serializers.ChoiceField(choices=["string", "number", "boolean"])
    count = serializers.IntegerField()
    unique_values = serializers.IntegerField(required=False)
    top_values = SpanAttributeTopValueSerializer(many=True, required=False)
    min = serializers.FloatField(required=False, allow_null=True)
    max = serializers.FloatField(required=False, allow_null=True)
    avg = serializers.FloatField(required=False, allow_null=True)
    p50 = serializers.FloatField(required=False, allow_null=True)
    p95 = serializers.FloatField(required=False, allow_null=True)
