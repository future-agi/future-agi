import json

from rest_framework import serializers

from model_hub.constants import MAX_EMPTY_DATASET_ROWS
from model_hub.models.choices import ModelTypes
from model_hub.serializers.optimize_dataset import (
    OptimizeDatasetKbSerializer,
    OptimizeDatasetSerializer,
)
from model_hub.serializers.experiments import _ExtraFieldsMixin
from model_hub.serializers.performance_report import PerformanceReportSerializer
from model_hub.services.ai_eval_writer_service import OUTPUT_FORMAT_PROMPTS
from tfc.utils.api_errors import API_ERROR_TYPE_CHOICES
from tfc.utils.serializer_fields import StringOrObjectField
from tracer.serializers.filters import (
    SortParamField,
    StrictInputSerializer,
    filter_list_field,
    filter_list_query_param_field,
    json_object_query_param_field,
    parse_filter_list_payload,
)


class ModelHubEmptyRequestSerializer(StrictInputSerializer):
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
    status = serializers.BooleanField(required=False)
    type = serializers.ChoiceField(
        choices=API_ERROR_TYPE_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    result = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attr = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class ModelHubTextErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    type = serializers.ChoiceField(
        choices=API_ERROR_TYPE_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    result = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attr = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class JsonObjectRequestField(serializers.JSONField):
    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": True,
        }

    def to_internal_value(self, data):
        if data in (None, ""):
            return None if self.allow_null else {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Value must be valid JSON.") from exc
        value = super().to_internal_value(data)
        if value is None and self.allow_null:
            return None
        if not isinstance(value, dict):
            raise serializers.ValidationError("Value must be an object.")
        return value


MODEL_HUB_ERROR_RESPONSES = {
    400: ModelHubErrorResponseSerializer,
    403: ModelHubErrorResponseSerializer,
    404: ModelHubErrorResponseSerializer,
    409: ModelHubErrorResponseSerializer,
    500: ModelHubErrorResponseSerializer,
}


MODEL_HUB_TEXT_ERROR_RESPONSES = {
    400: ModelHubTextErrorResponseSerializer,
    403: ModelHubTextErrorResponseSerializer,
    404: ModelHubTextErrorResponseSerializer,
    409: ModelHubTextErrorResponseSerializer,
    500: ModelHubTextErrorResponseSerializer,
}


class ModelHubStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class ModelHubStatusMessageResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()


class ModelHubStringResultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()


class ModelHubSuccessMessageResultSerializer(serializers.Serializer):
    success = serializers.CharField()


class ModelHubSuccessMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ModelHubSuccessMessageResultSerializer()


class CustomEvalTemplateCreateResponseResultSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()


class CustomEvalTemplateCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CustomEvalTemplateCreateResponseResultSerializer()


class DuplicateEvalTemplateResponseResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    eval_template_id = serializers.UUIDField()


class DuplicateEvalTemplateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DuplicateEvalTemplateResponseResultSerializer()


class BaseColumnsResponseResultSerializer(serializers.Serializer):
    base_columns = serializers.ListField(child=serializers.CharField())


class BaseColumnsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = BaseColumnsResponseResultSerializer()


class DatasetExplanationSummaryResponseResultSerializer(serializers.Serializer):
    response = serializers.JSONField(allow_null=True)
    last_updated = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()
    row_count = serializers.IntegerField()
    min_rows_required = serializers.IntegerField()


class DatasetExplanationSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetExplanationSummaryResponseResultSerializer()


class HuggingFaceDatasetListItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    downloads = serializers.IntegerField()
    likes = serializers.IntegerField()
    author = serializers.CharField(allow_null=True, required=False)


class HuggingFaceDatasetListResponseResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    total_datasets = serializers.IntegerField()
    datasets = HuggingFaceDatasetListItemSerializer(many=True)


class HuggingFaceDatasetListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = HuggingFaceDatasetListResponseResultSerializer()


class HuggingFaceDatasetDetailSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    downloads = serializers.IntegerField()
    likes = serializers.IntegerField()
    tags = serializers.ListField(child=serializers.CharField())
    author = serializers.CharField(allow_null=True, required=False)


class HuggingFaceDatasetDetailResponseResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    dataset = HuggingFaceDatasetDetailSerializer()


class HuggingFaceDatasetDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = HuggingFaceDatasetDetailResponseResultSerializer()


class EvalSummaryTemplateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    criteria = serializers.CharField()


class EvalSummaryTemplateListResponseResultSerializer(serializers.Serializer):
    templates = EvalSummaryTemplateSerializer(many=True)


class EvalSummaryTemplateListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalSummaryTemplateListResponseResultSerializer()


class EvalSummaryTemplateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalSummaryTemplateSerializer()


class EvalSummaryTemplateDeleteResponseResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()


class EvalSummaryTemplateDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalSummaryTemplateDeleteResponseResultSerializer()


class AIEvalWriterRequestSerializer(serializers.Serializer):
    description = serializers.CharField()
    output_format = serializers.ChoiceField(
        # Single source of truth: the dispatch dict in the service. Adding a
        # format there automatically extends the accepted choices here.
        choices=list(OUTPUT_FORMAT_PROMPTS),
        required=False,
        default="prompt",
    )


class AIEvalWriterResultSerializer(serializers.Serializer):
    # Exactly one field is set, matching the request's output_format:
    #   prompt    -> instruction text (string)
    #   messages  -> LLM-as-a-Judge messages (list of {role, content})
    #   test_data -> generated test data (object of variable -> value)
    prompt = serializers.CharField(required=False, allow_null=True)
    messages = serializers.ListField(
        child=serializers.DictField(), required=False, allow_null=True
    )
    test_data = serializers.DictField(required=False, allow_null=True)


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
    environment = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
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


class CustomAIModelDeleteRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class CustomAIModelEditResultSerializer(serializers.Serializer):
    model_name = serializers.CharField()
    input_token_cost = serializers.FloatField(required=False, allow_null=True)
    output_token_cost = serializers.FloatField(required=False, allow_null=True)
    model_provider = serializers.CharField()
    key = serializers.JSONField(required=False)
    config_json = serializers.JSONField(required=False)


class CustomAIModelEditResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CustomAIModelEditResultSerializer()


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


class MetricsByColumnItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    template_name = serializers.CharField()
    eval_template_name = serializers.CharField()
    eval_required_keys = serializers.ListField(child=serializers.CharField())
    eval_template_tags = serializers.ListField(child=serializers.CharField())
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    model = serializers.CharField(required=False, allow_blank=True)
    column_id = serializers.UUIDField(required=False, allow_null=True)
    updated_at = serializers.DateTimeField()
    eval_group = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    status = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    eval_type = serializers.CharField()
    template_type = serializers.CharField()
    template_id = serializers.UUIDField()
    owner = serializers.CharField()
    mapping = serializers.JSONField()
    params = serializers.JSONField()
    error_localizer = serializers.BooleanField()
    run_config = serializers.JSONField()
    output_type = serializers.CharField()
    aggregation_function = serializers.CharField(required=False, allow_blank=True)
    aggregation_enabled = serializers.BooleanField(required=False)
    children_count = serializers.IntegerField(required=False)


class MetricsByColumnResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MetricsByColumnItemSerializer(many=True)


class MetricTagOptionSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.CharField()


class ModelParameterSliderSerializer(serializers.Serializer):
    label = serializers.CharField()
    min = serializers.FloatField(required=False, allow_null=True)
    max = serializers.FloatField(required=False, allow_null=True)
    step = serializers.FloatField(required=False, allow_null=True)
    default = serializers.JSONField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True)


class ModelParameterChoiceSerializer(serializers.Serializer):
    label = serializers.CharField()
    options = serializers.ListField(child=serializers.JSONField())
    default = serializers.JSONField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True)


class ModelParameterBooleanSerializer(serializers.Serializer):
    label = serializers.CharField()
    default = serializers.BooleanField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)


class ModelParameterTextInputSerializer(serializers.Serializer):
    label = serializers.CharField()
    default = serializers.JSONField(required=False, allow_null=True)
    placeholder = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)


class ModelParameterResponseFormatSerializer(serializers.Serializer):
    value = serializers.CharField()


class ModelParameterReasoningSerializer(serializers.Serializer):
    dropdowns = ModelParameterChoiceSerializer(many=True, required=False)
    sliders = ModelParameterSliderSerializer(many=True, required=False)


class ModelParametersResultSerializer(serializers.Serializer):
    sliders = ModelParameterSliderSerializer(many=True, required=False)
    dropdowns = ModelParameterChoiceSerializer(many=True, required=False)
    booleans = ModelParameterBooleanSerializer(many=True, required=False)
    boolean = ModelParameterBooleanSerializer(many=True, required=False)
    checkboxes = ModelParameterBooleanSerializer(many=True, required=False)
    text_inputs = ModelParameterTextInputSerializer(many=True, required=False)
    responseFormat = ModelParameterResponseFormatSerializer(many=True, required=False)
    reasoning = ModelParameterReasoningSerializer(required=False)


class ModelParametersResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ModelParametersResultSerializer()


class OverviewPointSerializer(serializers.Serializer):
    x = serializers.JSONField()
    y = serializers.IntegerField()


class OverviewCountSeriesSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    change = serializers.FloatField(allow_null=True)


class OverviewVolumeSerializer(OverviewCountSeriesSerializer):
    volume = OverviewPointSerializer(many=True)


class OverviewIssuesSerializer(OverviewCountSeriesSerializer):
    last_day = OverviewPointSerializer(many=True)


class OverviewResponseSerializer(serializers.Serializer):
    volume = OverviewVolumeSerializer()
    issues = OverviewIssuesSerializer()
    versions = serializers.JSONField()

    class Meta:
        ref_name = "ModelHubOverviewResponse"


class PromptMetricsMetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()


class PromptMetricsResultSerializer(serializers.Serializer):
    prompt_template_id = serializers.UUIDField(required=False)
    prompt_template_name = serializers.CharField(required=False)
    table = serializers.ListField(child=serializers.JSONField())
    config = serializers.JSONField()
    metadata = PromptMetricsMetadataSerializer()


class PromptMetricsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PromptMetricsResultSerializer()


