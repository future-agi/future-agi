"""Exact Users acquisition contracts; offline, no production/DB fixtures."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from clickhouse_driver.errors import ServerException

from tracer.services.clickhouse.list_cursor import ListCursor, ListCursorError
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.users_list_manager import USER_LIST_CURSOR_ORDER, UsersListManager
from tracer.tests.test_user_latest_window_replay import assert_window_replay, cte

pytestmark = pytest.mark.unit
PROJECT = str(uuid.UUID(int=1))
OTHER = str(uuid.UUID(int=2))
USER = str(uuid.UUID(int=3))
START = datetime(2026, 9, 1, 12, 15, 0, 123456, tzinfo=UTC)
END = START + timedelta(microseconds=1)


def raw_filter(op="greater_than", value=1, kind="number", key="agent.duration_s"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def builder(workspace=False, filters=()):
    return UserListQueryBuilder(
        organization_id=PROJECT,
        **({"project_ids": [PROJECT, OTHER]} if workspace else {"project_id": PROJECT}),
        search="canonical-label",
        filters=list(filters),
    )


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("days", [1, 7, 30, 90, 180, 365])
def test_exact_candidate_population_window_order_and_scope(workspace, days):
    sql, params = builder(workspace).build_dimension_candidate_query(
        limit=26,
        window_start=START - timedelta(days=days),
        window_end=END,
        before_first_seen=START,
        before_end_user_id=USER,
    )
    assert "span_user_rollup" not in sql
    assert "FROM spans AS sp FINAL" not in sql
    assert_window_replay(sql, params)
    assert "count() OVER()" not in sql
    assert params["limit"] == 26
    assert params["before_activity_us"] + 1 == params["user_window_end_us"]
    assert "ORDER BY last_active DESC NULLS LAST, end_user_id DESC" in sql
    assert "end_user_id < toUUID(%(before_end_user_id)s)" in sql
    assert "last_active IS NULL OR last_active <" in sql
    assert "max(latest_end_time) AS last_active" in cte(sql, "exact_usage")
    assert "GROUP BY end_user_id" in cte(sql, "filtered_end_users")
    assert "positionCaseInsensitive(user_id" in cte(sql, "searched_end_users")
    assert "organization_id = toUUID(%(org_id)s)" in sql
    assert "eu.is_deleted = 0" in sql and "notEmpty(eu.user_id)" in sql
    assert ("project_ids" if workspace else "project_id") in params
    # Page limiting occurs only after canonical, full-window activity.
    assert "LIMIT" not in cte(sql, "exact_usage")
    assert "LIMIT" in cte(sql, "candidate_users")


def test_null_activity_boundary_stays_in_null_suffix():
    sql, params = builder().build_dimension_candidate_query(
        limit=2,
        before_first_seen=None,
        before_end_user_id=USER,
        window_start=START,
        window_end=END,
    )
    assert "last_active IS NULL AND end_user_id < toUUID(" in sql
    assert "before_activity_us" not in params


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize(
    "op,value", [("equals", "Guest-A"), ("in", ["Guest-A", "Guest-B"])]
)
def test_native_user_id_witness_is_after_latest_replay_before_candidate_limit(
    workspace, op, value
):
    item = {
        "column_id": "user_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }
    sql, params = builder(workspace, [item]).build_dimension_candidate_query(
        limit=2,
        window_start=START,
        window_end=END,
    )
    candidate = cte(sql, "candidate_users")
    assert (
        "NOT match(user_id, '^[[:ascii:]]*$') OR lower(user_id) IN %(candidate_user_label_0)s"
        in candidate
    )
    assert candidate.index("candidate_user_label_0") < candidate.index("LIMIT")
    assert "candidate_user_label_0" not in cte(sql, "filtered_end_users_raw")
    assert "candidate_user_label_0" not in cte(sql, "latest_candidate_spans")
    assert params["candidate_user_label_0"] == (
        ("guest-a",) if op == "equals" else ("guest-a", "guest-b")
    )
    assert_window_replay(sql, params)


@pytest.mark.parametrize(
    "source,op,value",
    [
        ("SPAN_ATTRIBUTE", "in", ["guest-a"]),
        ("EVAL_METRIC", "in", ["guest-a"]),
        ("SYSTEM_METRIC", "not_in", ["guest-a"]),
        ("SYSTEM_METRIC", "in", ["équipe"]),
        ("SYSTEM_METRIC", "in", ["true"]),
        ("SYSTEM_METRIC", "in", ['{"a":1}']),
        ("SYSTEM_METRIC", "in", [None]),
        ("SYSTEM_METRIC", "in", []),
    ],
)
def test_user_id_witness_declines_non_native_or_non_literal_shapes(source, op, value):
    item = {
        "column_id": "user_id",
        "filter_config": {
            "col_type": source,
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }
    assert builder(filters=[item])._positive_user_id_witness() == ("", {})


@pytest.mark.parametrize("workspace", [False, True])
def test_scalar_witness_narrows_groups_not_activity_or_replacement(workspace):
    sql, params = builder(workspace, [raw_filter()]).build_dimension_candidate_query(
        limit=26,
        window_start=START,
        window_end=END,
    )
    witness = cte(sql, "scalar_witness_identities")
    population = cte(sql, "scalar_candidate_users")
    assert "attrs_number[" in witness
    assert "LIMIT" not in witness + population
    assert "is_deleted = 0" not in witness + population
    # Complete same-partition versions preserve equal-version field choices,
    # moved timestamps and reassigned users, not only a raw witness's old user.
    assert "start_time >=" not in witness + population
    assert (
        "project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in population
    )
    assert "SELECT * FROM scalar_witness_identities" in population
    assert "eu_survivor_map" in population
    assert "scalar_candidate_users" in cte(sql, "filtered_end_users")
    aliases = cte(sql, "candidate_span_identities")
    assert aliases.count("FROM scalar_candidate_users") == 1
    assert "LEFT ALL JOIN" in aliases
    assert "ifNull(aliases.present, 0) = 1" in aliases
    usage = cte(sql, "exact_usage")
    assert "attrs_number" not in usage
    assert "max(latest_end_time) AS last_active" in usage
    assert_window_replay(sql, params)


@pytest.mark.parametrize(
    "item",
    [
        raw_filter("not_equals", 1),
        raw_filter("is_null", None),
        raw_filter("equals", 0),
        raw_filter("less_than", 1),
        raw_filter("equals", False, "boolean"),
        raw_filter("equals", "1", "text"),
        {
            "column_id": "total_cost",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 1,
            },
        },
    ],
)
def test_unproven_negative_missing_native_and_other_types_decline_scalar_seed(item):
    sql, _ = builder(filters=[item]).build_dimension_candidate_query(
        limit=2,
        window_start=START,
        window_end=END,
    )
    assert "scalar_witness_identities" not in sql
    assert "latest_candidate_spans" in sql


def test_alias_expansion_is_finite_without_another_order_or_population_scan():
    sql, params = builder(True).build_dimension_survivor_query([USER])
    assert "old_id IN %(dimension_candidate_ids)s" in sql
    assert "new_id IN %(dimension_candidate_ids)s" in sql
    assert "span_user_rollup" not in sql and "FROM spans" not in sql
    assert "group_order" not in sql and "LIMIT" not in sql
    assert params["dimension_candidate_ids"] == (USER,)


def manager(filters=()):
    return UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        filters=list(filters),
        requested_columns=[],
    )


def candidates(count):
    return [
        {
            "end_user_id": str(uuid.UUID(int=i + 10)),
            "user_id": f"u{i}",
            "first_seen": END - timedelta(seconds=i),
            "last_active": (END - timedelta(seconds=i)).isoformat(),
            "total_cost": i,
            "_candidate_scan_end_user_ids": (str(uuid.UUID(int=i + 10)),),
        }
        for i in range(count)
    ]


def reader(rows):
    def read(**kwargs):
        start = next(
            (
                i + 1
                for i, row in enumerate(rows)
                if row["end_user_id"] == kwargs["before_end_user_id"]
            ),
            0,
        )
        return rows[start : start + kwargs["limit"]]

    return read


@pytest.mark.parametrize(
    "operation,value,expected",
    [
        ("greater_than", 250, [251, 252]),
        ("not_equals", 0, [1, 2]),
        ("is_null", None, []),
    ],
)
def test_native_filters_walk_exact_order_and_prove_exhaustion(
    operation, value, expected
):
    m = manager(
        [
            {
                "column_id": "total_cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": operation,
                    "filter_value": value,
                },
            }
        ]
    )
    rows = candidates(253)
    with (
        patch.object(m, "_read_dimension_candidates", side_effect=reader(rows)),
        patch(
            "tracer.services.users_list_manager.ReadDeadline.start",
            side_effect=AssertionError("no admission wall"),
        ),
    ):
        page = m.list_cursor_payload(page_size=2)
    assert [row["total_cost"] for row in page.payload["table"]] == expected
    assert page.payload["query_exact"] is True
    assert page.payload["ordering_exact"] is True
    assert page.payload["query_provenance"] == "physical_latest_users"
    if expected == [251, 252] or not expected:
        assert page.has_more is False
    if page.checkpoint_order:
        assert page.checkpoint_order[0] == USER_LIST_CURSOR_ORDER


def test_sparse_raw_numeric_walk_releases_rejected_caches_and_keeps_types():
    m = manager([raw_filter()])
    rows = candidates(253)
    calls = []

    def enrich(batch, *_args, **_kwargs):
        assert not m._attribute_values_by_user
        assert not m._attribute_value_types_by_user
        assert not m._relation_matching_user_ids
        assert not m._native_filter_values_by_user
        calls.append(len(batch))
        for row in batch:
            # Missing, wrong-type, false, zero and negative values must not
            # become a >1 numeric match on the way to the sparse final user.
            value = [None, "2", False, 0, -2][row["total_cost"] % 5]
            if row["total_cost"] == 252:
                value = 2
            m._attribute_values_by_user[row["end_user_id"]] = {
                "agent.duration_s": value
            }
            m._native_filter_values_by_user[row["end_user_id"]] = {"last_active": None}
            m._attribute_value_types_by_user[row["end_user_id"]] = {
                "agent.duration_s": {
                    m._canonical_filter_value(value): frozenset(
                        {m._inferred_attribute_storage_type(value)}
                    )
                }
            }

    with (
        patch.object(m, "_read_dimension_candidates", side_effect=reader(rows)),
        patch.object(m, "_enrich_rows", side_effect=enrich),
    ):
        page = m.list_cursor_payload(page_size=2)
    assert [row["total_cost"] for row in page.payload["table"]] == [252]
    assert len(calls) > 8 and max(calls) <= 25
    assert page.has_more is False
    assert not m._attribute_values_by_user and not m._attribute_value_types_by_user
    assert not m._native_filter_values_by_user


@pytest.mark.parametrize(
    "failure", [ReadDeadlineExceeded("memory"), MemoryError("memory")]
)
def test_resource_failure_after_rejected_prefix_never_returns_exact_empty(failure):
    m = manager()
    with (
        patch.object(
            m,
            "_read_dimension_candidates",
            side_effect=[candidates(26), failure],
        ),
        patch.object(m, "_read_exact_candidate_rows", return_value=[]),
    ):
        with pytest.raises(type(failure)):
            m.list_cursor_payload(page_size=25)


def test_exact_page_does_not_replay_usage_twice_or_leak_private_state():
    m = manager()
    rows = candidates(2)
    with (
        patch.object(m, "_read_dimension_candidates", side_effect=reader(rows)),
        patch(
            "tracer.services.users_list_manager.V2AnalyticsQueryService",
            side_effect=AssertionError("duplicate replay"),
        ),
    ):
        page = m.list_cursor_payload(page_size=2)
    assert len(page.payload["table"]) == 2 and not page.has_more
    assert all(
        not key.startswith("_") and key != "first_seen"
        for row in page.payload["table"]
        for key in row
    )


def test_old_rollup_cursor_is_rejected_with_typed_error():
    with pytest.raises(ListCursorError, match="restart pagination"):
        manager().list_cursor_payload(
            page_size=2,
            cursor=ListCursor(
                window_start=START,
                window_end=END,
                order=(START, USER),
            ),
        )


def test_exact_candidate_reader_keeps_canonical_activity_and_all_aliases():
    m = manager()
    raw = [
        {
            "end_user_id": USER,
            "last_active": START,
            "user_id": "canonical-label",
            "total_cost": 3,
        }
    ]
    result = [
        SimpleNamespace(data=raw),
        SimpleNamespace(
            data=[
                {"any_id": USER, "survivor_id": USER},
                {"any_id": OTHER, "survivor_id": USER},
            ]
        ),
    ]
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = result
        rows = m._read_dimension_candidates(
            deadline=None,
            limit=2,
            before_first_seen=None,
            before_end_user_id=None,
            window_start=START,
            window_end=END,
        )
    assert len(rows) == 1 and rows[0]["end_user_id"] == USER
    assert rows[0]["first_seen"] == START
    assert set(rows[0]["_candidate_scan_end_user_ids"]) == {USER, OTHER}
    assert all(
        call.kwargs["timeout_ms"] is None
        for call in service.return_value.execute_ch_query.call_args_list
    )


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("family", ["ANNOTATION", "EVAL_METRIC"])
@pytest.mark.parametrize("operation", ["equals", "not_equals", "is_null"])
def test_finite_relations_replay_latest_window_without_relation_age_cutoff(
    workspace, family, operation
):
    b = UserListQueryBuilder(
        organization_id=PROJECT,
        **({"project_ids": [PROJECT, OTHER]} if workspace else {"project_id": PROJECT}),
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [START.isoformat(), END.isoformat()],
                },
            }
        ],
        candidate_end_user_ids=[USER],
        candidate_scan_end_user_ids=[USER],
        candidate_end_user_id_map={USER: USER},
    )
    item = {
        "column_id": OTHER,
        "filter_config": {
            "col_type": family,
            "filter_type": "number",
            "filter_op": operation,
            "filter_value": 1,
        },
    }
    sql, params = b.build_relation_filter_user_query(
        [item],
        eval_filter_metadata_by_project={
            project: {OTHER: EvalFilterMetadata((project,), "SCORE")}
            for project in ([PROJECT, OTHER] if workspace else [PROJECT])
        },
    )
    latest = cte(sql, "latest_relation_candidate_spans")
    scan = latest.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "start_time >=" not in scan and "start_time <" not in scan
    assert "end_user_id IN" not in scan and "is_deleted = 0" not in scan
    assert "service_name" in scan and "toStartOfHour(start_time)" in scan
    live = cte(sql, "relation_candidate_spans")
    assert "latest_spans.observation_type" in live
    assert "latest_spans.service_name" in live
    assert "latest_spans.is_deleted = 0" in live
    assert "latest_spans.latest_start_time >= fromUnixTimestamp64Micro" in live
    assert "latest_spans.latest_start_time < fromUnixTimestamp64Micro" in live
    assert "argMax(tuple(parent_span_id), _version).1 AS parent_span_id" in latest
    assert params["user_window_end_us"] - params["user_window_start_us"] == 1
    # Relations may be 400 days old: only physical activity bounds the window.
    assert "created_at >=" not in sql and "INTERVAL 7 DAY" not in sql
    assert "relation_candidate_span_entities" in sql
    assert sql.count("relation_candidate_span_entities") >= 2
    if family == "ANNOTATION":
        assert "scored_sp." not in sql and "root_sp." not in sql
        assert (
            "tuple(toString(s.tracer_project_id), ifNull(toString(s.trace_id), '')) IN"
            in sql
        )
        assert "FROM relation_candidate_spans WHERE" in sql
    if family == "EVAL_METRIC":
        assert "eval_scan" in sql and "latest_eval" in sql
        assert "ORDER BY eval_scan." in sql
    else:
        assert "model_hub_score" in sql and "FINAL" in sql


@pytest.mark.parametrize(
    "operation,expected,match",
    [
        ("greater_than", 0.0000003, True),
        ("less_than", 0.0000003, False),
        ("equals", 0.0000004, True),
        ("not_equals", 0.0000004, False),
        ("between", [0.0000003, 0.0000005], True),
    ],
)
def test_native_cost_membership_uses_unrounded_value(operation, expected, match):
    m = manager(
        [
            {
                "column_id": "total_cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": operation,
                    "filter_value": expected,
                },
            }
        ]
    )
    row = m._format_candidate_rows(
        builder(), [{"end_user_id": USER, "total_cost": 0.0000004}]
    )[0]
    assert row["total_cost"] == 0  # existing presentation contract
    assert m._row_matches_filters(row) is match


@pytest.mark.parametrize(
    "operation,expected,match",
    [
        ("greater_than", (START - timedelta(microseconds=1)).isoformat(), True),
        ("less_than", START.isoformat(), False),
        ("greater_than_or_equal", START.isoformat(), True),
        ("less_than_or_equal", START.isoformat(), True),
        ("equals", "2026-09-01T05:15:00.123456-07:00", True),
        ("in", [START.isoformat(), END.isoformat()], True),
        ("not_in", [START.isoformat()], False),
        ("between", [START.isoformat(), END.isoformat()], True),
        ("not_between", [START.isoformat(), END.isoformat()], False),
        ("is_null", None, False),
        ("is_not_null", None, True),
    ],
)
@pytest.mark.parametrize("column", ["activated_at", "last_active"])
def test_native_datetime_membership_retains_microseconds_and_timezones(
    column, operation, expected, match
):
    m = manager(
        [
            {
                "column_id": column,
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": operation,
                    "filter_value": expected,
                },
            }
        ]
    )
    row = m._format_candidate_rows(builder(), [{"end_user_id": USER, column: START}])[0]
    assert m._row_matches_filters(row) is match


@pytest.mark.parametrize(
    "operation,expected,match",
    [
        ("not_equals", START.isoformat(), False),
        ("not_in", [START.isoformat()], False),
        ("not_between", [START.isoformat(), END.isoformat()], False),
        ("is_null", None, True),
        ("is_not_null", None, False),
    ],
)
def test_native_absence_does_not_satisfy_value_negatives(operation, expected, match):
    m = manager(
        [
            {
                "column_id": "last_active",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": operation,
                    "filter_value": expected,
                },
            }
        ]
    )
    assert m._row_matches_filters({"end_user_id": USER, "last_active": None}) is match


def test_native_precision_cache_cannot_replace_raw_attribute_with_same_name():
    m = manager([raw_filter(key="total_cost")])
    row = m._format_candidate_rows(
        builder(), [{"end_user_id": USER, "total_cost": 10}]
    )[0]
    m._attribute_values_by_user[USER] = {}
    assert not m._row_matches_filters(row)
    m._attribute_values_by_user[USER] = {"total_cost": 2}
    assert m._row_matches_filters(row)


def test_nullable_order_continuation_and_repeated_page_do_not_skip_or_repeat():
    m = manager()
    rows = candidates(4)
    rows[0]["first_seen"] = rows[1]["first_seen"] = START
    rows[2]["first_seen"] = rows[3]["first_seen"] = None
    with patch.object(m, "_read_dimension_candidates", side_effect=reader(rows)):
        first = m.list_cursor_payload(page_size=2)
        cursor = ListCursor(
            window_start=first.window_start,
            window_end=first.window_end,
            order=first.checkpoint_order,
            seen_rows=first.seen_rows,
        )
        second = m.list_cursor_payload(page_size=2, cursor=cursor)
        repeated = m.list_cursor_payload(page_size=2, cursor=cursor)
    assert first.has_more and not second.has_more
    assert second.payload == repeated.payload
    assert second.checkpoint_order[1] is None
    assert {row["end_user_id"] for row in first.payload["table"]}.isdisjoint(
        {row["end_user_id"] for row in second.payload["table"]}
    )
    assert second.payload["total_count"] == 4


@pytest.mark.parametrize("page_size", [0, -1, True, 1.5])
def test_invalid_page_size_fails_before_any_candidate_read(page_size):
    m = manager()
    with patch.object(m, "_read_dimension_candidates") as read:
        with pytest.raises(ValueError, match="positive integer"):
            m.list_cursor_payload(page_size=page_size)
    read.assert_not_called()


@pytest.mark.parametrize(
    "failure", [ReadDeadlineExceeded("memory"), MemoryError("memory")]
)
def test_presentation_resource_failure_does_not_publish_completed_page(failure):
    m = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        requested_columns=[],
        attribute_keys=["display_key"],
    )
    with (
        patch.object(
            m, "_read_dimension_candidates", side_effect=reader(candidates(2))
        ),
        patch.object(m, "_enrich_rows", side_effect=failure),
    ):
        with pytest.raises(type(failure)):
            m.list_cursor_payload(page_size=2)


@pytest.mark.parametrize("family", ["ANNOTATION", "EVAL_METRIC"])
@pytest.mark.parametrize("matched", [False, True])
def test_relation_pages_report_qualified_physical_latest_exactness(family, matched):
    m = manager(
        [
            {
                "column_id": OTHER,
                "filter_config": {
                    "col_type": family,
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 1,
                },
            }
        ]
    )
    rows = candidates(2)
    membership = {rows[0]["end_user_id"]} if matched else set()
    with (
        patch.object(m, "_read_dimension_candidates", side_effect=reader(rows)),
        patch.object(m, "_read_relation_filter_matches", return_value=membership),
    ):
        page = m.list_cursor_payload(page_size=2)
    assert len(page.payload["table"]) == int(matched)
    assert page.payload["query_exact"] is True
    assert page.payload["ordering_exact"] is True
    assert page.payload["query_provenance"] == "physical_latest_users"


@pytest.mark.parametrize("approximate", [False, True])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_cursor_metadata_requires_exact_completed_page_or_exhaustion(
    approximate, count
):
    m = manager()
    m.approximate_num_sessions = approximate
    with patch.object(
        m, "_read_dimension_candidates", side_effect=reader(candidates(count))
    ):
        page = m.list_cursor_payload(page_size=2)
    assert page.payload["query_complete"] is True
    assert page.payload["query_status"] == "complete"
    assert page.payload["query_exact"] is (not approximate)
    assert page.payload["ordering_exact"] is (not approximate)
    assert page.payload["approximate_fields"] == (
        ["num_sessions"] if approximate else []
    )
    assert len(page.payload["table"]) == min(count, 2)


def test_cursor_metadata_excludes_successful_attribute_split_and_resets_next_request():
    end = START + timedelta(minutes=4)
    m = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        requested_columns=[],
        attribute_keys=["display_key"],
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [START, end],
                },
            }
        ],
    )
    with (
        patch.object(
            m, "_read_dimension_candidates", side_effect=reader(candidates(2))
        ),
        patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service,
    ):
        service.return_value.execute_ch_query.side_effect = [
            ServerException("MEMORY_LIMIT_EXCEEDED", code=241),
            SimpleNamespace(data=[]),
            SimpleNamespace(data=[]),
        ]
        recovered = m.list_cursor_payload(page_size=2)
        assert service.return_value.execute_ch_query.call_count == 3
        assert m._unqualified_attribute_fallback_used
        assert recovered.payload["query_complete"] is True
        assert recovered.payload["query_exact"] is False
        assert recovered.payload["ordering_exact"] is False
        assert recovered.payload["approximate_fields"] == []
        service.return_value.execute_ch_query.side_effect = [SimpleNamespace(data=[])]
        qualified = m.list_cursor_payload(page_size=2)
        assert not m._unqualified_attribute_fallback_used
        assert qualified.payload["query_exact"] is True
        assert qualified.payload["ordering_exact"] is True


def test_cursor_metadata_excludes_optional_witness_recovery():
    m = manager([raw_filter("equals", "yes", "text", key="tag")])
    rows = candidates(2)
    with (
        patch.object(m, "_read_dimension_candidates", side_effect=reader(rows)),
        patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service,
    ):
        service.return_value.execute_ch_query.side_effect = [
            ReadDeadlineExceeded("optional witness"),
            SimpleNamespace(data=[]),
        ]
        page = m.list_cursor_payload(page_size=2)
    assert m._attribute_witness_disabled
    assert page.payload["query_complete"] is True
    assert page.payload["query_exact"] is False
    assert page.payload["ordering_exact"] is False


def test_workspace_eval_config_ownership_is_cached_without_pooling():
    leaf = raw_filter(key=USER)
    leaf["filter_config"]["col_type"] = "EVAL_METRIC"
    m = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT, OTHER],
        filters=[leaf],
        requested_columns=[],
    )
    with patch(
        "tracer.services.users_list_manager.resolve_eval_filter_metadata",
        side_effect=lambda _, projects: EvalFilterMetadata(tuple(projects), "SCORE"),
    ) as resolve:
        metadata = m._relation_eval_metadata()
        assert metadata == {
            project: {USER: EvalFilterMetadata((project,), "SCORE")}
            for project in (PROJECT, OTHER)
        }
        assert m._relation_eval_metadata() is metadata
        assert resolve.call_count == 2


@pytest.mark.parametrize("operation", ["equals", "not_equals", "is_null"])
def test_workspace_eval_requirement_cannot_pool_configs_or_fall_back(operation):
    b = UserListQueryBuilder(
        organization_id=PROJECT,
        project_ids=[PROJECT, OTHER],
        candidate_end_user_ids=[USER],
    )
    item = raw_filter(operation, 1, key=USER)
    item["filter_config"]["col_type"] = "EVAL_METRIC"
    with pytest.raises(ValueError, match="project-scoped metadata"):
        b.build_relation_filter_user_query(
            [item],
            eval_filter_metadata={USER: EvalFilterMetadata((PROJECT, OTHER), "SCORE")},
        )
    sql, params = b.build_relation_filter_user_query(
        [item],
        eval_filter_metadata_by_project={
            PROJECT: {USER: EvalFilterMetadata((PROJECT,), "SCORE")},
            OTHER: {USER: EvalFilterMetadata((OTHER,), "SCORE")},
        },
    )
    for index, project in enumerate((PROJECT, OTHER)):
        prefix = f"relation_0_0_{index}"
        assert f"project_id = toUUID(%({prefix}_project)s) AND" in sql
        assert params[f"{prefix}_project"] == project
        config_bindings = [
            v for k, v in params.items() if k.startswith(prefix) and "eval_cfg" in k
        ]
        assert config_bindings == [(project,)]
    missing, _ = b.build_relation_filter_user_query(
        [item], eval_filter_metadata_by_project={}
    )
    assert "eval_scan" not in missing and "AND (0 = 1)" in missing


def test_cursor_keyset_does_not_discard_native_output_predicate():
    leaf = raw_filter("greater_than", 2, key="total_cost")
    leaf["filter_config"].pop("col_type")
    b = UserListQueryBuilder(
        organization_id=PROJECT,
        project_id=PROJECT,
        filters=[leaf],
        limit=2,
        offset=0,
    )
    sql, _ = b.build_candidate_page_query(cursor_mode=True, cursor_before=(START, USER))
    page = cte(sql, "candidate_users")
    assert "total_cost" in page and "AND (last_active IS NULL OR last_active <" in page


def test_gap_text_witness_preserves_microsecond_window():
    sql, params = builder().build_attribute_user_candidates_query(
        text_values_by_key={"tag": ("yes",)},
        window_start=START,
        window_end=END,
        candidate_scan_ids=(USER,),
    )
    assert "start_time >= fromUnixTimestamp64Micro(%(attribute_window_start_us)s" in sql
    assert "start_time < fromUnixTimestamp64Micro(%(attribute_window_end_us)s" in sql
    assert type(params["attribute_window_start_us"]) is int
    assert params["attribute_window_end_us"] - params["attribute_window_start_us"] == 1


def test_gap_metric_replay_keeps_nullable_winners_and_unrounded_averages():
    queries = builder().build_requested_page_metric_queries(
        [USER], {"avg_trace_latency", "num_traces_with_errors", "avg_session_duration"}
    )
    sql = "\n".join(query for query, _, _ in queries)
    assert "argMax(tuple(latency_ms), _version).1 AS latest_latency_ms" in sql
    assert "argMax(tuple(status), _version).1 AS latest_status" in sql
    assert "round(" not in sql


@pytest.mark.parametrize(
    "field",
    [
        "avg_trace_latency",
        "avg_session_duration",
        "bool_eval_pass_rate",
        "avg_output_float",
    ],
)
def test_gap_metric_filter_uses_raw_value_before_presentation_rounding(field):
    item = raw_filter("greater_than", 1.002, key=field)
    item["filter_config"].pop("col_type")
    m = manager([item])
    rows = [{"end_user_id": USER, field: 0}]
    apply = (
        m._apply_evals
        if field in {"bool_eval_pass_rate", "avg_output_float"}
        else m._apply_page_metrics
    )
    apply(rows, {USER: {field: 1.004}})
    assert rows[0][field] == 1.0
    assert m._native_filter_values_by_user[USER][field] == 1.004
    assert m._row_matches_filters(rows[0])


def test_gap_eval_summary_reuses_full_key_postwinner_activity():
    sql, params = builder().build_eval_query(
        [USER], allowed_eval_config_ids_by_project={PROJECT: [OTHER]}
    )
    assert_window_replay(sql, params)
    assert "observation_type" in cte(sql, "latest_candidate_spans")
    assert "service_name" in cte(sql, "latest_candidate_spans")
    assert "round(" not in sql
