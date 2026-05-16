from rest_framework import serializers


class AIFilterSchemaFieldSerializer(serializers.Serializer):
    field = serializers.CharField()
    label = serializers.CharField(required=False, allow_blank=True)
    type = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    operators = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    choices = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    choice_labels = serializers.DictField(
        child=serializers.CharField(),
        required=False,
        default=dict,
    )


class AIFilterRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=["build_filters", "select_fields", "smart"],
        required=False,
        default="build_filters",
    )
    query = serializers.CharField()
    schema = AIFilterSchemaFieldSerializer(many=True)
    source = serializers.ChoiceField(
        choices=["traces", "dataset"],
        required=False,
        default="traces",
    )
    project_id = serializers.UUIDField(required=False, allow_null=True)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)


class AIFilterConditionSerializer(serializers.Serializer):
    field = serializers.CharField()
    operator = serializers.CharField()
    value = serializers.JSONField(required=False, allow_null=True)


class AIFilterResultSerializer(serializers.Serializer):
    filters = AIFilterConditionSerializer(many=True, required=False)
    fields = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class AIFilterResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AIFilterResultSerializer()
