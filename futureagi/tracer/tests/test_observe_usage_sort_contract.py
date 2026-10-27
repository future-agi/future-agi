"""Public usage-sort contract: exact project-scoped, numbered reads only."""

from uuid import uuid4

import pytest

from tracer.serializers.trace import TraceObserveListQuerySerializer


def request_data(**overrides):
    return {
        "project_id": str(uuid4()),
        "filters": [],
        "sort_params": [{"column_id": "cost", "direction": "asc"}],
        **overrides,
    }


def test_usage_sort_accepts_exact_numbered_project_read():
    serializer = TraceObserveListQuerySerializer(data=request_data(page_number=1))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page_number"] == 1
    assert serializer.validated_data["sort_params"] == [
        {"column_id": "cost", "direction": "asc"}
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"cursor_mode": True},
        {"cursor": "opaque-cursor"},
        {"session_id": str(uuid4())},
        {"project_version_id": str(uuid4())},
        {"sort_params": [{"column_id": "latency", "direction": "asc"}]},
        {
            "filters": [
                {
                    "column_id": "cost",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 1,
                    },
                }
            ]
        },
    ],
)
def test_usage_sort_rejects_unsupported_scope_or_filter_before_queries(change):
    serializer = TraceObserveListQuerySerializer(data=request_data(**change))
    assert not serializer.is_valid()
    assert "sort_params" in serializer.errors


def test_usage_sort_cannot_be_promoted_to_an_org_wide_scan():
    data = request_data()
    del data["project_id"]
    serializer = TraceObserveListQuerySerializer(data=data)
    assert not serializer.is_valid()
    assert "sort_params" in serializer.errors
