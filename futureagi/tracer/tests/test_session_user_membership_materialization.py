"""User-detail session membership must be exact and reused before pagination."""

from datetime import datetime

import pytest

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECTS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
)
USER_ID = "00000000-0000-4000-8000-000000000003"
SESSION_ID = "00000000-0000-4000-8000-000000000004"


def _builder(*, cross_project=True, operation="in"):
    return SessionListQueryBuilderV2(
        **(
            {"project_ids": list(PROJECTS)}
            if cross_project
            else {"project_id": PROJECTS[0]}
        ),
        page_size=25,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-09-04T22:27:57Z", "2026-09-05T07:00:00Z"],
                },
            },
            {
                # The view supplies resolved UUIDs even when the public
                # user_id filter omitted col_type.
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": operation,
                    "filter_value": USER_ID if operation == "equals" else [USER_ID],
                },
            },
        ],
    )


@pytest.mark.parametrize("cross_project", [False, True])
@pytest.mark.parametrize("operation", ["equals", "in"])
@pytest.mark.parametrize("mode", ["page", "cursor", "count"])
def test_positive_user_membership_materializes_complete_relations(
    cross_project, operation, mode
):
    builder = _builder(cross_project=cross_project, operation=operation)
    method = {
        "page": builder.build_candidate_page_query,
        "cursor": builder.build_candidate_cursor_page_query,
        "count": builder.build_candidate_count_query,
    }[mode]
    sql, params = method()
    assert params["candidate_filter_user_ids"] == (USER_ID,)
    for name in (
        "candidate_user_pairs",
        "candidate_user_raw_session_pairs",
        "candidate_session_pairs",
    ):
        assert sql.count(f"AS {name},") == 1
        assert f"arrayJoin({name}) AS pair" in sql
        assert f" IN {name}" not in sql

    membership = sql.split("candidate_user_span_identities AS (", 1)[1].split(
        "candidate_root_identities AS (", 1
    )[0]
    assert "LIMIT" not in membership
    assert "SAMPLE" not in sql
    assert "groupArray(tuple(raw_session_id))" in membership
    assert "argMax(tuple(trace_session_id), _version).1" in membership
    assert "argMax(tuple(end_user_id), _version).1" in membership
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in membership
    exact_live = membership.split("groupArray(tuple(raw_session_id))", 1)[1].split(
        ")) AS candidate_user_raw_session_pairs", 1
    )[0]
    assert "WHERE latest_is_deleted = 0" in exact_live
    assert "isNotNull(latest_trace_session_id)" in exact_live
    assert "isNotNull(latest_end_user_id)" in exact_live
    assert "user_eu_remap.survivor_id" in exact_live
    assert "%(eu_remap_1)s" in exact_live
    assert (
        "GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in membership
    )
    scope = (
        "project_id IN %(project_ids)s"
        if cross_project
        else "project_id = %(project_id)s"
    )
    assert membership.count(scope) >= 2

    # Expand the whole touched session group, including sibling old aliases,
    # before canonicalizing and acquiring all roots for the selected user.
    assert "remap_match.old_id IN (" in membership
    assert "remap_match.new_id IN (" in membership
    assert (
        membership.count("SELECT raw_session_id FROM matching_user_raw_sessions") == 2
    )
    assert "groupArray(remap.old_id)" in membership
    assert "[remap.new_id]" in membership
    assert "argMin(remap.old_id, toString(remap.old_id))" in membership
    assert "groupUniqArray(user_session_aliases.any_id)" in membership
    roots = sql.split("candidate_root_identities AS (", 1)[1]
    assert "SELECT session_id FROM matching_user_root_ids" in roots
    assert "session_id IN (SELECT session_id FROM matching_user_sessions)" in roots
    if cross_project:
        assert "candidate_session_project_counts AS" in sql
        assert "max_project_count" in sql
    if mode != "count":
        assert sql.index("ORDER BY session_start DESC, session_id DESC") > sql.index(
            "matching_user_sessions AS"
        )
        assert params["limit"] == 26


