from rest_framework import serializers

from model_hub.serializers.performance_report import PerformanceReportSerializer


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


class OptimizeDatasetMutationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)
    model = serializers.UUIDField(required=False)
    optimize_type = serializers.CharField(required=False, allow_blank=True)
    environment = serializers.CharField(required=False, allow_blank=True)
    version = serializers.CharField(required=False, allow_blank=True)
    metrics = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class OptimizeDatasetKnowledgeBaseRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    knowledge_base_metrics = serializers.JSONField(required=False)
    knowledge_base_filters = serializers.JSONField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class OptimizeDatasetOperationRequestSerializer(serializers.Serializer):
    filters = serializers.JSONField(required=False)
    order = serializers.JSONField(required=False)
    page_number = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(required=False)
    columns = serializers.JSONField(required=False)
    prompt_template = serializers.CharField(required=False, allow_blank=True)
    prompt = serializers.CharField(required=False, allow_blank=True)
    variables = serializers.JSONField(required=False)


class PerformanceQueryRequestSerializer(serializers.Serializer):
    datasets = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    breakdown = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    agg_by = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)


class PerformanceDetailsRequestSerializer(serializers.Serializer):
    dataset = serializers.JSONField()
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    page = serializers.IntegerField(required=False, default=1)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)


class PerformanceExportRequestSerializer(serializers.Serializer):
    dataset = serializers.JSONField()
    metric = serializers.JSONField(required=False)


class PerformanceTagDistributionRequestSerializer(serializers.Serializer):
    dataset = serializers.JSONField()
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    agg_by = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)
    graph_type = serializers.CharField(required=False, allow_blank=True)


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
    value = serializers.JSONField(required=False)
    explanation = serializers.CharField(required=False, allow_blank=True)


class RunPromptForRowsRequestSerializer(serializers.Serializer):
    run_prompt_ids = serializers.ListField(child=serializers.UUIDField())
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    selected_all_rows = serializers.BooleanField(required=False, default=False)


class DerivedVariableExtractRequestSerializer(serializers.Serializer):
    version = serializers.CharField()
    column_name = serializers.CharField(required=False, default="output")
    output_index = serializers.IntegerField(required=False, default=0)
    response_format_type = serializers.CharField(required=False, allow_blank=True)


class DerivedVariablePreviewRequestSerializer(serializers.Serializer):
    content = serializers.JSONField()
    column_name = serializers.CharField(required=False, default="output")


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


class EvalTemplateBulkDeleteRequestSerializer(serializers.Serializer):
    template_ids = serializers.ListField(child=serializers.UUIDField())


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
    )
    model = serializers.CharField(required=False, default="turing_large")
    output_type = serializers.ChoiceField(
        choices=["pass_fail", "percentage", "deterministic"],
        required=False,
        default="pass_fail",
    )
    pass_threshold = serializers.FloatField(required=False, min_value=0, max_value=1)
    choice_scores = serializers.JSONField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
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
    instructions = serializers.CharField(required=False, allow_null=True)
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
    description = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
    )
    check_internet = serializers.BooleanField(required=False, allow_null=True)
    code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
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
    criteria = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    model = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    config_snapshot = serializers.JSONField(required=False, allow_null=True)


class CompositeEvalCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_null=True, allow_blank=True)
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
    composite_child_axis = serializers.ChoiceField(
        choices=["", "pass_fail", "percentage", "choices", "code"],
        required=False,
        allow_blank=True,
        default="",
    )


class CompositeEvalUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_null=True, max_length=255)
    description = serializers.CharField(required=False, allow_null=True, allow_blank=True)
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
    pass_threshold = serializers.FloatField(required=False, default=0.5)


class GroundTruthUploadRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    file_name = serializers.CharField(required=False, allow_blank=True, default="")
    columns = serializers.ListField(child=serializers.CharField())
    data = serializers.ListField(child=serializers.JSONField())
    variable_mapping = serializers.JSONField(required=False, allow_null=True)
    role_mapping = serializers.JSONField(required=False, allow_null=True)


class GroundTruthMappingRequestSerializer(serializers.Serializer):
    variable_mapping = serializers.JSONField()


class GroundTruthRoleMappingRequestSerializer(serializers.Serializer):
    role_mapping = serializers.JSONField()


class GroundTruthConfigRequestSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False, default=True)
    ground_truth_id = serializers.UUIDField(required=False, allow_null=True)
    mode = serializers.ChoiceField(
        choices=["auto", "manual", "disabled"],
        required=False,
        default="auto",
    )
    max_examples = serializers.IntegerField(required=False, min_value=1, max_value=10)
    similarity_threshold = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=1,
    )
    injection_format = serializers.ChoiceField(
        choices=["structured", "conversational", "xml"],
        required=False,
        default="structured",
    )


class GroundTruthSearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField()
    max_results = serializers.IntegerField(required=False, min_value=1, max_value=20)


class EvalMetricRequestSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )


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
    row = serializers.IntegerField(required=False, min_value=0)


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


class DatasetUpdateCellValueRequestSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    column_id = serializers.UUIDField()
    new_value = serializers.JSONField(required=False, allow_null=True)


class DatasetUpdateColumnTypeRequestSerializer(serializers.Serializer):
    new_column_type = serializers.CharField()
    preview = serializers.BooleanField(required=False, default=True)
    force_update = serializers.BooleanField(required=False, default=False)


class DatasetRowDataRequestSerializer(serializers.Serializer):
    filters = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
    sort = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        default=list,
    )
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
