from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.users_list_manager import (
    UsersListManager,
    _users_attr_enrichment_query,
)

pytestmark = pytest.mark.unit


def _manager(
    *, filters=None, requested_columns=None, attribute_keys=None
) -> UsersListManager:
    project_id = str(uuid.uuid4())
    return UsersListManager(
        organization_id=str(uuid.uuid4()),
        allowed_project_ids=[project_id],
        project_id=project_id,
        filters=filters or [],
        requested_columns=requested_columns or [],
        attribute_keys=attribute_keys or [],
    )


def _candidate(index: int, *, now: datetime) -> dict:
    return {
        "end_user_id": str(uuid.UUID(int=index + 1)),
        "first_seen": now - timedelta(seconds=index),
        "user_id": f"user-{index}",
        "user_id_type": "custom",
        "user_id_hash": "",
    }


def _exact(candidate: dict, *, cost: float = 1.0) -> dict:
    return {
        "end_user_id": candidate["end_user_id"],
        "user_id": candidate["user_id"],
        "total_cost": cost,
        "total_tokens": 1,
        "input_tokens": 1,
        "output_tokens": 0,
        "num_traces": 1,
        "last_active": candidate["first_seen"],
    }


def test_dimension_candidate_query_is_stable_keyset_and_finite():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        search="alice",
    )
    before = datetime(2026, 8, 5, 12, tzinfo=UTC)

    sql, params = builder.build_dimension_candidate_query(
        limit=26,
        before_first_seen=before,
        before_end_user_id=str(uuid.uuid4()),
    )

    assert "FROM end_users AS eu FINAL" in sql
    # The hot dimension scan is raw-key ordered. A separate finite query
    # classifies only this page's ids against the many-to-one remap; building
    # the global survivor map here exceeded the production memory ceiling.
    assert "end_user_id_remap" not in sql
    assert "ORDER BY first_seen DESC, toString(eu.end_user_id) DESC" in sql
    assert (
        "first_seen\n                    < parseDateTime64BestEffort("
        "%(before_first_seen)s, 6, 'UTC')"
    ) in sql
    assert (
        "= parseDateTime64BestEffort(\n"
        "                            %(before_first_seen)s, 6, 'UTC'"
    ) in sql
    # The SELECT/ORDER BY contract exposes ``end_user_id`` as a String.  Keep
    # the keyset tie-breaker in that same lexicographic domain; comparing the
    # aliased String to ``toUUID(...)`` fails in ClickHouse and UUID's internal
    # byte ordering would not match the published String ordering anyway.
    assert "toString(eu.end_user_id) < %(before_end_user_id)s" in sql
    assert "end_user_id < toUUID(%(before_end_user_id)s)" not in sql
    assert "LIMIT %(dimension_limit)s" in sql
    assert "FROM spans" not in sql
    assert params["dimension_limit"] == 26
    assert params["before_first_seen"] == "2026-08-05T12:00:00+00:00"
    assert isinstance(params["before_end_user_id"], str)


def test_dimension_candidate_cursor_preserves_microsecond_tie_precision():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )
    boundary = datetime(2026, 8, 5, 12, 0, 0, 52877, tzinfo=UTC)

    sql, params = builder.build_dimension_candidate_query(
        limit=26,
        before_first_seen=boundary,
        before_end_user_id=str(uuid.uuid4()),
    )

    assert params["before_first_seen"] == "2026-08-05T12:00:00.052877+00:00"
    assert sql.count("parseDateTime64BestEffort") == 2


def test_dimension_survivor_query_is_candidate_bounded():
    candidate_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    sql, params = builder.build_dimension_survivor_query(candidate_ids)

    assert "FROM end_user_id_remap FINAL" in sql
    assert "old_id IN %(dimension_candidate_ids)s" in sql
    assert "new_id IN %(dimension_candidate_ids)s" in sql
    # Return every alias in a touched remap group. The manager uses this finite
    # expansion as a literal IN-set so the span bloom index can prune before
    # the exact all-version replay.
    assert "WHERE any_id IN %(dimension_candidate_ids)s" not in sql
    assert params["dimension_candidate_ids"] == tuple(candidate_ids)


