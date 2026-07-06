import json
from pathlib import Path

import pytest
from rest_framework import status


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
    if schema.get("type") == "file":
        return "file"
    if schema.get("type") == "array" and schema.get("items", {}).get("$ref"):
        return f"{schema['items']['$ref'].rsplit('/', 1)[-1]}[]"
    raise AssertionError(f"Unexpected response schema: {schema}")


def _response_has_schema(operation, status_code="200"):
    responses = operation["responses"]
    if status_code not in responses:
        status_code = next(code for code in sorted(responses) if code.startswith("2"))
    return "schema" in responses[status_code]


MODEL_HUB_GUARD_UUID = "00000000-0000-0000-0000-000000000001"


def _model_hub_ai_custom_metric_guard_cases():
    return [
        ("post", "/model-hub/ai-eval-writer/", {}),
        ("post", "/model-hub/ai-filter/", {}),
        ("get", "/model-hub/api/model_voices/", None),
        (
            "get",
            f"/model-hub/cells/{MODEL_HUB_GUARD_UUID}/run-error-localizer/",
            None,
        ),
        (
            "post",
            f"/model-hub/cells/{MODEL_HUB_GUARD_UUID}/run-error-localizer/",
            {},
        ),
        ("get", f"/model-hub/column-config/{MODEL_HUB_GUARD_UUID}/", None),
        ("get", f"/model-hub/custom-metric/all/{MODEL_HUB_GUARD_UUID}/", None),
        ("post", "/model-hub/custom-metric/create/", {}),
        (
            "get",
            f"/model-hub/custom-metric/tag-options/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        ("post", "/model-hub/custom-metric/test/", {}),
        ("post", "/model-hub/custom-metric/update/", {}),
        ("get", f"/model-hub/custom-metric/{MODEL_HUB_GUARD_UUID}/", None),
        ("get", "/model-hub/custom-models/list/", None),
        ("get", f"/model-hub/custom-models/{MODEL_HUB_GUARD_UUID}/", None),
        ("post", f"/model-hub/custom-models/{MODEL_HUB_GUARD_UUID}/", {}),
        ("get", "/model-hub/custom_models/edit/", None),
        (
            "post",
            f"/model-hub/custom_models/update-baseline/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        (
            "post",
            f"/model-hub/custom_models/update-metric/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        ("get", "/model-hub/embeddings/", None),
        ("get", "/model-hub/embeddings/openai/", None),
    ]


def _model_hub_prompt_workbench_guard_cases():
    return [
        ("post", "/model-hub/prompt-templates/", {}),
        ("post", "/model-hub/prompt-templates/analyze-prompt/", {}),
        ("post", "/model-hub/prompt-templates/derived-variables/preview/", {}),
        ("post", "/model-hub/prompt-templates/generate-prompt/", {}),
        ("post", "/model-hub/prompt-templates/generate-variables/", {}),
        ("post", "/model-hub/prompt-templates/improve-prompt/", {}),
        ("put", f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/", {}),
        ("delete", f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/", None),
        (
            "get",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/all-variables/",
            None,
        ),
        (
            "post",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/save-name/",
            {},
        ),
        (
            "post",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/save-prompt-folder/",
            {},
        ),
        (
            "get",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/stop-streaming/",
            None,
        ),
        (
            "get",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/derived-variables/",
            None,
        ),
        (
            "post",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/derived-variables/extract/",
            {},
        ),
        (
            "get",
            f"/model-hub/prompt-templates/{MODEL_HUB_GUARD_UUID}/derived-variables/output/schema/",
            None,
        ),
        ("get", "/model-hub/prompt/metrics/", None),
        ("get", "/model-hub/prompt/metrics/empty-screen", None),
        ("get", "/model-hub/prompt/span-metrics/", None),
        ("get", "/model-hub/response_schema/", None),
        ("post", "/model-hub/response_schema/", {}),
        ("get", f"/model-hub/response_schema/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/response_schema/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/response_schema/{MODEL_HUB_GUARD_UUID}/", {}),
        ("delete", f"/model-hub/response_schema/{MODEL_HUB_GUARD_UUID}/", None),
    ]


def _model_hub_prompt_library_guard_cases():
    return [
        ("get", "/model-hub/prompt-base-templates/", None),
        ("post", "/model-hub/prompt-base-templates/", {}),
        ("get", "/model-hub/prompt-base-templates/get-all-categories/", None),
        ("get", f"/model-hub/prompt-base-templates/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/prompt-base-templates/{MODEL_HUB_GUARD_UUID}/", {}),
        (
            "patch",
            f"/model-hub/prompt-base-templates/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        (
            "delete",
            f"/model-hub/prompt-base-templates/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        ("get", f"/model-hub/prompt-executions/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/prompt-folders/{MODEL_HUB_GUARD_UUID}/", {}),
        ("get", "/model-hub/prompt-history-executions/", None),
        (
            "get",
            f"/model-hub/prompt-history-executions/execution-details/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        (
            "get",
            f"/model-hub/prompt-history-executions/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        ("get", "/model-hub/prompt-labels/", None),
        ("post", "/model-hub/prompt-labels/", {}),
        ("post", "/model-hub/prompt-labels/assign-multiple-labels/", {}),
        ("post", "/model-hub/prompt-labels/create-system-labels/", {}),
        ("get", "/model-hub/prompt-labels/get-by-name/", None),
        ("post", "/model-hub/prompt-labels/remove/", {}),
        ("post", "/model-hub/prompt-labels/set-default/", {}),
        ("get", "/model-hub/prompt-labels/template-labels/", None),
        ("get", f"/model-hub/prompt-labels/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/prompt-labels/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/prompt-labels/{MODEL_HUB_GUARD_UUID}/", {}),
        ("delete", f"/model-hub/prompt-labels/{MODEL_HUB_GUARD_UUID}/", None),
        (
            "post",
            f"/model-hub/prompt-labels/{MODEL_HUB_GUARD_UUID}/{MODEL_HUB_GUARD_UUID}/assign-label-by-id/",
            {},
        ),
    ]


def _model_hub_feedback_score_guard_cases():
    return [
        ("get", "/model-hub/feedback/", None),
        ("post", "/model-hub/feedback/", {}),
        ("get", "/model-hub/feedback/get-feedback-details/", None),
        ("get", "/model-hub/feedback/get-feedback-summary/", None),
        ("get", "/model-hub/feedback/get_template/", None),
        ("post", "/model-hub/feedback/submit-feedback/", {}),
        ("put", f"/model-hub/feedback/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/feedback/{MODEL_HUB_GUARD_UUID}/", {}),
        ("get", "/model-hub/scores/", None),
        ("post", "/model-hub/scores/", {}),
        ("get", f"/model-hub/scores/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/scores/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/scores/{MODEL_HUB_GUARD_UUID}/", {}),
    ]


def _model_hub_provider_asset_guard_cases():
    return [
        ("put", f"/model-hub/api-keys/{MODEL_HUB_GUARD_UUID}/", {}),
        ("put", f"/model-hub/secrets/{MODEL_HUB_GUARD_UUID}/", {}),
        ("get", f"/model-hub/tools/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/tools/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/tools/{MODEL_HUB_GUARD_UUID}/", {}),
        ("delete", f"/model-hub/tools/{MODEL_HUB_GUARD_UUID}/", None),
        ("get", f"/model-hub/tts-voices/{MODEL_HUB_GUARD_UUID}/", None),
        ("put", f"/model-hub/tts-voices/{MODEL_HUB_GUARD_UUID}/", {}),
        ("patch", f"/model-hub/tts-voices/{MODEL_HUB_GUARD_UUID}/", {}),
        ("delete", f"/model-hub/tts-voices/{MODEL_HUB_GUARD_UUID}/", None),
        ("post", "/model-hub/upload-file/", {}),
    ]


def _model_hub_org_user_guard_cases():
    return [
        (
            "get",
            f"/model-hub/organizations/{MODEL_HUB_GUARD_UUID}/users/",
            None,
        ),
        (
            "post",
            f"/model-hub/organizations/{MODEL_HUB_GUARD_UUID}/users/",
            {},
        ),
        (
            "get",
            f"/model-hub/organizations/{MODEL_HUB_GUARD_UUID}/users/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        (
            "put",
            f"/model-hub/organizations/{MODEL_HUB_GUARD_UUID}/users/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        (
            "patch",
            f"/model-hub/organizations/{MODEL_HUB_GUARD_UUID}/users/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        (
            "delete",
            f"/model-hub/organizations/{MODEL_HUB_GUARD_UUID}/users/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
    ]


def _model_hub_queue_review_discussion_guard_cases():
    return [
        (
            "post",
            f"/model-hub/annotation-queues/{MODEL_HUB_GUARD_UUID}/items/bulk-review/",
            {"action": "approve", "item_ids": [MODEL_HUB_GUARD_UUID]},
        ),
        (
            "patch",
            f"/model-hub/annotation-queues/{MODEL_HUB_GUARD_UUID}/items/{MODEL_HUB_GUARD_UUID}/discussion/comments/{MODEL_HUB_GUARD_UUID}/",
            {"comment": "updated anonymous comment"},
        ),
        (
            "delete",
            f"/model-hub/annotation-queues/{MODEL_HUB_GUARD_UUID}/items/{MODEL_HUB_GUARD_UUID}/discussion/comments/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
    ]


def _model_hub_eval_experiment_utility_guard_cases():
    return [
        ("post", "/model-hub/delete-eval-template/", {}),
        (
            "post",
            "/model-hub/duplicate-eval-template/",
            {"name": "anonymous duplicate eval"},
        ),
        ("get", "/model-hub/experiment-detail/", None),
        ("post", "/model-hub/get-column-values/", {}),
        ("patch", "/model-hub/knowledge-base/", {}),
        ("get", "/model-hub/metrics/by-column/", None),
    ]


def _model_hub_optimisation_guard_cases():
    return [
        ("get", "/model-hub/optimisation/", None),
        ("post", "/model-hub/optimisation/create/", {}),
        ("put", "/model-hub/optimisation/create/", {}),
        (
            "post",
            f"/model-hub/optimisation/update/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        (
            "put",
            f"/model-hub/optimisation/update/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        ("get", f"/model-hub/optimisation/{MODEL_HUB_GUARD_UUID}/", None),
        (
            "get",
            f"/model-hub/optimisation/{MODEL_HUB_GUARD_UUID}/details/",
            None,
        ),
    ]


def _model_hub_legacy_optimize_dataset_guard_cases():
    page_body = {"page": 1, "limit": 10}
    column_config_body = {"columns": []}
    return [
        ("get", "/model-hub/optimize-dataset/", None),
        (
            "get",
            f"/model-hub/optimize-dataset/kb/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        ("post", "/model-hub/optimize-dataset/knowledge-base/", {}),
        ("get", f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/", None),
        ("post", f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/", {}),
        (
            "get",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/column-config/",
            None,
        ),
        (
            "post",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/column-config/",
            column_config_body,
        ),
        (
            "get",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/column-config/prompt-template-explore/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        (
            "post",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/column-config/prompt-template-explore/{MODEL_HUB_GUARD_UUID}/",
            column_config_body,
        ),
        (
            "get",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/column-config/right-answers/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        (
            "post",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/column-config/right-answers/{MODEL_HUB_GUARD_UUID}/",
            column_config_body,
        ),
        (
            "post",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/prompt-template-explore/{MODEL_HUB_GUARD_UUID}/",
            page_body,
        ),
        (
            "post",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/prompt-template-result/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        (
            "post",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/right-answers/{MODEL_HUB_GUARD_UUID}/",
            page_body,
        ),
        (
            "get",
            f"/model-hub/optimize-dataset/{MODEL_HUB_GUARD_UUID}/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
    ]


def _model_hub_performance_and_eval_guard_cases():
    return [
        ("get", "/model-hub/overview/", None),
        ("post", f"/model-hub/performance/{MODEL_HUB_GUARD_UUID}/", {}),
        ("post", f"/model-hub/performance/detail/{MODEL_HUB_GUARD_UUID}/", {}),
        ("post", f"/model-hub/performance/export/{MODEL_HUB_GUARD_UUID}/", {}),
        (
            "get",
            f"/model-hub/performance/options/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        ("get", f"/model-hub/performance/report/{MODEL_HUB_GUARD_UUID}/", None),
        ("post", f"/model-hub/performance/report/{MODEL_HUB_GUARD_UUID}/", {}),
        (
            "delete",
            f"/model-hub/performance/report/{MODEL_HUB_GUARD_UUID}/{MODEL_HUB_GUARD_UUID}/",
            None,
        ),
        (
            "post",
            f"/model-hub/performance/tag-distribution/{MODEL_HUB_GUARD_UUID}/",
            {},
        ),
        ("post", "/model-hub/update-eval-template/", {}),
    ]


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
        "/model-hub/custom_models/delete/",
        "/model-hub/custom_models/edit/",
        "/model-hub/custom_models/update-baseline/{id}/",
        "/model-hub/custom_models/update-metric/{id}/",
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
        "/model-hub/develops/retrieve_run_prompt_column_config/",
        "/model-hub/develops/retrieve_run_prompt_options/",
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
        "/model-hub/ground-truth/{ground_truth_id}/setup/",
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


@pytest.mark.parametrize(
    "method,path,body", _model_hub_ai_custom_metric_guard_cases()
)
def test_model_hub_ai_custom_metric_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_prompt_workbench_guard_cases()
)
def test_model_hub_prompt_workbench_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_prompt_library_guard_cases()
)
def test_model_hub_prompt_library_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_feedback_score_guard_cases()
)
def test_model_hub_feedback_score_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_provider_asset_guard_cases()
)
def test_model_hub_provider_asset_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize("method,path,body", _model_hub_org_user_guard_cases())
def test_model_hub_org_user_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_queue_review_discussion_guard_cases()
)
def test_model_hub_queue_review_discussion_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_eval_experiment_utility_guard_cases()
)
def test_model_hub_eval_experiment_utility_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize("method,path,body", _model_hub_optimisation_guard_cases())
def test_model_hub_optimisation_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_legacy_optimize_dataset_guard_cases()
)
def test_model_hub_legacy_optimize_dataset_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "method,path,body", _model_hub_performance_and_eval_guard_cases()
)
def test_model_hub_performance_and_eval_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)
    response = (
        request(path, data=body, format="json")
        if body is not None
        else request(path)
    )

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response["Content-Type"].startswith("application/json")
    assert response.data["type"] == "authentication_error"
    assert response.data["code"] == "not_authenticated"


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
        ("DELETE", "/model-hub/custom_models/delete/"): (
            "CustomAIModelDeleteRequest"
        ),
        ("PATCH", "/model-hub/custom_models/edit/"): "CustomAIModelEditRequest",
        ("POST", "/model-hub/custom_models/update-baseline/{id}/"): (
            "CustomAIModelBaselineRequest"
        ),
        ("POST", "/model-hub/custom_models/update-metric/{id}/"): (
            "CustomAIModelDefaultMetricRequest"
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
            "CompareExperimentEvalRequest"
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
        ): "UserEvalUpdateRequest",
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
        ("PUT", "/model-hub/experiments/"): "ExperimentsTableUpdate",
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
            "UserEvalMutationRequest"
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
            "OptimizeDatasetColumnConfigUpdateRequest"
        ),
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetColumnConfigUpdateRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "OptimizeDatasetColumnConfigUpdateRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetPageRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        ): "ModelHubEmptyRequest",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        ): "OptimizeDatasetPageRequest",
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
        ("GET", "/model-hub/api/model_parameters/"): "ModelParametersResponse",
        ("GET", "/model-hub/api/model_voices/"): "LiteLLMModelVoicesResponse",
        ("GET", "/model-hub/api/models_list/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/columns/{column_id}/operation-config/"): (
            "OperationConfigResponse"
        ),
        ("POST", "/model-hub/columns/{column_id}/rerun-operation/"): (
            "RerunOperationResponse"
        ),
        ("GET", "/model-hub/cells/{cell_id}/run-error-localizer/"): (
            "CellErrorLocalizerResponse"
        ),
        ("POST", "/model-hub/cells/{cell_id}/run-error-localizer/"): (
            "CellErrorLocalizerResponse"
        ),
        ("GET", "/model-hub/custom-models/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/custom-models/list/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/custom-models/{id}/"): "CustomAIModel",
        ("POST", "/model-hub/custom-models/{id}/"): "CustomAIModel",
        ("POST", "/model-hub/custom_models/create/"): (
            "CustomAIModelCreateResponse"
        ),
        ("DELETE", "/model-hub/custom_models/delete/"): (
            "ModelHubStringResultResponse"
        ),
        ("GET", "/model-hub/custom_models/edit/"): "CustomAIModelEditResponse",
        ("PATCH", "/model-hub/custom_models/edit/"): "ModelHubStringResultResponse",
        ("POST", "/model-hub/custom_models/update-baseline/{id}/"): (
            "ModelHubStatusMessageResponse"
        ),
        ("POST", "/model-hub/custom_models/update-metric/{id}/"): (
            "ModelHubStatusMessageResponse"
        ),
        ("GET", "/model-hub/custom-metric/all/{model_id}/"): (
            "CustomMetricListResponse"
        ),
        ("POST", "/model-hub/custom-metric/create/"): "ModelHubStatusResponse",
        ("GET", "/model-hub/custom-metric/tag-options/{metric_id}/"): (
            "MetricTagOption[]"
        ),
        ("POST", "/model-hub/custom-metric/test/"): "CustomMetricTestResponse",
        ("POST", "/model-hub/custom-metric/update/"): "ModelHubStatusResponse",
        ("GET", "/model-hub/custom-metric/{model_id}/"): "ModelHubPaginatedResponse",
        ("GET", "/model-hub/column-config/{column_id}/"): "ColumnConfigResponse",
        ("GET", "/model-hub/dataset/columns/{dataset_id}/"): (
            "DatasetColumnDetailResponse"
        ),
        ("GET", "/model-hub/dataset/{dataset_id}/eval-stats/"): (
            "DatasetEvalStatsResponse"
        ),
        ("GET", "/model-hub/dataset/{dataset_id}/json-schema/"): (
            "DatasetJsonSchemaResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add-api-column/"): (
            "DynamicColumnCreateResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/add_vector_db_column/"): (
            "DynamicColumnCreateResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/classify-column/"): (
            "DynamicColumnCreateResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/"): (
            "CompareDatasetResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/"): (
            "DevelopDatasetMessageResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/download/"): (
            "file"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/"): (
            "DevelopDatasetMessageResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/compare-stats/"): (
            "CompareDatasetStatsResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/conditional-column/"): (
            "DynamicColumnCreateResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/extract-entities/"): (
            "DynamicColumnMessageResponse"
        ),
        ("POST", "/model-hub/datasets/{dataset_id}/preview/{operation_type}/"): (
            "PreviewDatasetOperationResponse"
        ),
        ("POST", "/model-hub/datasets/compare/get-evals-list/"): (
            "CompareEvalListResponse"
        ),
        ("POST", "/model-hub/datasets/compare/preview-run-eval/"): (
            "EvalPreviewResponse"
        ),
        ("GET", "/model-hub/datasets/delete-compare/{compare_id}/"): (
            "CompareDatasetRowResponse"
        ),
        ("DELETE", "/model-hub/datasets/delete-compare/{compare_id}/"): (
            "CompareDatasetDeleteResponse"
        ),
        ("GET", "/model-hub/datasets/explanation-summary/{dataset_id}/"): (
            "DatasetExplanationSummaryResponse"
        ),
        ("POST", "/model-hub/datasets/explanation-summary/{dataset_id}/refresh/"): (
            "DatasetExplanationSummaryResponse"
        ),
        ("GET", "/model-hub/datasets/get-base-columns/"): "BaseColumnsResponse",
        ("GET", "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/"): (
            "CompareDatasetRowResponse"
        ),
        ("DELETE", "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/"): (
            "CompareDatasetDeleteResponse"
        ),
        ("POST", "/model-hub/datasets/huggingface/detail/"): (
            "HuggingFaceDatasetDetailResponse"
        ),
        ("POST", "/model-hub/datasets/huggingface/list/"): (
            "HuggingFaceDatasetListResponse"
        ),
        ("GET", "/model-hub/dataset/{dataset_id}/run-prompt-stats/"): (
            "DatasetRunPromptStatsResponse"
        ),
        ("POST", "/model-hub/develops/create-dataset-from-huggingface/"): (
            "DatasetCreateStartedResponse"
        ),
        ("GET", "/model-hub/develops/dataset-creation-progress/{dataset_id}/"): (
            "DatasetCreationProgressResponse"
        ),
        ("POST", "/model-hub/develops/get-huggingface-dataset-config/"): (
            "HuggingFaceDatasetConfigResponse"
        ),
        ("GET", "/model-hub/develops/retrieve_run_prompt_column_config/"): (
            "RunPromptColumnConfigResponse"
        ),
        ("GET", "/model-hub/develops/retrieve_run_prompt_options/"): (
            "RunPromptOptionsResponse"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/add_rows_from_huggingface/"): (
            "DatasetRowsImportMessageResponse"
        ),
        ("POST", "/model-hub/develops/{dataset_id}/extract-json-column/"): (
            "DynamicColumnCreateResponse"
        ),
        ("POST", "/model-hub/create_custom_evals/"): (
            "CustomEvalTemplateCreateResponse"
        ),
        ("POST", "/model-hub/delete-eval-template/"): (
            "ModelHubStringResultResponse"
        ),
        ("POST", "/model-hub/duplicate-eval-template/"): (
            "DuplicateEvalTemplateResponse"
        ),
        ("POST", "/model-hub/evaluate-rows/"): "SingleRowEvaluationResponse",
        ("POST", "/model-hub/eval-playground/"): "EvalExecutionResponse",
        ("POST", "/model-hub/eval-playground/feedback/"): (
            "EvalPlaygroundFeedbackResponse"
        ),
        ("GET", "/model-hub/eval-sdk-code/"): "EvalCodeSnippetResponse",
        ("GET", "/model-hub/eval-summary-templates/"): (
            "EvalSummaryTemplateListResponse"
        ),
        ("POST", "/model-hub/eval-summary-templates/"): (
            "EvalSummaryTemplateResponse"
        ),
        ("PUT", "/model-hub/eval-summary-templates/{template_id}/"): (
            "EvalSummaryTemplateResponse"
        ),
        ("DELETE", "/model-hub/eval-summary-templates/{template_id}/"): (
            "EvalSummaryTemplateDeleteResponse"
        ),
        ("POST", "/model-hub/eval-templates/bulk-delete/"): (
            "EvalTemplateBulkDeleteResponse"
        ),
        ("POST", "/model-hub/eval-templates/composite/execute-adhoc/"): (
            "CompositeEvalExecuteResponse"
        ),
        ("POST", "/model-hub/eval-templates/create-composite/"): (
            "CompositeEvalCreateResponse"
        ),
        ("POST", "/model-hub/eval-templates/create-v2/"): (
            "EvalTemplateCreateResponse"
        ),
        ("POST", "/model-hub/eval-templates/list/"): "EvalTemplateListResponse",
        ("POST", "/model-hub/eval-templates/list-charts/"): (
            "EvalTemplateListChartsResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/composite/"): (
            "CompositeEvalDetailResponse"
        ),
        ("PATCH", "/model-hub/eval-templates/{template_id}/composite/"): (
            "CompositeEvalDetailResponse"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/composite/execute/"): (
            "CompositeEvalExecuteResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/detail/"): (
            "EvalTemplateDetailResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/ground-truth/"): (
            "GroundTruthListResponse"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/ground-truth/upload/"): (
            "GroundTruthUploadResponse"
        ),
        ("PUT", "/model-hub/eval-templates/{template_id}/update/"): (
            "EvalTemplateUpdateResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/feedback-list/"): (
            "EvalFeedbackListResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/usage/"): (
            "EvalUsageStatsResponse"
        ),
        ("GET", "/model-hub/eval-templates/{template_id}/versions/"): (
            "EvalTemplateVersionListResponse"
        ),
        ("POST", "/model-hub/eval-templates/{template_id}/versions/create/"): (
            "EvalTemplateVersionResponse"
        ),
        (
            "POST",
            "/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/",
        ): "EvalTemplateVersionRestoreResponse",
        (
            "PUT",
            "/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/",
        ): "EvalTemplateVersionResponse",
        ("POST", "/model-hub/eval-template/create/"): (
            "ModelHubStringResultResponse"
        ),
        ("POST", "/model-hub/eval-user-template/create/"): (
            "ModelHubStringResultResponse"
        ),
        ("GET", "/model-hub/embeddings/"): "EmbeddingsResponse",
        ("GET", "/model-hub/embeddings/{type}/"): "EmbeddingsResponse",
        ("GET", "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/"): (
            "ExperimentFeedbackTemplateResponse"
        ),
        ("POST", "/model-hub/experiments/v2/{experiment_id}/feedback/"): (
            "ExperimentFeedbackCreateResponse"
        ),
        (
            "GET",
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
        ): "ExperimentFeedbackDetailsResponse",
        (
            "POST",
            "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
        ): "ExperimentFeedbackSubmitResponse",
        ("POST", "/model-hub/get-column-values/"): "ColumnValuesResponse",
        ("GET", "/model-hub/get-eval-config"): "ModelHubEvalConfigResponse",
        ("GET", "/model-hub/get-eval-logs"): "EvalApiLogRowResponse",
        ("PATCH", "/model-hub/get-eval-logs"): "ModelHubStringResultResponse",
        ("GET", "/model-hub/get-eval-logs-details"): "EvalApiLogTableResponse",
        ("GET", "/model-hub/get-eval-metrics"): "EvalMetricResponse",
        ("POST", "/model-hub/get-eval-metrics"): "EvalMetricResponse",
        ("POST", "/model-hub/get-eval-template-names"): (
            "EvalTemplateNamesResponse"
        ),
        ("POST", "/model-hub/get-eval-templates"): "LegacyEvalTemplatesResponse",
        ("DELETE", "/model-hub/ground-truth/{ground_truth_id}/"): (
            "GroundTruthDeleteResponse"
        ),
        ("GET", "/model-hub/ground-truth/{ground_truth_id}/data/"): (
            "GroundTruthDataResponse"
        ),
        ("POST", "/model-hub/ground-truth/{ground_truth_id}/embed/"): (
            "GroundTruthEmbedResponse"
        ),
        ("GET", "/model-hub/ground-truth/{ground_truth_id}/status/"): (
            "GroundTruthStatusResponse"
        ),
        ("GET", "/model-hub/kb/"): "KnowledgeBaseListResponse",
        ("POST", "/model-hub/kb/"): "KnowledgeBaseResponse",
        ("GET", "/model-hub/kb/supported-embedding-models"): (
            "KnowledgeBaseEmbeddingModelsResponse"
        ),
        ("GET", "/model-hub/kb/supported_embedding_models/"): (
            "KnowledgeBaseEmbeddingModelsResponse"
        ),
        ("GET", "/model-hub/kb/{id}/"): "KnowledgeBaseResponse",
        ("PUT", "/model-hub/kb/{id}/"): "KnowledgeBaseResponse",
        ("GET", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseSdkCodeResponse"
        ),
        ("POST", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseCreateResponse"
        ),
        ("PATCH", "/model-hub/knowledge-base/"): (
            "LegacyKnowledgeBaseMutationResponse"
        ),
        ("POST", "/model-hub/knowledge-base/files/"): (
            "LegacyKnowledgeBaseFilesResponse"
        ),
        ("GET", "/model-hub/knowledge-base/get/"): (
            "LegacyKnowledgeBaseTableResponse"
        ),
        ("GET", "/model-hub/knowledge-base/list/"): (
            "LegacyKnowledgeBaseListResponse"
        ),
        ("GET", "/model-hub/metrics/by-column/"): "MetricsByColumnResponse",
        ("GET", "/model-hub/optimize-dataset/kb/{optim_id}/"): (
            "OptimizeDatasetKnowledgeBaseDetailResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/knowledge-base/"): (
            "OptimizeDatasetKnowledgeBaseCreateResponse"
        ),
        ("GET", "/model-hub/optimize-dataset/{model_id}/"): (
            "OptimizeDatasetPaginatedResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/"): (
            "OptimizeDatasetCreateResponse"
        ),
        ("GET", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "OptimizeDatasetColumnConfigResponse"
        ),
        ("POST", "/model-hub/optimize-dataset/{model_id}/column-config/"): (
            "OptimizeDatasetColumnConfigUpdateResponse"
        ),
        (
            "GET",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetColumnConfigResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        ): "OptimizeDatasetColumnConfigUpdateResponse",
        (
            "GET",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "OptimizeDatasetColumnConfigResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        ): "OptimizeDatasetColumnConfigUpdateResponse",
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        ): "OptimizeDatasetTemplateResultsResponse",
        ("GET", "/model-hub/optimize-dataset/{model_id}/{optimization_id}/"): (
            "OptimizeDatasetDetailResponse"
        ),
        ("POST", "/model-hub/optimisation/create/"): "ModelHubStringResultResponse",
        ("PUT", "/model-hub/optimisation/create/"): "ModelHubStringResultResponse",
        ("POST", "/model-hub/optimisation/update/{id}/"): "ModelHubStringResultResponse",
        ("PUT", "/model-hub/optimisation/update/{id}/"): "ModelHubStringResultResponse",
        ("POST", "/model-hub/performance/detail/{id}/"): (
            "PerformanceDetailsResponse"
        ),
        ("GET", "/model-hub/performance/options/{model_id}/"): (
            "PerformanceOptionsResponse"
        ),
        ("GET", "/model-hub/performance/report/{model_id}/"): (
            "PerformanceReportPaginatedResponse"
        ),
        ("POST", "/model-hub/performance/report/{model_id}/"): (
            "PerformanceReportCreateResponse"
        ),
        ("GET", "/model-hub/overview/"): "ModelHubOverviewResponse",
        ("GET", "/model-hub/prompt/metrics/"): "PromptMetricsResponse",
        ("GET", "/model-hub/prompt/metrics/empty-screen"): "PromptMetricsEmptyScreenResponse",
        ("GET", "/model-hub/prompt/span-metrics/"): "PromptMetricsResponse",
        ("POST", "/model-hub/prompt-templates/derived-variables/preview/"): (
            "DerivedVariableDetailResponse"
        ),
        ("GET", "/model-hub/prompt-templates/{prompt_id}/derived-variables/"): (
            "PromptDerivedVariablesResponse"
        ),
        (
            "POST",
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
        ): "DerivedVariableDetailResponse",
        (
            "GET",
            "/model-hub/prompt-templates/{prompt_id}/derived-variables/{column_name}/schema/",
        ): "DerivedVariableDetailResponse",
        ("POST", "/model-hub/run-prompt-for-rows/"): (
            "ModelHubSuccessMessageResponse"
        ),
        ("POST", "/model-hub/run-prompt/"): "ModelHubStringResultResponse",
        ("POST", "/model-hub/test-evaluation/"): "EvalExecutionResponse",
        ("POST", "/model-hub/update-eval-template/"): (
            "LegacyEvalTemplateUpdateResponse"
        ),
        ("POST", "/model-hub/upload-file/"): "UploadFileResponse",
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_model_hub_performance_endpoints_with_dynamic_payloads_have_response_contracts():
    expected = [
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        ),
        (
            "POST",
            "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        ),
        ("POST", "/model-hub/performance/tag-distribution/{model_id}/"),
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
