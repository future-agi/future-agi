"""The dashboard breakdown widget publishes a bounded, self-describing series set.

A breakdown groups by an attribute whose distinct-value count the request does
not bound, and the formatter fills every time bucket of every series, so the
response grew as ``series x buckets`` with nothing to stop it. These tests pin
the ceiling, the ranking that decides what survives it, and the fields that tell
a client the grouping was wider than what it received.

Synthetic rows only; no ClickHouse, no network, no fixtures from any dataset.
"""

import json
from datetime import datetime

import pytest
from django.conf import settings

from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DatasetQueryBuilder,
)

BUCKETS = 53


def _config(breakdowns):
    return {
        "project_ids": ["00000000-0000-4000-8000-000000000001"],
        "granularity": "week",
        "time_range": {
            "custom_start": "2025-09-11T00:00:00+00:00",
            "custom_end": "2026-09-11T00:00:00+00:00",
        },
        "metrics": [{"id": "latency", "name": "latency", "aggregation": "avg"}],
        "filters": [],
        "breakdowns": breakdowns,
    }


def _attribute_breakdown():
    return [
        {
            "name": "attribute_key",
            "type": "custom_attribute",
            "source": "traces",
            "attribute_type": "string",
        }
    ]


def _rows(series_count, *, label_chars=775, buckets=BUCKETS):
    """One row per (series, bucket), the shape a grouped read returns."""
    rows = []
    for index in range(series_count):
        label = f"v{index:05d}".ljust(label_chars, "x")
        for bucket in range(buckets):
            rows.append(
                {
                    "time_bucket": datetime(2025, 9, 15 + (bucket % 14)),
                    "value": float(index),
                    "breakdown_value": label,
                }
            )
    return rows


def _format(config, rows):
    metric_info = {"id": "latency", "name": "latency", "aggregation": "avg"}
    return DashboardQueryBuilder(config).format_results([(metric_info, rows)])


def test_wide_breakdown_response_stays_bounded():
    """Without the ceiling this payload is tens of megabytes."""
    ceiling = settings.DASHBOARD_BREAKDOWN_MAX_SERIES
    result = _format(_config(_attribute_breakdown()), _rows(ceiling * 20))
    metric = result["metrics"][0]

    assert len(metric["series"]) == ceiling
    assert metric["series_total"] == ceiling * 20
    assert metric["series_truncated"] is True
    assert sum(len(s["data"]) for s in metric["series"]) == ceiling * BUCKETS
    assert len(json.dumps(result, default=str)) < 4 * 1024 * 1024


def test_capped_series_are_the_highest_ranked_ones():
    """The cut keeps the largest summed values, in descending order."""
    ceiling = settings.DASHBOARD_BREAKDOWN_MAX_SERIES
    count = ceiling + 7
    metric = _format(_config(_attribute_breakdown()), _rows(count))["metrics"][0]

    kept = [series["name"] for series in metric["series"]]
    assert kept == [
        f"v{index:05d}".ljust(775, "x") for index in range(count - 1, 6, -1)
    ]


def test_breakdown_inside_the_ceiling_is_not_truncated():
    ceiling = settings.DASHBOARD_BREAKDOWN_MAX_SERIES
    metric = _format(_config(_attribute_breakdown()), _rows(ceiling))["metrics"][0]

    assert len(metric["series"]) == ceiling
    assert metric["series_total"] == ceiling
    assert metric["series_truncated"] is False


def test_unbroken_down_metric_keeps_its_single_total_series():
    rows = [
        {"time_bucket": datetime(2025, 9, 15), "value": 1.0},
        {"time_bucket": datetime(2025, 9, 22), "value": 2.0},
    ]
    metric = _format(_config([]), rows)["metrics"][0]

    assert [series["name"] for series in metric["series"]] == ["total"]
    assert metric["series_total"] == 1
    assert metric["series_truncated"] is False


def test_empty_result_declares_one_empty_series():
    metric = _format(_config(_attribute_breakdown()), [])["metrics"][0]

    assert [series["name"] for series in metric["series"]] == ["total"]
    assert metric["series_total"] == 1
    assert metric["series_truncated"] is False


@pytest.mark.parametrize("count", [1, 40])
def test_dataset_builder_shares_one_ceiling_with_the_trace_builder(count):
    """Both formatters answer through the same ranking and the same ceiling."""
    config = _config(_attribute_breakdown())
    rows = _rows(settings.DASHBOARD_BREAKDOWN_MAX_SERIES + count)

    capped, total = DatasetQueryBuilder(config)._build_series_data(rows)

    assert len(capped) == settings.DASHBOARD_BREAKDOWN_MAX_SERIES
    assert total == settings.DASHBOARD_BREAKDOWN_MAX_SERIES + count


def test_a_breakdown_value_of_the_sentinel_string_does_not_lift_the_ceiling():
    """A group whose attribute value is literally ``total`` is still capped.

    ``total`` is the key the formatter uses for a read with no breakdown, so a
    grouping that happens to contain that value collides with the sentinel. The
    collision must not switch the ceiling off.
    """
    ceiling = settings.DASHBOARD_BREAKDOWN_MAX_SERIES
    count = ceiling * 20
    rows = _rows(count - 1)
    rows.extend(
        {
            "time_bucket": datetime(2025, 9, 15 + (bucket % 14)),
            "value": float(count),
            "breakdown_value": "total",
        }
        for bucket in range(BUCKETS)
    )

    metric = _format(_config(_attribute_breakdown()), rows)["metrics"][0]

    assert len(metric["series"]) == ceiling
    assert metric["series_total"] == count
    assert metric["series_truncated"] is True


def test_a_project_named_like_the_sentinel_does_not_lift_the_ceiling():
    """The same collision through project-name resolution is still capped."""
    ceiling = settings.DASHBOARD_BREAKDOWN_MAX_SERIES
    count = ceiling * 2
    rows = [
        {
            "time_bucket": datetime(2025, 9, 15 + (bucket % 14)),
            "value": float(index),
            "breakdown_value": f"00000000-0000-4000-8000-{index:012d}",
        }
        for index in range(count)
        for bucket in range(BUCKETS)
    ]
    metric_info = {"id": "latency", "name": "latency", "aggregation": "avg"}

    result = DashboardQueryBuilder(
        _config([{"name": "project", "type": "system_metric"}])
    ).format_results(
        [(metric_info, rows)],
        project_name_map={"00000000-0000-4000-8000-000000000000": "total"},
    )
    metric = result["metrics"][0]

    assert len(metric["series"]) == ceiling
    assert metric["series_total"] == count
    assert metric["series_truncated"] is True
