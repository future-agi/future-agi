"""Opaque runtime drilldown sets never widen on expiry or project changes."""

import json
from uuid import uuid4

import pytest
from django.core.cache import cache

from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.runtime_cohorts import cohort_filter
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


def test_bounded_handle_roundtrip_and_project_scope():
    project = str(uuid4())
    ids = [str(uuid4()) for _ in range(1000)]
    item = cohort_filter(project, ids)
    assert len(json.dumps(item)) < 300
    assert FilterListField().run_validation([item]) == [item]
    sql, params = ClickHouseFilterBuilderV2(
        project_id=project, query_mode="trace"
    ).translate([item])
    assert "IN %(runtime_cohort" in sql
    assert set(next(iter(params.values()))) == {(project, value) for value in ids}
    assert cohort_filter(project, reversed(ids)) == item
    with pytest.raises(
        UnsupportedFilterShapeError, match="outside the selected project"
    ):
        ClickHouseFilterBuilderV2(
            project_id=str(uuid4()), query_mode="trace"
        ).translate([item])
    cache.delete(f"runtime-cohort:{item['filter_config']['filter_value']}")
    with pytest.raises(UnsupportedFilterShapeError, match="expired"):
        ClickHouseFilterBuilderV2(project_id=project, query_mode="trace").translate(
            [item]
        )


def test_empty_cohort_is_always_false():
    project = str(uuid4())
    sql, params = ClickHouseFilterBuilderV2(
        project_id=project, query_mode="trace"
    ).translate([cohort_filter(project, [])])
    assert "0 = 1" in sql
    assert not params


def test_cohort_bound_is_not_a_truncation():
    with pytest.raises(ValueError, match="too large"):
        cohort_filter(str(uuid4()), [str(index) for index in range(20001)])


def test_latency_sort_requires_the_measured_window_and_rejects_oversized_literals():
    from datetime import UTC, datetime

    from tracer.services.clickhouse.query_builders.base import _unix_microseconds
    from tracer.services.clickhouse.runtime_cohorts import LATENCY_DESC
    from tracer.services.clickhouse.v2.query_builders.observe_runtime import (
        RuntimeLatencyQueryBuilderV2,
    )

    project = str(uuid4())
    dates = [datetime(2026, 11, day, tzinfo=UTC) for day in (1, 2)]
    window = tuple(map(_unix_microseconds, dates))
    date = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [d.isoformat() for d in dates],
        },
    }

    def build(cohort):
        return RuntimeLatencyQueryBuilderV2(
            project_id=project, filters=[date, cohort]
        ).build_runtime_latency_page(LATENCY_DESC, max_read_bytes=256 * 1024**2)

    with pytest.raises(UnsupportedFilterShapeError, match="window changed"):
        build(cohort_filter(project, [str(uuid4())]))  # not a latency measurement
    with pytest.raises(UnsupportedFilterShapeError, match="statement budget"):
        build(
            cohort_filter(
                project, [str(uuid4()) for _ in range(5000)], latency_window=window
            )
        )
    sql, _ = build(cohort_filter(project, [], latency_window=window))
    assert "FROM spans" not in sql