class PromptMetricsQuerySerializer(serializers.Serializer):
    prompt_template_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)
    search_term = serializers.CharField(required=False, allow_blank=True, default="")
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=10, min_value=1, max_value=100
    )


class PromptMetricsEmptyScreenResultSerializer(serializers.Serializer):
    python = serializers.CharField()
    typescript = serializers.CharField()


class PromptMetricsEmptyScreenResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PromptMetricsEmptyScreenResultSerializer()


class LiteLLMVoiceOptionSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()


class LiteLLMModelVoicesResultSerializer(serializers.Serializer):
    model_name = serializers.CharField()
    provider = serializers.CharField(allow_blank=True)
    custom_voice_supported = serializers.BooleanField()
    supported_voices = LiteLLMVoiceOptionSerializer(many=True)
    supported_formats = serializers.ListField(child=serializers.CharField())
    default_voice = serializers.CharField(required=False, allow_null=True)
    default_format = serializers.CharField(required=False, allow_null=True)


class LiteLLMModelVoicesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LiteLLMModelVoicesResultSerializer()


class RunPromptColumnConfigResultSerializer(serializers.Serializer):
    config = serializers.JSONField()


class RunPromptColumnConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RunPromptColumnConfigResultSerializer()


class RunPromptToolOptionSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    yaml_config = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    config = serializers.JSONField(required=False)
    config_type = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    description = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )


class RunPromptChoiceOptionSerializer(serializers.Serializer):
    value = serializers.JSONField()
    label = serializers.CharField()


class RunPromptOptionsResultSerializer(serializers.Serializer):
    models = serializers.ListField(child=serializers.JSONField())
    tool_config = serializers.JSONField()
    available_tools = RunPromptToolOptionSerializer(many=True)
    output_formats = RunPromptChoiceOptionSerializer(many=True)
    tool_choices = RunPromptChoiceOptionSerializer(many=True)


class RunPromptOptionsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RunPromptOptionsResultSerializer()


class DatasetRunPromptStatsPromptSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    input_token = serializers.FloatField()
    output_token = serializers.FloatField()
    total_token = serializers.FloatField()


class DatasetRunPromptStatsResultSerializer(serializers.Serializer):
    avg_tokens = serializers.FloatField()
    avg_cost = serializers.FloatField()
    avg_time = serializers.FloatField()
    prompts = DatasetRunPromptStatsPromptSerializer(many=True)


class DatasetRunPromptStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRunPromptStatsResultSerializer()


class EmbeddingModelOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class KnowledgeBaseEmbeddingModelsResponseSerializer(serializers.Serializer):
    status = serializers.IntegerField()
    result = EmbeddingModelOptionSerializer(many=True)


class EmbeddingConfigOptionSerializer(serializers.Serializer):
    type = serializers.CharField()
    required = serializers.BooleanField()
    description = serializers.CharField()
    default = serializers.CharField(required=False, allow_blank=True)


class EmbeddingProviderSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()
    requires_api_key = serializers.BooleanField()
    config_schema = serializers.DictField(child=EmbeddingConfigOptionSerializer())


class EmbeddingsResponseResultSerializer(serializers.Serializer):
    embeddings = serializers.DictField(
        child=EmbeddingProviderSerializer(),
        required=False,
    )
    embedding = EmbeddingProviderSerializer(required=False)


class EmbeddingsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EmbeddingsResponseResultSerializer()


class KnowledgeBaseItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    embedding_model = serializers.CharField()
    chunk_size = serializers.IntegerField()
    organization = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class KnowledgeBaseResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = KnowledgeBaseItemSerializer()


class KnowledgeBasePaginatedResultSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = KnowledgeBaseItemSerializer(many=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_queries = serializers.IntegerField(required=False)


class KnowledgeBaseListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = KnowledgeBasePaginatedResultSerializer()


class LegacyKnowledgeBaseMutationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    kb_id = serializers.UUIDField(required=False)
    files = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class LegacyKnowledgeBaseFilesRequestSerializer(serializers.Serializer):
    kb_id = serializers.UUIDField()
    search = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    sort = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    page_number = serializers.IntegerField(required=False, default=0)
    page_size = serializers.IntegerField(required=False, default=10)


class LegacyKnowledgeBaseSortQueryParamField(serializers.Field):
    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Sort must be valid JSON.") from exc
        if not isinstance(data, list):
            raise serializers.ValidationError("Sort must be a list.")
        for item in data:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each sort item must be an object.")
            unknown = sorted(set(item.keys()) - {"column_id", "type"})
            if unknown:
                raise serializers.ValidationError(
                    {key: ["Unknown field."] for key in unknown}
                )
            if "column_id" not in item or "type" not in item:
                raise serializers.ValidationError(
                    "Each sort item requires column_id and type."
                )
            if item["type"] not in ("ascending", "descending"):
                raise serializers.ValidationError(
                    "Sort type must be ascending or descending."
                )
        return data

    def to_representation(self, value):
        return value


class LegacyKnowledgeBaseTableQuerySerializer(StrictInputSerializer):
    search = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    sort = LegacyKnowledgeBaseSortQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(required=False, default=10, min_value=1)


class LegacyKnowledgeBaseSdkCodeResultSerializer(serializers.Serializer):
    code = serializers.CharField()


class LegacyKnowledgeBaseSdkCodeResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyKnowledgeBaseSdkCodeResultSerializer()


class LegacyKnowledgeBaseCreateResultSerializer(serializers.Serializer):
    detail = serializers.CharField()
    kb_id = serializers.UUIDField()
    kb_name = serializers.CharField()
    file_ids = serializers.ListField(child=serializers.UUIDField())


class LegacyKnowledgeBaseCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyKnowledgeBaseCreateResultSerializer()


class LegacyKnowledgeBaseMutationResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    organization = serializers.UUIDField()
    status = serializers.CharField()
    files = serializers.ListField(child=serializers.UUIDField())
    updated_at = serializers.DateTimeField()
    created_by = serializers.CharField(allow_blank=True, allow_null=True)
    last_error = serializers.CharField(allow_blank=True, allow_null=True)


class LegacyKnowledgeBaseMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyKnowledgeBaseMutationResultSerializer()


class LegacyKnowledgeBaseTableColumnSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class LegacyKnowledgeBaseTableRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    files_uploaded = serializers.IntegerField()
    status = serializers.CharField()
    error = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    updated_at = serializers.DateTimeField()
    created_by = serializers.CharField(allow_blank=True, allow_null=True)


class LegacyKnowledgeBaseTableResultSerializer(serializers.Serializer):
    column_config = LegacyKnowledgeBaseTableColumnSerializer(many=True, required=False)
    table_data = LegacyKnowledgeBaseTableRowSerializer(many=True, required=False)
    total_rows = serializers.IntegerField(required=False)


class LegacyKnowledgeBaseTableResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyKnowledgeBaseTableResultSerializer()


class LegacyKnowledgeBaseOptionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class LegacyKnowledgeBaseListResultSerializer(serializers.Serializer):
    table_data = LegacyKnowledgeBaseOptionSerializer(many=True)


class LegacyKnowledgeBaseListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyKnowledgeBaseListResultSerializer()


class LegacyKnowledgeBaseFileRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    file_size = serializers.IntegerField()
    status = serializers.CharField()
    updated = serializers.DateTimeField()
    updated_by = serializers.CharField(allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class LegacyKnowledgeBaseFilesResultSerializer(serializers.Serializer):
    table_data = LegacyKnowledgeBaseFileRowSerializer(many=True)
    last_updated = serializers.DateTimeField()
    status = serializers.CharField()
    status_count = serializers.IntegerField()
    total_rows = serializers.IntegerField()


class LegacyKnowledgeBaseFilesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyKnowledgeBaseFilesResultSerializer()


class OptimizeDatasetMutationRequestSerializer(StrictInputSerializer):
    name = serializers.CharField(allow_blank=True)
    start_date = serializers.CharField(allow_blank=True)
    end_date = serializers.CharField(allow_blank=True)
    model = serializers.UUIDField()
    optimize_type = serializers.CharField(allow_blank=True)
    environment = serializers.CharField(allow_blank=True)
    version = serializers.CharField(allow_blank=True)
    metrics = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class OptimizeDatasetFilterSerializer(serializers.Serializer):
    key = serializers.ChoiceField(
        choices=[
            "name",
            "optimize_type",
            "environment",
            "version",
            "status",
            "created_at",
            "updated_at",
            "start_date",
            "end_date",
        ]
    )
    operator = serializers.ChoiceField(choices=["equals", "between"])
    value = serializers.ListField(child=serializers.JSONField(), allow_empty=False)
    data_type = serializers.ChoiceField(
        choices=["string", "number", "date", "datetime"], required=False
    )

    def validate(self, data):
        if data["operator"] == "between" and len(data["value"]) != 2:
            raise serializers.ValidationError("between requires exactly two values.")
        return data


class OptimizeDatasetFilterListQueryParamField(serializers.CharField):
    def to_internal_value(self, data):
        filters = parse_filter_list_payload(data)
        return serializers.ListField(
            child=OptimizeDatasetFilterSerializer()
        ).run_validation(filters)


class OptimizeDatasetListQuerySerializer(StrictInputSerializer):
    filters = OptimizeDatasetFilterListQueryParamField(required=False, default=list)
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    limit = serializers.IntegerField(required=False, default=15, min_value=1)


class OptimizeDatasetKnowledgeBaseRequestSerializer(StrictInputSerializer):
    name = serializers.CharField(required=False, allow_blank=True)
    knowledge_base_metrics = serializers.JSONField(required=False)
    knowledge_base_filters = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        default=list,
    )
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class OptimizeDatasetPageRequestSerializer(StrictInputSerializer):
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    limit = serializers.IntegerField(required=False, default=10, min_value=1)


class OptimizeDatasetColumnConfigUpdateRequestSerializer(StrictInputSerializer):
    columns = serializers.ListField(child=serializers.JSONField(), allow_empty=True)


class OptimizeDatasetPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = OptimizeDatasetSerializer(many=True)
    total_pages = serializers.IntegerField(required=False)
    current_page = serializers.IntegerField(required=False)


class OptimizeDatasetCreateDataSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class OptimizeDatasetCreateResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = OptimizeDatasetCreateDataSerializer(allow_null=True)


class OptimizeDatasetDetailResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    data = OptimizeDatasetSerializer(allow_null=True)


class OptimizeDatasetColumnConfigResponseSerializer(serializers.Serializer):
    columns = serializers.ListField(child=serializers.JSONField())
    status = serializers.CharField()


class OptimizeDatasetColumnConfigUpdateResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    status = serializers.CharField()


class OptimizeDatasetTemplateResultSerializer(serializers.Serializer):
    metric_name = serializers.CharField()
    templates = serializers.ListField(child=serializers.FloatField())
    old_template = serializers.FloatField()


class OptimizeDatasetTemplateResultsResponseSerializer(serializers.Serializer):
    k_prompts = serializers.ListField(child=serializers.CharField())
    results = OptimizeDatasetTemplateResultSerializer(many=True)


class OptimizeDatasetKnowledgeBaseCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.UUIDField()


class OptimizeDatasetKnowledgeBaseListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OptimizeDatasetKbSerializer(many=True)


class OptimizeDatasetKnowledgeBaseDetailResultSerializer(serializers.Serializer):
    name = serializers.CharField()
    prompt = serializers.CharField(allow_blank=True, allow_null=True)
    knowledge_base_filters = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )
    knowledge_base_metrics = serializers.JSONField(allow_null=True)
    variables = serializers.JSONField(allow_null=True)
    status = serializers.CharField()
    optimized_k_prompts = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )


class OptimizeDatasetKnowledgeBaseDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OptimizeDatasetKnowledgeBaseDetailResultSerializer()


class EvalConfigQuerySerializer(StrictInputSerializer):
    eval_id = serializers.UUIDField()


class EvalStructureQuerySerializer(StrictInputSerializer):
    eval_type = serializers.ChoiceField(
        choices=["preset", "user", "previously_configured"]
    )


PERFORMANCE_FILTER_TYPES = ("property", "performanceMetric", "performanceTag")
PERFORMANCE_DATA_TYPES = ("string", "number")
PERFORMANCE_OPERATORS = (
    "equal",
    "notEqual",
    "greaterThan",
    "greaterThanEqualTo",
    "lessThan",
    "lessThanEqualTo",
)
PERFORMANCE_AGGREGATIONS = ("hourly", "daily", "weekly", "monthly")
PERFORMANCE_TAG_GRAPH_TYPES = ("all", "good", "bad")


class PerformanceFilterSerializer(StrictInputSerializer):
    type = serializers.ChoiceField(choices=PERFORMANCE_FILTER_TYPES)
    datatype = serializers.ChoiceField(choices=PERFORMANCE_DATA_TYPES)
    operator = serializers.ChoiceField(choices=PERFORMANCE_OPERATORS)
    values = serializers.ListField(child=serializers.JSONField(), default=list)
    key = serializers.CharField(allow_blank=True)
    key_id = serializers.CharField(allow_blank=True)


class PerformanceDatasetSerializer(StrictInputSerializer):
    environment = serializers.CharField()
    version = serializers.CharField()
    metric_id = serializers.UUIDField()
    filters = PerformanceFilterSerializer(many=True, required=False, default=list)


class PerformanceBreakdownSerializer(StrictInputSerializer):
    key = serializers.CharField()
    key_id = serializers.CharField()


class PerformanceQueryRequestSerializer(StrictInputSerializer):
    datasets = PerformanceDatasetSerializer(many=True)
    filters = PerformanceFilterSerializer(many=True, required=False, default=list)
    breakdown = PerformanceBreakdownSerializer(many=True, required=False, default=list)
    agg_by = serializers.ChoiceField(choices=PERFORMANCE_AGGREGATIONS)
    start_date = serializers.CharField()
    end_date = serializers.CharField()


class PerformanceDetailsRequestSerializer(StrictInputSerializer):
    dataset = PerformanceDatasetSerializer()
    filters = PerformanceFilterSerializer(many=True, required=False, default=list)
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    start_date = serializers.CharField()
    end_date = serializers.CharField()


class PerformanceExportRequestSerializer(StrictInputSerializer):
    dataset = PerformanceDatasetSerializer()
    filters = PerformanceFilterSerializer(many=True, required=False, default=list)
    page = serializers.IntegerField(required=False, min_value=1)
    start_date = serializers.CharField()
    end_date = serializers.CharField()


class PerformanceTagDistributionRequestSerializer(StrictInputSerializer):
    dataset = PerformanceDatasetSerializer()
    filters = PerformanceFilterSerializer(many=True, required=False, default=list)
    agg_by = serializers.ChoiceField(choices=PERFORMANCE_AGGREGATIONS)
    start_date = serializers.CharField()
    end_date = serializers.CharField()
    graph_type = serializers.ChoiceField(choices=PERFORMANCE_TAG_GRAPH_TYPES)


class PerformanceMetricOptionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class PerformancePropertyOptionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    datatype = serializers.CharField()
    values = serializers.ListField(child=serializers.JSONField())


class PerformanceOptionsResultSerializer(serializers.Serializer):
    performance_metric = PerformanceMetricOptionSerializer(many=True)
    properties = PerformancePropertyOptionSerializer(many=True)
    meta_tags = serializers.ListField(child=serializers.CharField())
    performance_tags = serializers.ListField(child=serializers.CharField())


class PerformanceOptionsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PerformanceOptionsResultSerializer()


class PerformanceDetailsResponseSerializer(serializers.Serializer):
    result = serializers.ListField(child=serializers.JSONField())
    processing_count = serializers.IntegerField()
    count = serializers.IntegerField()
    is_next = serializers.BooleanField()
    page = serializers.IntegerField()


class PerformanceReportPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = PerformanceReportSerializer(many=True)
    total_pages = serializers.IntegerField(required=False)
    current_page = serializers.IntegerField(required=False)


class PerformanceReportCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PerformanceReportSerializer()


class VectorDBColumnRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    new_column_name = serializers.CharField(required=False, allow_blank=True)
    sub_type = serializers.CharField()
    api_key = serializers.CharField()
    collection_name = serializers.CharField(required=False, allow_blank=True)
    url = serializers.CharField(required=False, allow_blank=True)
    search_type = serializers.CharField(required=False, allow_blank=True)
    key = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False)
    index_name = serializers.CharField(required=False, allow_blank=True)
    top_k = serializers.IntegerField(required=False)
    namespace = serializers.CharField(required=False, allow_blank=True)
    embedding_config = serializers.JSONField(required=False)
    concurrency = serializers.IntegerField(required=False, default=5)
    query_key = serializers.CharField(required=False, allow_blank=True)
    vector_length = serializers.IntegerField(required=False)


class ExtractJsonColumnRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    json_key = serializers.CharField()
    new_column_name = serializers.CharField(required=False, allow_blank=True)
    concurrency = serializers.IntegerField(required=False, default=5)


class ClassifyColumnRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    labels = serializers.ListField(child=serializers.CharField())
    language_model_id = serializers.CharField(required=False, default="gpt-4o")
    concurrency = serializers.IntegerField(required=False, default=5)
    new_column_name = serializers.CharField(required=False, allow_blank=True)


class ExtractEntitiesRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    instruction = serializers.CharField()
    language_model_id = serializers.CharField(required=False, default="gpt-4")
    concurrency = serializers.IntegerField(required=False, default=5)
    new_column_name = serializers.CharField(required=False, allow_blank=True)


class AddApiColumnRequestSerializer(serializers.Serializer):
    column_name = serializers.CharField()
    config = serializers.JSONField()
    concurrency = serializers.IntegerField(required=False, default=5)


class PythonCodeColumnRequestSerializer(serializers.Serializer):
    code = serializers.CharField()
    new_column_name = serializers.CharField(required=False, allow_blank=True)
    concurrency = serializers.IntegerField(required=False, default=5)


class ConditionalColumnRequestSerializer(serializers.Serializer):
    config = serializers.ListField(child=serializers.JSONField())
    new_column_name = serializers.CharField()
    concurrency = serializers.IntegerField(required=False, default=5)


class RerunOperationRequestSerializer(serializers.Serializer):
    operation_type = serializers.CharField()
    config = serializers.JSONField(required=False)


class OperationConfigResultSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    metadata = serializers.JSONField()


class OperationConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OperationConfigResultSerializer()


class RerunOperationResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    column_id = serializers.UUIDField()
    status = serializers.CharField()


class RerunOperationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RerunOperationResultSerializer()


class CellErrorLocalizerResultSerializer(serializers.Serializer):
    task_id = serializers.UUIDField(required=False)
    cell_id = serializers.UUIDField()
    status = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error_analysis = serializers.JSONField(required=False, allow_null=True)
    selected_input_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    input_data = serializers.JSONField(required=False, allow_null=True)
    input_types = serializers.JSONField(required=False, allow_null=True)
    error_message = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class CellErrorLocalizerResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CellErrorLocalizerResultSerializer()


class ColumnConfigResultSerializer(serializers.Serializer):
    name = serializers.CharField()
    template = serializers.UUIDField(required=False)
    template_config = serializers.JSONField(required=False)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    config = serializers.JSONField(required=False)
    status = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    prompt_config = serializers.JSONField(required=False)
    model = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    messages = serializers.JSONField(required=False)
    output_format = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    temperature = serializers.FloatField(required=False, allow_null=True)
    frequency_penalty = serializers.FloatField(required=False, allow_null=True)
    presence_penalty = serializers.FloatField(required=False, allow_null=True)
    max_tokens = serializers.IntegerField(required=False, allow_null=True)
    top_p = serializers.FloatField(required=False, allow_null=True)
    response_format = serializers.JSONField(required=False)
    tool_choice = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    tools = serializers.ListField(child=serializers.CharField(), required=False)

    optimize_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    optimized_k_prompts = serializers.JSONField(required=False)
    model_config = serializers.JSONField(required=False)
    user_eval_template_ids = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
    )
    optimisation_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    optimisation_config = serializers.JSONField(required=False)
    experiment_dataset = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    experiment_dataset_config = serializers.JSONField(required=False)


class ColumnConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ColumnConfigResultSerializer()


class DatasetColumnDetailItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    data_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class DatasetColumnDetailResultSerializer(serializers.Serializer):
    columns = DatasetColumnDetailItemSerializer(many=True)


class DatasetColumnDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetColumnDetailResultSerializer()


class DatasetEvalStatsMetricSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    name = serializers.CharField()
    total_cells = serializers.IntegerField(required=False, allow_null=True)
    output = serializers.JSONField()


class DatasetEvalStatsItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    output_type = serializers.CharField()
    result = DatasetEvalStatsMetricSerializer(many=True)
    total_pass_rate = serializers.FloatField(required=False, allow_null=True)
    total_avg = serializers.JSONField(required=False, allow_null=True)
    total_choices_avg = serializers.JSONField(required=False, allow_null=True)
    is_numeric_eval = serializers.BooleanField(required=False)
    is_numeric_eval_percentage = serializers.BooleanField(required=False)


class DatasetEvalStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetEvalStatsItemSerializer(many=True)


class JsonColumnSchemaEntrySerializer(serializers.Serializer):
    name = serializers.CharField()
    keys = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    sample = serializers.JSONField(required=False, allow_null=True)
    max_array_count = serializers.IntegerField(required=False)
    max_images_count = serializers.IntegerField(required=False)


class DatasetJsonSchemaResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(child=JsonColumnSchemaEntrySerializer())


class PreviewDatasetOperationRequestSerializer(serializers.Serializer):
    column_id = serializers.UUIDField(required=False)
    json_key = serializers.CharField(required=False, allow_blank=True)
    labels = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    instruction = serializers.CharField(required=False, allow_blank=True)
    language_model_id = serializers.CharField(required=False, allow_blank=True)
    config = serializers.JSONField(required=False)
    code = serializers.CharField(required=False, allow_blank=True)


class ExperimentFeedbackSubmitRequestSerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(
        choices=[
            "retune",
            "recalculate_row",
            "recalculate_dataset",
            "retune_recalculate",
        ]
    )
    feedback_id = serializers.UUIDField()
    user_eval_metric_id = serializers.UUIDField()
    value = serializers.CharField(required=False, allow_blank=True)
    explanation = serializers.CharField(required=False, allow_blank=True)


class RunPromptForRowsRequestSerializer(serializers.Serializer):
    run_prompt_ids = serializers.ListField(child=serializers.UUIDField())
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    selected_all_rows = serializers.BooleanField(required=False, default=False)


class UploadedFileResultSerializer(serializers.Serializer):
    url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    file_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    error = serializers.CharField(required=False, allow_blank=True)


class UploadFileResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UploadedFileResultSerializer(many=True)


class ColumnValuesItemSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    column_name = serializers.CharField()
    values = serializers.ListField(child=serializers.CharField(allow_blank=True))


class ColumnValuesResponseResultSerializer(serializers.Serializer):
    result = serializers.DictField(child=ColumnValuesItemSerializer())


class ColumnValuesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ColumnValuesResponseResultSerializer()


class DerivedVariableExtractRequestSerializer(serializers.Serializer):
    version = serializers.CharField()
    column_name = serializers.CharField(required=False, default="output")
    output_index = serializers.IntegerField(required=False, default=0)
    response_format_type = serializers.CharField(required=False, allow_blank=True)


class DerivedVariablePreviewRequestSerializer(serializers.Serializer):
    content = serializers.JSONField()
    column_name = serializers.CharField(required=False, default="output")


class DerivedVariableDetailSerializer(serializers.Serializer):
    paths = serializers.ListField(child=serializers.CharField(), required=False)
    schema = serializers.JSONField(required=False)
    full_variables = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    raw_sample = serializers.JSONField(required=False, allow_null=True)
    is_json = serializers.BooleanField(required=False)


class PromptDerivedVariablesResultSerializer(serializers.Serializer):
    version = serializers.CharField()
    derived_variables = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField())
    )


class PromptDerivedVariablesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PromptDerivedVariablesResultSerializer()


class DerivedVariableDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DerivedVariableDetailSerializer()


class EvalSummaryTemplateMutationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    criteria = serializers.CharField(required=False, allow_blank=True)


class ColumnValuesRequestSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    column_placeholders = serializers.JSONField()


class SingleRowEvaluationRequestSerializer(serializers.Serializer):
    user_eval_metric_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    selected_all_rows = serializers.BooleanField(required=False, default=False)


class EvalTemplateListChartsRequestSerializer(serializers.Serializer):
    template_ids = serializers.ListField(child=serializers.UUIDField())


class DatasetSortParamField(SortParamField):
    ALLOWED_KEYS = {"column_id", "type"}

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "properties": {
                "column_id": {"type": "string"},
                "type": {"type": "string", "enum": ["ascending", "descending"]},
            },
            "required": ["column_id"],
            "additionalProperties": False,
        }

    def to_internal_value(self, data):
        value = serializers.JSONField().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Sort item must be an object.")
        missing = sorted(self.REQUIRED_KEYS - set(value))
        if missing:
            raise serializers.ValidationError(
                f"Missing sort item keys: {', '.join(missing)}"
            )
        extra = sorted(set(value) - self.ALLOWED_KEYS)
        if extra:
            raise serializers.ValidationError(
                f"Unknown sort item keys: {', '.join(extra)}"
            )
        sort_type = value.get("type", "ascending")
        if sort_type not in ("ascending", "descending"):
            raise serializers.ValidationError(
                "type must be 'ascending' or 'descending'."
            )
        return {"column_id": value["column_id"], "type": sort_type}


class DatasetSortListField(serializers.ListField):
    child = DatasetSortParamField()


class DatasetSortListQueryParamField(serializers.CharField):
    def to_internal_value(self, data):
        sort_params = parse_filter_list_payload(data)
        return DatasetSortListField().run_validation(sort_params)


class DatasetTableQuerySerializer(StrictInputSerializer):
    filters = filter_list_query_param_field(required=False, default=list)
    sort = DatasetSortListQueryParamField(required=False, default=list)
    search = json_object_query_param_field(required=False, default=dict)
    page_size = serializers.IntegerField(required=False, default=10, min_value=1)
    current_page_index = serializers.IntegerField(
        required=False, default=0, min_value=0
    )
    column_config_only = serializers.BooleanField(required=False, default=False)


class EvalApiLogTableQuerySerializer(StrictInputSerializer):
    eval_template_id = serializers.UUIDField()
    page_size = serializers.IntegerField(required=False, default=10, min_value=1)
    current_page_index = serializers.IntegerField(
        required=False, default=0, min_value=0
    )
    source = serializers.ChoiceField(
        choices=["logs", "feedback", "eval_playground"],
        required=False,
        default="logs",
    )
    search = json_object_query_param_field(required=False, default=dict)
    filters = filter_list_query_param_field(required=False, default=list)
    sort = DatasetSortListQueryParamField(required=False, default=list)


class EvalMetricQuerySerializer(StrictInputSerializer):
    eval_template_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)


class EvalTemplateBulkDeleteRequestSerializer(serializers.Serializer):
    template_ids = serializers.ListField(child=serializers.UUIDField())


class EvalTemplateChartPointSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    value = serializers.FloatField()


class EvalTemplateListChartsItemSerializer(serializers.Serializer):
    chart = EvalTemplateChartPointSerializer(many=True)
    error_rate = EvalTemplateChartPointSerializer(many=True)
    run_count = serializers.IntegerField()


class EvalTemplateListChartsResponseResultSerializer(serializers.Serializer):
    charts = serializers.DictField(child=EvalTemplateListChartsItemSerializer())


class EvalTemplateListChartsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateListChartsResponseResultSerializer()


class EvalTemplateBulkDeleteResponseResultSerializer(serializers.Serializer):
    deleted_count = serializers.IntegerField()


class EvalTemplateBulkDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateBulkDeleteResponseResultSerializer()


class EvalTemplateListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    template_type = serializers.CharField()
    eval_type = serializers.CharField()
    output_type = serializers.CharField()
    owner = serializers.CharField()
    created_by_name = serializers.CharField()
    version_count = serializers.IntegerField()
    current_version = serializers.CharField()
    last_updated = serializers.CharField()
    thirty_day_chart = EvalTemplateChartPointSerializer(many=True)
    thirty_day_error_rate = EvalTemplateChartPointSerializer(many=True)
    thirty_day_run_count = serializers.IntegerField()
    tags = serializers.ListField(child=serializers.CharField())


class EvalTemplateListResponseResultSerializer(serializers.Serializer):
    items = EvalTemplateListItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()


class EvalTemplateListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateListResponseResultSerializer()


class EvalTemplateCreateResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    version = serializers.CharField()


class EvalTemplateCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateCreateResponseResultSerializer()


class EvalTemplateDetailResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    template_type = serializers.CharField()
    eval_type = serializers.CharField()
    instructions = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    model = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    output_type = serializers.CharField()
    pass_threshold = serializers.FloatField()
    choice_scores = serializers.JSONField(required=False, allow_null=True)
    choices = serializers.JSONField(required=False, allow_null=True)
    multi_choice = serializers.BooleanField()
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    code_language = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    required_keys = serializers.ListField(child=serializers.CharField())
    owner = serializers.CharField()
    created_by_name = serializers.CharField()
    version_count = serializers.IntegerField()
    current_version = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField())
    check_internet = serializers.BooleanField()
    error_localizer_enabled = serializers.BooleanField()
    template_format = serializers.CharField()
    aggregation_enabled = serializers.BooleanField()
    aggregation_function = serializers.CharField()
    composite_child_axis = serializers.CharField(required=False, allow_blank=True)
    config = serializers.JSONField(required=False, allow_null=True)
    created_at = serializers.CharField()
    updated_at = serializers.CharField()


class EvalTemplateDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateDetailResponseResultSerializer()


class EvalTemplateUpdateResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    updated = serializers.BooleanField()


class EvalTemplateUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateUpdateResponseResultSerializer()


class EvalUsageChartPointSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    calls = serializers.IntegerField(required=False)
    avg_latency_ms = serializers.IntegerField(required=False)
    avg_score = serializers.FloatField(required=False, allow_null=True)
    pass_count = serializers.IntegerField(required=False)
    fail_count = serializers.IntegerField(required=False)


class EvalUsageQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, default=0, min_value=0, max_value=10000)
    page_size = serializers.IntegerField(required=False, default=25, min_value=1, max_value=100)
    period = serializers.ChoiceField(
        choices=["30m", "6h", "1d", "7d", "30d", "90d", "180d", "365d"],
        required=False,
        default="30d",
    )
    # Optional explicit date range — when provided, overrides the period string.
    # Sent by the frontend for Today, Yesterday, and Custom date picker selections.
    start_date = serializers.DateTimeField(required=False, allow_null=True, default=None)
    end_date = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        # Reject asymmetric date ranges. If the caller sends start without end
        # (or vice versa) the view used to silently fall back to `period`,
        # which is the difference between "user picked Yesterday" and "user got
        # 30d of data." Force the contract — both or neither.
        start, end = attrs.get("start_date"), attrs.get("end_date")
        if (start is None) != (end is None):
            raise serializers.ValidationError(
                "start_date and end_date must be provided together."
            )
        if start and end and start > end:
            raise serializers.ValidationError(
                "start_date must be on or before end_date."
            )
        return attrs


class EvalUsageStatsSerializer(serializers.Serializer):
    total_runs = serializers.IntegerField()
    runs_period = serializers.IntegerField()
    success_count = serializers.IntegerField()
    error_count = serializers.IntegerField()
    pass_rate = serializers.FloatField()


class EvalUsageFeedbackSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    # Feedback.value is a TextField — always a string (thumbs_up/down or custom)
    value = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    explanation = serializers.CharField(required=False, allow_blank=True)
    action_type = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.CharField(required=False, allow_blank=True)
    user = serializers.CharField(required=False, allow_blank=True)


class EvalUsageLogItemDetailSerializer(serializers.Serializer):
    """Typed shape for the detail blob on each log row.

    Field-by-field rationale (closes Round-6 #4):
    - ``input_variables``: typed ``Dict[str, str]`` — variable keys per
      template, string values (truncated by the view).
    - ``mappings``: typed ``Dict[str, str]`` — keys are the eval's
      required_keys, values are the bound column id or literal string.
      Both keys and values are strings; no JSON variance here.
    - ``model``: ``StringOrObjectField`` — legitimate ``string | object``
      union (bare model name or full ModelSpec dict).
    - ``output``: stays ``JSONField``. Its *runtime* shape is per-eval-type
      (boolean for pass_fail, float for regression, ``{label, score}`` for
      choices). There is no single static schema that fits every template;
      a per-eval-type discriminated ``oneOf`` lives in TH-6029 with the
      wider prompt-config contract cleanup. JsonValueField was rejected
      here because it maps to ``z.any()`` at runtime — that's strictly
      worse than the current narrowing (no contract enforcement at all).
    """
    input_variables = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False, allow_null=True, default=dict,
    )
    output = serializers.JSONField(required=False, allow_null=True)
    warnings = serializers.ListField(required=False, default=list)
    mappings = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False, allow_null=True, default=dict,
    )
    model = StringOrObjectField(required=False, allow_null=True)
    # composite-only fields
    children = serializers.ListField(required=False, default=list)
    aggregation_function = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    total_children = serializers.IntegerField(required=False, allow_null=True)
    completed_children = serializers.IntegerField(required=False, allow_null=True)
    failed_children = serializers.IntegerField(required=False, allow_null=True)


class EvalUsageLogItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    input = serializers.CharField(allow_blank=True)
    result = serializers.CharField(required=False, allow_blank=True)
    score = serializers.FloatField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField()
    source = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.CharField()
    detail = EvalUsageLogItemDetailSerializer()
    feedback = EvalUsageFeedbackSerializer(required=False, allow_null=True)
    composite = serializers.BooleanField(required=False)
    aggregate_pass = serializers.BooleanField(required=False, allow_null=True)


class EvalUsagePaginationSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()


class EvalUsageNumberCellSerializer(serializers.Serializer):
    """`{cell_value: number|null}` — for numeric cells (score)."""

    cell_value = serializers.FloatField(required=False, allow_null=True)


class EvalUsageStringCellSerializer(serializers.Serializer):
    """`{cell_value: string}` — for plain text cells."""

    cell_value = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )


class EvalUsageVersionCellSerializer(serializers.Serializer):
    """`{cell_value: string|number|null}` — version can be either a version
    number (int) or blank string for system-owned templates."""

    cell_value = StringOrObjectField(required=False, allow_null=True)


class EvalUsageWarningsCellSerializer(serializers.Serializer):
    """`{cell_value: array}` — list of warning objects."""

    cell_value = serializers.ListField(
        child=serializers.JSONField(), required=False, allow_null=True
    )


class EvalUsageFeedbackCellSerializer(serializers.Serializer):
    """`{cell_value: object|null}` — nested feedback record or null."""

    cell_value = serializers.JSONField(required=False, allow_null=True)


class EvalUsageTableRowSerializer(_ExtraFieldsMixin, serializers.Serializer):
    """One row in the eval-usage / eval-api-log table.

    Known columns are typed explicitly as ``{cell_value: <type>}`` wrappers so
    the contract describes what the FE actually receives. The per-eval
    ``input_var_<name>`` columns are user-controlled dynamic keys — their
    exact set depends on the tenant's dataset + eval mapping — so they pass
    through via ``additionalProperties: True``. ``_ExtraFieldsMixin`` is what
    actually preserves those dynamic cell keys through ``.data`` — without
    it DRF strips undeclared fields at serialize time and the FE grid renders
    empty rows even though the swagger says extras are allowed.

    This matches Nikhil's ask (PR #747 round 3): "type the row with the known
    scalar fields plus a JSON-value map for the dynamic columns."
    """

    row_id = serializers.CharField()
    score = EvalUsageNumberCellSerializer(required=False)
    result = EvalUsageStringCellSerializer(required=False)
    input = EvalUsageStringCellSerializer(required=False)
    reason = EvalUsageStringCellSerializer(required=False)
    source = EvalUsageStringCellSerializer(required=False)
    version = EvalUsageVersionCellSerializer(required=False)
    feedback = EvalUsageFeedbackCellSerializer(required=False)
    created_at = EvalUsageStringCellSerializer(required=False)
    status = EvalUsageStringCellSerializer(required=False)
    warnings = EvalUsageWarningsCellSerializer(required=False)

    class Meta:
        # Dynamic `input_var_<name>` keys — user-controlled, can't be typed
        # ahead of time. FE reads column_config separately and renders each
        # extra cell by key at runtime.
        swagger_schema_fields = {"additionalProperties": True}


class EvalUsageStatsResponseResultSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    is_composite = serializers.BooleanField()
    stats = EvalUsageStatsSerializer()
    chart = EvalUsageChartPointSerializer(many=True)
    table = serializers.ListField(child=EvalUsageTableRowSerializer())
    logs = EvalUsagePaginationSerializer()


class EvalUsageStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalUsageStatsResponseResultSerializer()


class EvalFeedbackListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    value = serializers.CharField(allow_blank=True)
    explanation = serializers.CharField(allow_blank=True)
    source = serializers.CharField(allow_blank=True)
    source_id = serializers.CharField(allow_blank=True)
    action_type = serializers.CharField(allow_blank=True)
    user_name = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField()
    user_eval_metric_id = serializers.CharField(allow_blank=True)
    custom_eval_config_id = serializers.CharField(allow_blank=True)
    experiment_id = serializers.CharField(allow_blank=True)


class EvalFeedbackListResponseResultSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    items = EvalFeedbackListItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()


class EvalFeedbackListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalFeedbackListResponseResultSerializer()


class FeedbackDetailsItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    value = serializers.CharField(allow_blank=True, allow_null=True)
    comment = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.CharField()
    action_type = serializers.CharField(allow_blank=True, allow_null=True)


class FeedbackDetailsResultSerializer(serializers.Serializer):
    feedback = FeedbackDetailsItemSerializer(many=True)
    total_count = serializers.IntegerField()


class FeedbackDetailsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = FeedbackDetailsResultSerializer()


class EvalApiLogRowResponseResultSerializer(serializers.Serializer):
    log_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    evaluation_id = serializers.UUIDField()
    source = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    required_keys = serializers.ListField(child=serializers.CharField())
    values = serializers.JSONField()
    output = serializers.JSONField()
    input_data_types = serializers.JSONField()
    error_details = serializers.JSONField(required=False)
    error_localizer_status = serializers.CharField(required=False, allow_blank=True)
    error_localizer_message = serializers.CharField(required=False, allow_blank=True)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)
    span_id = serializers.UUIDField(required=False, allow_null=True)
    trace_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_id = serializers.UUIDField(required=False, allow_null=True)
    optimization_id = serializers.UUIDField(required=False, allow_null=True)
    experiment_id = serializers.UUIDField(required=False, allow_null=True)


class EvalApiLogRowResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalApiLogRowResponseResultSerializer()


class EvalApiLogTableMetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()
    total_pages = serializers.IntegerField()


class EvalColumnConfigItemSerializer(serializers.Serializer):
    """Stable shape for column_config entries returned by eval log table endpoints.

    Built by ``create_column_config_playground`` in views/separate_evals.py.
    Required fields are always present in the builder; optional fields appear
    only on certain column types (data/eval/datetime), so they declare
    ``required=False``. Keep the builder and this serializer in sync.
    """
    id = serializers.CharField()
    name = serializers.CharField()
    is_visible = serializers.BooleanField()
    status = serializers.CharField()
    source_type = serializers.CharField()
    is_frozen = serializers.BooleanField(required=False, allow_null=True)
    data_type = serializers.CharField(required=False)
    origin_type = serializers.CharField(required=False)
    output_type = serializers.CharField(required=False)


class EvalApiLogTableResponseResultSerializer(serializers.Serializer):
    # Same row shape as EvalUsageStatsResponseResultSerializer.table —
    # known cell-wrapped columns + `additionalProperties: True` for the
    # per-eval `input_var_<name>` dynamic keys.
    table = serializers.ListField(child=EvalUsageTableRowSerializer())
    column_config = EvalColumnConfigItemSerializer(many=True)
    metadata = EvalApiLogTableMetadataSerializer(required=False)


class EvalApiLogTableResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalApiLogTableResponseResultSerializer()


class EvalMetricCountSerializer(serializers.Serializer):
    api_call_count = serializers.IntegerField()
    count_graph_data = serializers.JSONField(required=False, allow_null=True)


class EvalMetricAverageSerializer(serializers.Serializer):
    average = serializers.JSONField()
    avg_graph_data = serializers.JSONField(required=False, allow_null=True)


class EvalMetricResponseResultSerializer(serializers.Serializer):
    base_eval_template_id = serializers.UUIDField()
    api_call_count = EvalMetricCountSerializer()
    average = EvalMetricAverageSerializer()
    error_rate = serializers.JSONField(required=False, allow_null=True)


class EvalMetricResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalMetricResponseResultSerializer()


class EvalTemplateNameItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)


class EvalTemplateNamesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateNameItemSerializer(many=True)


class LegacyEvalTemplateAverageSerializer(serializers.Serializer):
    average = serializers.JSONField(required=False)
    avg_graph_data = serializers.ListField(child=serializers.JSONField())


class LegacyEvalTemplateItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    max_axis = serializers.IntegerField(required=False, allow_null=True)
    eval_template_name = serializers.CharField()
    average = LegacyEvalTemplateAverageSerializer()
    error_rate = serializers.ListField(child=serializers.JSONField())
    last30_run = serializers.IntegerField()
    updated_at = serializers.CharField()


class LegacyEvalTemplatesResponseResultSerializer(serializers.Serializer):
    row_data = LegacyEvalTemplateItemSerializer(many=True)
    total_rows = serializers.IntegerField()
    data_available = serializers.BooleanField()


class LegacyEvalTemplatesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LegacyEvalTemplatesResponseResultSerializer()


class EvalConfigSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    name = serializers.CharField()
    owner = serializers.CharField(required=False)
    type = serializers.CharField(required=False)
    eval_type = serializers.CharField(required=False)
    eval_type_id = serializers.JSONField(required=False)
    function_eval = serializers.BooleanField(required=False)
    reason_column = serializers.JSONField(required=False)
    eval_tags = serializers.ListField(child=serializers.CharField(), required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    criteria = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    models = serializers.JSONField(required=False)
    selected_model = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    required_keys = serializers.ListField(child=serializers.CharField())
    optional_keys = serializers.ListField(child=serializers.CharField(), required=False)
    variable_keys = serializers.ListField(child=serializers.CharField(), required=False)
    run_prompt_column = serializers.BooleanField(required=False)
    template_name = serializers.CharField()
    mapping = serializers.JSONField()
    config = serializers.JSONField()
    params = serializers.JSONField(required=False)
    function_params_schema = serializers.JSONField(required=False)
    output = serializers.CharField(required=False, allow_blank=True)
    config_params_desc = serializers.JSONField(required=False)
    config_params_option = serializers.JSONField(required=False)
    param_modalities = serializers.JSONField(required=False)
    choices = serializers.JSONField(required=False)
    check_internet = serializers.BooleanField(required=False)
    kb_id = serializers.JSONField(required=False, allow_null=True)
    error_localizer = serializers.BooleanField(required=False)
    api_key_available = serializers.BooleanField(required=False)
    run_config = serializers.JSONField(required=False)


class ModelHubEvalConfigResponseResultSerializer(serializers.Serializer):
    eval = EvalConfigSerializer()
    owner = serializers.CharField(required=False)
    type = serializers.CharField(required=False)


class ModelHubEvalConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ModelHubEvalConfigResponseResultSerializer()


class EvalCodeSnippetResponseResultSerializer(serializers.Serializer):
    python = serializers.CharField()
    curl = serializers.CharField()
    javascript = serializers.CharField()


class EvalCodeSnippetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalCodeSnippetResponseResultSerializer()


class EvalExecutionResponseResultSerializer(serializers.Serializer):
    output = serializers.JSONField(required=False)
    result = serializers.JSONField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True)
    score = serializers.JSONField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, allow_null=True)
    log_id = serializers.UUIDField(required=False)
    error_localizer = serializers.JSONField(required=False)
    error_details = serializers.JSONField(required=False)


class EvalExecutionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalExecutionResponseResultSerializer()


class EvalPlaygroundFeedbackResponseResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    feedback_id = serializers.UUIDField()


class EvalPlaygroundFeedbackResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalPlaygroundFeedbackResponseResultSerializer()


class LegacyEvalTemplateUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()


class SingleRowEvaluationResponseResultSerializer(serializers.Serializer):
    success = serializers.CharField()


class SingleRowEvaluationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SingleRowEvaluationResponseResultSerializer()


class EvalTemplateCreateV2RequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    is_draft = serializers.BooleanField(required=False, default=False)
    eval_type = serializers.ChoiceField(
        choices=["llm", "code", "agent"],
        required=False,
        default="llm",
    )
    instructions = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100000,
        trim_whitespace=False,
    )
    model = serializers.CharField(required=False, default="turing_large")
    output_type = serializers.ChoiceField(
        choices=["pass_fail", "percentage", "deterministic"],
        required=False,
        default="pass_fail",
    )
    pass_threshold = serializers.FloatField(required=False, min_value=0, max_value=1)
    choice_scores = serializers.JSONField(required=False, allow_null=True)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, trim_whitespace=False
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    check_internet = serializers.BooleanField(required=False, default=False)
    code = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=100000,
        trim_whitespace=False,
    )
    code_language = serializers.ChoiceField(
        choices=["python", "javascript"],
        required=False,
        allow_null=True,
    )
    messages = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )
    few_shot_examples = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )
    mode = serializers.ChoiceField(
        choices=["auto", "agent", "quick"],
        required=False,
        allow_null=True,
    )
    tools = serializers.JSONField(required=False, allow_null=True)
    knowledge_bases = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
    )
    data_injection = serializers.JSONField(required=False, allow_null=True)
    summary = serializers.JSONField(required=False, allow_null=True)
    error_localizer_enabled = serializers.BooleanField(required=False, default=False)
    template_format = serializers.ChoiceField(
        choices=["mustache", "jinja"],
        required=False,
        default="mustache",
    )


class EvalTemplateUpdateV2RequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_null=True, max_length=255)
    eval_type = serializers.ChoiceField(
        choices=["llm", "code", "agent"],
        required=False,
        allow_null=True,
    )
    instructions = serializers.CharField(
        required=False,
        allow_null=True,
        trim_whitespace=False,
    )
    model = serializers.CharField(required=False, allow_null=True)
    output_type = serializers.ChoiceField(
        choices=["pass_fail", "percentage", "deterministic"],
        required=False,
        allow_null=True,
    )
    pass_threshold = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=1,
    )
    choice_scores = serializers.JSONField(required=False, allow_null=True)
    multi_choice = serializers.BooleanField(required=False, allow_null=True)
    description = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=False,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
    )
    check_internet = serializers.BooleanField(required=False, allow_null=True)
    code = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=False,
    )
    code_language = serializers.ChoiceField(
        choices=["python", "javascript"],
        required=False,
        allow_null=True,
    )
    messages = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )
    few_shot_examples = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )
    mode = serializers.ChoiceField(
        choices=["auto", "agent", "quick"],
        required=False,
        allow_null=True,
    )
    tools = serializers.JSONField(required=False, allow_null=True)
    knowledge_bases = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
    )
    data_injection = serializers.JSONField(required=False, allow_null=True)
    summary = serializers.JSONField(required=False, allow_null=True)
    error_localizer_enabled = serializers.BooleanField(required=False, allow_null=True)
    publish = serializers.BooleanField(required=False, allow_null=True)
    template_format = serializers.ChoiceField(
        choices=["mustache", "jinja"],
        required=False,
        allow_null=True,
    )


class EvalTemplateVersionCreateRequestSerializer(serializers.Serializer):
    criteria = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=False,
    )
    model = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    config_snapshot = serializers.JSONField(required=False, allow_null=True)


class EvalTemplateVersionItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version_number = serializers.IntegerField()
    is_default = serializers.BooleanField()
    criteria = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)
    config_snapshot = serializers.JSONField(required=False)
    created_by_name = serializers.CharField(required=False, allow_blank=True)
    created_at = serializers.CharField(required=False, allow_blank=True)


class EvalTemplateVersionListResponseResultSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    versions = EvalTemplateVersionItemSerializer(many=True)
    total = serializers.IntegerField()


class EvalTemplateVersionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateVersionListResponseResultSerializer()


class EvalTemplateVersionResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version_number = serializers.IntegerField()
    is_default = serializers.BooleanField()


class EvalTemplateVersionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateVersionResponseResultSerializer()


class EvalTemplateVersionRestoreResponseResultSerializer(
    EvalTemplateVersionResponseResultSerializer
):
    restored_from = serializers.IntegerField()


class EvalTemplateVersionRestoreResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalTemplateVersionRestoreResponseResultSerializer()


class CompositeEvalCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=False,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    child_template_ids = serializers.ListField(child=serializers.UUIDField())
    aggregation_enabled = serializers.BooleanField(required=False, default=True)
    aggregation_function = serializers.ChoiceField(
        choices=["weighted_avg", "avg", "min", "max", "pass_rate"],
        required=False,
        default="weighted_avg",
    )
    child_weights = serializers.JSONField(required=False, allow_null=True)
    child_pinned_versions = serializers.JSONField(required=False, allow_null=True)
    child_configs = serializers.JSONField(required=False, allow_null=True)
    composite_child_axis = serializers.ChoiceField(
        choices=["", "pass_fail", "percentage", "choices", "code"],
        required=False,
        allow_blank=True,
        default="",
    )


class CompositeEvalUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_null=True, max_length=255)
    description = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=False,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
    )
    aggregation_enabled = serializers.BooleanField(required=False, allow_null=True)
    aggregation_function = serializers.ChoiceField(
        choices=["weighted_avg", "avg", "min", "max", "pass_rate"],
        required=False,
        allow_null=True,
    )
    child_template_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_null=True,
    )
    child_weights = serializers.JSONField(required=False, allow_null=True)
    child_pinned_versions = serializers.JSONField(required=False, allow_null=True)
    child_configs = serializers.JSONField(required=False, allow_null=True)
    composite_child_axis = serializers.ChoiceField(
        choices=["", "pass_fail", "percentage", "choices", "code"],
        required=False,
        allow_null=True,
        allow_blank=True,
    )


