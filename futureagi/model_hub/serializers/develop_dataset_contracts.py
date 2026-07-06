from rest_framework import serializers

from model_hub.serializers.contracts import DerivedVariableDetailSerializer
from model_hub.serializers.develop_dataset import ColumnSerializer, DatasetSerializer


class _StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = sorted(set(data.keys()) - set(self.fields.keys()))
            if unknown:
                raise serializers.ValidationError(
                    {key: ["Unknown field."] for key in unknown}
                )
        return super().to_internal_value(data)


class DevelopDatasetMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()


class DatasetCopyResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()


class DatasetCopyResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetCopyResultSerializer()


class ManualDatasetCreateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    dataset_id = serializers.UUIDField()
    rows_created = serializers.IntegerField()
    columns_created = serializers.IntegerField()


class ManualDatasetCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManualDatasetCreateResultSerializer()


class DatasetListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    number_of_datapoints = serializers.IntegerField()
    number_of_experiments = serializers.IntegerField()
    number_of_optimisations = serializers.IntegerField()
    derived_datasets = serializers.IntegerField()
    created_at = serializers.CharField()
    dataset_type = serializers.CharField()


class DatasetListQuerySerializer(_StrictSerializer):
    search_text = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
    )
    page = serializers.IntegerField(required=False, min_value=0, default=0)
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=100,
        default=10,
    )
    sort = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )


class DatasetListResultSerializer(serializers.Serializer):
    datasets = DatasetListItemSerializer(many=True)
    total_pages = serializers.IntegerField()
    total_count = serializers.IntegerField()


class DatasetListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetListResultSerializer()


class DatasetTableMetadataSerializer(serializers.Serializer):
    dataset_name = serializers.CharField()
    experiment_id = serializers.UUIDField(required=False)
    experiment_name = serializers.CharField(required=False)
    total_rows = serializers.IntegerField(required=False)
    total_pages = serializers.IntegerField(required=False)
    error_messages = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    status = serializers.JSONField(required=False, allow_null=True)


class DatasetTableColumnSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    data_type = serializers.CharField(allow_null=True)
    is_visible = serializers.BooleanField()
    is_frozen = serializers.BooleanField(allow_null=True)
    source_type = serializers.CharField()
    origin_type = serializers.CharField(allow_null=True)
    source_id = serializers.CharField(allow_null=True)
    order_index = serializers.IntegerField()
    status = serializers.CharField(allow_null=True)
    average_score = serializers.FloatField(allow_null=True)
    reason_column = serializers.BooleanField()
    is_numeric_eval = serializers.BooleanField()
    is_numeric_eval_percentage = serializers.BooleanField()
    eval_tag = serializers.JSONField()
    metadata = serializers.JSONField()
    choices_map = serializers.JSONField()


class DatasetTableRowSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()


class DatasetTableResultSerializer(serializers.Serializer):
    metadata = DatasetTableMetadataSerializer(required=False)
    column_config = DatasetTableColumnSerializer(many=True)
    table = serializers.ListField(
        child=DatasetTableRowSerializer(), required=False
    )
    dataset_config = serializers.JSONField(required=False)
    synthetic_dataset = serializers.BooleanField(required=False)
    synthetic_dataset_percentage = serializers.FloatField(
        required=False,
        allow_null=True,
    )
    synthetic_regenerate = serializers.BooleanField(required=False)
    is_processing_data = serializers.BooleanField(required=False)


class DatasetTableResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetTableResultSerializer()


class DatasetRowNavigationSerializer(serializers.Serializer):
    row_id = serializers.ListField(child=serializers.UUIDField(), required=False)


class DatasetRowDataResultSerializer(serializers.Serializer):
    next = DatasetRowNavigationSerializer()
    current = serializers.JSONField()


class DatasetRowDataResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRowDataResultSerializer()


class DatasetNameItemSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    name = serializers.CharField()
    model_type = serializers.CharField(required=False, allow_blank=True)


class DatasetNamesResultSerializer(serializers.Serializer):
    datasets = DatasetNameItemSerializer(many=True)


class DatasetNamesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetNamesResultSerializer()


class DatasetColumnsMutationResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    data = ColumnSerializer(many=True, required=False)


class DatasetColumnsMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetColumnsMutationResultSerializer()


class DatasetCellInnerMetadataSerializer(serializers.Serializer):
    explanation = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    error_analysis = serializers.JSONField(required=False, allow_null=True)
    selected_input_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class DatasetCellMetadataSerializer(serializers.Serializer):
    response_time_ms = serializers.FloatField(required=False, allow_null=True)
    token_count = serializers.IntegerField(required=False, allow_null=True)
    cost = serializers.JSONField(required=False, allow_null=True)
    cell_metadata = DatasetCellInnerMetadataSerializer(required=False)
    reason = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class DatasetCellValueSerializer(serializers.Serializer):
    cell_value = serializers.JSONField(allow_null=True, required=False)
    cell_diff_value = serializers.JSONField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    value_infos = serializers.JSONField(required=False, allow_null=True)
    feedback_info = serializers.JSONField(required=False, allow_null=True)
    metadata = DatasetCellMetadataSerializer(required=False)


class DatasetCellDataResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(
        child=serializers.DictField(child=DatasetCellValueSerializer())
    )


class DatasetSdkRowsCodeSerializer(serializers.Serializer):
    python_add_row = serializers.CharField()
    python_add_col = serializers.CharField()
    typescript_add_col = serializers.CharField()
    typescript_add_row = serializers.CharField()
    curl_add_col = serializers.CharField()
    curl_add_row = serializers.CharField()


class DatasetSdkRowsResultSerializer(serializers.Serializer):
    api_keys = serializers.JSONField()
    dataset = DatasetSerializer()
    code = DatasetSdkRowsCodeSerializer()


class DatasetSdkRowsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSdkRowsResultSerializer()


class ColumnTypeConversionResultSerializer(serializers.Serializer):
    message = serializers.CharField(required=False)
    column_id = serializers.UUIDField(required=False)
    new_data_type = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    invalid_count = serializers.IntegerField(required=False)
    invalid_values = serializers.ListField(
        child=serializers.JSONField(), required=False
    )
    valid_conversion_samples = serializers.JSONField(required=False)


class ColumnTypeConversionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ColumnTypeConversionResultSerializer()


class DynamicColumnCreateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    new_column_id = serializers.UUIDField()
    new_column_name = serializers.CharField()


class DynamicColumnCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DynamicColumnCreateResultSerializer()


class DynamicColumnMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class DynamicColumnMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DynamicColumnMessageResultSerializer()


class PreviewDatasetOperationResultItemSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    input = serializers.JSONField(required=False, allow_null=True)
    output = serializers.JSONField(required=False, allow_null=True)
    details = serializers.JSONField(required=False, allow_null=True)


class PreviewDatasetOperationResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    preview_results = PreviewDatasetOperationResultItemSerializer(many=True)
    sample_size = serializers.IntegerField()


class PreviewDatasetOperationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PreviewDatasetOperationResultSerializer()


class RunPromptColumnPreviewResultSerializer(serializers.Serializer):
    responses = serializers.ListField(child=serializers.JSONField())
    token_usage = serializers.JSONField()
    cost = serializers.JSONField()


class RunPromptColumnPreviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RunPromptColumnPreviewResultSerializer()


class DatasetDerivedVariablesResultSerializer(serializers.Serializer):
    derived_variables = serializers.DictField(
        child=DerivedVariableDetailSerializer()
    )


class DatasetDerivedVariablesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetDerivedVariablesResultSerializer()


class EvalFunctionListResultSerializer(serializers.Serializer):
    functions = serializers.ListField(child=serializers.JSONField())


class EvalFunctionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalFunctionListResultSerializer()


class EvalListResultSerializer(serializers.Serializer):
    evals = serializers.ListField(child=serializers.JSONField())
    eval_recommendations = serializers.ListField(
        child=serializers.CharField(), required=False
    )


class EvalListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalListResultSerializer()


class EvalStructureSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    eval_tags = serializers.ListField(child=serializers.CharField(), required=False)
    template_name = serializers.CharField(required=False)
    required_keys = serializers.ListField(child=serializers.CharField(), required=False)
    optional_keys = serializers.ListField(child=serializers.CharField(), required=False)
    variable_keys = serializers.ListField(child=serializers.CharField(), required=False)
    run_prompt_column = serializers.BooleanField(required=False)
    mapping = serializers.JSONField(required=False)
    config = serializers.JSONField(required=False)
    params = serializers.JSONField(required=False)
    function_params_schema = serializers.JSONField(required=False)
    eval_type_id = serializers.CharField(required=False, allow_blank=True)
    eval_type = serializers.CharField(required=False, allow_blank=True)
    reason_column = serializers.BooleanField(required=False)
    models = serializers.JSONField(required=False)
    selected_model = serializers.CharField(required=False, allow_blank=True)
    output = serializers.JSONField(required=False)
    config_params_desc = serializers.JSONField(required=False)
    config_params_option = serializers.JSONField(required=False)
    kb_id = serializers.UUIDField(required=False, allow_null=True)
    error_localizer = serializers.BooleanField(required=False)
    choices = serializers.JSONField(required=False, allow_null=True)
    api_key_available = serializers.BooleanField(required=False)
    run_config = serializers.JSONField(required=False)


class EvalStructureResultSerializer(serializers.Serializer):
    eval = EvalStructureSerializer()


class EvalStructureResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalStructureResultSerializer()


class EvalPreviewResultSerializer(serializers.Serializer):
    responses = serializers.ListField(child=serializers.JSONField())


class EvalPreviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalPreviewResultSerializer()


