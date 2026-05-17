from rest_framework import serializers


class ModelHubEmptyRequestSerializer(serializers.Serializer):
    pass


class ModelHubJSONResponseSerializer(serializers.Serializer):
    status = serializers.JSONField(required=False)
    message = serializers.CharField(required=False, allow_blank=True)
    result = serializers.JSONField(required=False)
    data = serializers.JSONField(required=False)
    error = serializers.JSONField(required=False)
    detail = serializers.JSONField(required=False)


class ModelHubPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = serializers.ListField(child=serializers.JSONField())


class ModelHubErrorResponseSerializer(serializers.Serializer):
    status = serializers.JSONField(required=False)
    message = serializers.JSONField(required=False)
    error = serializers.JSONField(required=False)
    detail = serializers.JSONField(required=False)


MODEL_HUB_ERROR_RESPONSES = {
    400: ModelHubErrorResponseSerializer,
    403: ModelHubErrorResponseSerializer,
    404: ModelHubErrorResponseSerializer,
    409: ModelHubErrorResponseSerializer,
    500: ModelHubErrorResponseSerializer,
}


class AIEvalWriterRequestSerializer(serializers.Serializer):
    description = serializers.CharField()
    output_format = serializers.ChoiceField(
        choices=["prompt", "messages"],
        required=False,
        default="prompt",
    )


class AIEvalWriterResultSerializer(serializers.Serializer):
    prompt = serializers.CharField()


class AIEvalWriterResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AIEvalWriterResultSerializer()


class CustomAIModelCreateRequestSerializer(serializers.Serializer):
    model_provider = serializers.CharField()
    model_name = serializers.CharField()
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)
    config_json = serializers.JSONField(required=False, default=dict)
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CustomAIModelUpdateRequestSerializer(serializers.Serializer):
    model_name = serializers.CharField(required=False, allow_blank=True)
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)


class CustomAIModelDefaultMetricRequestSerializer(serializers.Serializer):
    metric_id = serializers.UUIDField()


class CustomAIModelBaselineRequestSerializer(serializers.Serializer):
    environment = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    model_version = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class CustomAIModelEditRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    model_name = serializers.CharField(required=False, allow_blank=True)
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)
    config_json = serializers.JSONField(required=False, default=dict)
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CustomAIModelCreateResponseDataSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class CustomAIModelCreateResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = CustomAIModelCreateResponseDataSerializer()


class CustomMetricMutationRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    model_id = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    prompt = serializers.CharField(required=False, allow_blank=True)
    metric_type = serializers.CharField(required=False, allow_blank=True)
    evaluation_type = serializers.CharField(required=False, allow_blank=True)
    datasets = serializers.JSONField(required=False)


class CustomMetricTestRequestSerializer(serializers.Serializer):
    prompt = serializers.CharField()


class CustomMetricTestResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    prompts = serializers.JSONField(required=False)


class CustomMetricListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    evaluation_type = serializers.CharField()


class CustomMetricListResponseSerializer(serializers.Serializer):
    metrics = CustomMetricListItemSerializer(many=True)


class MetricTagOptionSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.CharField()


class EmbeddingModelOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class KnowledgeBaseEmbeddingModelsResponseSerializer(serializers.Serializer):
    status = serializers.IntegerField()
    result = EmbeddingModelOptionSerializer(many=True)