class CompositeEvalExecuteRequestSerializer(serializers.Serializer):
    mapping = serializers.JSONField()
    model = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    config = serializers.JSONField(required=False, default=dict)
    error_localizer = serializers.BooleanField(required=False, default=False)
    input_data_types = serializers.JSONField(required=False, default=dict)
    span_context = serializers.JSONField(required=False, allow_null=True)
    trace_context = serializers.JSONField(required=False, allow_null=True)
    session_context = serializers.JSONField(required=False, allow_null=True)
    call_context = serializers.JSONField(required=False, allow_null=True)
    row_context = serializers.JSONField(required=False, allow_null=True)


class CompositeEvalAdhocExecuteRequestSerializer(CompositeEvalExecuteRequestSerializer):
    child_template_ids = serializers.ListField(child=serializers.UUIDField())
    aggregation_enabled = serializers.BooleanField(required=False, default=True)
    aggregation_function = serializers.ChoiceField(
        choices=["weighted_avg", "avg", "min", "max", "pass_rate"],
        required=False,
        default="weighted_avg",
    )
    composite_child_axis = serializers.ChoiceField(
        choices=["", "pass_fail", "percentage", "choices", "code"],
        required=False,
        allow_blank=True,
        default="",
    )
    child_weights = serializers.JSONField(required=False, allow_null=True)
    child_configs = serializers.JSONField(required=False, allow_null=True)
    pass_threshold = serializers.FloatField(required=False, default=0.5)


class CompositeChildItemSerializer(serializers.Serializer):
    child_id = serializers.UUIDField()
    child_name = serializers.CharField()
    order = serializers.IntegerField()
    eval_type = serializers.CharField(required=False)
    pinned_version_id = serializers.UUIDField(required=False, allow_null=True)
    pinned_version_number = serializers.IntegerField(required=False, allow_null=True)
    weight = serializers.FloatField(required=False)
    config = serializers.JSONField(required=False, default=dict)
    required_keys = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class CompositeEvalCreateResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    template_type = serializers.CharField(required=False)
    aggregation_enabled = serializers.BooleanField()
    aggregation_function = serializers.CharField()
    composite_child_axis = serializers.CharField(required=False, allow_blank=True)
    children = CompositeChildItemSerializer(many=True)


class CompositeEvalCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompositeEvalCreateResponseResultSerializer()


class CompositeEvalDetailResponseResultSerializer(
    CompositeEvalCreateResponseResultSerializer
):
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    created_at = serializers.CharField(required=False, allow_blank=True)
    updated_at = serializers.CharField(required=False, allow_blank=True)
    version_number = serializers.IntegerField(required=False, allow_null=True)


class CompositeEvalDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompositeEvalDetailResponseResultSerializer()


class CompositeChildResultSerializer(serializers.Serializer):
    child_id = serializers.UUIDField()
    child_name = serializers.CharField()
    order = serializers.IntegerField()
    score = serializers.FloatField(required=False, allow_null=True)
    output = serializers.JSONField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    output_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    status = serializers.CharField()
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    log_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    weight = serializers.FloatField(required=False)
    error_localizer_result = serializers.JSONField(required=False, allow_null=True)


class CompositeEvalExecuteResponseResultSerializer(serializers.Serializer):
    composite_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    composite_name = serializers.CharField()
    aggregation_enabled = serializers.BooleanField()
    aggregation_function = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    aggregate_score = serializers.FloatField(required=False, allow_null=True)
    aggregate_pass = serializers.BooleanField(required=False, allow_null=True)
    children = CompositeChildResultSerializer(many=True)
    summary = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error_localizer_results = serializers.JSONField(required=False, allow_null=True)
    total_children = serializers.IntegerField()
    completed_children = serializers.IntegerField()
    failed_children = serializers.IntegerField()
    evaluation_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class CompositeEvalExecuteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompositeEvalExecuteResponseResultSerializer()


class GroundTruthUploadRequestSerializer(serializers.Serializer):
    file = serializers.FileField(required=False, write_only=True)
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
        trim_whitespace=False,
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=False,
    )
    file_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=False,
    )
    columns = serializers.ListField(
        child=serializers.CharField(trim_whitespace=False),
        required=False,
    )
    data = serializers.ListField(child=serializers.JSONField(), required=False)
    variable_mapping = JsonObjectRequestField(required=False, allow_null=True)
    role_mapping = JsonObjectRequestField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get("file") is not None:
            return attrs

        errors = {}
        if not attrs.get("name"):
            errors["name"] = ["This field is required."]
        if not attrs.get("columns"):
            errors["columns"] = ["Columns list is required."]
        if "data" not in attrs:
            errors["data"] = ["This field is required."]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class GroundTruthRoleMappingSerializer(serializers.Serializer):
    """Maps GT dataset columns to their semantic role for the eval prompt.

    ``output`` (or legacy ``expected_output``) is required at the service
    layer; ``explanation`` (or legacy ``reasoning`` / ``reason``) is
    optional. All values are GT dataset column names.
    """

    output = serializers.CharField(required=False, allow_blank=False)
    explanation = serializers.CharField(required=False, allow_blank=False)
    expected_output = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Legacy alias for `output`.",
    )
    reasoning = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Legacy alias for `explanation`.",
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Legacy alias for `explanation`.",
    )


class GroundTruthSetupRequestSerializer(serializers.Serializer):
    """Atomic write covering variable mapping, role mapping, max_examples, enabled."""

    variable_mapping = JsonObjectRequestField(
        help_text=(
            "Map of template variable name to GT column name (string) "
            "or list of column names. Keys are dynamic per-template."
        ),
    )
    role_mapping = GroundTruthRoleMappingSerializer()
    max_examples = serializers.IntegerField(min_value=1, max_value=20)
    enabled = serializers.BooleanField(required=False, default=True)


class GroundTruthItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    file_name = serializers.CharField(required=False, allow_blank=True)
    columns = serializers.ListField(child=serializers.CharField())
    row_count = serializers.IntegerField()
    variable_mapping = JsonObjectRequestField(
        required=False,
        allow_null=True,
        help_text=(
            "Map of template variable name to GT column name (string) "
            "or list of column names."
        ),
    )
    role_mapping = GroundTruthRoleMappingSerializer(required=False, allow_null=True)
    embedding_status = serializers.CharField(required=False)
    embedded_row_count = serializers.IntegerField(required=False)
    storage_type = serializers.CharField(required=False)
    created_at = serializers.CharField(required=False, allow_blank=True)
    embeddings_stale = serializers.BooleanField(required=False, default=False)
    is_active = serializers.BooleanField(required=False, default=False)
    enabled = serializers.BooleanField(required=False, default=True)
    max_examples = serializers.IntegerField(required=False, default=3)
    similarity_threshold = serializers.FloatField(required=False, default=0.7)


class GroundTruthListResponseResultSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    items = GroundTruthItemSerializer(many=True)
    total = serializers.IntegerField()


class GroundTruthListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthListResponseResultSerializer()


class GroundTruthUploadResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    row_count = serializers.IntegerField()
    columns = serializers.ListField(child=serializers.CharField())
    embedding_status = serializers.CharField()


class GroundTruthUploadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthUploadResponseResultSerializer()


class GroundTruthRuntimeConfigSerializer(serializers.Serializer):
    """Per-tenant runtime knobs that drive GT retrieval at eval time."""

    enabled = serializers.BooleanField()
    ground_truth_id = serializers.UUIDField()
    max_examples = serializers.IntegerField(min_value=1, max_value=20)
    similarity_threshold = serializers.FloatField(min_value=0.0, max_value=1.0)


class GroundTruthSetupResponseResultSerializer(serializers.Serializer):
    """Shape returned by GroundTruthService.update_setup."""

    id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    variable_mapping = JsonObjectRequestField(
        required=False,
        allow_null=True,
        help_text=(
            "Map of template variable name to GT column name (string) "
            "or list of column names."
        ),
    )
    role_mapping = GroundTruthRoleMappingSerializer(required=False, allow_null=True)
    embedding_status = serializers.CharField()
    embeddings_stale = serializers.BooleanField(required=False, default=False)
    config = GroundTruthRuntimeConfigSerializer()


class GroundTruthSetupResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthSetupResponseResultSerializer()


class GroundTruthDataResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    total_rows = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    columns = serializers.ListField(child=serializers.CharField())
    rows = serializers.ListField(child=serializers.JSONField())


class GroundTruthDataResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthDataResponseResultSerializer()


class GroundTruthStatusResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    embedding_status = serializers.CharField()
    embedded_row_count = serializers.IntegerField()
    total_rows = serializers.IntegerField()
    progress_percent = serializers.FloatField()
    embeddings_stale = serializers.BooleanField(required=False, default=False)


class GroundTruthStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthStatusResponseResultSerializer()


class GroundTruthDeleteResponseResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()
    id = serializers.UUIDField()


class GroundTruthDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthDeleteResponseResultSerializer()


class GroundTruthEmbedResponseResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    embedding_status = serializers.CharField()
    message = serializers.CharField()


class GroundTruthEmbedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = GroundTruthEmbedResponseResultSerializer()


class EvalMetricRequestSerializer(StrictInputSerializer):
    eval_template_id = serializers.UUIDField()
    filters = filter_list_field(required=False, default=list)


class EvalTemplateNamesRequestSerializer(serializers.Serializer):
    search_text = serializers.CharField(required=False, allow_blank=True, default="")


class LegacyEvalTemplatesRequestSerializer(serializers.Serializer):
    page_size = serializers.IntegerField(required=False, default=10)
    current_page_index = serializers.IntegerField(required=False, default=0)
    search_text = serializers.CharField(required=False, allow_blank=True, default="")
    sort = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )


class HuggingFaceDatasetConfigRequestSerializer(serializers.Serializer):
    dataset_path = serializers.CharField()


class HuggingFaceDatasetListRequestSerializer(serializers.Serializer):
    search_query = serializers.CharField(required=False, allow_blank=True, default="")
    filter_params = serializers.JSONField(required=False, default=dict)


class HuggingFaceDatasetDetailRequestSerializer(serializers.Serializer):
    dataset_id = serializers.CharField()


class HuggingFaceDatasetCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, default="")
    model_type = serializers.CharField(required=False, allow_blank=True, default="")
    num_rows = serializers.IntegerField(required=False, min_value=0)
    huggingface_dataset_name = serializers.CharField()
    huggingface_dataset_config = serializers.CharField(required=False, allow_blank=True)
    huggingface_dataset_split = serializers.CharField()


class HuggingFaceAddRowsRequestSerializer(serializers.Serializer):
    num_rows = serializers.IntegerField(required=False, min_value=0)
    huggingface_dataset_name = serializers.CharField()
    huggingface_dataset_config = serializers.CharField()
    huggingface_dataset_split = serializers.CharField()


class CompareDatasetStatsRequestSerializer(serializers.Serializer):
    base_column_name = serializers.CharField()
    dataset_ids = serializers.ListField(child=serializers.UUIDField())
    stat_type = serializers.ChoiceField(
        choices=["evaluation", "run_prompt"],
        required=False,
        default="evaluation",
    )


class CompareStartEvalsRequestSerializer(serializers.Serializer):
    user_eval_names = serializers.ListField(child=serializers.CharField())
    dataset_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class CompareEvalsListRequestSerializer(serializers.Serializer):
    search_text = serializers.CharField(required=False, allow_blank=True, default="")
    eval_type = serializers.ChoiceField(choices=["user"])
    dataset_ids = serializers.ListField(child=serializers.UUIDField())


class ComparePreviewRunEvalRequestSerializer(serializers.Serializer):
    config = serializers.JSONField()
    model = serializers.CharField(required=False, allow_blank=True, default="")
    template_id = serializers.UUIDField()
    dataset_ids = serializers.ListField(child=serializers.UUIDField())
    dataset_info = serializers.JSONField(required=False, default=dict)
    source = serializers.CharField(
        required=False,
        allow_blank=True,
        default="dataset_evaluation",
    )


class CompareExperimentEvalRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    template_id = serializers.CharField(max_length=500)
    config = serializers.JSONField()
    kb_id = serializers.UUIDField(required=False)
    error_localizer = serializers.BooleanField(required=False, default=False)
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    eval_type = serializers.CharField(required=False, allow_blank=True)
    run = serializers.BooleanField(required=False, default=False)
    save_as_template = serializers.BooleanField(required=False, default=False)
    experiment_id = serializers.UUIDField(required=False)
    composite_weight_overrides = serializers.JSONField(
        required=False,
        allow_null=True,
        default=None,
    )
    dataset_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class DatasetRowSelectionRequestSerializer(serializers.Serializer):
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    selected_all_rows = serializers.BooleanField(required=False, default=False)


class DuplicateRowsRequestSerializer(DatasetRowSelectionRequestSerializer):
    num_copies = serializers.IntegerField(required=False, min_value=1, default=1)


class DuplicateDatasetRequestSerializer(DatasetRowSelectionRequestSerializer):
    name = serializers.CharField()


class MergeDatasetRequestSerializer(DatasetRowSelectionRequestSerializer):
    target_dataset_id = serializers.UUIDField()


class AddAsNewDatasetRequestSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    name = serializers.CharField(required=False, allow_blank=True)
    columns = serializers.JSONField(required=False, default=dict)


class AddRowsFromFileRequestSerializer(serializers.Serializer):
    file = serializers.FileField()
    dataset_id = serializers.UUIDField()
    model_type = serializers.CharField(required=False, allow_blank=True)


class CloneDatasetRequestSerializer(serializers.Serializer):
    new_dataset_name = serializers.CharField(required=False, allow_blank=True)


class CreateDatasetFromLocalFileRequestSerializer(serializers.Serializer):
    file = serializers.FileField()
    new_dataset_name = serializers.CharField(required=False, allow_blank=True)
    model_type = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)


class CreateDatasetFromExperimentRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    model_type = serializers.CharField(required=False, allow_blank=True)


class CreateEmptyDatasetRequestSerializer(serializers.Serializer):
    new_dataset_name = serializers.CharField()
    model_type = serializers.CharField(required=False, allow_blank=True)
    is_sdk = serializers.BooleanField(required=False, default=False)
    row = serializers.IntegerField(
        required=False, min_value=0, max_value=MAX_EMPTY_DATASET_ROWS
    )

    def validate_model_type(self, value):
        canonical = ModelTypes.coerce_value(value)
        if canonical and canonical not in {tag.value for tag in ModelTypes}:
            raise serializers.ValidationError(f'"{value}" is not a valid choice.')
        return canonical


class ManualDatasetCreateRequestSerializer(serializers.Serializer):
    dataset_name = serializers.CharField()
    number_of_rows = serializers.IntegerField(required=False, min_value=1, default=1)
    number_of_columns = serializers.IntegerField(
        required=False,
        min_value=1,
        default=1,
    )


class DatasetAddColumnsRequestSerializer(serializers.Serializer):
    new_columns_data = serializers.ListField(child=serializers.JSONField())


class DatasetAddEmptyColumnsRequestSerializer(serializers.Serializer):
    num_cols = serializers.IntegerField(required=False, min_value=0, default=0)


class DatasetCellDataRequestSerializer(serializers.Serializer):
    row_ids = serializers.ListField(child=serializers.UUIDField())
    column_ids = serializers.ListField(child=serializers.UUIDField())


class DatasetStaticColumnRequestSerializer(serializers.Serializer):
    new_column_name = serializers.CharField()
    column_type = serializers.CharField()
    source = serializers.CharField(required=False, allow_blank=True)


class DatasetMultipleStaticColumnsRequestSerializer(serializers.Serializer):
    columns = serializers.ListField(child=serializers.JSONField())


class DatasetAddEmptyRowsRequestSerializer(serializers.Serializer):
    num_rows = serializers.IntegerField(required=False, min_value=1, default=1)


class DatasetSdkRowsRequestSerializer(serializers.Serializer):
    dataset_name = serializers.CharField(required=False, allow_blank=True)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)


class DatasetAddRowsRequestSerializer(serializers.Serializer):
    rows = serializers.ListField(child=serializers.JSONField())


class DatasetAddRowsFromExistingRequestSerializer(serializers.Serializer):
    source_dataset_id = serializers.UUIDField()
    column_mapping = serializers.DictField(child=serializers.UUIDField())


class DatasetUpdateColumnNameRequestSerializer(serializers.Serializer):
    new_column_name = serializers.CharField()


class DatasetBehaviorRequestSerializer(serializers.Serializer):
    dataset_name = serializers.CharField(required=False, allow_blank=True)
    column_order = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    column_config = serializers.JSONField(required=False, default=dict)
    dataset_config = serializers.JSONField(required=False, default=dict)


class DatasetCellValueField(serializers.Field):
    class Meta:
        swagger_schema_fields = {
            "description": "New cell value. Accepts JSON primitives or multipart file uploads.",
            "x-nullable": True,
        }

    def to_internal_value(self, data):
        return data

    def to_representation(self, value):
        return value


class DatasetUpdateCellValueRequestSerializer(StrictInputSerializer):
    row_id = serializers.UUIDField()
    column_id = serializers.UUIDField()
    new_value = DatasetCellValueField(required=False, allow_null=True)


class DatasetUpdateColumnTypeRequestSerializer(serializers.Serializer):
    new_column_type = serializers.CharField()
    preview = serializers.BooleanField(required=False, default=True)
    force_update = serializers.BooleanField(required=False, default=False)


class DatasetRowDataRequestSerializer(StrictInputSerializer):
    filters = filter_list_field(required=False, default=list)
    sort = DatasetSortListField(required=False, default=list)
    row_id = serializers.UUIDField()


class DatasetRowDiffRequestSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    column_ids = serializers.ListField(child=serializers.UUIDField())
    row_ids = serializers.ListField(child=serializers.UUIDField())
    compare_column_ids = serializers.ListField(child=serializers.UUIDField())


class UserEvalMutationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    template_id = serializers.CharField(max_length=500)
    config = serializers.JSONField()
    kb_id = serializers.UUIDField(required=False)
    error_localizer = serializers.BooleanField(required=False, default=False)
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    eval_type = serializers.CharField(required=False, allow_blank=True)
    run = serializers.BooleanField(required=False, default=False)
    save_as_template = serializers.BooleanField(required=False, default=False)
    experiment_id = serializers.UUIDField(required=False)
    composite_weight_overrides = serializers.JSONField(
        required=False,
        allow_null=True,
        default=None,
    )


class UserEvalUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    template_id = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    config = serializers.JSONField()
    kb_id = serializers.UUIDField(required=False)
    error_localizer = serializers.BooleanField(required=False, default=False)
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    eval_type = serializers.CharField(required=False, allow_blank=True)
    run = serializers.BooleanField(required=False, default=False)
    save_as_template = serializers.BooleanField(required=False, default=False)
    experiment_id = serializers.UUIDField(required=False)
    composite_weight_overrides = serializers.JSONField(
        required=False,
        allow_null=True,
        default=None,
    )


class StartEvalsProcessRequestSerializer(serializers.Serializer):
    user_eval_ids = serializers.ListField(child=serializers.UUIDField())
    experiment_id = serializers.UUIDField(required=False)
    failed_only = serializers.BooleanField(required=False, default=False)


class StopUserEvalRequestSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField(required=False)


class PreviewRunEvalRequestSerializer(serializers.Serializer):
    config = serializers.JSONField()
    template_id = serializers.UUIDField()
    model = serializers.CharField(required=False, allow_blank=True)
    sdk_uuid = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)
    protect_flash = serializers.BooleanField(required=False, default=False)


class ExperimentRerunRequestSerializer(serializers.Serializer):
    experiment_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    use_temporal = serializers.BooleanField(required=False, default=True)
    max_concurrent_rows = serializers.IntegerField(required=False, min_value=1)


class ExperimentComparisonWeightsRequestSerializer(serializers.Serializer):
    eval_template_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    weights = serializers.JSONField(required=False, default=dict)


class ExperimentAdditionalEvaluationsRequestSerializer(serializers.Serializer):
    eval_template_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
