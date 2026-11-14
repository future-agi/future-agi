import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def performance_dataset():
    return {
        "environment": "production",
        "version": "v1",
        "metric_id": str(uuid.uuid4()),
        "filters": [],
    }


@pytest.mark.django_db
def test_performance_query_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/performance/{uuid.uuid4()}/",
        {
            "datasets": [performance_dataset()],
            "filters": [],
            "breakdown": [],
            "agg_by": "daily",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "startDate": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "startDate")


@pytest.mark.django_db
def test_performance_details_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/performance/detail/{uuid.uuid4()}/",
        {
            "dataset": performance_dataset(),
            "filters": [],
            "page": 1,
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "endDate": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "endDate")


@pytest.mark.django_db
def test_performance_export_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/performance/export/{uuid.uuid4()}/",
        {
            "dataset": performance_dataset(),
            "filters": [],
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "startDate": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "startDate")


@pytest.mark.django_db
def test_performance_tag_distribution_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/performance/tag-distribution/{uuid.uuid4()}/",
        {
            "dataset": performance_dataset(),
            "filters": [],
            "agg_by": "daily",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "graph_type": "all",
            "graphType": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "graphType")
