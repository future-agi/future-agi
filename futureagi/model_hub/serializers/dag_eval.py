"""Request and response contracts for decision-tree evals."""

from rest_framework import serializers

MAX_VALUE = 20000
MAX_KEYS = 50


class BoundedDictField(serializers.DictField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if len(value) > MAX_KEYS:
            raise serializers.ValidationError(f"At most {MAX_KEYS} entries.")
        return value


class DagDefinitionField(serializers.JSONField):
    """The definition is validated by the service; only its envelope here."""

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("A definition is an object.")
        return super().to_internal_value(data)


class DagCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    output_type = serializers.ChoiceField(
        choices=("score", "pass_fail"), required=False
    )
    pass_threshold = serializers.FloatField(
        min_value=0, max_value=1, required=False, allow_null=True
    )
    dag = DagDefinitionField()
    mapping = BoundedDictField(
        child=serializers.CharField(max_length=255, allow_blank=True), required=False
    )
    model = serializers.CharField(max_length=255, required=False, allow_blank=True)


class DagUpdateRequestSerializer(serializers.Serializer):
    description = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    output_type = serializers.ChoiceField(
        choices=("score", "pass_fail"), required=False
    )
    pass_threshold = serializers.FloatField(
        min_value=0, max_value=1, required=False, allow_null=True
    )
    dag = DagDefinitionField(required=False)
    mapping = BoundedDictField(
        child=serializers.CharField(max_length=255, allow_blank=True), required=False
    )


class DagExecuteRequestSerializer(serializers.Serializer):
    dag = DagDefinitionField()
    output = serializers.CharField(max_length=MAX_VALUE, allow_blank=True)
    pass_threshold = serializers.FloatField(
        min_value=0, max_value=1, required=False, allow_null=True
    )
    template_id = serializers.UUIDField(required=False, allow_null=True)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)
    row_id = serializers.UUIDField(required=False, allow_null=True)
    mapping = BoundedDictField(
        child=serializers.CharField(max_length=255, allow_blank=True), required=False
    )
    values = BoundedDictField(
        child=serializers.CharField(max_length=MAX_VALUE, allow_blank=True),
        required=False,
    )

    def validate(self, attrs):
        if bool(attrs.get("dataset_id")) != bool(attrs.get("row_id")):
            raise serializers.ValidationError("Name both a dataset and a row.")
        return attrs


class DagRunRequestSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    mapping = BoundedDictField(child=serializers.UUIDField())
    limit = serializers.IntegerField(min_value=1, max_value=20, required=False)

    def validate_mapping(self, mapping):
        if "output" not in mapping:
            raise serializers.ValidationError("Map {{output}} to a column.")
        return {key: str(value) for key, value in mapping.items()}


class DagErrorResponseSerializer(serializers.Serializer):
    errors = serializers.ListField(child=serializers.CharField())
    error = serializers.CharField(required=False)


class DagCreatedSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    eval_type = serializers.CharField()
    version = serializers.CharField()


class DagCreateResponseSerializer(serializers.Serializer):
    result = DagCreatedSerializer()


class DagDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    output_type = serializers.CharField()
    pass_threshold = serializers.FloatField(allow_null=True)
    dag = serializers.JSONField()
    mapping = serializers.JSONField(allow_null=True)
    instructions = serializers.CharField(allow_blank=True)
    model = serializers.CharField(allow_null=True)
    version = serializers.CharField()


class DagDetailResponseSerializer(serializers.Serializer):
    result = DagDetailSerializer()


class DagUpdatedSerializer(serializers.Serializer):
    version = serializers.CharField()
    instructions = serializers.CharField()


class DagUpdateResponseSerializer(serializers.Serializer):
    result = DagUpdatedSerializer()


class DagExecutionSerializer(serializers.Serializer):
    value = serializers.FloatField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    skipped = serializers.CharField(allow_null=True)
    reason = serializers.CharField()
    path = serializers.JSONField()
    path_key = serializers.CharField()
    path_label = serializers.CharField(allow_blank=True)
    leaf = serializers.JSONField(allow_null=True)
    judge_calls = serializers.IntegerField()
    judge_cost_usd = serializers.FloatField(allow_null=True)
    latency_ms = serializers.IntegerField()


class DagExecuteResponseSerializer(serializers.Serializer):
    result = DagExecutionSerializer()


class DagRunRowSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    value = serializers.FloatField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)
    skipped = serializers.CharField(allow_null=True)
    path_key = serializers.CharField()
    path_label = serializers.CharField(allow_blank=True)


class DagRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    run_label = serializers.CharField()
    n = serializers.IntegerField()
    rows = DagRunRowSerializer(many=True)


class DagRunResponseSerializer(serializers.Serializer):
    result = DagRunSerializer()


class DagPathRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    run_label = serializers.CharField()


class DagPathGroupSerializer(serializers.Serializer):
    path_key = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    rows = serializers.ListField(child=serializers.UUIDField())
    n = serializers.IntegerField(min_value=0)
    share = serializers.FloatField(min_value=0, max_value=1)
    mean = serializers.FloatField(allow_null=True)


class DagPathsSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    name = serializers.CharField()
    runs = DagPathRunSerializer(many=True)
    experiment_id = serializers.UUIDField(allow_null=True)
    run_id = serializers.UUIDField(allow_null=True)
    run_label = serializers.CharField(allow_null=True)
    dataset_id = serializers.UUIDField(allow_null=True)
    n = serializers.IntegerField(min_value=0)
    paths = DagPathGroupSerializer(many=True)


class DagPathsResponseSerializer(serializers.Serializer):
    result = DagPathsSerializer()


class DagPathsQuerySerializer(serializers.Serializer):
    run_id = serializers.UUIDField(required=False)