def test_cursor_keyset_follows_complete_user_membership():
    sql, params = _builder().build_candidate_cursor_page_query(
        before_start_time=datetime(2026, 9, 3, 3, 55, 48, 987190),
        before_session_id=SESSION_ID,
    )
    assert params["cursor_before_start_us"] == 1788407748987190
    assert params["cursor_before_session_id"] == SESSION_ID
    assert "count() OVER() AS remaining_count" in sql
    assert sql.index("WHERE session_start <") > sql.index("FROM sessions")
    assert "session_id < toUUID(%(cursor_before_session_id)s)" in sql


def test_finite_session_classifier_keeps_existing_exact_user_path():
    sql, params = _builder().build_filter_match_query([SESSION_ID])
    assert "candidate_user_raw_session_pairs" not in sql
    assert "resolved_user_spans AS" in sql
    assert "SELECT session_id FROM candidate_filter_sessions" in sql
    assert params["candidate_filter_user_ids"] == (USER_ID,)


def test_negative_user_membership_is_not_positive_seeded():
    sql, _ = _builder(operation="not_in").build_candidate_page_query()
    assert "candidate_user_raw_session_pairs" not in sql
    assert "resolved_user_spans AS" in sql


def _attribute(key="company_id", kind="text", operation="in", value=None):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": ["0012", "0042"] if value is None else value,
        },
    }


@pytest.mark.parametrize("mode", ["page", "cursor", "count"])
@pytest.mark.parametrize("attribute_count", [1, 2, 5, 10])
def test_user_detail_scalar_filters_keep_complete_user_session_population(
    mode, attribute_count
):
    builder = _builder(cross_project=False)
    builder.filters.extend(_attribute(f"custom_{i}") for i in range(attribute_count))
    assert builder.supports_candidate_first_page()
    assert builder.supports_candidate_cursor_page()
    method = {
        "page": builder.build_candidate_page_query,
        "cursor": builder.build_candidate_cursor_page_query,
        "count": builder.build_candidate_count_query,
    }[mode]
    sql, params = method()
    assert params["candidate_filter_user_ids"] == (USER_ID,)
    scalar_seed = sql.split("candidate_scalar_span_identities AS (", 1)[1].split(
        "latest_candidate_scalar_spans AS (", 1
    )[0]
    assert "SELECT session_id FROM matching_user_root_ids" in scalar_seed
    assert "end_user_id" not in scalar_seed
    assert "parent_span_id" not in scalar_seed
    assert "LIMIT" not in scalar_seed
    assert "attrs_string" not in scalar_seed
    membership = sql.split("matching_scalar_sessions AS (", 1)[1].split(
        "sessions AS (", 1
    )[0]
    assert membership.count("countIf(") == attribute_count
    assert "GROUP BY project_id, session_id" in membership
    assert "SAMPLE" not in sql
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql
    assert sql.index("matching_scalar_sessions AS (") < sql.index("FROM sessions")


@pytest.mark.parametrize(
    "attribute",
    [
        _attribute("duration_s", "number", "greater_than", 0.01),
        _attribute("enabled", "boolean", "equals", True),
        _attribute("message", "text", "contains", "x" * 1000),
        _attribute("company_id", "text", "is_null"),
        _attribute("end_user_id"),  # Raw/native collision must stay scalar.
    ],
)
def test_user_scalar_page_uses_existing_typed_entity_predicates(attribute):
    builder = _builder(cross_project=False)
    builder.filters.append(attribute)
    assert builder.supports_candidate_cursor_page()
    sql, _ = builder.build_candidate_cursor_page_query()
    assert "matching_scalar_sessions AS" in sql
    assert "SELECT session_id FROM matching_user_root_ids" in sql


@pytest.mark.parametrize("shape", ["negative_user", "org", "json", "no_user", "sort"])
def test_complete_scalar_witness_routes_and_unsupported_fallbacks(shape):
    builder = _builder(
        cross_project=shape == "org",
        operation="not_in" if shape == "negative_user" else "in",
    )
    builder.filters.append(
        _attribute("context", "map", "contains", {"x": 1})
        if shape == "json"
        else _attribute()
    )
    if shape == "no_user":
        builder.filters = [
            f for f in builder.filters if f["column_id"] != "end_user_id"
        ]
    if shape == "sort":
        builder.sort_params = [{"column_id": "duration", "direction": "desc"}]
    supported = shape not in {"json", "sort"}
    assert builder.supports_candidate_cursor_page() is supported
    if supported:
        sql, _ = builder.build_candidate_cursor_page_query()
        assert "AS candidate_witness_session_ids," in sql
