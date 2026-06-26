"""Regression test: system-metric charts bucket on event time (start_time).

The live ClickHouse dashboard (``TimeSeriesQueryBuilder``) buckets and windows
metrics on ``start_time``. The Postgres path used to bucket on ``created_at``
(ingestion time), so a backfilled or replayed span — ``created_at`` now,
``start_time`` in the past — landed in a different time bucket depending on
which store served the request. This pins the PG path to ``start_time``.

Integration test: needs the Postgres test DB (the ORM aggregation runs real
SQL). It does not exercise ClickHouse.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.observation_span import ObservationSpan
from tracer.utils.graphs_optimized import get_all_system_metrics


def _make_span(project, trace, *, start_time):
    """Create a span with a given start_time. created_at is auto-set to now."""
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="s",
        observation_type="llm",
        start_time=start_time,
        end_time=start_time + timedelta(seconds=1),
        latency_ms=100,
        total_tokens=10,
        cost=0.001,
        status="OK",
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_system_metrics_bucket_on_start_time(project, trace):
    """Spans land in their event-time bucket, not their ingestion-time bucket.

    Both spans are created now (``created_at`` ~ today) but carry ``start_time``
    on different past days inside the default 7-day window. Bucketing on
    ``start_time`` puts each on its event day; bucketing on ``created_at`` would
    pile both onto today and leave the event days empty.
    """
    now = timezone.now()
    _make_span(project, trace, start_time=now - timedelta(days=2))
    _make_span(project, trace, start_time=now - timedelta(days=5))

    result = get_all_system_metrics(
        interval="day",
        filters=[],
        property="",
        system_metric_filters={"project_id": str(project.id)},
    )

    traffic = {p["timestamp"][:10]: p["traffic"] for p in result["traffic"]}
    today = now.date().isoformat()
    day2 = (now - timedelta(days=2)).date().isoformat()
    day5 = (now - timedelta(days=5)).date().isoformat()

    assert traffic.get(day2) == 1, traffic
    assert traffic.get(day5) == 1, traffic
    # created_at-bucketing would have put both spans here.
    assert traffic.get(today, 0) == 0, traffic
