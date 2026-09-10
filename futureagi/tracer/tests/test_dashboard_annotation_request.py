"""Own-label grouping has the same capability boundary on reads and writes."""

from copy import deepcopy

import pytest
from rest_framework.exceptions import ValidationError

from tracer.serializers.dashboard import (
    DashboardQuerySerializer,
    DashboardWidgetSerializer,
)
from tracer.views.dashboard import DashboardReadQuerySerializer

pytestmark = pytest.mark.unit
LABEL = "44444444-4444-4444-4444-444444444444"
OTHER = "55555555-5555-4555-8555-555555555555"


def _payload():
    return {
        "project_ids": ["11111111-1111-4111-8111-111111111111"],
        "time_range": {"preset": "7D"},
        "granularity": "day",
        "metrics": [
            {
                "id": LABEL,
                "name": "Quality",
                "type": "annotation_metric",
                "label_id": LABEL,
                "source": "both",
                "output_type": "numeric",
                "aggregation": "avg",
            },
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "count_distinct",
            },
        ],
        "breakdowns": [
            {
                "name": LABEL,
                "type": "annotation_metric",
                "label_id": LABEL,
                "source": "both",
                "output_type": "numeric",
            }
        ],
    }


@pytest.mark.parametrize(
    "serializer_class", [DashboardQuerySerializer, DashboardReadQuerySerializer]
)
@pytest.mark.parametrize("grouped", [False, True])
def test_annotation_request_preserves_supported_grouping(serializer_class, grouped):
    payload = _payload()
    if not grouped:
        payload["breakdowns"] = []
    before = deepcopy(payload)
    serializer = serializer_class(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert payload == before
    assert len(serializer.validated_data["breakdowns"]) == int(grouped)
    if grouped:
        assert serializer.validated_data["breakdowns"][0]["label_id"] == LABEL
    assert DashboardWidgetSerializer().validate_query_config(payload) == before


@pytest.mark.parametrize(
    "kind",
    [
        "different-label",
        "system",
        "eval",
        "multiple",
        "incompatible-source",
        "mixed-label-metrics",
    ],
)
@pytest.mark.parametrize(
    "serializer_class", [DashboardQuerySerializer, DashboardReadQuerySerializer]
)
def test_annotation_request_rejects_ignored_breakdowns(serializer_class, kind):
    payload = _payload()
    breakdown = payload["breakdowns"][0]
    if kind == "different-label":
        breakdown.update(name=OTHER, label_id=OTHER)
    elif kind in {"system", "eval"}:
        payload["breakdowns"] = [
            {
                "name": "model" if kind == "system" else OTHER,
                "type": f"{kind}_metric",
                "source": "traces",
            }
        ]
    elif kind == "multiple":
        payload["breakdowns"].append(deepcopy(breakdown))
    elif kind == "incompatible-source":
        breakdown["source"] = "simulation"
    else:
        payload["metrics"].append(
            {**payload["metrics"][0], "id": OTHER, "label_id": OTHER}
        )
    before = deepcopy(payload)
    serializer = serializer_class(data=payload)
    assert not serializer.is_valid()
    assert "breakdowns" in serializer.errors
    with pytest.raises(ValidationError):
        DashboardWidgetSerializer().validate_query_config(payload)
    assert payload == before
