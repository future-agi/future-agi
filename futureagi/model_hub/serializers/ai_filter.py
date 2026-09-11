from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField


class AIFilterSchemaFieldSerializer(serializers.Serializer):
    field = serializers.CharField(max_length=512)
    property_id = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=512,
    )
    label = serializers.CharField(required=False, allow_blank=True, max_length=512)
    type = serializers.CharField(required=False, allow_blank=True, max_length=64)
    category = serializers.CharField(required=False, allow_blank=True, max_length=64)
    operators = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        default=list,
        max_length=32,
    )
    choices = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
        max_length=256,
    )
    choice_labels = serializers.DictField(
        child=serializers.CharField(max_length=512),
        required=False,
        default=dict,
    )

    def validate_choice_labels(self, value):
        if len(value) > 256:
            raise serializers.ValidationError(
                "Ensure this field has no more than 256 entries."
            )
        return value


class AIFilterRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=["build_filters", "select_fields", "smart"],
        required=False,
        default="build_filters",
    )
    query = serializers.CharField(max_length=4096)
    schema = AIFilterSchemaFieldSerializer(many=True, max_length=512)
    source = serializers.ChoiceField(
        choices=["traces", "sessions", "simulation", "dataset"],
        required=False,
        default="traces",
    )
    project_id = serializers.UUIDField(required=False, allow_null=True)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    run_test_id = serializers.UUIDField(required=False, allow_null=True)
    test_execution_id = serializers.UUIDField(required=False, allow_null=True)


class AIFilterConditionSerializer(serializers.Serializer):
    field = serializers.CharField()
    property_id = serializers.CharField(required=False, allow_blank=False)
    operator = serializers.CharField()
    # Shape varies by operator: string, number, bool, list, or null.
    value = JsonValueField(required=False, allow_null=True)


class AIFilterResultSerializer(serializers.Serializer):
    filters = AIFilterConditionSerializer(many=True, required=False)
    fields = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class AIFilterResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AIFilterResultSerializer()