def test_finite_candidate_ids_narrow_identity_before_latest_replay():
    candidate_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        filters=[],
        limit=2,
        offset=0,
        candidate_end_user_ids=candidate_ids,
    )

    sql, params = builder.build_candidate_page_query()

    assert "HAVING end_user_id IN %(candidate_end_user_ids)s" in sql
    assert params["candidate_end_user_ids"] == tuple(candidate_ids)
    assert "candidate_span_identities" in sql
    assert "end_user_id IN %(candidate_scan_end_user_ids)s" in sql
    assert params["candidate_scan_end_user_ids"] == tuple(candidate_ids)
    assert "latest_candidate_spans" in sql
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql
    assert "latest_is_deleted = 0" in sql

    # The mutable user predicate is legal only in the identity-superset scan.
    # Latest-state membership/deletion are decided after every version of each
    # immutable identity has been replayed.
    replay = sql.split("latest_candidate_spans AS", 1)[1]
    assert "end_user_id IN" not in replay.split("GROUP BY", 1)[0]
    assert "argMax(tuple(end_user_id), _version)" in replay


def test_unbounded_numbered_page_uses_final_before_mutable_filters():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        "2026-07-01T00:00:00Z",
                        "2026-08-01T00:00:00Z",
                    ],
                },
            }
        ],
        limit=25,
        offset=0,
    )
    sql, _ = builder.build_candidate_page_query()

    assert "FROM spans AS sp FINAL" in sql
    assert "candidate_span_identities" not in sql
    assert "latest_candidate_spans" not in sql
    final_scan = sql.split("FROM spans AS sp FINAL", 1)[1]
    prewhere = final_scan.split("PREWHERE", 1)[1].split("WHERE sp.is_deleted = 0", 1)[0]
    assert "sp.project_id" in prewhere
    assert "sp.start_time" in prewhere
    assert "sp.end_user_id" not in prewhere
    assert "sp.is_deleted" not in prewhere


def test_user_attribute_enrichment_projects_requested_direct_write_keys_only():
    sql, params = _users_attr_enrichment_query(
        project_ids=[str(uuid.uuid4())],
        attribute_keys=["final_status", "score"],
    )

    assert "ARRAY JOIN %(requested_attribute_keys)s AS attribute_key" in sql
    assert "JSONExtractRaw(attributes_extra, attribute_key)" in sql
    assert "mapContains(attrs_string, attribute_key)" in sql
    assert "mapContains(attrs_number, attribute_key)" in sql
    assert "mapContains(attrs_bool, attribute_key)" in sql
    assert "arraySort(groupUniqArray(latest_attribute_value_json))" in sql
    assert "end_user_id IN %(eu_scan_ids)s" in sql
    assert params["requested_attribute_keys"] == ["final_status", "score"]


def test_user_attribute_enrichment_skips_query_without_requested_keys():
    assert _users_attr_enrichment_query(project_ids=[str(uuid.uuid4())]) == ("", {})


def test_cursor_page_publishes_only_fully_hydrated_matching_rows():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(3)]
    filters = [
        {
            "column_id": "total_cost",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 5,
            },
        }
    ]
    manager = _manager(filters=filters)
    exact_rows = [
        _exact(candidates[0], cost=10),
        _exact(candidates[1], cost=1),
        _exact(candidates[2], cost=20),
    ]

    with (
        patch.object(
            manager,
            "_read_dimension_candidates",
            return_value=candidates,
        ),
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            return_value=exact_rows,
        ),
    ):
        result = manager.list_cursor_payload(page_size=25)

    assert [row["user_id"] for row in result.payload["table"]] == [
        "user-0",
        "user-2",
    ]
    assert result.payload["total_count"] == 2
    assert result.payload["count_is_lower_bound"] is False
    assert result.payload["query_complete"] is True
    assert result.has_more is False
    assert result.checkpoint_order == (
        candidates[-1]["first_seen"],
        candidates[-1]["end_user_id"],
    )


def test_cursor_checkpoint_survives_later_deadline_without_inventing_match():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(100)]
    manager = _manager()

    with (
        patch.object(
            manager,
            "_read_dimension_candidates",
            side_effect=[candidates, ReadDeadlineExceeded("deadline")],
        ),
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            return_value=[],
        ),
    ):
        result = manager.list_cursor_payload(page_size=25)

    assert result.payload["table"] == []
    assert result.payload["total_count"] == 0
    assert result.payload["count_is_lower_bound"] is True
    assert result.payload["query_complete"] is True
    assert result.payload["query_status"] == "complete"
    assert result.has_more is True
    assert result.unseen_row_proven is False
    assert result.checkpoint_order == (
        candidates[24]["first_seen"],
        candidates[24]["end_user_id"],
    )


