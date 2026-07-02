import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import PromptTemplate, PromptVersion


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def _create_prompt_template(organization, workspace, user, name):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "Hello {{customer}}"}]
        },
        variable_names={"customer": ["Ada"]},
    )
    return template


@pytest.mark.django_db
def test_performance_report_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/performance/report/{uuid.uuid4()}/",
        {
            "name": "Daily quality",
            "datasets": [],
            "filters": [],
            "breakdown": [],
            "aggregation": "daily",
            "start_date": "2026-01-01 00:00:00",
            "end_date": "2026-01-02 00:00:00",
            "startDate": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "startDate")


@pytest.mark.django_db
def test_prompt_observe_metrics_rejects_unknown_query_fields(auth_client):
    response = auth_client.get(
        "/model-hub/prompt/metrics/",
        {
            "prompt_template_id": str(uuid.uuid4()),
            "promptTemplateId": "legacy camel alias",
        },
    )

    assert_unknown_field(response, "promptTemplateId")


@pytest.mark.django_db
def test_prompt_span_metrics_rejects_unknown_query_fields(auth_client):
    response = auth_client.get(
        "/model-hub/prompt/span-metrics/",
        {
            "prompt_template_id": str(uuid.uuid4()),
            "promptTemplateId": "legacy camel alias",
        },
    )

    assert_unknown_field(response, "promptTemplateId")


@pytest.mark.django_db
def test_prompt_metrics_routes_scope_prompt_template_to_workspace(
    auth_client, organization, workspace, user
):
    active_template = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt metrics active template",
    )
    other_workspace = Workspace.objects.create(
        name="Prompt metrics hidden workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden_template = _create_prompt_template(
        organization,
        other_workspace,
        user,
        "Prompt metrics hidden template",
    )

    with patch(
        "model_hub.services.prompt_metrics.fetch_prompt_metrics_query_sql_cte",
        return_value=[],
    ) as metrics_query:
        response = auth_client.get(
            "/model-hub/prompt/metrics/",
            {"prompt_template_id": str(active_template.id), "page_size": 5},
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["prompt_template_id"] == str(active_template.id)
        assert result["prompt_template_name"] == active_template.name
        assert result["table"] == []
        assert result["metadata"]["total_rows"] == 0
        metrics_query.assert_called_once()

        metrics_query.reset_mock()
        hidden_response = auth_client.get(
            "/model-hub/prompt/metrics/",
            {"prompt_template_id": str(hidden_template.id)},
        )

        assert hidden_response.status_code == status.HTTP_404_NOT_FOUND
        metrics_query.assert_not_called()


@pytest.mark.django_db
def test_prompt_span_metrics_route_scopes_prompt_template_to_workspace(
    auth_client, organization, workspace, user
):
    active_template = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt span metrics active template",
    )
    other_workspace = Workspace.objects.create(
        name="Prompt span metrics hidden workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden_template = _create_prompt_template(
        organization,
        other_workspace,
        user,
        "Prompt span metrics hidden template",
    )

    with patch(
        "model_hub.services.prompt_metrics.fetch_prompt_metrics_span_query",
        return_value=([], 0),
    ) as span_query:
        response = auth_client.get(
            "/model-hub/prompt/span-metrics/",
            {
                "prompt_template_id": str(active_template.id),
                "search_term": "span",
                "page_size": 5,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["table"] == []
        assert result["metadata"]["total_rows"] == 0
        span_query.assert_called_once()

        span_query.reset_mock()
        hidden_response = auth_client.get(
            "/model-hub/prompt/span-metrics/",
            {"prompt_template_id": str(hidden_template.id)},
        )

        assert hidden_response.status_code == status.HTTP_404_NOT_FOUND
        span_query.assert_not_called()


@pytest.mark.django_db
def test_prompt_metrics_empty_screen_returns_placeholder_snippets(auth_client):
    response = auth_client.get("/model-hub/prompt/metrics/empty-screen")

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert "your-futureagi-api-key" in result["python"]
    assert "setPromptTemplate" in result["typescript"]


@pytest.mark.django_db
def test_column_values_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/get-column-values/",
        {
            "dataset_id": str(uuid.uuid4()),
            "column_placeholders": {"answer": str(uuid.uuid4())},
            "datasetId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "datasetId")
