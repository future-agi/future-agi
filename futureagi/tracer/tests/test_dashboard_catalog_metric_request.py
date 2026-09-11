"""Editor-shaped catalog requests survive validation and query compilation.

These are offline wire/serializer contracts, not browser, storage or permission
qualification. The project scope is already resolved by the caller.
"""

import re
from copy import deepcopy

import pytest

from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.views.dashboard import _normalize_dashboard_query_filters

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
NAMES = ("prompt_tokens", "completion_tokens", "total_tokens", "agent_talk_percentage")


def _payload(name):
    identity = f"system_attribute:traces:{name}"
    binding = {
        "name": name,
        "property_id": identity,
        "type": "system_metric",
        "source": "traces",
    }
    return {
        "project_ids": [PROJECT],
        "time_range": {"preset": "7D"},
        "granularity": "day",
        "metrics": [{**binding, "id": name, "aggregation": "avg"}],
        "breakdowns": [binding],
        "filters": [
            {
                "column_id": name,
                "property_id": identity,
                "source": "traces",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 17,
                },
            }
        ],
    }


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("builder", [DashboardQueryBuilder, DashboardQueryBuilderV2])
@pytest.mark.parametrize("placement", ["global", "per-metric"])
def test_catalog_metric_filter_and_breakdown_survive_request_normalization(
    name, builder, placement
):
    payload = _payload(name)
    if placement == "per-metric":
        payload["metrics"][0]["filters"] = payload.pop("filters")
    before = deepcopy(payload)
    serializer = DashboardQuerySerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    validated = deepcopy(serializer.validated_data)
    config = _normalize_dashboard_query_filters(serializer.validated_data)
    metric = config["metrics"][0]
    condition = (metric if placement == "per-metric" else config)["filters"][0]
    identity = f"system_attribute:traces:{name}"
    assert metric["property_id"] == config["breakdowns"][0]["property_id"] == identity
    assert condition["property_id"] == identity
    assert condition["canonical_filter"]["filter_config"]["filter_value"] == 17
    sql, params = builder(config).build_metric_query(metric)
    assert "AS value" in sql and "AS breakdown_value" in sql
    assert " > " in sql and 17 in params.values()
    assert params["project_ids"] == [PROJECT]
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    assert serializer.validated_data == validated
    assert payload == before


@pytest.mark.parametrize("role", ["metric", "filter", "breakdown"])
def test_public_request_rejects_a_dataset_identity_disguised_as_a_trace_token(role):
    payload = _payload("prompt_tokens")
    selected = payload[
        {"metric": "metrics", "filter": "filters", "breakdown": "breakdowns"}[role]
    ][0]
    selected["property_id"] = "system_attribute:datasets:prompt_tokens"
    serializer = DashboardQuerySerializer(data=payload)
    assert not serializer.is_valid()
    assert "property_id" in str(serializer.errors)
