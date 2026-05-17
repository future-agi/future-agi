import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (
        _repo_root() / "api_contracts" / "openapi" / "swagger.json"
    ).open() as f:
        return json.load(f)


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    responses = operation["responses"]
    if status_code not in responses:
        status_code = next(code for code in sorted(responses) if code.startswith("2"))
    schema = responses[status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array" and schema.get("items", {}).get("$ref"):
        return f"{schema['items']['$ref'].rsplit('/', 1)[-1]}[]"
    raise AssertionError(f"Unexpected response schema: {schema}")


def _response_has_schema(operation, status_code="200"):
    responses = operation["responses"]
    if status_code not in responses:
        status_code = next(code for code in sorted(responses) if code.startswith("2"))
    return "schema" in responses[status_code]


def test_model_hub_ai_writer_and_custom_model_apis_stay_out_of_contract_debt():
    report = _debt_report()
    protected_paths = {
        "/model-hub/ai-eval-writer/",
        "/model-hub/api/model_parameters/",
        "/model-hub/api/model_voices/",
        "/model-hub/api/models_list/",
        "/model-hub/columns/{column_id}/operation-config/",
        "/model-hub/columns/{column_id}/rerun-operation/",
        "/model-hub/cells/{cell_id}/run-error-localizer/",
        "/model-hub/custom-models/",
        "/model-hub/custom-models/list/",
        "/model-hub/custom-models/{id}/",
        "/model-hub/custom_models/create/",
        "/model-hub/custom_models/edit/",
        "/model-hub/custom_models/update-baseline/{id}/",
        "/model-hub/custom-metric/all/{model_id}/",
        "/model-hub/custom-metric/create/",
        "/model-hub/custom-metric/tag-options/{metric_id}/",
        "/model-hub/custom-metric/test/",
        "/model-hub/custom-metric/update/",
        "/model-hub/custom-metric/{model_id}/",
        "/model-hub/column-config/{column_id}/",
        "/model-hub/dataset/columns/{dataset_id}/",
        "/model-hub/dataset/{dataset_id}/json-schema/",
        "/model-hub/datasets/{dataset_id}/add-api-column/",
        "/model-hub/datasets/{dataset_id}/add_vector_db_column/",
        "/model-hub/datasets/{dataset_id}/classify-column/",
        "/model-hub/datasets/{dataset_id}/compare-datasets/",
        "/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/",
        "/model-hub/datasets/{dataset_id}/compare-datasets/download/",
        "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/",
        "/model-hub/datasets/{dataset_id}/compare-stats/",
        "/model-hub/datasets/{dataset_id}/conditional-column/",
        "/model-hub/datasets/{dataset_id}/extract-entities/",
        "/model-hub/datasets/{dataset_id}/preview/{operation_type}/",
        "/model-hub/datasets/compare/get-evals-list/",
        "/model-hub/datasets/compare/preview-run-eval/",
        "/model-hub/datasets/delete-compare/{compare_id}/",
        "/model-hub/datasets/explanation-summary/{dataset_id}/",
        "/model-hub/datasets/explanation-summary/{dataset_id}/refresh/",
        "/model-hub/datasets/get-base-columns/",
        "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/",
        "/model-hub/datasets/huggingface/detail/",
        "/model-hub/datasets/huggingface/list/",
        "/model-hub/dataset/{dataset_id}/run-prompt-stats/",
        "/model-hub/develops/create-dataset-from-huggingface/",
        "/model-hub/develops/dataset-creation-progress/{dataset_id}/",
        "/model-hub/develops/get-huggingface-dataset-config/",
        "/model-hub/develops/{dataset_id}/add_rows_from_huggingface/",
        "/model-hub/develops/{dataset_id}/extract-json-column/",
        "/model-hub/create_custom_evals/",
        "/model-hub/delete-eval-template/",
        "/model-hub/duplicate-eval-template/",
        "/model-hub/evaluate-rows/",
        "/model-hub/eval-playground/",
        "/model-hub/eval-playground/feedback/",
        "/model-hub/eval-sdk-code/",
        "/model-hub/eval-summary-templates/",
        "/model-hub/eval-summary-templates/{template_id}/",
        "/model-hub/eval-templates/bulk-delete/",
        "/model-hub/eval-templates/composite/execute-adhoc/",
        "/model-hub/eval-templates/create-composite/",
        "/model-hub/eval-templates/create-v2/",
        "/model-hub/eval-templates/list/",
        "/model-hub/eval-templates/list-charts/",
        "/model-hub/eval-templates/{template_id}/composite/",
        "/model-hub/eval-templates/{template_id}/composite/execute/",
        "/model-hub/eval-templates/{template_id}/detail/",
        "/model-hub/eval-templates/{template_id}/ground-truth/",
        "/model-hub/eval-templates/{template_id}/ground-truth-config/",
        "/model-hub/eval-templates/{template_id}/ground-truth/upload/",
        "/model-hub/eval-templates/{template_id}/update/",
        "/model-hub/eval-templates/{template_id}/versions/",
        "/model-hub/eval-templates/{template_id}/versions/create/",
        "/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/",
        "/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/",
        "/model-hub/eval-template/create/",
        "/model-hub/eval-user-template/create/",
        "/model-hub/embeddings/",
        "/model-hub/embeddings/{type}/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/",
        "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        "/model-hub/get-column-values/",
        "/model-hub/get-eval-config",
        "/model-hub/get-eval-logs",
        "/model-hub/get-eval-logs-details",
        "/model-hub/get-eval-metrics",
        "/model-hub/get-eval-template-names",
        "/model-hub/get-eval-templates",
        "/model-hub/ground-truth/{ground_truth_id}/",
        "/model-hub/ground-truth/{ground_truth_id}/data/",
        "/model-hub/ground-truth/{ground_truth_id}/embed/",
        "/model-hub/ground-truth/{ground_truth_id}/mapping/",
        "/model-hub/ground-truth/{ground_truth_id}/role-mapping/",
        "/model-hub/ground-truth/{ground_truth_id}/search/",
        "/model-hub/ground-truth/{ground_truth_id}/status/",
        "/model-hub/kb/",
        "/model-hub/kb/supported-embedding-models",
        "/model-hub/kb/supported_embedding_models/",
        "/model-hub/kb/{id}/",
        "/model-hub/knowledge-base/",
        "/model-hub/knowledge-base/files/",
        "/model-hub/knowledge-base/get/",
        "/model-hub/knowledge-base/list/",
        "/model-hub/metrics/by-column/",
        "/model-hub/optimize-dataset/kb/{optim_id}/",
        "/model-hub/optimize-dataset/knowledge-base/",
        "/model-hub/optimize-dataset/{model_id}/",
        "/model-hub/optimize-dataset/{model_id}/column-config/",
        "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        "/model-hub/optimize-dataset/{model_id}/{optimization_id}/",
        "/model-hub/optimisation/create/",
        "/model-hub/optimisation/update/{id}/",
        "/model-hub/performance/detail/{id}/",
        "/model-hub/performance/export/{id}/",
        "/model-hub/performance/options/{model_id}/",
        "/model-hub/performance/report/{model_id}/",
        "/model-hub/performance/report/{model_id}/{report_id}/",
        "/model-hub/performance/tag-distribution/{model_id}/",
        "/model-hub/performance/{id}/",
        "/model-hub/overview/",
        "/model-hub/prompt/metrics/",
        "/model-hub/prompt/metrics/empty-screen",
        "/model-hub/prompt/span-metrics/",
        "/model-hub/prompt-templates/derived-variables/preview/",
        "/model-hub/prompt-templates/{prompt_id}/derived-variables/",
        "/model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
        "/model-hub/prompt-templates/{prompt_id}/derived-variables/{column_name}/schema/",
        "/model-hub/run-prompt-for-rows/",
        "/model-hub/run-prompt/",
        "/model-hub/test-evaluation/",
        "/model-hub/update-eval-template/",
        "/model-hub/upload-file/",
    }

    body_gaps = {
        item["path"]
        for item in report["mutation_endpoints_without_body_schema"]
        if item["group"] == "model-hub"
    }
    response_gaps = {
        item["path"]
        for item in report["operations_without_response_schema"]
        if item["group"] == "model-hub"
    }

    assert not body_gaps
    assert not response_gaps
    assert protected_paths.isdisjoint(body_gaps)
    assert protected_paths.isdisjoint(response_gaps)


def test_model_hub_ai_writer_and_custom_model_mutations_have_request_contracts():
    expected = {
        ("POST", "/model-hub/ai-eval-writer/"): "AIEvalWriterRequest",
        ("POST", "/model-hub/columns/{column_id}/rerun-operation/"): (
            "RerunOperationRequest"
        ),
        ("POST", "/model-hub/cells/{cell_id}/run-error-localizer/"): (
            "ModelHubEmptyRequest"
        ),
        ("POST", "/model-hub/custom-models/{id}/"): (
            "CustomAIModelUpdateRequest"
        ),
        ("POST", "/model-hub/custom_models/create/"): (
            "CustomAIModelCreateRequest"
        ),
        ("PATCH", "/model-hub/custom_models/edit/"): "CustomAIModelEditRequest",
        ("POST", "/model-hub/custom_models/update-baseline/{id}/"): (
            "CustomAIModelBaselineRequest"
        ),
        ("POST", "/model-hub/custom-metric/create/"): (
            "CustomMetricMutationRequest"
        ),
        ("POST", "/model-hub/custom-metric/test/"): "CustomMetricTestRequest",
        ("POST", "/model-hub/custom-metric/update/"): (
            "CustomMetricMutationRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add-api-column/"): (
            "AddApiColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add_vector_db_column/"): (
            "VectorDBColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/classify-column/"): (
            "ClassifyColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/"): (
            "CompareDataset"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/"): (
            "UserEval"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/download/"): (
            "CompareDataset"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/"): (
            "CompareStartEvalsRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-stats/"): (
            "CompareDatasetStatsRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/duplicate-rows/"): (
            "DuplicateRowsRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/duplicate/"): (
            "DuplicateDatasetRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/merge/"): (
            "MergeDatasetRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/conditional-column/"): (
            "ConditionalColumnRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/extract-entities/"): (
            "ExtractEntitiesRequest"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/preview/{operation_type}/"): (
            "PreviewDatasetOperationRequest"
        ),
        ("POST", "/model-hub/datasets/compare/get-evals-list/"): (
            "CompareEvalsListRequest"
        ),
        ("POST", "/model-hub/datasets/compare/preview-run-eval/"): (
            "ComparePreviewRunEvalRequest"
        ),
        ("POST", "/model-hub/datasets/explanation-summary/{dataset_id}/refresh/"): (
            "ModelHubEmptyRequest"
        ),
        ("POST", "/model-hub/datasets/huggingface/detail/"): (
            "HuggingFaceDatasetDetailRequest"
        ),
        ("POST", "/model-hub/datasets/huggingface/list/"): (
            "HuggingFaceDatasetListRequest"
        ),
        ("POST", "/model-hub/develops/create-dataset-from-huggingface/"): (
            "HuggingFaceDatasetCreateRequest"
        ),
        ("POST", "/model-hub/develops/add-as-new/"): (
            "AddAsNewDatasetRequest"
        ),
        ("POST", "/model-hub/develops/add_rows_from_file/"): (
            "AddRowsFromFileRequest"
        ),
        ("POST", "/model-hub/develops/add_run_prompt_column/"): (
            "AddRunPrompt"
        ),
        ("POST", "/model-hub/develops/clone-dataset/{dataset_id}/"): (
            "CloneDatasetRequest"
        ),
        ("POST", "/model-hub/develops/create-dataset-from-local-file/"): (
            "CreateDatasetFromLocalFileRequest"
        ),
        ("POST", "/model-hub/develops/create-dataset-manually/"): (
            "ManualDatasetCreateRequest"
        ),
        ("POST", "/model-hub/develops/create-empty-dataset/"): (
            "CreateEmptyDatasetRequest"
        ),
        ("POST", "/model-hub/develops/create-synthetic-dataset/"): (
            "SyntheticDatasetCreation"
        ),
        ("POST", "/model-hub/develops/edit_run_prompt_column/"): (
            "EditRunPromptColumn"
        ),
        ("POST", "/model-hub/develops/get-cell-data/"): (
            "DatasetCellDataRequest"
        ),
        ("POST", "/model-hub/develops/get-row-diff/"): (
            "DatasetRowDiffRequest"
        ),
        ("POST", "/model-hub/develops/get-huggingface-dataset-config/"): (
            "HuggingFaceDatasetConfigRequest"
        ),
        ("POST", "/model-hub/develops/preview_run_prompt_column/"): (
            "PreviewRunPrompt"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_columns/"): (
            "DatasetAddColumnsRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_empty_columns/"): (
            "DatasetAddEmptyColumnsRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_empty_rows/"): (
            "DatasetAddEmptyRowsRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_multiple_static_columns/"): (
            "DatasetMultipleStaticColumnsRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_rows/"): (
            "DatasetAddRowsRequest"
        ),
        (
            "POST",
            "/model-hub/develops/{dataset_id}/add_rows_from_existing_dataset/",
        ): "DatasetAddRowsFromExistingRequest",
        ("POST", "/model-hub/develops/{dataset_id}/add_rows_from_huggingface/"): (
            "HuggingFaceAddRowsRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_static_column/"): (
            "DatasetStaticColumnRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_synthetic_data/"): (
            "SyntheticData"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_user_eval/"): (
            "UserEvalMutationRequest"
        ),
        (
            "POST",
            "/model-hub/develops/{dataset_id}/edit_and_run_user_eval/{eval_id}/",
        ): "UserEvalMutationRequest",
        ("PUT", "/model-hub/develops/{dataset_id}/edit_dataset_behavior/"): (
            "DatasetBehaviorRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/get-row-data/"): (
            "DatasetRowDataRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/preview_run_eval/"): (
            "PreviewRunEvalRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/start_evals_process/"): (
            "StartEvalsProcessRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/stop_user_eval/{eval_id}/"): (
            "StopUserEvalRequest"
        ),
        ("PUT", "/model-hub/develops/{dataset_id}/update-synthetic-config/"): (
            "SyntheticDatasetConfig"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/update_cell_value/"): (
            "DatasetUpdateCellValueRequest"
        ),
        ("PUT", "/model-hub/develops/{dataset_id}/update_column_name/{column_id}/"): (
            "DatasetUpdateColumnNameRequest"
        ),
        ("PUT", "/model-hub/develops/{dataset_id}/update_column_type/{column_id}/"): (
            "DatasetUpdateColumnTypeRequest"
        ),
        ("POST", "/model-hub/develops/{exp_dataset_id}/create-dataset/"): (
            "CreateDatasetFromExperimentRequest"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/extract-json-column/"): (
            "ExtractJsonColumnRequest"
        ),
        ("POST", "/model-hub/create_custom_evals/"): "CustomEvalTemplateCreate",
        ("POST", "/model-hub/delete-eval-template/"): "DeleteEvalTemplate",
        ("POST", "/model-hub/duplicate-eval-template/"): "DuplicateEvalTemplate",
        ("POST", "/model-hub/evaluate-rows/"): "SingleRowEvaluationRequest",
        ("POST", "/model-hub/eval-playground/"): "EvalPlayGround",
        ("POST", "/model-hub/eval-playground/feedback/"): "EvalPlayGroundFeedback",
        ("POST", "/model-hub/eval-templates/bulk-delete/"): (
            "EvalTemplateBulkDeleteRequest"
        ),
        ("POST", "/model-hub/eval-templates/composite/execute-adhoc/"): (
            "CompositeEvalAdhocExecuteRequest"
        ),
        ("POST", "/model-hub/eval-templates/create-composite/"): (
            "CompositeEvalCreateRequest"
        ),
        ("POST", "/model-hub/eval-templates/create-v2/"): (
            "EvalTemplateCreateV2Request"
        ),
        ("POST", "/model-hub/eval-templates/list/"): "EvalListRequest",
        ("POST", "/model-hub/eval-templates/list-charts/"): (
            "EvalTemplateListChartsRequest"
        ),
        ("PATCH", "/model-hub/eval-templates/{template_id}/composite/"): (
            "CompositeEvalUpdateRequest"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/composite/execute/"): (
            "CompositeEvalExecuteRequest"
        ),
        ("PUT", "/model-hub/eval-templates/{template_id}/ground-truth-config/"): (
            "GroundTruthConfigRequest"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/ground-truth/upload/"): (
            "GroundTruthUploadRequest"
        ),
        ("PUT", "/model-hub/eval-templates/{template_id}/update/"): (
            "EvalTemplateUpdateV2Request"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/versions/create/"): (
            "EvalTemplateVersionCreateRequest"
        ),
        (
            "POST",
            "/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/",
        ): "ModelHubEmptyRequest",
        (
            "PUT",
            "/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/",
        ): "ModelHubEmptyRequest",
        ("POST", "/model-hub/experiments/v2/{experiment_id}/feedback/"): "Feedback",
        ("POST", "/model-hub/eval-summary-templates/"): (
            "EvalSummaryTemplateMutationRequest"
        ),
        ("PUT", "/model-hub/eval-summary-templates/{template_id}/"): (
            "EvalSummaryTemplateMutationRequest"
        ),
        ("POST", "/model-hub/experiments/"): "ExperimentsTable",
        ("PUT", "/model-hub/experiments/"): "ExperimentsTable",
        ("POST", "/model-hub/experiments/re-run/"): (
            "ExperimentRerunRequest"
        ),
        ("POST", "/model-hub/experiments/v2/"): "ExperimentCreateV2",
        ("POST", "/model-hub/experiments/v2/re-run/"): (
            "ExperimentRerunRequest"
        ),
        ("POST", "/model-hub/experiments/v2/row-diff/"): (
            "DatasetRowDiffRequest"
        ),
        ("PUT", "/model-hub/experiments/v2/{experiment_id}/"): (
            "ExperimentUpdateV2"
        ),
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/compare-experiments/",
        ): "ExperimentComparisonWeightsRequest",
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/rerun-cells/",
        ): "ExperimentRerunCells",
        ("POST", "/model-hub/experiments/v2/{experiment_id}/stop/"): (
            "ModelHubEmptyRequest"
        ),
        ("POST", "/model-hub/experiments/{experiment_id}/add-eval/"): (
            "UserEval"
        ),
        (
            "POST",
            "/model-hub/experiments/{experiment_id}/compare-experiments/",
        ): "ExperimentComparisonWeightsRequest",
        (
            "POST",
            "/model-hub/experiments/{experiment_id}/run-evaluations/",
        ): "ExperimentAdditionalEvaluationsRequest",
        ("POST", "/model-hub/eval-template/create/"): "EvalTemplate",
        ("POST", "/model-hub/eval-user-template/create/"): "EvalUserTemplate",
        ("POST", "/model-hub/get-column-values/"): "ColumnValuesRequest",
        ("PATCH", "/model-hub/get-eval-logs"): "UpdateColumnConfig",
        ("POST", "/model-hub/get-eval-metrics"): "EvalMetricRequest",
        ("POST", "/model-hub/get-eval-template-names"): (
            "EvalTemplateNamesRequest"
        ),
        ("POST", "/model-hub/get-eval-templates"): "LegacyEvalTemplatesRequest",
        ("POST", "/model-hub/ground-truth/{ground_truth_id}/embed/"): (
            "ModelHubEmptyRequest"
        ),
        ("PUT", "/model-hub/ground-truth/{ground_truth_id}/mapping/"): (
            "GroundTruthMappingRequest"
        ),
        ("PUT", "/model-hub/ground-truth/{ground_truth_id}/role-mapping/"): (
            "GroundTruthRoleMappingRequest"
        ),
        ("POST", "/model-hub/ground-truth/{ground_truth_id}/search/"): (
            "GroundTruthSearchRequest"
        ),
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        ): "ExperimentFeedbackSubmitRequest",
        ("POST", "/model-hub/kb/"): "KnowledgeBaseCreate",
        ("PUT", "/model-hub/kb/{id}/"): "KnowledgeBase",
        ("POST", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseMutationRequest"
        ),
        ("PATCH", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseMutationRequest"
        ),
        ("POST", "/model-hub/knowledge-base/files/"): (
            "LegacyKnowledgeBaseFilesRequest"
        ),
        ("POST", "/model-hub/optimize-dataset/knowledge-base/"): (
            "OptimizeDatasetKnowledgeBaseRequest"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/"): (
            "OptimizeDatasetMutationRequest"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "OptimizeDatasetOperationRequest"
        ),
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        ): "OptimizeDatasetOperationRequest",
        ("POST", "/model-hub/optimisation/create/"): "OptimizationDataset",
        ("PUT", "/model-hub/optimisation/create/"): "OptimizationDataset",
        ("POST", "/model-hub/optimisation/update/{id}/"): "OptimizationDataset",
        ("PUT", "/model-hub/optimisation/update/{id}/"): "OptimizationDataset",
        ("POST", "/model-hub/performance/detail/{id}/"): (
            "PerformanceDetailsRequest"
        ),
        ("POST", "/model-hub/performance/export/{id}/"): (
            "PerformanceExportRequest"
        ),
        ("POST", "/model-hub/performance/report/{model_id}/"): (
            "PerformanceReportCreate"
        ),
        ("POST", "/model-hub/performance/tag-distribution/{model_id}/"): (
            "PerformanceTagDistributionRequest"
        ),
        ("POST", "/model-hub/performance/{id}/"): "PerformanceQueryRequest",
        ("POST", "/model-hub/prompt-templates/derived-variables/preview/"): (
            "DerivedVariablePreviewRequest"
        ),
        (
            "POST",
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
        ): "DerivedVariableExtractRequest",
        ("POST", "/model-hub/run-prompt-for-rows/"): "RunPromptForRowsRequest",
        ("POST", "/model-hub/run-prompt/"): "Litellm",
        ("POST", "/model-hub/test-evaluation/"): "TestEvalTemplate",
        ("POST", "/model-hub/update-eval-template/"): "UpdateEvalTemplate",
        ("POST", "/model-hub/upload-file/"): "UploadFile",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_model_hub_ai_writer_and_custom_model_endpoints_have_response_contracts():
    expected = {
        ("POST", "/model-hub/ai-eval-writer/"): "AIEvalWriterResponse",
        ("GET", "/model-hub/api/model_parameters/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/api/model_voices/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/api/models_list/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/columns/{column_id}/operation-config/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/columns/{column_id}/rerun-operation/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/cells/{cell_id}/run-error-localizer/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/cells/{cell_id}/run-error-localizer/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/custom-models/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/custom-models/list/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/custom-models/{id}/"): "CustomAIModel",
        ("POST", "/model-hub/custom-models/{id}/"): "CustomAIModel",
        ("POST", "/model-hub/custom_models/create/"): (
            "CustomAIModelCreateResponse"
        ),
        ("GET", "/model-hub/custom_models/edit/"): "ModelHubJSONResponse",
        ("PATCH", "/model-hub/custom_models/edit/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/custom_models/update-baseline/{id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/custom-metric/all/{model_id}/"): (
            "CustomMetricListResponse"
        ),
        ("POST", "/model-hub/custom-metric/create/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/custom-metric/tag-options/{metric_id}/"): (
            "MetricTagOption[]"
        ),
        ("POST", "/model-hub/custom-metric/test/"): "CustomMetricTestResponse",
        ("POST", "/model-hub/custom-metric/update/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/custom-metric/{model_id}/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/column-config/{column_id}/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/dataset/columns/{dataset_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/dataset/{dataset_id}/json-schema/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add-api-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add_vector_db_column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/classify-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/download/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-stats/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/conditional-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/extract-entities/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/preview/{operation_type}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/compare/get-evals-list/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/compare/preview-run-eval/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/datasets/delete-compare/{compare_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("DELETE", "/model-hub/datasets/delete-compare/{compare_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/datasets/explanation-summary/{dataset_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/explanation-summary/{dataset_id}/refresh/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/datasets/get-base-columns/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("DELETE", "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/huggingface/detail/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/datasets/huggingface/list/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/dataset/{dataset_id}/run-prompt-stats/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/develops/create-dataset-from-huggingface/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/develops/dataset-creation-progress/{dataset_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/develops/get-huggingface-dataset-config/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_rows_from_huggingface/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/extract-json-column/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/create_custom_evals/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/delete-eval-template/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/duplicate-eval-template/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/evaluate-rows/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/eval-playground/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/eval-playground/feedback/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/eval-sdk-code/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/eval-summary-templates/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/eval-summary-templates/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/eval-summary-templates/{template_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/bulk-delete/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/composite/execute-adhoc/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/create-composite/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/create-v2/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/list/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/eval-templates/list-charts/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/composite/"): (
            "ModelHubJSONResponse"
        ),
        ("PATCH", "/model-hub/eval-templates/{template_id}/composite/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/composite/execute/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/detail/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/ground-truth/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/ground-truth/upload/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/ground-truth-config/"): (
            "ModelHubJSONResponse"
        ),
        ("PUT", "/model-hub/eval-templates/{template_id}/ground-truth-config/"): (
            "ModelHubJSONResponse"
        ),
        ("PUT", "/model-hub/eval-templates/{template_id}/update/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/versions/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/versions/create/"): (
            "ModelHubJSONResponse"
        ),
        (
            "POST",
            "/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/",
        ): "ModelHubJSONResponse",
        (
            "PUT",
            "/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/",
        ): "ModelHubJSONResponse",
        ("POST", "/model-hub/eval-template/create/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/eval-user-template/create/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/embeddings/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/embeddings/{type}/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/experiments/v2/{experiment_id}/feedback/"): (
            "ModelHubJSONResponse"
        ),
        (
            "GET",
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        ): "ModelHubJSONResponse",
        ("POST", "/model-hub/get-column-values/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/get-eval-config"): "ModelHubJSONResponse",
        ("GET", "/model-hub/get-eval-logs"): "ModelHubJSONResponse",
        ("PATCH", "/model-hub/get-eval-logs"): "ModelHubJSONResponse",
        ("GET", "/model-hub/get-eval-logs-details"): "ModelHubJSONResponse",
        ("GET", "/model-hub/get-eval-metrics"): "ModelHubJSONResponse",
        ("POST", "/model-hub/get-eval-metrics"): "ModelHubJSONResponse",
        ("POST", "/model-hub/get-eval-template-names"): "ModelHubJSONResponse",
        ("POST", "/model-hub/get-eval-templates"): "ModelHubJSONResponse",
        ("DELETE", "/model-hub/ground-truth/{ground_truth_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/ground-truth/{ground_truth_id}/data/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/ground-truth/{ground_truth_id}/embed/"): (
            "ModelHubJSONResponse"
        ),
        ("PUT", "/model-hub/ground-truth/{ground_truth_id}/mapping/"): (
            "ModelHubJSONResponse"
        ),
        ("PUT", "/model-hub/ground-truth/{ground_truth_id}/role-mapping/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/ground-truth/{ground_truth_id}/search/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/ground-truth/{ground_truth_id}/status/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/kb/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/kb/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/kb/supported-embedding-models"): (
            "KnowledgeBaseEmbeddingModelsResponse"
        ),
        ("GET", "/model-hub/kb/supported_embedding_models/"): (
            "KnowledgeBaseEmbeddingModelsResponse"
        ),
        ("GET", "/model-hub/kb/{id}/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/kb/{id}/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/knowledge-base/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/knowledge-base/"): "ModelHubJSONResponse",
        ("PATCH", "/model-hub/knowledge-base/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/knowledge-base/files/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/knowledge-base/get/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/knowledge-base/list/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/metrics/by-column/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/optimize-dataset/kb/{optim_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/knowledge-base/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/optimize-dataset/{model_id}/"): (
            "ModelHubPaginatedResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "ModelHubJSONResponse"
        ),
        (
            "GET",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "GET",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        ): "ModelHubJSONResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        ): "ModelHubJSONResponse",
        ("GET", "/model-hub/optimize-dataset/{model_id}/{optimization_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/optimisation/create/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/optimisation/create/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/optimisation/update/{id}/"): "ModelHubJSONResponse",
        ("PUT", "/model-hub/optimisation/update/{id}/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/performance/detail/{id}/"): (
            "PerformanceDetailsResponse"
        ),
        ("GET", "/model-hub/performance/options/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/performance/report/{model_id}/"): (
            "PerformanceReportPaginatedResponse"
        ),
        ("POST", "/model-hub/performance/report/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("POST", "/model-hub/performance/tag-distribution/{model_id}/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/overview/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/prompt/metrics/"): "ModelHubJSONResponse",
        ("GET", "/model-hub/prompt/metrics/empty-screen"): "ModelHubJSONResponse",
        ("GET", "/model-hub/prompt/span-metrics/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/prompt-templates/derived-variables/preview/"): (
            "ModelHubJSONResponse"
        ),
        ("GET", "/model-hub/prompt-templates/{prompt_id}/derived-variables/"): (
            "ModelHubJSONResponse"
        ),
        (
            "POST",
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
        ): "ModelHubJSONResponse",
        (
            "GET",
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/{column_name}/schema/",
        ): "ModelHubJSONResponse",
        ("POST", "/model-hub/run-prompt-for-rows/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/run-prompt/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/test-evaluation/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/update-eval-template/"): "ModelHubJSONResponse",
        ("POST", "/model-hub/upload-file/"): "ModelHubJSONResponse",
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_model_hub_performance_endpoints_with_dynamic_payloads_have_response_contracts():
    expected = [
        ("POST", "/model-hub/performance/{id}/"),
        ("POST", "/model-hub/performance/export/{id}/"),
    ]

    for method, path in expected:
        assert _response_has_schema(_operation(path, method))


def test_model_hub_performance_report_detail_exposes_supported_methods_only():
    assert set(_swagger()["paths"]["/model-hub/performance/report/{model_id}/{report_id}/"]) == {
        "delete",
        "parameters",
    }
