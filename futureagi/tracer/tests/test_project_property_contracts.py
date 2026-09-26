"""Exact public contracts for project and static trace property catalogs."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.serializers.project import (
    ProjectListQuerySerializer,
    ProjectListResponseSerializer,
)
from tracer.serializers.trace import TracePropertiesResponseSerializer
from tracer.views.project import ProjectView
from tracer.views.trace import TraceView


def _swagger():
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as file:
        return json.load(file)


@pytest.mark.unit
def test_project_list_query_accepts_bounded_frontend_pagination():
    filters = [
        {
            "column_id": "tags",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "production",
            },
        }
    ]
    serializer = ProjectListQuerySerializer(
        data={
            "name": "agent",
            "project_type": "observe",
            "tags": "production,priority",
            "filters": json.dumps(filters),
            "sort_by": "updated_at",
            "sort_direction": "asc",
            "page_number": "0",
            "page_size": "100",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == filters
    assert serializer.validated_data["page_number"] == 0
    assert serializer.validated_data["page_size"] == 100


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "field"),
    [
        ({"page_number": "-1"}, "page_number"),
        ({"page_size": "0"}, "page_size"),
        ({"page_size": "101"}, "page_size"),
        ({"page": "1"}, "page"),
        ({"limit": "20"}, "limit"),
        ({"sort_direction": "sideways"}, "sort_direction"),
    ],
)
def test_project_list_query_rejects_invalid_or_generic_paging(query, field):
    serializer = ProjectListQuerySerializer(data=query)

    assert not serializer.is_valid()
    assert field in serializer.errors


@pytest.mark.unit
def test_project_list_response_accepts_real_envelope():
    serializer = ProjectListResponseSerializer(
        data={
            "status": True,
            "result": {
                "metadata": {
                    "total_rows": 1,
                    "page_number": 0,
                    "page_size": 100,
                    "total_pages": 1,
                },
                "table": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "name": "Observe",
                        "last_30_days_vol": 12,
                        "daily_volume": [1, 2],
                        "created_at": "2026-08-12T00:00:00Z",
                        "updated_at": "2026-08-12T00:00:00Z",
                        "last_active": None,
                        "activity_query_complete": True,
                        "activity_error_code": None,
                        "activity_query_exact": False,
                        "activity_query_provenance": "trace_count_rollup",
                        "run_count": 0,
                        "issues": 0,
                        "tags": ["production"],
                    }
                ],
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.unit
def test_project_list_response_accepts_explicit_activity_degradation():
    serializer = ProjectListResponseSerializer(
        data={
            "status": True,
            "result": {
                "metadata": {
                    "total_rows": 1,
                    "page_number": 0,
                    "page_size": 20,
                    "total_pages": 1,
                },
                "table": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "name": "Observe",
                        "last_30_days_vol": None,
                        "daily_volume": None,
                        "created_at": "2026-08-12T00:00:00Z",
                        "updated_at": "2026-08-12T00:00:00Z",
                        "last_active": None,
                        "activity_query_complete": False,
                        "activity_error_code": "project_activity_unavailable",
                        "activity_query_exact": False,
                        "activity_query_provenance": "trace_count_rollup",
                        "run_count": 0,
                        "issues": 0,
                        "tags": [],
                    }
                ],
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.unit
def test_trace_properties_response_is_a_string_catalog():
    serializer = TracePropertiesResponseSerializer(
        data={"status": True, "result": ["Count", "Average", "P95"]}
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.unit
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page_number": -1}, "page_number"),
        ({"page_size": 0}, "page_size"),
        ({"page_size": 101}, "page_size"),
        ({"page": 1}, "page"),
        ({"limit": 20}, "limit"),
    ],
)
def test_project_list_api_rejects_invalid_paging_before_query_execution(params, field):
    request = APIRequestFactory().get("/tracer/project/list_projects/", params)
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))

    response = ProjectView.as_view({"get": "list_projects"})(request)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field in response.data["details"]


@pytest.mark.unit
def test_trace_properties_api_returns_exact_static_catalog_without_database_access():
    request = APIRequestFactory().get("/tracer/trace/get_properties/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))

    response = TraceView.as_view({"get": "get_properties"})(request)

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "status": True,
        "result": [
            "Count",
            "Percentile Empty",
            "Average",
            "Sum",
            "Standard Deviation",
            "P50",
            "P75",
            "P95",
        ],
    }


@pytest.mark.unit
def test_openapi_wires_exact_project_and_property_contracts():
    swagger = _swagger()
    project_operation = swagger["paths"]["/tracer/project/list_projects/"]["get"]
    parameters = {
        parameter["name"]: parameter for parameter in project_operation["parameters"]
    }

    assert set(parameters) == {
        "name",
        "project_type",
        "tags",
        "filters",
        "sort_by",
        "sort_direction",
        "page_number",
        "page_size",
    }
    assert parameters["page_number"]["minimum"] == 0
    assert parameters["page_size"]["minimum"] == 1
    assert parameters["page_size"]["maximum"] == 100
    assert (
        project_operation["responses"]["200"]["schema"]["$ref"].rsplit("/", 1)[-1]
        == "ProjectListResponse"
    )

    properties_operation = swagger["paths"]["/tracer/trace/get_properties/"]["get"]
    assert properties_operation["parameters"] == []
    assert (
        properties_operation["responses"]["200"]["schema"]["$ref"].rsplit("/", 1)[-1]
        == "TracePropertiesResponse"
    )
    properties_result = swagger["definitions"]["TracePropertiesResponse"]["properties"][
        "result"
    ]
    assert properties_result["type"] == "array"
    assert properties_result["items"]["type"] == "string"