def test_cursor_resume_reuses_frozen_window_and_keyset():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidate = _candidate(3, now=now)
    manager = _manager()
    cursor = ListCursor(
        window_start=now - timedelta(days=30),
        window_end=now,
        order=(candidate["first_seen"], candidate["end_user_id"]),
        seen_rows=7,
    )

    with patch.object(
        manager,
        "_read_dimension_candidates",
        return_value=[],
    ) as read_candidates:
        result = manager.list_cursor_payload(page_size=25, cursor=cursor)

    assert result.window_start == cursor.window_start
    assert result.window_end == cursor.window_end
    assert result.seen_rows == 7
    assert result.payload["total_count"] == 7
    kwargs = read_candidates.call_args.kwargs
    assert kwargs["before_first_seen"] == candidate["first_seen"]
    assert kwargs["before_end_user_id"] == candidate["end_user_id"]
    assert "snapshot_settings" not in kwargs


def test_numbered_page_attribute_enrichment_uses_requested_window():
    manager = _manager(attribute_keys=["final_status"])
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    row = _exact(_candidate(0, now=end))
    builder = MagicMock()
    builder.parse_time_range.return_value = (start, end)

    with (
        patch.object(manager, "_fetch_rows", return_value=([row], 1, builder)),
        patch.object(manager, "_read_page_metrics", return_value={}),
        patch.object(manager, "_read_span_attributes", return_value={}) as attrs,
        patch.object(manager, "_read_evals", return_value={}),
    ):
        manager.list_payload(page_size=25, current_page=0)

    assert attrs.call_args.kwargs["start_date"] == start
    assert attrs.call_args.kwargs["end_date"] == end


@pytest.mark.parametrize(
    ("candidate", "operator", "expected", "matches"),
    [
        (["Rechazado", "Completed"], "in", ["Rechazado"], True),
        (["Rechazado", "Completed"], "not_in", ["Failed"], True),
        (["Rechazado", "Completed"], "not_in", ["Completed"], False),
        ({"final_status": "Rechazado"}, "contains", "Rechazado", True),
        (
            '{"final_status":"Rechazado","nested":{"attempt":2}}',
            "equals",
            {"nested": {"attempt": 2}, "final_status": "Rechazado"},
            True,
        ),
        (
            '{"final_status":"Rechazado","nested":{"attempt":2}}',
            "contains",
            {"attempt": 2},
            True,
        ),
        (12.0, "greater_than", 10, True),
        (12.0, "equals", 12, True),
        # ClickHouse's numeric BETWEEN contract is inclusive at both bounds.
        (20, "between", [10, 20], True),
        ([5, 15, 25], "not_between", [10, 20], False),
        ([5, 25], "not_between", [10, 20], True),
        ("true", "equals", True, True),
        ("false", "in", [False], True),
        (None, "is_null", None, True),
        ("value", "unsupported", "value", False),
    ],
)
def test_candidate_filter_matrix(candidate, operator, expected, matches):
    assert (
        UsersListManager._candidate_value_matches(candidate, operator, expected)
        is matches
    )


@pytest.mark.parametrize("structured_first", [False, True])
def test_span_attribute_collector_preserves_mixed_scalar_and_json_values(
    structured_first,
):
    manager = _manager(attribute_keys=["mixed"])
    end_user_id = str(uuid.uuid4())
    scalar_row = {
        "end_user_id": end_user_id,
        "attribute_key": "mixed",
        "attribute_values_json": ['"plain"'],
    }
    structured_row = {
        "end_user_id": end_user_id,
        "attribute_key": "mixed",
        "attribute_values_json": ['{"attempt":2}'],
    }
    attribute_rows = (
        [structured_row, scalar_row]
        if structured_first
        else [scalar_row, structured_row]
    )

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        analytics_cls.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=attribute_rows
        )
        attributes = manager._read_span_attributes(
            [{"end_user_id": end_user_id}], ReadDeadline.start(10_000)
        )

    rows = [{"end_user_id": end_user_id}]
    manager._apply_span_attributes(rows, attributes)

    assert rows[0]["mixed"] == ["plain", '{"attempt":2}']


