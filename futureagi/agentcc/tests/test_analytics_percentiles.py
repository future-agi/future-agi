"""R3-LOGOBS-07: percentiles describe every request, not a fast prefix."""

from datetime import UTC, datetime, timedelta

import pytest

from agentcc.models.request_log import AgentccRequestLog
from agentcc.services.analytics import (
    get_latency_stats,
    get_model_comparison,
    get_overview_kpis,
)


@pytest.mark.django_db
@pytest.mark.integration
def test_percentiles_include_slow_tail_later_buckets_and_models(organization):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = start + timedelta(hours=3)
    # Cross both historical caps: 50k summary/model and 100k bucket samples.
    # B's bucket is partly outside the old cap, and its model entirely outside.
    for model, latency, at in (
        ("A", 10, start + timedelta(minutes=10)),
        ("B", 1000, start + timedelta(hours=1, minutes=10)),
    ):
        AgentccRequestLog.no_workspace_objects.bulk_create(
            [
                AgentccRequestLog(
                    organization=organization,
                    request_id=f"percentile-{model}-{i}",
                    model=model,
                    latency_ms=latency,
                    started_at=at,
                )
                for i in range(50_001)
            ],
            batch_size=2000,
        )
    AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        request_id="percentile-last-bucket",
        model="C",
        latency_ms=2000,
        started_at=start + timedelta(hours=2, minutes=10),
    )
    AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        request_id="percentile-previous",
        latency_ms=100,
        started_at=start - timedelta(hours=1),
    )
    qs = AgentccRequestLog.no_workspace_objects.filter(organization=organization)
    overview = get_overview_kpis(qs, start, end)
    assert overview["p95_latency_ms"] == {"value": 1000.0, "trend": 900.0}

    latency = get_latency_stats(qs, start, end, "hour")
    assert latency["summary"]["total_requests"] == 100_003
    assert latency["summary"]["p95_ms"] == 1000
    assert latency["summary"]["p99_ms"] == 1000
    occupied = [row for row in latency["timeseries"] if row["request_count"]]
    assert [row["p95_ms"] for row in occupied] == [10, 1000, 2000]

    for grouping in ("model", "billing_mode"):
        models = get_model_comparison(qs, start, end, group_by=grouping)["models"]
        assert {row["model"]: row["p95_latency_ms"] for row in models} == {
            "A": 10,
            "B": 1000,
            "C": 2000,
        }


@pytest.mark.django_db
@pytest.mark.integration
def test_latency_percentiles_use_continuous_interpolation_and_empty_zero(organization):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    qs = AgentccRequestLog.no_workspace_objects.filter(organization=organization)
    assert get_latency_stats(qs, start, end, "hour")["summary"]["p95_ms"] == 0
    for i, latency in enumerate((0, 100)):
        AgentccRequestLog.no_workspace_objects.create(
            organization=organization,
            request_id=f"percentile-small-{i}",
            latency_ms=latency,
            started_at=start + timedelta(minutes=10),
        )
    summary = get_latency_stats(qs, start, end, "hour")["summary"]
    assert summary["p50_ms"] == 50
    assert summary["p95_ms"] == 95
    assert summary["p99_ms"] == 99
