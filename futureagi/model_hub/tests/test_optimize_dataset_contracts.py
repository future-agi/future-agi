import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.column_config import ColumnConfig
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def create_ai_model(organization, workspace, name=None):
    return AIModel.all_objects.create(
        user_model_id=name or f"api-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def create_metric(ai_model, name=None):
    return Metric.objects.create(
        name=name or f"Metric {uuid.uuid4()}",
        text_prompt="Score the output.",
        criteria_breakdown=["Score the output."],
        model=ai_model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
    )


def create_legacy_optimize_run(
    ai_model,
    metric,
    organization=None,
    workspace=None,
    name=None,
    optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
):
    optimization = OptimizeDataset.no_workspace_objects.create(
        name=name or f"Legacy optimization {uuid.uuid4()}",
        model=ai_model,
        organization=organization,
        workspace=workspace,
        optimize_type=optimize_type,
        start_date="2026-01-01T00:00:00Z",
        end_date="2026-01-02T00:00:00Z",
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version="v1",
        status=OptimizeDataset.StatusType.COMPLETED,
        optimized_k_prompts=["Optimized prompt A", "Optimized prompt B"],
    )
    optimization.metrics.set([metric])
    return optimization


def make_workspace(organization, user, name=None):
    return Workspace.no_workspace_objects.create(
        name=name or f"Workspace {uuid.uuid4()}",
        organization=organization,
        created_by=user,
    )


@pytest.mark.django_db
def test_optimize_dataset_list_rejects_unknown_query_fields(auth_client):
    response = auth_client.get(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/",
        {"filterConfig": "legacy camel alias"},
    )

    assert_unknown_field(response, "filterConfig")


@pytest.mark.django_db
def test_optimize_dataset_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/",
        {
            "name": "Optimize responses",
            "start_date": "2026-01-01T00:00:00",
            "end_date": "2026-01-02T00:00:00",
            "model": str(uuid.uuid4()),
            "optimize_type": "template",
            "environment": "production",
            "version": "v1",
            "metrics": [str(uuid.uuid4())],
            "optimizeType": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "optimizeType")


@pytest.mark.django_db
def test_optimize_right_answer_results_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/right-answers/{uuid.uuid4()}/",
        {"page": 1, "limit": 10, "pageSize": 10},
        format="json",
    )

    assert_unknown_field(response, "pageSize")


@pytest.mark.django_db
def test_optimize_template_results_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/prompt-template-result/{uuid.uuid4()}/",
        {"unexpected": "legacy field"},
        format="json",
    )

    assert_unknown_field(response, "unexpected")


@pytest.mark.django_db
def test_optimize_template_explore_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/prompt-template-explore/{uuid.uuid4()}/",
        {"page": 1, "limit": 10, "pageSize": 10},
        format="json",
    )

    assert_unknown_field(response, "pageSize")


@pytest.mark.django_db
def test_optimize_column_config_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/column-config/",
        {"columns": [], "columnConfig": []},
        format="json",
    )

    assert_unknown_field(response, "columnConfig")


@pytest.mark.django_db
def test_optimize_right_answer_column_config_rejects_unknown_request_fields(
    auth_client,
):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/column-config/right-answers/{uuid.uuid4()}/",
        {"columns": [], "columnConfig": []},
        format="json",
    )

    assert_unknown_field(response, "columnConfig")


@pytest.mark.django_db
def test_optimize_prompt_explore_column_config_rejects_unknown_request_fields(
    auth_client,
):
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{uuid.uuid4()}/column-config/prompt-template-explore/{uuid.uuid4()}/",
        {"columns": [], "columnConfig": []},
        format="json",
    )

    assert_unknown_field(response, "columnConfig")


@pytest.mark.django_db
def test_optimize_knowledge_base_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/optimize-dataset/knowledge-base/",
        {
            "name": "Optimize RAG",
            "knowledge_base_metrics": {},
            "knowledge_base_filters": [],
            "prompt": "Improve retrieval",
            "knowledgeBaseMetrics": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "knowledgeBaseMetrics")


