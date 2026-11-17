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
    sql, params = ClickHouseFilterBuilderV2(project_id=project, query_mode="trace").translate([item])
    assert "IN %(runtime_cohort" in sql
    assert set(next(iter(params.values()))) == {(project, value) for value in ids}
    assert cohort_filter(project, reversed(ids)) == item
    with pytest.raises(UnsupportedFilterShapeError, match="outside the selected project"):
        ClickHouseFilterBuilderV2(project_id=str(uuid4()), query_mode="trace").translate([item])
    cache.delete(f'runtime-cohort:{item["filter_config"]["filter_value"]}')
    with pytest.raises(UnsupportedFilterShapeError, match="expired"):
        ClickHouseFilterBuilderV2(project_id=project, query_mode="trace").translate([item])


def test_empty_cohort_is_always_false():
    project = str(uuid4())
    sql, params = ClickHouseFilterBuilderV2(project_id=project, query_mode="trace").translate([cohort_filter(project, [])])
    assert "0 = 1" in sql
    assert not params


def test_cohort_bound_is_not_a_truncation():
    with pytest.raises(ValueError, match="too large"):
        cohort_filter(str(uuid4()), [str(index) for index in range(20001)])
