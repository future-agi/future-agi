"""Keep the public initialization response aligned with the real serializer."""

import json
from pathlib import Path

import pytest

from tracer.serializers.dashboard import (
    DashboardFilterValuesResultSerializer,
    DashboardMetricsCatalogResultSerializer,
)


@pytest.mark.parametrize(
    "name,serializer_class",
    [
        ("DashboardMetricsCatalogResult", DashboardMetricsCatalogResultSerializer),
        ("DashboardFilterValuesResult", DashboardFilterValuesResultSerializer),
    ],
)
def test_managed_catalog_response_enums_match_checked_in_openapi(
    name, serializer_class
):
    contract = (
        Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    )
    schema = json.loads(contract.read_text())["definitions"][name]["properties"]
    fields = serializer_class().fields
    for name in ("query_status", "query_provenance"):
        assert schema[name]["enum"] == list(fields[name].choices)


def test_pending_response_is_valid_without_a_fake_activation():
    serializer = DashboardMetricsCatalogResultSerializer(
        data={
            "metrics": [],
            "total": None,
            "total_is_exact": False,
            "category_counts_exact": False,
            "page_size": 50,
            "has_more": False,
            "next_cursor": None,
            "query_complete": False,
            "query_exact": False,
            "query_status": "pending",
            "query_provenance": "property_catalog_bootstrap",
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert "catalog_revision" not in serializer.validated_data


def test_pending_values_are_not_an_empty_active_snapshot():
    serializer = DashboardFilterValuesResultSerializer(
        data={
            "values": [],
            "query_complete": False,
            "query_status": "pending",
            "query_provenance": "property_catalog_bootstrap",
            "has_more": False,
            "next_cursor": None,
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert "catalog_revision" not in serializer.validated_data
