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


def test_model_hub_ai_writer_and_custom_model_apis_stay_out_of_contract_debt():
    report = _debt_report()
    protected_paths = {
        "/model-hub/ai-eval-writer/",
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
        "/model-hub/kb/",
        "/model-hub/kb/supported-embedding-models",
        "/model-hub/kb/supported_embedding_models/",
        "/model-hub/kb/{id}/",
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

    assert protected_paths.isdisjoint(body_gaps)
    assert protected_paths.isdisjoint(response_gaps)


def test_model_hub_ai_writer_and_custom_model_mutations_have_request_contracts():
    expected = {
        ("POST", "/model-hub/ai-eval-writer/"): "AIEvalWriterRequest",
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
        ("POST", "/model-hub/kb/"): "KnowledgeBaseCreate",
        ("PUT", "/model-hub/kb/{id}/"): "KnowledgeBase",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_model_hub_ai_writer_and_custom_model_endpoints_have_response_contracts():
    expected = {
        ("POST", "/model-hub/ai-eval-writer/"): "AIEvalWriterResponse",
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
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name
