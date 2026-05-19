import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


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