class ProviderStatusItemSerializer(serializers.Serializer):
    provider = serializers.CharField()
    display_name = serializers.CharField()
    has_key = serializers.BooleanField()
    masked_key = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    logo_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    type = serializers.CharField()
    id = serializers.UUIDField(required=False, allow_null=True)


class ProviderStatusResultSerializer(serializers.Serializer):
    providers = ProviderStatusItemSerializer(many=True)


class ProviderStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ProviderStatusResultSerializer()


class DatasetCreateStartedResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()
    dataset_model_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class DatasetCreateStartedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetCreateStartedResultSerializer()


class LocalFileDatasetCreateStartedResultSerializer(
    DatasetCreateStartedResultSerializer
):
    processing_status = serializers.CharField()
    estimated_rows = serializers.IntegerField()
    estimated_columns = serializers.IntegerField()


class LocalFileDatasetCreateStartedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LocalFileDatasetCreateStartedResultSerializer()


class DatasetCreationProgressResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()
    processing_status = serializers.CharField()
    is_processing = serializers.BooleanField()
    is_completed = serializers.BooleanField()
    is_failed = serializers.BooleanField()
    original_filename = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    estimated_rows = serializers.IntegerField(required=False, allow_null=True)
    estimated_columns = serializers.IntegerField(required=False, allow_null=True)
    queued_at = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    started_at = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    completed_at = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    failed_at = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error_message = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class DatasetCreationProgressResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetCreationProgressResultSerializer()


class HuggingFaceDatasetConfigResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    dataset_info = serializers.JSONField()


class HuggingFaceDatasetConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = HuggingFaceDatasetConfigResultSerializer()


class SyntheticDatasetCreateStartedResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    data = DatasetSerializer()


class SyntheticDatasetCreateStartedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SyntheticDatasetCreateStartedResultSerializer()


class SyntheticDatasetConfigPayloadSerializer(serializers.Serializer):
    num_rows = serializers.IntegerField(required=False)
    columns = serializers.ListField(child=serializers.JSONField(), required=False)
    dataset = serializers.JSONField(required=False)
    kb_id = serializers.UUIDField(required=False, allow_null=True)


class SyntheticDatasetConfigResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    data = SyntheticDatasetConfigPayloadSerializer()


class SyntheticDatasetConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SyntheticDatasetConfigResultSerializer()


class SyntheticDatasetUpdateDataSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()
    num_rows = serializers.IntegerField(required=False)
    num_columns = serializers.IntegerField(required=False)


class SyntheticDatasetUpdateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    data = SyntheticDatasetUpdateDataSerializer()


class SyntheticDatasetUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SyntheticDatasetUpdateResultSerializer()


class DatasetRowsImportedResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    rows_added = serializers.IntegerField()


class DatasetRowsImportedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRowsImportedResultSerializer()


class DatasetRowsImportMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class DatasetRowsImportMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRowsImportMessageResultSerializer()


class DuplicateRowsResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    source_rows = serializers.IntegerField()
    copies_per_row = serializers.IntegerField()
    total_new_rows = serializers.IntegerField()
    new_row_ids = serializers.ListField(child=serializers.UUIDField())


class DuplicateRowsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DuplicateRowsResultSerializer()


class DuplicateDatasetResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    new_dataset_id = serializers.UUIDField()
    new_dataset_name = serializers.CharField()
    columns_copied = serializers.IntegerField()
    rows_copied = serializers.IntegerField()


class DuplicateDatasetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DuplicateDatasetResultSerializer()


class MergeDatasetResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    rows_added = serializers.IntegerField()
    new_columns_created = serializers.IntegerField()
    columns_mapped = serializers.IntegerField()


class MergeDatasetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MergeDatasetResultSerializer()


class CompareDatasetMetadataSerializer(serializers.Serializer):
    compare_id = serializers.UUIDField()
    total_rows = serializers.IntegerField()
    total_pages = serializers.IntegerField()


class CompareDatasetResultSerializer(serializers.Serializer):
    metadata = CompareDatasetMetadataSerializer(required=False)
    column_config = serializers.ListField(
        child=serializers.JSONField(), required=False
    )
    table = serializers.ListField(child=serializers.JSONField(), required=False)


class CompareDatasetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompareDatasetResultSerializer()


class CompareDatasetRowResultSerializer(serializers.Serializer):
    prev_row_id = serializers.UUIDField(required=False, allow_null=True)
    next_row_id = serializers.UUIDField(required=False, allow_null=True)
    table = serializers.ListField(child=serializers.JSONField())


class CompareDatasetRowResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompareDatasetRowResultSerializer()


class CompareDatasetDeleteResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class CompareDatasetDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompareDatasetDeleteResultSerializer()


class CompareDatasetStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(
        child=serializers.ListField(child=serializers.JSONField())
    )


class CompareEvalListResultSerializer(serializers.Serializer):
    evals = serializers.ListField(child=serializers.JSONField())


class CompareEvalListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CompareEvalListResultSerializer()
