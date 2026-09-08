"""Immutable primary-index coordinates may prune I/O, never latest membership."""

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    _attribute_filter,
    _render_driver_sql,
    _time_filter,
)
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000001"
END = datetime(2026, 9, 5)


def builder(
    width=1,
    *,
    kind="text",
    operation="equals",
    value="long ' value",
    cls=TraceListQueryBuilderV2,
    **kwargs,
):
    return cls(
        project_id=PROJECT,
        filters=[
            _time_filter(END - timedelta(days=365), END),
            *[
                _attribute_filter(
                    f"attribute_{index}", value, filter_type=kind, operation=operation
                )
                for index in range(width)
            ],
        ],
        page_size=25,
        **kwargs,
    )


@pytest.mark.parametrize("width", [1, 2, 5, 10])
@pytest.mark.parametrize(
    "kind,operation,value",
    [
        ("text", "equals", "001234"),
        ("text", "contains", "long" * 600),
        ("text", "not_equals", "absent"),
        ("text", "is_null", None),
        ("number", "greater_than", 0.01),
        ("number", "equals", 0),
        ("boolean", "equals", False),
    ],
)
def test_coordinate_seed_cannot_filter_versions_values_roots_or_child_time(
    width, kind, operation, value
):
    subject = builder(width, kind=kind, operation=operation, value=value)
    assert subject._uses_scalar_coordinate_replay()
    assert subject.recommended_filter_classify_batch_size() == 200
    assert subject.recommended_filter_cursor_seed_batch_size() == 200
    assert not subject.allow_filter_anchor_probe_for_initial_continuation()
    assert (
        subject.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == 200
    )
    assert not subject.prefer_filter_candidate_witness_probe_first()
    coordinates = subject._filter_classifier_coordinate_predicate()
    for forbidden in (
        "is_deleted",
        "parent_span_id",
        "attrs_",
        "span_attr",
        "LIMIT",
        "FINAL",
        "_version",
        "start_date",
        "end_date",
        "latest_filter",
    ):
        assert forbidden not in coordinates
    assert "SELECT DISTINCT observation_type, service_name," in coordinates
    assert "toStartOfHour(start_time), trace_id" in coordinates
    assert "project_id = %(project_id)s" in coordinates
    assert "trace_id IN %(candidate_trace_ids)s" in coordinates
    sql, params = subject.build_filter_identity_match_query_from_seed_rows(
        [
            {"trace_id": "one", "root_span_id": "stale-root"},
            {"trace_id": "two", "root_span_id": "other-root"},
        ]
    )
    assert "SELECT DISTINCT observation_type, service_name," in sql
    assert (
        "GROUP BY observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in sql
    )
    assert_coherent_classifier(sql)
    assert "WHERE latest_is_deleted = 0" in sql
    assert "latest_attr_exists_0" in sql
    assert params["candidate_trace_ids"] == ("one", "two")
    assert "stale-root" not in str(params)
    _render_driver_sql(sql, params)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"bounded_sampling_salt": "test", "bounded_sampling_rate": 10},
        {"search": "query"},
    ],
)
def test_other_execution_modes_keep_existing_path(kwargs):
    subject = builder(**kwargs)
    assert not subject._uses_scalar_coordinate_replay()
    assert subject._filter_classifier_coordinate_predicate() == ""


def test_legacy_schema_never_receives_v2_primary_coordinates():
    subject = builder(cls=TraceListQueryBuilder)
    assert subject._filter_classifier_coordinate_predicate() == ""
    assert subject.recommended_filter_classify_batch_size() == 10


def test_project_list_keeps_existing_path_and_json_never_uses_scalar_witness():
    subject = builder()
    subject.project_ids = [PROJECT]
    assert not subject._uses_scalar_coordinate_replay()
    subject = builder(kind="map", value={"path": "value"})
    assert not subject._uses_scalar_coordinate_replay()
    assert subject._uses_attribute_coordinate_replay()


@pytest.mark.parametrize("width", [1, 2, 5, 10])
@pytest.mark.parametrize(
    "kind,operation,value",
    [
        ("map", "contains", {"tier": "VIP"}),
        ("map", "not_equals", {"n": 1}),
        ("map", "is_null", None),
        ("array", "contains", ["0012", 12, True]),
        ("array", "not_contains", ["x"]),
        ("array", "is_not_null", None),
    ],
)
def test_json_uses_same_complete_prefix_and_exact_typed_classifier(
    width, kind, operation, value
):
    subject = builder(width, kind=kind, operation=operation, value=value)
    assert subject._uses_attribute_coordinate_replay()
    assert not subject._uses_scalar_coordinate_replay()
    assert not subject.supports_filter_windowed_candidate_seed_page()
    coordinates = subject._filter_classifier_coordinate_predicate()
    for forbidden in (
        "JSON",
        "LIMIT",
        "is_deleted",
        "parent_span_id",
        "start_date",
        "end_date",
    ):
        assert forbidden not in coordinates
    assert "trace_id IN %(candidate_trace_ids)s" in coordinates
    sql, params = subject.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "one", "root_span_id": "not-authoritative"}]
    )
    assert "JSON" in sql
    assert "SELECT DISTINCT observation_type, service_name," in sql
    assert (
        "GROUP BY observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in sql
    )
    assert "WHERE latest_is_deleted = 0" in sql
    assert "not-authoritative" not in str(params)
    _render_driver_sql(sql, params)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_numeric_seed_limits_root_population_not_child_history(days):
    subject = builder(kind="number", operation="greater_than", value=0.01)
    start = END - timedelta(days=days)
    sql, params = subject.build_filter_windowed_candidate_seed_page(
        slice_start=start, slice_end=END, limit=50
    )
    population = subject._scalar_candidate_root_population_predicate()
    assert subject.supports_filter_windowed_candidate_seed_page()
    assert "start_time >= fromUnixTimestamp64Micro" in population
    assert "start_time < fromUnixTimestamp64Micro" in population
    assert "parent_span_id IS NULL OR parent_span_id = ''" in population
    for forbidden in ("is_deleted", "LIMIT", "attrs_", "filter_before", "FINAL"):
        assert forbidden not in population
    # Time belongs only to the necessary raw-root subquery, not to the
    # all-history numeric witness query wrapped around that subquery.
    seed, _ = sql.split("SELECT trace_id, id AS root_span_id, start_time", 1)
    assert seed.count("start_time >=") == 1
    assert seed.count("start_time <") == 1
    assert "attrs_number" in seed
    assert "LIMIT" not in seed
    assert "is_deleted" not in seed
    assert params["filter_slice_start"] == start
    _render_driver_sql(sql, params)


def test_non_numeric_and_internal_shapes_do_not_opt_into_primary_seed_budget():
    assert not builder().supports_filter_windowed_candidate_seed_page()
    subject = builder(
        kind="number", operation="greater_than", value=0.01, bounded_internal_scan=True
    )
    assert not subject.supports_filter_windowed_candidate_seed_page()
    assert subject._scalar_candidate_root_population_predicate() == ""