@pytest.mark.django_db
def test_legacy_optimize_dataset_model_routes_scope_detail_and_column_configs(
    auth_client, organization, workspace, user
):
    model = create_ai_model(organization, workspace, "visible-model")
    metric = create_metric(model, "Visible metric")
    visible_run = create_legacy_optimize_run(
        model,
        metric,
        organization=organization,
        workspace=workspace,
        name="Visible legacy optimization",
    )

    other_workspace = make_workspace(organization, user, "Other optimize workspace")
    other_model = create_ai_model(organization, other_workspace, "other-model")
    other_metric = create_metric(other_model, "Other metric")
    other_run = create_legacy_optimize_run(
        other_model,
        other_metric,
        organization=organization,
        workspace=other_workspace,
        name="Other workspace legacy optimization",
    )

    list_response = auth_client.get(f"/model-hub/optimize-dataset/{model.id}/")
    assert list_response.status_code == status.HTTP_200_OK
    ids = {row["id"] for row in list_response.json()["results"]}
    assert str(visible_run.id) in ids
    assert str(other_run.id) not in ids

    detail_response = auth_client.get(
        f"/model-hub/optimize-dataset/{model.id}/{visible_run.id}/"
    )
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.json()["data"]["id"] == str(visible_run.id)

    guarded_detail = auth_client.get(
        f"/model-hub/optimize-dataset/{model.id}/{other_run.id}/"
    )
    assert guarded_detail.status_code == status.HTTP_404_NOT_FOUND

    guarded_model_list = auth_client.get(
        f"/model-hub/optimize-dataset/{other_model.id}/"
    )
    assert guarded_model_list.status_code == status.HTTP_404_NOT_FOUND

    columns_response = auth_client.get(
        f"/model-hub/optimize-dataset/{model.id}/column-config/"
    )
    assert columns_response.status_code == status.HTTP_200_OK
    assert columns_response.json()["columns"]
    column_config = ColumnConfig.objects.get(
        table_name=ColumnConfig.TableName.OPTIMIZE_DATASET,
        identifier=str(model.id),
    )
    assert column_config.organization == organization
    assert column_config.workspace == workspace

    update_columns_response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/column-config/",
        {"columns": [{"label": "Name", "value": "name", "enabled": False}]},
        format="json",
    )
    assert update_columns_response.status_code == status.HTTP_200_OK
    column_config.refresh_from_db()
    assert column_config.columns[0]["enabled"] is False

    right_columns_response = auth_client.get(
        f"/model-hub/optimize-dataset/{model.id}/column-config/right-answers/{visible_run.id}/"
    )
    assert right_columns_response.status_code == status.HTTP_200_OK
    right_values = {
        column["value"] for column in right_columns_response.json()["columns"]
    }
    assert f"{metric.id}-old" in right_values
    assert f"{metric.id}-new" in right_values

    prompt_columns_response = auth_client.get(
        f"/model-hub/optimize-dataset/{model.id}/column-config/prompt-template-explore/{visible_run.id}/"
    )
    assert prompt_columns_response.status_code == status.HTTP_200_OK
    prompt_values = {
        column["value"] for column in prompt_columns_response.json()["columns"]
    }
    assert f"{metric.id}-0" in prompt_values
    assert f"{metric.id}-original" in prompt_values

    guarded_column_config = auth_client.get(
        f"/model-hub/optimize-dataset/{model.id}/column-config/right-answers/{other_run.id}/"
    )
    assert guarded_column_config.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_legacy_optimize_dataset_result_routes_scope_and_tolerate_empty_clickhouse(
    auth_client, organization, workspace, user, monkeypatch
):
    class EmptyClickHouseClient:
        def execute(self, query):
            if "COUNT(*)" in query:
                return [(0,)]
            return []

    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton",
        EmptyClickHouseClient,
    )

    model = create_ai_model(organization, workspace)
    metric = create_metric(model)
    visible_run = create_legacy_optimize_run(
        model,
        metric,
        organization=organization,
        workspace=workspace,
    )

    other_workspace = make_workspace(organization, user)
    other_model = create_ai_model(organization, other_workspace)
    other_metric = create_metric(other_model)
    other_run = create_legacy_optimize_run(
        other_model,
        other_metric,
        organization=organization,
        workspace=other_workspace,
    )

    page_payload = {"page": 1, "limit": 10}
    for path in [
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-explore/{visible_run.id}/",
        f"/model-hub/optimize-dataset/{model.id}/right-answers/{visible_run.id}/",
    ]:
        response = auth_client.post(path, page_payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["results"] == []
        assert body["count"] == 0

    result_response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-result/{visible_run.id}/",
        {},
        format="json",
    )
    assert result_response.status_code == status.HTTP_200_OK
    assert result_response.json()["k_prompts"] == visible_run.optimized_k_prompts
    assert result_response.json()["results"] == []

    guarded_response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-result/{other_run.id}/",
        {},
        format="json",
    )
    assert guarded_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_legacy_optimize_dataset_result_routes_tolerate_clickhouse_read_errors(
    auth_client, organization, workspace, monkeypatch
):
    class MissingEventsClickHouseClient:
        def execute(self, query):
            raise RuntimeError("ClickHouse events table is unavailable")

    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton",
        MissingEventsClickHouseClient,
    )

    model = create_ai_model(organization, workspace)
    metric = create_metric(model)
    visible_run = create_legacy_optimize_run(
        model,
        metric,
        organization=organization,
        workspace=workspace,
    )

    page_payload = {"page": 1, "limit": 10}
    for path in [
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-explore/{visible_run.id}/",
        f"/model-hub/optimize-dataset/{model.id}/right-answers/{visible_run.id}/",
    ]:
        response = auth_client.post(path, page_payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["results"] == []
        assert body["count"] == 0

    result_response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-result/{visible_run.id}/",
        {},
        format="json",
    )
    assert result_response.status_code == status.HTTP_200_OK
    assert result_response.json()["k_prompts"] == visible_run.optimized_k_prompts
    assert result_response.json()["results"] == []


