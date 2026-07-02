from rest_framework import serializers

from model_hub.serializers.contracts import JsonColumnSchemaEntrySerializer
from model_hub.serializers.experiments import (
    ExperimentDetailV2Serializer,
    ExperimentsTableSerializer,
)


class ExperimentStringResultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()


class ExperimentLegacyDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentsTableSerializer()


class ExperimentV2DetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentDetailV2Serializer()


class ExperimentTableRowsColumnConfigSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    origin_type = serializers.CharField(required=False, allow_blank=True)
    data_type = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    group = serializers.JSONField(required=False, allow_null=True)
    average_score = serializers.JSONField(required=False, allow_null=True)
    dataset_id = serializers.CharField(required=False, allow_blank=True)
    choices_map = serializers.JSONField(required=False)
    is_base_column = serializers.BooleanField(required=False)
    output_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    eval_template_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    source_id = serializers.CharField(required=False, allow_blank=True)
    is_agent = serializers.BooleanField(required=False)
    is_final = serializers.BooleanField(required=False)


class ExperimentTableRowsMetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField(required=False)
    dataset = serializers.CharField(required=False, allow_blank=True)
    dataset_name = serializers.CharField(required=False, allow_blank=True)
    column = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    total_pages = serializers.IntegerField(required=False)
    description = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )


class ExperimentRowCellInnerMetadataSerializer(serializers.Serializer):
    explanation = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    error_analysis = serializers.JSONField(required=False, allow_null=True)
    selected_input_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class ExperimentRowCellMetadataSerializer(serializers.Serializer):
    response_time_ms = serializers.FloatField(required=False, allow_null=True)
    token_count = serializers.IntegerField(required=False, allow_null=True)
    cost = serializers.JSONField(required=False, allow_null=True)
    cell_metadata = ExperimentRowCellInnerMetadataSerializer(required=False)
    reason = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class ExperimentRowCellSerializer(serializers.Serializer):
    cell_value = serializers.JSONField(required=False, allow_null=True)
    cell_diff_value = serializers.JSONField(required=False, allow_null=True)
    status = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    metadata = ExperimentRowCellMetadataSerializer(required=False)
    value_infos = serializers.JSONField(required=False, allow_null=True)


# Per-column cells are keyed by runtime column UUID and stay dynamic;
# row_id is the only statically typed field on the row dict.
class ExperimentTableRowSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()


class ExperimentTableRowsResultSerializer(serializers.Serializer):
    column_config = ExperimentTableRowsColumnConfigSerializer(many=True)
    table = serializers.ListField(
        child=ExperimentTableRowSerializer(),
        required=False,
    )
    metadata = ExperimentTableRowsMetadataSerializer(required=False)
    output_format = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    next_row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
    )


class ExperimentTableRowsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentTableRowsResultSerializer()


class ExperimentRowDiffResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(
        child=serializers.DictField(child=ExperimentRowCellSerializer())
    )


class ExperimentStatsColumnConfigSerializer(serializers.Serializer):
    status = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField()
    reverse_output = serializers.BooleanField(required=False)
    output_type = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    eval_template_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )


class ExperimentStatsMetadataSerializer(serializers.Serializer):
    is_winner_chosen = serializers.BooleanField()


class ExperimentStatsResultSerializer(serializers.Serializer):
    column_config = ExperimentStatsColumnConfigSerializer(many=True)
    table_data = serializers.ListField(child=serializers.JSONField())
    metadata = ExperimentStatsMetadataSerializer()


class ExperimentStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentStatsResultSerializer()


class ExperimentEvaluationTokenUsageSerializer(serializers.Serializer):
    avg_completion_tokens = serializers.FloatField()
    avg_prompt_tokens = serializers.FloatField()
    avg_total_tokens = serializers.FloatField()
    total_tokens = serializers.IntegerField()


class ExperimentEvaluationColumnStatsSerializer(serializers.Serializer):
    column_name = serializers.CharField()
    column_id = serializers.UUIDField()
    total_rows = serializers.IntegerField()
    success_rate = serializers.FloatField()
    avg_response_time = serializers.FloatField()
    token_usage = ExperimentEvaluationTokenUsageSerializer()
    avg_score = serializers.JSONField(required=False, allow_null=True)


class ExperimentEvaluationStatsResultSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    experiment_name = serializers.CharField()
    evaluation_id = serializers.UUIDField()
    evaluation_name = serializers.CharField()
    evaluation_template_id = serializers.UUIDField()
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()
    evaluation_columns = ExperimentEvaluationColumnStatsSerializer(many=True)


class ExperimentEvaluationStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentEvaluationStatsResultSerializer()


class ExperimentComparisonColumnMetricSerializer(serializers.Serializer):
    column_id = serializers.UUIDField()
    column_name = serializers.CharField()
    avg_completion_tokens = serializers.FloatField()
    avg_total_tokens = serializers.FloatField()
    avg_response_time = serializers.FloatField()
    avg_score = serializers.JSONField(required=False, allow_null=True)


class ExperimentComparisonDatasetMetricSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    avg_completion_tokens = serializers.FloatField(required=False, allow_null=True)
    avg_total_tokens = serializers.FloatField(required=False, allow_null=True)
    avg_response_time = serializers.FloatField(required=False, allow_null=True)
    avg_score = serializers.FloatField(required=False, allow_null=True)
    columns = ExperimentComparisonColumnMetricSerializer(many=True, required=False)
    normalized_scores = serializers.JSONField(required=False)
    overall_rating = serializers.FloatField(required=False, allow_null=True)
    rank = serializers.IntegerField(required=False, allow_null=True)
    rank_suffix = serializers.CharField(required=False, allow_blank=True)
    total_datasets = serializers.IntegerField(required=False)


class ExperimentDatasetComparisonResultSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    experiment_name = serializers.CharField()
    total_datasets = serializers.IntegerField()
    weights_applied = serializers.JSONField(required=False)
    dataset_comparisons = ExperimentComparisonDatasetMetricSerializer(many=True)


class ExperimentDatasetComparisonResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentDatasetComparisonResultSerializer()


class ExperimentComparisonRawMetricsSerializer(serializers.Serializer):
    avg_completion_tokens = serializers.FloatField(required=False, allow_null=True)
    avg_total_tokens = serializers.FloatField(required=False, allow_null=True)
    avg_response_time = serializers.FloatField(required=False, allow_null=True)
    avg_score = serializers.FloatField(required=False, allow_null=True)


class ExperimentComparisonNormalizedMetricsSerializer(serializers.Serializer):
    completion_tokens = serializers.FloatField(required=False, allow_null=True)
    total_tokens = serializers.FloatField(required=False, allow_null=True)
    response_time = serializers.FloatField(required=False, allow_null=True)
    score = serializers.FloatField(required=False, allow_null=True)


class ExperimentComparisonMetricsSerializer(serializers.Serializer):
    raw = ExperimentComparisonRawMetricsSerializer()
    normalized = ExperimentComparisonNormalizedMetricsSerializer()


class ExperimentComparisonWeightsSerializer(serializers.Serializer):
    response_time = serializers.FloatField(required=False, allow_null=True)
    scores = serializers.JSONField(required=False)
    total_tokens = serializers.FloatField(required=False, allow_null=True)
    completion_tokens = serializers.FloatField(required=False, allow_null=True)


class ExperimentComparisonDetailSerializer(serializers.Serializer):
    scores_weight = serializers.JSONField(required=False)
    experiment_dataset_id = serializers.UUIDField(required=False, allow_null=True)
    rank = serializers.IntegerField(required=False, allow_null=True)
    rank_suffix = serializers.CharField(required=False, allow_blank=True)
    metrics = ExperimentComparisonMetricsSerializer()
    weights = ExperimentComparisonWeightsSerializer()
    overall_rating = serializers.FloatField(required=False, allow_null=True)


class ExperimentComparisonDetailsResultSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    total_comparisons = serializers.IntegerField()
    comparisons = ExperimentComparisonDetailSerializer(many=True)


class ExperimentComparisonDetailsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentComparisonDetailsResultSerializer()


class ExperimentMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class ExperimentMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentMessageResultSerializer()


class ExperimentAddEvalResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    eval_id = serializers.UUIDField()


class ExperimentAddEvalResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentAddEvalResultSerializer()


class ExperimentWorkflowResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    workflow_id = serializers.CharField(required=False, allow_blank=True)


class ExperimentWorkflowResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentWorkflowResultSerializer()


class ExperimentStopWorkflowsCancelledSerializer(serializers.Serializer):
    main = serializers.BooleanField()
    reruns = serializers.BooleanField()


class ExperimentStopResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    experiment_id = serializers.UUIDField()
    workflows_cancelled = ExperimentStopWorkflowsCancelledSerializer()


class ExperimentStopResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentStopResultSerializer()


class ExperimentNameSuggestionResultSerializer(serializers.Serializer):
    suggested_name = serializers.CharField()


class ExperimentNameSuggestionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentNameSuggestionResultSerializer()


class ExperimentNameValidationResultSerializer(serializers.Serializer):
    is_valid = serializers.BooleanField()
    message = serializers.CharField(required=False, allow_blank=True)


class ExperimentNameValidationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentNameValidationResultSerializer()


class ExperimentJsonSchemaResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(child=JsonColumnSchemaEntrySerializer())


class ExperimentDerivedVariablesResultSerializer(serializers.Serializer):
    version = serializers.CharField(required=False, allow_blank=True)
    derived_variables = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
    )


class ExperimentDerivedVariablesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentDerivedVariablesResultSerializer()


class ExperimentFeedbackTemplateResultSerializer(serializers.Serializer):
    output_type = serializers.CharField(required=False, allow_null=True)
    eval_description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    eval_name = serializers.CharField()
    user_eval_name = serializers.CharField()
    choices = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )
    multi_choice = serializers.BooleanField(required=False)


class ExperimentFeedbackTemplateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentFeedbackTemplateResultSerializer()


class ExperimentFeedbackCreateResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class ExperimentFeedbackCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentFeedbackCreateResultSerializer()


class ExperimentFeedbackDetailItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    value = serializers.JSONField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField()
    action_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class ExperimentFeedbackDetailsResultSerializer(serializers.Serializer):
    feedback = ExperimentFeedbackDetailItemSerializer(many=True)
    total_count = serializers.IntegerField()


class ExperimentFeedbackDetailsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentFeedbackDetailsResultSerializer()


class ExperimentFeedbackSubmitResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    action_type = serializers.CharField()
    user_eval_metric_id = serializers.UUIDField()
    workflow_id = serializers.CharField(required=False, allow_blank=True)


class ExperimentFeedbackSubmitResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentFeedbackSubmitResultSerializer()
