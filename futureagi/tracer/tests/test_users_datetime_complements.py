"""Users reuse canonical datetime complements on latest-live contributions."""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilderV2,
)
from tracer.services.users_list_manager import (
    UsersListManager,
    _users_attr_enrichment_query,
)

START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=1)
PROJECT = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"


def date_leaf(operation, value=None, **extra):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": operation,
            "filter_value": value,
            **extra,
        },
    }


def filters():
    return [
        date_leaf("between", [START, END]),
        date_leaf("not_equals", START + timedelta(hours=2)),
    ]


def assert_complement(query, params, column):
    canonical, expected = BaseQueryBuilder.bounded_datetime_exclusion_sql(
        filters(), column=column, param_prefix="users_datetime"
    )
    assert canonical in query
    assert all(params[key] == value for key, value in expected.items())
    assert len(expected) == 2
    assert expected["users_datetime_0_end"] - expected["users_datetime_0_start"] == 1


def test_frozen_window_retains_complements_and_explicit_raw_dates():
    raw = date_leaf("not_equals", START, col_type="SPAN_ATTRIBUTE")
    frozen = UsersListManager._frozen_filters(
        [*filters(), raw], window_start=START, window_end=END
    )
    assert frozen == [filters()[1], raw, date_leaf("between", [START, END])]


@pytest.mark.parametrize("family", ["SPAN_ATTRIBUTE", "EVAL_METRIC", "ANNOTATION"])
def test_native_dates_do_not_consume_explicit_namesakes(family):
    raw = date_leaf("equals", START + timedelta(hours=2), col_type=family)
    assert not UserListQueryBuilderV2._is_date_filter(raw)
    assert UserListQueryBuilderV2.parse_time_range([filters()[0], raw]) == (
        START.replace(tzinfo=None),
        END.replace(tzinfo=None),
    )


def test_registry_only_raw_date_is_not_a_window():
    raw = {
        **date_leaf("equals", START),
        "property_id": "custom_attribute:traces:created_at",
    }
    assert not UserListQueryBuilderV2._is_date_filter(raw)


def test_malformed_native_date_fails_closed():
    with pytest.raises(ValueError, match="valid ISO-8601"):
        UserListQueryBuilderV2.parse_time_range(
            [filters()[0], date_leaf("not_equals", "bad")]
        )


def test_candidate_and_finite_replays_filter_after_winner():
    builder = UserListQueryBuilderV2(
        organization_id=PROJECT, project_id=PROJECT, filters=filters()
    )
    query, _, params = builder._candidate_page_ctes()
    assert_complement(query, params, "latest_start_time")
    assert query.index("argMax(tuple(") < query.index(
        "latest_start_time < fromUnixTimestamp64Micro(%(users_datetime"
    )
    query, params = builder._finite_user_activity_ctes([USER], [])
    assert_complement(query, params, "latest_start_time")
    assert query.index("argMax(start_time") < query.index("HAVING")
    query, params = builder.build_dimension_candidate_query(
        limit=10, window_start=START, window_end=END
    )
    assert_complement(query, params, "latest_start_time")


@pytest.mark.parametrize(
    "membership",
    ["SELECT end_user_id FROM end_users", "SELECT end_user_id FROM latest_spans"],
)
def test_both_graph_snapshot_shapes_filter_latest_contributions(membership):
    builder = UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        filters=filters(),
        interval="day",
        user_membership_sql=membership,
    )
    query, params = builder.build()
    assert_complement(query, params, "start_time")
    # Neither FINAL nor argMax may see exclusions before choosing the winner.
    assert query.index("users_datetime_0_start") > query.index("FROM spans")


def test_raw_attribute_certification_excludes_witness_after_replay():
    query, params = _users_attr_enrichment_query(
        project_id=PROJECT,
        attribute_keys=["tag"],
        start_date=START,
        end_date=END,
        filters=filters(),
    )
    assert_complement(query, params, "latest_start_time")
    assert query.index("users_datetime_0_start") > query.index("argMax(start_time")


def test_standalone_aggregate_selector_filters_latest_contributions():
    from tracer.services.clickhouse.exact_graph_reads import _user_aggregate_source_sql

    query, params, _ = _user_aggregate_source_sql(
        project_id=PROJECT,
        filters=filters(),
        start_date=START,
        end_date=END,
        include_trace_ids=True,
        all_snapshot_users=True,
    )
    assert_complement(query, params, "start_time")
    assert query.index("users_datetime_0_start") > query.index("FROM spans")


@pytest.mark.parametrize(
    "exclusion", [date_leaf("is_null"), date_leaf("not_between", [START, END])]
)
def test_exact_empty_short_circuits_before_tag_binding(monkeypatch, exclusion):
    from tracer.services.clickhouse import exact_graph_reads
    from tracer.services.clickhouse.v2 import effective_tags
    from tracer.tests.test_r2_tags_users import tag_leaf

    def forbidden(*args, **kwargs):
        pytest.fail("empty window must not bind Tag or execute membership reads")

    monkeypatch.setattr(effective_tags, "bind_effective_tag_query", forbidden)
    monkeypatch.setattr(UsersListManager, "matching_activity_walk_applies", forbidden)
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=[filters()[0], exclusion, tag_leaf()],
    )
    result = manager._read_cursor_page(page_size=10)
    assert result.payload["table"] == []
    assert result.payload["query_exact"] is True
    assert result.payload["query_complete"] is True
    graph = exact_graph_reads.read_exact_user_system_graph(
        analytics=None,
        project_id=PROJECT,
        filters=manager.filters,
        interval="day",
        metric_id="active_users",
    )
    assert sum(point["value"] for point in graph["data"]) == 0