@pytest.mark.django_db
def test_legacy_optimize_dataset_model_create_scopes_model_and_metrics(
    auth_client, organization, workspace, user, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.check_valid_metrics",
        lambda *args, **kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.get_topk_prompts.apply_async",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    model = create_ai_model(organization, workspace)
    metric = create_metric(model)
    other_workspace = make_workspace(organization, user)
    other_model = create_ai_model(organization, other_workspace)
    other_metric = create_metric(other_model)

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/",
        {
            "name": "Created legacy optimize run",
            "start_date": "2026-01-01T00:00:00",
            "end_date": "2026-01-02T00:00:00",
            "model": str(model.id),
            "optimize_type": OptimizeDataset.OptimizeType.TEMPLATE,
            "environment": OptimizeDataset.EnvTypes.TRAINING,
            "version": "v1",
            "metrics": [str(metric.id)],
            "prompt": "Answer {{input}}",
            "variables": {"input": "question"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    created_id = response.json()["data"]["id"]
    created = OptimizeDataset.no_workspace_objects.get(id=created_id)
    assert created.model == model
    assert created.organization == organization
    assert created.workspace == workspace
    assert list(created.metrics.values_list("id", flat=True)) == [metric.id]
    assert dispatched

    mismatch_response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/",
        {
            "name": "Blocked mismatched model",
            "start_date": "2026-01-01T00:00:00",
            "end_date": "2026-01-02T00:00:00",
            "model": str(other_model.id),
            "optimize_type": OptimizeDataset.OptimizeType.TEMPLATE,
            "environment": OptimizeDataset.EnvTypes.TRAINING,
            "version": "v1",
            "metrics": [str(metric.id)],
        },
        format="json",
    )
    assert mismatch_response.status_code == status.HTTP_400_BAD_REQUEST

    other_metric_response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/",
        {
            "name": "Blocked other metric",
            "start_date": "2026-01-01T00:00:00",
            "end_date": "2026-01-02T00:00:00",
            "model": str(model.id),
            "optimize_type": OptimizeDataset.OptimizeType.TEMPLATE,
            "environment": OptimizeDataset.EnvTypes.TRAINING,
            "version": "v1",
            "metrics": [str(other_metric.id)],
        },
        format="json",
    )
    assert other_metric_response.status_code == status.HTTP_400_BAD_REQUEST

    other_model_response = auth_client.post(
        f"/model-hub/optimize-dataset/{other_model.id}/",
        {
            "name": "Blocked other model",
            "start_date": "2026-01-01T00:00:00",
            "end_date": "2026-01-02T00:00:00",
            "model": str(other_model.id),
            "optimize_type": OptimizeDataset.OptimizeType.TEMPLATE,
            "environment": OptimizeDataset.EnvTypes.TRAINING,
            "version": "v1",
            "metrics": [str(other_metric.id)],
        },
        format="json",
    )
    assert other_model_response.status_code == status.HTTP_404_NOT_FOUND
    assert not OptimizeDataset.no_workspace_objects.filter(
        name__in=[
            "Blocked mismatched model",
            "Blocked other metric",
            "Blocked other model",
        ],
    ).exists()


@pytest.mark.django_db
def test_legacy_optimize_dataset_kb_routes_create_and_scope(
    auth_client, organization, workspace, user, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.rag_prompt_optimzer.apply_async",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.create_criteria_text_prompt",
        lambda metric_text: ["Criterion"],
    )

    other_workspace = make_workspace(organization, user)
    other_run = OptimizeDataset.no_workspace_objects.create(
        name="Other workspace KB optimization",
        optimize_type=OptimizeDataset.OptimizeType.RAG_TEMPLATE,
        organization=organization,
        workspace=other_workspace,
        environment=OptimizeDataset.EnvTypes.CORPUS,
        version="",
        status=OptimizeDataset.StatusType.COMPLETED,
    )

    response = auth_client.post(
        "/model-hub/optimize-dataset/knowledge-base/",
        {
            "name": "Scoped KB optimization",
            "knowledge_base_metrics": ["Metric A"],
            "knowledge_base_filters": ["topic"],
            "prompt": "Improve retrieval",
            "variables": {"topic": "support"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    created_id = response.json()["result"]
    created = OptimizeDataset.no_workspace_objects.get(id=created_id)
    assert created.organization == organization
    assert created.workspace == workspace
    assert created.environment == OptimizeDataset.EnvTypes.CORPUS
    assert created.version == ""
    assert dispatched

    list_response = auth_client.get("/model-hub/optimize-dataset/")
    assert list_response.status_code == status.HTTP_200_OK
    ids = {row["id"] for row in list_response.json()["result"]}
    assert str(created.id) in ids
    assert str(other_run.id) not in ids

    detail_response = auth_client.get(f"/model-hub/optimize-dataset/kb/{created.id}/")
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.json()["result"]["name"] == "Scoped KB optimization"

    guarded_detail = auth_client.get(f"/model-hub/optimize-dataset/kb/{other_run.id}/")
    assert guarded_detail.status_code == status.HTTP_404_NOT_FOUND