def test_span_attribute_collector_preserves_explicit_null_for_is_null_filter():
    manager = _manager(attribute_keys=["optional"])
    end_user_id = str(uuid.uuid4())
    attribute_row = {
        "end_user_id": end_user_id,
        "attribute_key": "optional",
        "attribute_values_json": ["null"],
    }

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        analytics_cls.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[attribute_row]
        )
        attributes = manager._read_span_attributes(
            [{"end_user_id": end_user_id}], ReadDeadline.start(10_000)
        )

    rows = [{"end_user_id": end_user_id}]
    manager._apply_span_attributes(rows, attributes)

    assert "optional" in rows[0]
    assert rows[0]["optional"] is None
    assert manager._candidate_value_matches(rows[0]["optional"], "is_null", None)


def test_span_attribute_collector_unions_typed_maps_with_structured_extra():
    manager = _manager(
        attribute_keys=["structured", "final_status", "score", "approved"]
    )
    end_user_id = str(uuid.uuid4())
    attribute_rows = [
        {
            "end_user_id": end_user_id,
            "attribute_key": "structured",
            "attribute_values_json": ['{"attempt":2}'],
        },
        {
            "end_user_id": end_user_id,
            "attribute_key": "final_status",
            "attribute_values_json": ['"Rechazado"'],
        },
        {
            "end_user_id": end_user_id,
            "attribute_key": "score",
            "attribute_values_json": ["12.0"],
        },
        {
            "end_user_id": end_user_id,
            "attribute_key": "approved",
            "attribute_values_json": ["true"],
        },
    ]

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        analytics_cls.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=attribute_rows
        )
        attributes = manager._read_span_attributes(
            [{"end_user_id": end_user_id}], ReadDeadline.start(10_000)
        )

    rows = [{"end_user_id": end_user_id}]
    manager._apply_span_attributes(rows, attributes)

    assert rows[0]["structured"] == '{"attempt":2}'
    assert rows[0]["final_status"] == "Rechazado"
    assert rows[0]["score"] == 12.0
    assert rows[0]["approved"] == "true"
    assert manager._candidate_value_matches(rows[0]["approved"], "equals", True)


def test_span_attribute_read_is_skipped_when_no_keys_are_requested():
    manager = _manager()
    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        attributes = manager._read_span_attributes(
            [{"end_user_id": str(uuid.uuid4())}], ReadDeadline.start(10_000)
        )

    assert attributes == {}
    analytics_cls.assert_not_called()


@pytest.mark.parametrize(
    ("column", "metric"),
    [
        ("active_days", "num_active_days"),
        ("avg_latency", "avg_trace_latency"),
        ("latency", "avg_trace_latency"),
        ("latency_ms", "avg_trace_latency"),
    ],
)
def test_user_metric_aliases_enable_exact_hydration(column, metric):
    manager = _manager(
        filters=[
            {
                "column_id": column,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                },
            }
        ]
    )

    assert metric in manager.metric_keys


def test_active_days_only_metric_query_has_valid_projection():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    queries = builder.build_requested_page_metric_queries(
        [str(uuid.uuid4())], {"num_active_days"}
    )

    assert len(queries) == 1
    sql, _, fields = queries[0]
    assert fields == ("num_active_days",)
    assert "latest_end_user_id,\n                ," not in sql
    assert "uniqExact(toDate(start_time)) AS num_active_days" in sql


def test_user_eval_query_joins_trace_and_config_with_project_scope():
    project_a = str(uuid.uuid4())
    project_b = str(uuid.uuid4())
    config_a = str(uuid.uuid4())
    config_b = str(uuid.uuid4())
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[project_a, project_b],
    )

    sql, params = builder.build_eval_query(
        [str(uuid.uuid4())],
        allowed_eval_config_ids_by_project={
            project_a: [config_a],
            project_b: [config_b],
        },
    )

    assert "SELECT DISTINCT\n                project_id," in sql
    assert "ut.project_id = toUUID(%(eval_project_id_0)s)" in sql
    assert "ut.project_id = toUUID(%(eval_project_id_1)s)" in sql
    assert "eval_scan.custom_eval_config_id IN %(eval_config_ids_0)s" in sql
    assert "eval_scan.custom_eval_config_id IN %(eval_config_ids_1)s" in sql
    expected_by_project = {project_a: (config_a,), project_b: (config_b,)}
    for index, project_id in enumerate(sorted(expected_by_project)):
        assert params[f"eval_project_id_{index}"] == project_id
        assert params[f"eval_config_ids_{index}"] == expected_by_project[project_id]


def test_user_eval_query_requires_finite_allowed_config_scope():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    assert builder.build_eval_query([str(uuid.uuid4())]) == ("", {})
