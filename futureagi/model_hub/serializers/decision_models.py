"""Typed rows for the evaluator-only decision catalog on models_list."""

from rest_framework import serializers
from tfc.utils.serializer_fields import JsonValueField


class DecisionConnectionSerializer(serializers.Serializer):
    model_name = serializers.CharField()
    provider = serializers.CharField()
    label = serializers.CharField()
    is_available = serializers.BooleanField()


class DecisionModelGroupSerializer(serializers.Serializer):
    model_name = serializers.CharField()
    label = serializers.CharField()
    author = serializers.CharField()
    version = serializers.CharField()
    question_types = serializers.ListField(child=serializers.CharField())
    min_choices = serializers.IntegerField(min_value=1)
    max_choices = serializers.IntegerField(min_value=1)
    max_state_bytes = serializers.IntegerField(min_value=1)
    notice = serializers.CharField(allow_blank=True)
    unavailable_reason = serializers.CharField(allow_null=True)
    connections = DecisionConnectionSerializer(many=True)
    type = serializers.CharField()


class ModelListEntrySerializer(DecisionModelGroupSerializer):
    """One models_list row: decision fields occur only for model_type=decision.

    Swagger2 has no discriminated unions. Publish the additive typed fields as
    optional for this mixed endpoint; decision responses use the strict parent.
    """

    providers = serializers.CharField(required=False, allow_blank=True)
    is_available = serializers.BooleanField(required=False)
    logo_url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    best_for = JsonValueField(required=False, allow_null=True)
    use_case = JsonValueField(required=False, allow_null=True)
    cutoff = JsonValueField(required=False, allow_null=True)
    rate_limits = JsonValueField(required=False, allow_null=True)
    latency = JsonValueField(required=False, allow_null=True)
    pricing = JsonValueField(required=False, allow_null=True)

    def get_fields(self):
        fields = super().get_fields()
        for name, field in fields.items():
            if name not in {"model_name", "type"}:
                field.required = False
        return fields


class ModelListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = ModelListEntrySerializer(many=True)


class ModelListQuerySerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    search = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    model_type = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    exclude_providers = serializers.ListField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        required=False,
    )
