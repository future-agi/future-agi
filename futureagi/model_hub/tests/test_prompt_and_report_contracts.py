import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


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
