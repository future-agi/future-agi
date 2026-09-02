from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import serializers

from tracer.serializers.trace import (
    TraceObserveListMetadataSerializer,
    TraceSessionListMetadataSerializer,
    UsersResultSerializer,
    UsersTableRowSerializer,
)
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.users_list_manager import (
    USER_LIST_ATTRIBUTE_FILTER_CANDIDATE_BATCH_SIZE,
    USER_LIST_CANDIDATE_BATCH_SIZE,
    USER_LIST_REFILL_MIN_BUDGET_MS,
    USER_LIST_WALL_DEADLINE_MS,
    UsersListManager,
    _users_attr_enrichment_query,
)
from tracer.views.trace import UsersView

pytestmark = pytest.mark.unit


def test_users_list_reserves_transport_inside_ten_second_sla():
    assert USER_LIST_WALL_DEADLINE_MS == 8_000


def test_users_rollup_exactness_metadata_is_declared_on_users_response_only():
    users_fields = UsersResultSerializer().fields
    trace_fields = TraceObserveListMetadataSerializer().fields
    session_fields = TraceSessionListMetadataSerializer().fields

    assert {"query_exact", "query_provenance", "ordering_exact"} <= users_fields.keys()
    assert (
        "span_user_rollup_end_users_candidate"
        in users_fields["query_provenance"].choices
    )
    assert "approximate_fields" in users_fields
    assert "num_sessions_is_approximate" in UsersTableRowSerializer().fields
    assert isinstance(users_fields["table"], serializers.ListSerializer)
    assert isinstance(users_fields["table"].child, UsersTableRowSerializer)
    rendered = UsersResultSerializer(
        {
            "table": [
                {
                    "user_id": "user-1",
                    "total_cost": 1.0,
                    "num_sessions": 2,
                    "num_sessions_is_approximate": True,
                }
            ],
            "total_count": 1,
            "total_pages": 1,
        }
    ).data
    assert rendered["table"][0]["num_sessions_is_approximate"] is True
    assert "query_provenance" not in trace_fields
    assert "ordering_exact" not in trace_fields
    assert {
        "query_exact",
        "query_provenance",
        "ordering_exact",
    } <= session_fields.keys()
    assert "spans_per_session_candidate" in session_fields["query_provenance"].choices


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


def test_dimension_candidate_query_is_windowed_rollup_keyset_and_finite():
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
        window_start=before - timedelta(days=365),
        window_end=before + timedelta(seconds=1),
    )

    assert "FROM span_user_rollup AS rollup" in sql
    # Raw candidate ids can be the new side of a remap while curated labels
    # remain survivor-old keyed. Search must wait for finite canonical replay.
    assert "candidate_population AS" in sql
    assert "FROM end_users AS eu FINAL" in sql
    assert "eu.organization_id = toUUID(%(org_id)s)" in sql
    assert "eu.is_deleted = 0" in sql
    assert "notEmpty(eu.user_id)" in sql
    assert "- INTERVAL 1 MICROSECOND AS first_seen" in sql
    assert "max(first_seen) AS first_seen" in sql
    assert "positionCaseInsensitive" not in sql
    assert "search" not in params
    assert "hour_first_seen >=" in sql
    assert "%(candidate_window_start)s" in sql
    assert "hour_first_seen <" in sql
    assert "%(candidate_window_end)s" in sql
    # The seed remains remap-free. A separate finite query classifies only this
    # page's ids against the many-to-one remap.
    assert "end_user_id_remap" not in sql
    assert "ORDER BY first_seen DESC, toString(rc.end_user_id) DESC" in sql
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
    assert "toString(rc.end_user_id) < %(before_end_user_id)s" in sql
    assert "end_user_id < toUUID(%(before_end_user_id)s)" not in sql
    assert "LIMIT %(dimension_limit)s" in sql
    assert "FROM spans" not in sql
    assert params["dimension_limit"] == 26
    assert params["candidate_window_start"] == before - timedelta(days=365)
    assert params["candidate_window_end"] == before + timedelta(seconds=1)
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


def test_default_dimension_candidate_page_uses_compact_curated_fallback():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )
    end = datetime(2026, 8, 5, 12, tzinfo=UTC)

    sql, params = builder.build_dimension_candidate_query(
        limit=26,
        window_start=end - timedelta(days=30),
        window_end=end,
    )

    assert "FROM span_user_rollup AS rollup" in sql
    assert "FROM end_users AS eu FINAL" in sql
    assert "candidate_population AS" in sql
    assert "raw_candidates AS" in sql
    assert "FROM spans" not in sql
    assert params["candidate_window_start"] == end - timedelta(days=30)
    assert params["candidate_window_end"] == end


@pytest.mark.parametrize(
    ("period", "days"),
    [("today", 1), ("7d", 7), ("30d", 30), ("3m", 90), ("6m", 180), ("12m", 365)],
    ids=lambda value: str(value),
)
def test_dimension_candidate_period_matrix_stays_on_windowed_rollup(period, days):
    del period
    end = datetime(2026, 8, 5, 12, tzinfo=UTC)
    start = end - timedelta(days=days)
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    sql, params = builder.build_dimension_candidate_query(
        limit=26,
        window_start=start,
        window_end=end,
    )

    assert "FROM span_user_rollup AS rollup" in sql
    assert "FROM spans" not in sql
    assert "FROM end_users AS eu FINAL" in sql
    assert "- INTERVAL 1 MICROSECOND AS first_seen" in sql
    assert params["candidate_window_start"] == start
    assert params["candidate_window_end"] == end


def test_dimension_survivor_query_is_candidate_bounded():
    candidate_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    end = datetime(2026, 8, 5, 12, tzinfo=UTC)
    start = end - timedelta(days=365)
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    sql, params = builder.build_dimension_survivor_query(
        candidate_ids,
        window_start=start,
        window_end=end,
    )

    assert "FROM end_user_id_remap FINAL" in sql
    assert "old_id IN %(dimension_candidate_ids)s" in sql
    assert "new_id IN %(dimension_candidate_ids)s" in sql
    # Return every alias in a touched remap group. The manager uses this finite
    # expansion as a literal IN-set so the span bloom index can prune before
    # the exact all-version replay.
    assert "WHERE any_id IN %(dimension_candidate_ids)s" not in sql
    assert "FROM span_user_rollup AS rollup" in sql
    assert "end_user_id IN (SELECT any_id FROM bounded_map)" in sql
    assert "candidate_alias_order_inputs AS" in sql
    assert "FROM end_users AS eu FINAL" in sql
    assert "- INTERVAL 1 MICROSECOND AS candidate_order_time" in sql
    assert "AS group_order_time" in sql
    assert "AS group_order_id" in sql
    assert params["dimension_candidate_ids"] == tuple(candidate_ids)
    assert params["candidate_window_start"] == start
    assert params["candidate_window_end"] == end


def test_curated_fallback_recovers_insert_block_blind_spot_across_cursor_pages():
    """An old block minimum cannot hide its in-window user from pagination."""

    window_end = datetime(2026, 8, 5, 12, tzinfo=UTC)
    window_start = window_end - timedelta(days=30)
    fallback_order = window_start - timedelta(microseconds=1)
    hot_id = str(uuid.UUID(int=3))
    survivor_id = str(uuid.UUID(int=1))
    in_window_span_alias = str(uuid.UUID(int=2))
    manager = _manager(
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [window_start, window_end],
                },
            }
        ]
    )
    analytics = MagicMock()
    # ``survivor_id`` represents a user whose old and in-window spans arrived
    # in one insert block. The MV stored that block under the old minimum hour,
    # so the windowed rollup contributes only ``hot_id``; the curated UNION arm
    # contributes ``survivor_id`` at the fallback sentinel.
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(
            data=[
                {"end_user_id": hot_id, "first_seen": window_end},
                {"end_user_id": survivor_id, "first_seen": fallback_order},
            ]
        ),
        SimpleNamespace(
            data=[
                {
                    "any_id": survivor_id,
                    "survivor_id": survivor_id,
                    "group_order_time": fallback_order,
                    "group_order_id": survivor_id,
                },
                {
                    "any_id": in_window_span_alias,
                    "survivor_id": survivor_id,
                    "group_order_time": fallback_order,
                    "group_order_id": survivor_id,
                },
            ]
        ),
    ]

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService",
        return_value=analytics,
    ):
        candidates = manager._read_dimension_candidates(
            deadline=ReadDeadline.start(9_500),
            limit=26,
            before_first_seen=None,
            before_end_user_id=None,
            window_start=window_start,
            window_end=window_end,
        )

    candidate_sql = analytics.execute_ch_query.call_args_list[0].args[0]
    survivor_sql = analytics.execute_ch_query.call_args_list[1].args[0]
    assert "UNION ALL" in candidate_sql
    assert "FROM end_users AS eu FINAL" in candidate_sql
    assert "- INTERVAL 1 MICROSECOND AS first_seen" in candidate_sql
    assert "FROM end_users AS eu FINAL" in survivor_sql
    assert candidates[1]["end_user_id"] == survivor_id
    assert candidates[1]["_candidate_scan_end_user_ids"] == (
        survivor_id,
        in_window_span_alias,
    )

    exact_rows = {
        hot_id: {
            "end_user_id": hot_id,
            "user_id": "hot-user",
            "last_active": window_end,
        },
        survivor_id: {
            "end_user_id": survivor_id,
            "user_id": "fallback-user",
            "last_active": window_end - timedelta(hours=1),
        },
    }
    exact_calls: list[dict] = []

    def read_candidates(**kwargs):
        if kwargs["before_end_user_id"] is None:
            return candidates
        assert kwargs["before_end_user_id"] == hot_id
        return candidates[1:]

    def read_exact(**kwargs):
        exact_calls.append(kwargs)
        return [exact_rows[end_user_id] for end_user_id in kwargs["candidate_ids"]]

    with (
        patch.object(
            manager,
            "_read_dimension_candidates",
            side_effect=read_candidates,
        ),
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            side_effect=read_exact,
        ),
    ):
        first = manager.list_cursor_payload(page_size=1)
        second = manager.list_cursor_payload(
            page_size=1,
            cursor=ListCursor(
                window_start=first.window_start,
                window_end=first.window_end,
                order=first.checkpoint_order,
                seen_rows=first.seen_rows,
            ),
        )

    assert [row["user_id"] for row in first.payload["table"]] == ["hot-user"]
    assert [row["user_id"] for row in second.payload["table"]] == ["fallback-user"]
    assert first.checkpoint_order == (window_end, hot_id)
    assert second.checkpoint_order == (fallback_order, survivor_id)
    assert first.payload["count_is_lower_bound"] is True
    assert second.has_more is False
    assert second.payload["count_is_lower_bound"] is False
    assert second.payload["total_count"] == 2
    assert {
        survivor_id,
        in_window_span_alias,
    } <= set(exact_calls[0]["candidate_scan_ids"])
    assert exact_calls[0]["candidate_end_user_id_map"][in_window_span_alias] == (
        survivor_id
    )


def test_finite_candidate_ids_narrow_identity_before_latest_replay():
    candidate_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        search="survivor-label",
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
    assert (
        "project_id,\n"
        "                  observation_type,\n"
        "                  service_name,\n"
        "                  toStartOfHour(start_time),\n"
        "                  trace_id,\n"
        "                  id"
    ) in sql
    assert (
        "GROUP BY\n                project_id,\n                observation_type" in sql
    )

    # The mutable user predicate is legal only in the identity-superset scan.
    # Latest-state membership/deletion are decided after every version of each
    # immutable identity has been replayed.
    replay = sql.split("latest_candidate_spans AS", 1)[1]
    assert "end_user_id IN" not in replay.split("GROUP BY", 1)[0]
    assert "argMax(tuple(end_user_id), _version)" in replay
    curated = sql.split("filtered_end_users_raw AS", 1)[1].split(
        "filtered_end_users AS", 1
    )[0]
    assert "eu.end_user_id IN %(candidate_scan_end_user_ids)s" in curated
    assert "positionCaseInsensitive" not in curated
    canonical = sql.split("searched_end_users AS", 1)[1].split("exact_usage AS", 1)[0]
    assert "positionCaseInsensitive(user_id, %(search)s)" in canonical


def test_cursor_candidate_replay_injects_page_remap_without_table_scan():
    survivor_ids = [str(uuid.UUID(int=index + 1)) for index in range(25)]
    alias_ids = [str(uuid.UUID(int=index + 101)) for index in range(25)]
    candidate_map = {
        any_id: survivor_id
        for survivor_id, alias_id in zip(survivor_ids, alias_ids, strict=True)
        for any_id in (survivor_id, alias_id)
    }
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        filters=[],
        limit=25,
        offset=0,
        candidate_end_user_ids=survivor_ids,
        candidate_scan_end_user_ids=list(candidate_map),
        candidate_end_user_id_map=candidate_map,
    )

    sql, params = builder.build_candidate_page_query()

    assert "end_user_id_remap" not in sql
    assert (
        "arrayZip(%(candidate_remap_any_ids)s, %(candidate_remap_survivor_ids)s)" in sql
    )
    assert params["candidate_remap_any_ids"] == list(candidate_map)
    assert params["candidate_remap_survivor_ids"] == list(candidate_map.values())
    assert params["candidate_scan_end_user_ids"] == tuple(candidate_map)


def test_cursor_enrichments_reuse_literal_page_remap():
    project_id = str(uuid.uuid4())
    config_id = str(uuid.uuid4())
    survivor_ids = [str(uuid.UUID(int=index + 1)) for index in range(25)]
    alias_ids = [str(uuid.UUID(int=index + 101)) for index in range(25)]
    candidate_map = {
        any_id: survivor_id
        for survivor_id, alias_id in zip(survivor_ids, alias_ids, strict=True)
        for any_id in (survivor_id, alias_id)
    }
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[project_id],
        candidate_end_user_ids=survivor_ids,
        candidate_scan_end_user_ids=list(candidate_map),
        candidate_end_user_id_map=candidate_map,
    )

    metric_queries = builder.build_requested_page_metric_queries(
        survivor_ids, {"num_active_days"}
    )
    eval_sql, eval_params = builder.build_eval_query(
        survivor_ids,
        allowed_eval_config_ids_by_project={project_id: [config_id]},
    )
    attr_sql, attr_params = _users_attr_enrichment_query(
        project_ids=[project_id],
        attribute_keys=["final_status"],
        candidate_end_user_id_map=candidate_map,
    )

    assert len(metric_queries) == 1
    metric_sql, metric_params, _ = metric_queries[0]
    for sql in (metric_sql, eval_sql, attr_sql):
        assert "end_user_id_remap" not in sql
        assert "candidate_remap_any_ids" in sql
        assert "candidate_remap_survivor_ids" in sql
    for sql in (metric_sql, attr_sql):
        assert "toStartOfHour(start_time) AS identity_hour" in sql
        assert "service_name" in sql
    for params in (metric_params, eval_params, attr_params):
        assert params["candidate_remap_any_ids"] == list(candidate_map)
        assert params["candidate_remap_survivor_ids"] == list(candidate_map.values())


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
    assert "AS latest_attribute_value_type" in sql
    assert "tuple(latest_attribute_value_type, latest_attribute_value_json)" in sql
    assert "AS attribute_typed_values" in sql
    assert "end_user_id IN %(eu_scan_ids)s" in sql
    assert params["requested_attribute_keys"] == ["final_status", "score"]


def test_custom_eval_and_annotation_filters_are_not_span_attribute_keys():
    eval_id = str(uuid.uuid4())
    annotation_id = str(uuid.uuid4())
    filters = [
        {
            "column_id": eval_id,
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 50,
                "col_type": "EVAL_METRIC",
            },
        },
        {
            "column_id": annotation_id,
            "filter_config": {
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 4,
                "col_type": "ANNOTATION",
            },
        },
        {
            "column_id": "customer_tier",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "enterprise",
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
    ]

    manager = _manager(filters=filters)

    assert manager.relation_filters == tuple(filters[:2])
    assert manager.attribute_keys == ("customer_tier",)
    assert eval_id not in manager.attribute_keys
    assert annotation_id not in manager.attribute_keys
    assert manager.filters_need_enrichment is True


def test_relation_filter_query_compiles_eval_and_annotation_semantics_together():
    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    alias_id = str(uuid.uuid4())
    eval_id = str(uuid.uuid4())
    eval_config_id = str(uuid.uuid4())
    annotation_id = str(uuid.uuid4())
    relation_filters = [
        {
            "column_id": eval_id,
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": "Passed",
                "col_type": "EVAL_METRIC",
            },
        },
        {
            "column_id": annotation_id,
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 3,
                "col_type": "ANNOTATION",
            },
        },
    ]
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[project_id],
        candidate_end_user_ids=[user_id],
        candidate_scan_end_user_ids=[user_id, alias_id],
        candidate_end_user_id_map={user_id: user_id, alias_id: user_id},
    )

    sql, params = builder.build_relation_filter_user_query(
        relation_filters,
        eval_filter_metadata={
            eval_id: EvalFilterMetadata((eval_config_id,), "PASS_FAIL")
        },
    )

    assert "latest_relation_candidate_spans AS" in sql
    assert "argMax(tuple(end_user_id), _version).1 AS end_user_id" in sql
    assert "eval_scan.custom_eval_config_id IN" in sql
    assert "FROM model_hub_score AS s FINAL" in sql
    assert "s.label_id = toUUID" in sql
    assert "AND (" in sql
    assert "attrs_string" not in sql
    assert "attributes_extra" not in sql
    assert params["candidate_end_user_ids"] == (user_id,)
    assert params["candidate_scan_end_user_ids"] == (user_id, alias_id)
    assert (eval_config_id,) in params.values()
    assert annotation_id in params.values()


def test_relation_membership_gates_exact_rows_before_other_enrichment():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(3)]
    matching_ids = {candidates[0]["end_user_id"], candidates[2]["end_user_id"]}
    eval_id = str(uuid.uuid4())
    annotation_id = str(uuid.uuid4())
    manager = _manager(
        filters=[
            {
                "column_id": eval_id,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 50,
                    "col_type": "EVAL_METRIC",
                },
            },
            {
                "column_id": annotation_id,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 4,
                    "col_type": "ANNOTATION",
                },
            },
        ]
    )
    builder = MagicMock()
    builder.build_candidate_page_query.return_value = ("SELECT candidate page", {})
    builder.format_rows.return_value = {
        "table": [_exact(candidate) for candidate in candidates],
        "total_count": len(candidates),
    }
    builder.build_relation_filter_user_query.return_value = (
        "SELECT matching users",
        {},
    )
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(data=[]),
        SimpleNamespace(
            data=[{"end_user_id": end_user_id} for end_user_id in matching_ids]
        ),
    ]

    with (
        patch.object(manager, "_exact_candidate_builder", return_value=builder),
        patch.object(manager, "_relation_eval_metadata", return_value={}),
        patch.object(manager, "_enrich_rows") as enrich_rows,
        patch(
            "tracer.services.users_list_manager.V2AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        rows = manager._read_exact_candidate_rows(
            candidate_ids=[candidate["end_user_id"] for candidate in candidates],
            candidate_scan_ids=[candidate["end_user_id"] for candidate in candidates],
            candidate_end_user_id_map={
                candidate["end_user_id"]: candidate["end_user_id"]
                for candidate in candidates
            },
            frozen_filters=[],
            window_start=now - timedelta(days=30),
            window_end=now,
            deadline=ReadDeadline.start(10_000),
        )

    assert {row["end_user_id"] for row in rows} == matching_ids
    assert manager._relation_matching_user_ids == matching_ids
    assert {
        row["end_user_id"] for row in enrich_rows.call_args.args[0]
    } == matching_ids
    builder.build_relation_filter_user_query.assert_called_once_with(
        manager.relation_filters,
        eval_filter_metadata={},
    )


def test_mixed_user_filter_requires_system_attribute_eval_and_annotation_matches():
    end_user_id = str(uuid.uuid4())
    manager = _manager(
        filters=[
            {
                "column_id": "total_cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 5,
                },
            },
            {
                "column_id": "customer_tier",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "enterprise",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            },
            {
                "column_id": str(uuid.uuid4()),
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 50,
                    "col_type": "EVAL_METRIC",
                },
            },
            {
                "column_id": str(uuid.uuid4()),
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 4,
                    "col_type": "ANNOTATION",
                },
            },
        ]
    )
    matching_row = {
        "end_user_id": end_user_id,
        "total_cost": 10,
        "customer_tier": "enterprise",
    }

    manager._relation_matching_user_ids.add(end_user_id)

    assert manager._row_matches_filters(matching_row)
    assert not manager._row_matches_filters(
        {**matching_row, "customer_tier": "self-serve"}
    )
    assert not manager._row_matches_filters(
        {**matching_row, "end_user_id": str(uuid.uuid4())}
    )


def test_positive_text_attribute_filter_prunes_only_physical_candidate_seed():
    project_id = str(uuid.uuid4())
    sql, params = _users_attr_enrichment_query(
        project_ids=[project_id],
        attribute_keys=["call_id"],
        candidate_text_values_by_key={"call_id": ("call-a", "call-b")},
    )

    candidate_sql, latest_sql = sql.split(
        "),\n    latest_candidate_attribute_values AS (", 1
    )
    assert "mapContains(attrs_string, %(candidate_attribute_key_0)s)" in candidate_sql
    assert "lowerUTF8(attrs_string[%(candidate_attribute_key_0)s])" in candidate_sql
    assert "JSONExtractString(attributes_extra" not in candidate_sql
    assert "candidate_attribute_key_0" not in latest_sql
    assert params["candidate_attribute_key_0"] == "call_id"
    assert params["candidate_attribute_values_0"] == ("call-a", "call-b")


def test_positive_text_attribute_accelerator_and_matcher_share_casefolding():
    manager = _manager(
        filters=[
            {
                "column_id": "call_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "call-a",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
    )
    end_user_id = str(uuid.uuid4())

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        analytics_cls.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[
                {
                    "end_user_id": end_user_id,
                    "attribute_key": "call_id",
                    "attribute_typed_values": [("string", '"CALL-A"')],
                }
            ]
        )
        attributes = manager._read_span_attributes(
            [{"end_user_id": end_user_id}], ReadDeadline.start(10_000)
        )

    row = {"end_user_id": end_user_id}
    manager._apply_span_attributes([row], attributes)

    assert manager.attribute_exact_text_filters == {"call_id": ("call-a",)}
    assert row["call_id"] == "CALL-A"
    assert manager._row_matches_filters(row)


@pytest.mark.parametrize(
    ("stored_type", "stored_json", "selected_type", "selected_value"),
    [
        ("string", '"42"', "number", 42),
        ("number", "42", "string", "42"),
    ],
)
def test_attribute_filter_preserves_storage_type_provenance(
    stored_type, stored_json, selected_type, selected_value
):
    manager = _manager(
        filters=[
            {
                "column_id": "mixed",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [selected_value],
                    "attribute_value_types": [selected_type],
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
    )
    end_user_id = str(uuid.uuid4())

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        analytics_cls.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[
                {
                    "end_user_id": end_user_id,
                    "attribute_key": "mixed",
                    "attribute_typed_values": [(stored_type, stored_json)],
                }
            ]
        )
        attributes = manager._read_span_attributes(
            [{"end_user_id": end_user_id}], ReadDeadline.start(10_000)
        )

    row = {"end_user_id": end_user_id}
    manager._apply_span_attributes([row], attributes)

    assert not manager._row_matches_filters(row)


def test_positive_text_attribute_filter_uses_smaller_user_candidate_batch():
    manager = _manager(
        filters=[
            {
                "column_id": "call_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "call-a",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
    )
    captured_limits: list[int] = []

    def no_candidates(**kwargs):
        captured_limits.append(kwargs["limit"])
        return []

    with patch.object(manager, "_read_dimension_candidates", side_effect=no_candidates):
        result = manager.list_cursor_payload(page_size=25)

    assert manager.attribute_exact_text_filters == {"call_id": ("call-a",)}
    assert captured_limits == [USER_LIST_ATTRIBUTE_FILTER_CANDIDATE_BATCH_SIZE + 1]
    assert result.payload["table"] == []


def test_negative_attribute_filter_never_uses_positive_candidate_pruning():
    manager = _manager(
        filters=[
            {
                "column_id": "call_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_equals",
                    "filter_value": "call-a",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
    )

    assert manager.attribute_exact_text_filters == {}


def test_mixed_storage_attribute_filter_never_uses_string_candidate_pruning():
    manager = _manager(
        filters=[
            {
                "column_id": "call_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["call-a", "42"],
                    "attribute_value_types": ["string", "number"],
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
    )

    assert manager.attribute_exact_text_filters == {}


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
    assert result.payload["query_exact"] is False
    assert result.payload["query_provenance"] == "span_user_rollup_end_users_candidate"
    assert result.payload["ordering_exact"] is False
    assert result.has_more is False
    assert result.checkpoint_order == (
        candidates[-1]["first_seen"],
        candidates[-1]["end_user_id"],
    )


def test_cursor_threads_classified_alias_map_into_exact_replay():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(2)]
    alias_id = str(uuid.uuid4())
    candidates[0]["_is_survivor_candidate"] = True
    candidates[0]["_candidate_scan_end_user_ids"] = (
        candidates[0]["end_user_id"],
        alias_id,
    )
    candidates[1]["_is_survivor_candidate"] = True
    candidates[1]["_candidate_scan_end_user_ids"] = (candidates[1]["end_user_id"],)
    manager = _manager()

    with (
        patch.object(
            manager,
            "_read_dimension_candidates",
            return_value=candidates,
        ),
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            return_value=[],
        ) as exact_read,
    ):
        manager.list_cursor_payload(page_size=25)

    assert exact_read.call_args.kwargs["candidate_end_user_id_map"] == {
        candidates[0]["end_user_id"]: candidates[0]["end_user_id"],
        alias_id: candidates[0]["end_user_id"],
        candidates[1]["end_user_id"]: candidates[1]["end_user_id"],
    }


def test_dimension_candidate_new_alias_emits_old_survivor_with_all_aliases():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    survivor_id = str(uuid.UUID(int=1))
    new_id = str(uuid.UUID(int=2))
    manager = _manager()
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(data=[{"end_user_id": new_id, "first_seen": now}]),
        SimpleNamespace(
            data=[
                {
                    "any_id": survivor_id,
                    "survivor_id": survivor_id,
                    "group_order_time": now,
                    "group_order_id": new_id,
                },
                {
                    "any_id": new_id,
                    "survivor_id": survivor_id,
                    "group_order_time": now,
                    "group_order_id": new_id,
                },
            ]
        ),
    ]

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService",
        return_value=analytics,
    ):
        rows = manager._read_dimension_candidates(
            deadline=ReadDeadline.start(9_500),
            limit=26,
            before_first_seen=None,
            before_end_user_id=None,
            window_start=now - timedelta(days=365),
            window_end=now + timedelta(seconds=1),
        )

    assert rows == [
        {
            "end_user_id": survivor_id,
            "first_seen": now,
            "_candidate_order_time": now,
            "_candidate_order_id": new_id,
            "_is_survivor_candidate": True,
            "_candidate_scan_end_user_ids": (survivor_id, new_id),
        }
    ]
    remap_params = analytics.execute_ch_query.call_args_list[1].args[1]
    assert remap_params["candidate_window_start"] == now - timedelta(days=365)
    assert remap_params["candidate_window_end"] == now + timedelta(seconds=1)


def test_equal_time_remap_group_uses_greatest_raw_alias_as_only_emitter():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    survivor_id = str(uuid.UUID(int=1))
    new_id = str(uuid.UUID(int=2))
    manager = _manager()
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(
            data=[
                {"end_user_id": new_id, "first_seen": now},
                {"end_user_id": survivor_id, "first_seen": now},
            ]
        ),
        SimpleNamespace(
            data=[
                {
                    "any_id": survivor_id,
                    "survivor_id": survivor_id,
                    "group_order_time": now,
                    "group_order_id": new_id,
                },
                {
                    "any_id": new_id,
                    "survivor_id": survivor_id,
                    "group_order_time": now,
                    "group_order_id": new_id,
                },
            ]
        ),
    ]

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService",
        return_value=analytics,
    ):
        rows = manager._read_dimension_candidates(
            deadline=ReadDeadline.start(9_500),
            limit=26,
            before_first_seen=None,
            before_end_user_id=None,
            window_start=now - timedelta(days=30),
            window_end=now + timedelta(seconds=1),
        )

    assert rows[0]["end_user_id"] == survivor_id
    assert rows[0]["_candidate_order_id"] == new_id
    assert rows[0]["_is_survivor_candidate"] is True
    assert rows[1]["end_user_id"] == survivor_id
    assert rows[1]["_candidate_order_id"] == survivor_id
    assert rows[1]["_is_survivor_candidate"] is False


def test_remap_emitter_page_resume_uses_raw_order_and_suppresses_lower_alias():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    survivor_id = str(uuid.UUID(int=1))
    new_id = str(uuid.UUID(int=2))
    emitting = {
        **_candidate(0, now=now),
        "end_user_id": survivor_id,
        "first_seen": now,
        "_candidate_order_time": now,
        "_candidate_order_id": new_id,
        "_is_survivor_candidate": True,
        "_candidate_scan_end_user_ids": (survivor_id, new_id),
    }
    lower_alias = {
        **_candidate(1, now=now),
        "end_user_id": survivor_id,
        "first_seen": now,
        "_candidate_order_time": now,
        "_candidate_order_id": survivor_id,
        "_is_survivor_candidate": False,
    }
    exact = _exact(emitting)
    manager = _manager()

    def read_candidates(**kwargs):
        if kwargs["before_end_user_id"] is None:
            return [emitting, lower_alias]
        assert kwargs["before_end_user_id"] == new_id
        return [lower_alias]

    def read_exact(**kwargs):
        return [exact] if kwargs["candidate_ids"] else []

    with (
        patch.object(
            manager,
            "_read_dimension_candidates",
            side_effect=read_candidates,
        ),
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            side_effect=read_exact,
        ),
    ):
        first = manager.list_cursor_payload(page_size=1)
        second = manager.list_cursor_payload(
            page_size=1,
            cursor=ListCursor(
                window_start=first.window_start,
                window_end=first.window_end,
                order=first.checkpoint_order,
                seen_rows=first.seen_rows,
            ),
        )

    assert [row["end_user_id"] for row in first.payload["table"]] == [survivor_id]
    assert first.checkpoint_order == (now, new_id)
    assert first.has_more is True
    assert second.payload["table"] == []
    assert second.checkpoint_order == (now, survivor_id)
    assert second.has_more is False
    assert second.seen_rows == 1


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
        candidates[USER_LIST_CANDIDATE_BATCH_SIZE - 1]["first_seen"],
        candidates[USER_LIST_CANDIDATE_BATCH_SIZE - 1]["end_user_id"],
    )


def test_cursor_three_drops_use_bounded_refill_and_resume_without_overlap():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(70)]
    exact_by_id = {
        candidate["end_user_id"]: _exact(candidate) for candidate in candidates
    }
    dropped_ids = {candidate["end_user_id"] for candidate in candidates[:3]}
    manager = _manager()
    candidate_limits: list[int] = []
    replay_sizes: list[int] = []

    def read_candidates(**kwargs):
        candidate_limits.append(kwargs["limit"])
        before_id = kwargs["before_end_user_id"]
        start_index = 0
        if before_id is not None:
            start_index = (
                next(
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate["end_user_id"] == before_id
                )
                + 1
            )
        return candidates[start_index : start_index + kwargs["limit"]]

    def read_exact(**kwargs):
        replay_sizes.append(len(kwargs["candidate_ids"]))
        return [
            exact_by_id[end_user_id]
            for end_user_id in kwargs["candidate_ids"]
            if end_user_id not in dropped_ids
        ]

    with (
        patch.object(
            manager,
            "_read_dimension_candidates",
            side_effect=read_candidates,
        ),
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            side_effect=read_exact,
        ),
    ):
        first = manager.list_cursor_payload(page_size=25)

        assert len(first.payload["table"]) == 25
        assert candidate_limits == [26, 5]
        assert replay_sizes == [25, 4]
        assert first.checkpoint_order == (
            candidates[27]["first_seen"],
            candidates[27]["end_user_id"],
        )

        second = manager.list_cursor_payload(
            page_size=25,
            cursor=ListCursor(
                window_start=first.window_start,
                window_end=first.window_end,
                order=first.checkpoint_order,
                seen_rows=first.seen_rows,
            ),
        )

    first_ids = {row["end_user_id"] for row in first.payload["table"]}
    second_ids = {row["end_user_id"] for row in second.payload["table"]}
    assert len(second.payload["table"]) == 25
    assert first_ids.isdisjoint(second_ids)
    assert second.checkpoint_order == (
        candidates[52]["first_seen"],
        candidates[52]["end_user_id"],
    )
    assert candidate_limits == [26, 5, 26]
    assert replay_sizes == [25, 4, 25]


def test_cursor_refills_base_rows_before_one_presentation_enrichment():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(40)]
    alias_id = str(uuid.uuid4())
    candidates[3]["_candidate_scan_end_user_ids"] = (
        candidates[3]["end_user_id"],
        alias_id,
    )
    exact_by_id = {
        candidate["end_user_id"]: _exact(candidate) for candidate in candidates
    }
    dropped_ids = {candidate["end_user_id"] for candidate in candidates[:3]}
    manager = _manager(requested_columns=["num_sessions"])

    def read_candidates(**kwargs):
        before_id = kwargs["before_end_user_id"]
        start_index = 0
        if before_id is not None:
            start_index = (
                next(
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate["end_user_id"] == before_id
                )
                + 1
            )
        return candidates[start_index : start_index + kwargs["limit"]]

    def read_exact(**kwargs):
        return [
            exact_by_id[end_user_id]
            for end_user_id in kwargs["candidate_ids"]
            if end_user_id not in dropped_ids
        ]

    with (
        patch.object(
            manager, "_read_dimension_candidates", side_effect=read_candidates
        ),
        patch.object(
            manager, "_read_exact_candidate_rows", side_effect=read_exact
        ) as exact_read,
        patch.object(manager, "_enrich_rows") as enrich_rows,
    ):
        result = manager.list_cursor_payload(page_size=25)

    assert len(result.payload["table"]) == 25
    assert exact_read.call_count == 2
    assert all(
        call.kwargs["enrich_rows"] is False for call in exact_read.call_args_list
    )
    enrich_rows.assert_called_once()
    assert enrich_rows.call_args.args[0] == result.payload["table"]
    expected_published_ids = {
        candidate["end_user_id"] for candidate in candidates[3:28]
    }
    assert set(enrich_rows.call_args.kwargs["candidate_scan_ids"]) == {
        *expected_published_ids,
        alias_id,
    }
    assert (
        enrich_rows.call_args.kwargs["candidate_end_user_id_map"][alias_id]
        == (candidates[3]["end_user_id"])
    )
    assert result.payload["approximate_fields"] == ["num_sessions"]


def test_cursor_keeps_enrichment_before_enrichment_dependent_filter_matching():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(2)]
    exact_rows = [
        {**_exact(candidates[0]), "num_sessions": 1},
        {**_exact(candidates[1]), "num_sessions": 0},
    ]
    manager = _manager(
        filters=[
            {
                "column_id": "num_sessions",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                },
            }
        ],
        requested_columns=["num_sessions"],
    )

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
        ) as exact_read,
        patch.object(manager, "_enrich_rows") as final_enrichment,
    ):
        result = manager.list_cursor_payload(page_size=25)

    assert result.payload["table"] == [exact_rows[0]]
    assert result.payload["approximate_fields"] == []
    assert exact_read.call_args.kwargs["enrich_rows"] is True
    final_enrichment.assert_not_called()


def test_cursor_skips_refill_without_reserved_budget_and_preserves_checkpoint():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(26)]
    exact_rows = [_exact(candidate) for candidate in candidates[:13]]
    manager = _manager()
    deadline = MagicMock()
    deadline.remaining_ms.side_effect = ReadDeadlineExceeded("deadline")

    with (
        patch(
            "tracer.services.users_list_manager.ReadDeadline.start",
            return_value=deadline,
        ),
        patch.object(
            manager,
            "_read_dimension_candidates",
            return_value=candidates,
        ) as dimension_read,
        patch.object(
            manager,
            "_read_exact_candidate_rows",
            return_value=exact_rows,
        ) as exact_read,
    ):
        result = manager.list_cursor_payload(page_size=25)

    assert len(result.payload["table"]) == 13
    assert result.has_more is True
    assert result.payload["count_is_lower_bound"] is True
    assert result.checkpoint_order == (
        candidates[USER_LIST_CANDIDATE_BATCH_SIZE - 1]["first_seen"],
        candidates[USER_LIST_CANDIDATE_BATCH_SIZE - 1]["end_user_id"],
    )
    assert dimension_read.call_count == 1
    assert exact_read.call_count == 1
    deadline.remaining_ms.assert_called_once_with(
        floor_ms=USER_LIST_REFILL_MIN_BUDGET_MS
    )


def test_cursor_two_pages_do_not_overlap_and_advance_the_lower_bound():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidates = [_candidate(index, now=now) for index in range(30)]
    exact_by_id = {
        candidate["end_user_id"]: _exact(candidate) for candidate in candidates
    }
    manager = _manager()

    def read_candidates(**kwargs):
        before_id = kwargs["before_end_user_id"]
        start_index = 0
        if before_id is not None:
            start_index = (
                next(
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate["end_user_id"] == before_id
                )
                + 1
            )
        return candidates[start_index : start_index + kwargs["limit"]]

    def read_exact(**kwargs):
        return [exact_by_id[end_user_id] for end_user_id in kwargs["candidate_ids"]]

    with (
        patch.object(
            manager, "_read_dimension_candidates", side_effect=read_candidates
        ),
        patch.object(manager, "_read_exact_candidate_rows", side_effect=read_exact),
    ):
        first = manager.list_cursor_payload(page_size=2)
        second = manager.list_cursor_payload(
            page_size=2,
            cursor=ListCursor(
                window_start=first.window_start,
                window_end=first.window_end,
                order=first.checkpoint_order,
                seen_rows=first.seen_rows,
            ),
        )

    first_ids = {row["end_user_id"] for row in first.payload["table"]}
    second_ids = {row["end_user_id"] for row in second.payload["table"]}
    assert first_ids.isdisjoint(second_ids)
    assert first.payload["total_count"] == 3
    assert first.payload["count_is_lower_bound"] is True
    assert second.payload["total_count"] == 5
    assert second.payload["count_is_lower_bound"] is True
    assert second.seen_rows == 4
    assert second.checkpoint_order < first.checkpoint_order


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
    assert kwargs["window_start"] == cursor.window_start
    assert kwargs["window_end"] == cursor.window_end
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


def test_omitted_projection_hydrates_legacy_metrics_and_evals_without_attributes():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    project_id = str(uuid.uuid4())
    manager = UsersListManager(
        organization_id=str(uuid.uuid4()),
        allowed_project_ids=[project_id],
        project_id=project_id,
        # Deliberately omit requested_columns: this is the legacy wire shape.
    )
    expected_metrics = {
        "num_sessions",
        "avg_session_duration",
        "avg_trace_latency",
        "num_llm_calls",
        "num_guardrails_triggered",
        "num_active_days",
        "num_traces_with_errors",
    }
    row = _exact(_candidate(0, now=now))
    end_user_id = str(row["end_user_id"])
    builder = MagicMock()

    with (
        patch.object(
            manager,
            "_read_page_metrics",
            return_value={end_user_id: {"num_sessions": 1}},
        ) as read_metrics,
        patch.object(manager, "_read_span_attributes") as read_attributes,
        patch.object(
            manager,
            "_read_evals",
            return_value={end_user_id: {"bool_eval_pass_rate": 0.75}},
        ) as read_evals,
    ):
        manager._enrich_rows(
            [row],
            builder,
            ReadDeadline.start(10_000),
            start_date=now - timedelta(days=1),
            end_date=now,
        )

    assert manager.metric_keys == expected_metrics
    assert manager.needs_evals is True
    assert manager.attribute_keys == ()
    read_metrics.assert_called_once()
    read_evals.assert_called_once()
    read_attributes.assert_not_called()
    assert row["num_sessions"] == 1
    assert row["bool_eval_pass_rate"] == 0.75


@pytest.mark.parametrize(
    ("query_params", "validated_columns", "expected"),
    (
        ({}, [], None),
        ({"requested_columns": "[]"}, [], []),
        (
            {"requested_columns": '["num_sessions"]'},
            ["num_sessions"],
            ["num_sessions"],
        ),
    ),
)
def test_users_view_preserves_projection_wire_semantics(
    query_params, validated_columns, expected
):
    request = SimpleNamespace(query_params=query_params)

    assert (
        UsersView._requested_columns_for_request(
            request, {"requested_columns": validated_columns}
        )
        == expected
    )


def test_explicit_empty_projection_remains_a_bounded_enrichment_opt_out():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    manager = _manager(requested_columns=[])
    row = _exact(_candidate(0, now=now))

    with (
        patch.object(manager, "_read_page_metrics") as read_metrics,
        patch.object(manager, "_read_span_attributes") as read_attributes,
        patch.object(manager, "_read_evals") as read_evals,
    ):
        manager._enrich_rows(
            [row],
            MagicMock(),
            ReadDeadline.start(10_000),
            start_date=now - timedelta(days=1),
            end_date=now,
        )

    assert manager.metric_keys == frozenset()
    assert manager.needs_evals is False
    assert manager.attribute_keys == ()
    read_metrics.assert_not_called()
    read_attributes.assert_not_called()
    read_evals.assert_not_called()


def test_explicit_projection_hydrates_only_requested_builtin_metric():
    manager = _manager(requested_columns=["num_sessions"])

    assert manager.requested_columns == frozenset({"num_sessions"})
    assert manager.metric_keys == frozenset({"num_sessions"})
    assert manager.needs_evals is False
    assert manager.attribute_keys == ()


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
    assert "argMax(start_time, _version) AS latest_start_time" in sql
    assert "uniqExact(toDate(latest_start_time)) AS num_active_days" in sql
    assert "toStartOfHour(start_time) AS identity_hour" in sql
    assert "service_name" in sql


def test_num_sessions_only_metric_query_skips_duration_state_and_replay():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    queries = builder.build_requested_page_metric_queries(
        [str(uuid.uuid4())], {"num_sessions"}
    )

    assert len(queries) == 1
    sql, _, fields = queries[0]
    assert fields == ("num_sessions",)
    assert "count() AS num_sessions" in sql
    assert "argMax(tuple(end_time), _version)" not in sql
    assert "argMax(start_time, _version)" not in sql
    assert "duration_seconds" not in sql


def test_cursor_page_embeds_requested_session_count_in_exact_usage_replay():
    end_user_id = str(uuid.uuid4())
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        limit=25,
        offset=0,
        candidate_end_user_ids=[end_user_id],
        candidate_scan_end_user_ids=[end_user_id],
        candidate_end_user_id_map={end_user_id: end_user_id},
        include_num_sessions=True,
    )

    sql, _ = builder.build_candidate_page_query()

    assert builder.embedded_page_metric_fields == frozenset({"num_sessions"})
    assert "AS latest_trace_session_id" in sql
    assert "uniqExactIf(" in sql
    assert ") AS num_sessions" in sql
    assert "num_sessions,\n            activated_at" in sql
    assert "trace_session_id_remap" not in sql
    assert "ts_survivor_map" not in sql
    assert "span_ts_remap" not in sql
    assert "duration_seconds" not in sql


def test_embedded_page_metric_is_not_replayed_by_optional_enrichment():
    manager = _manager(requested_columns=["num_sessions"])
    end_user_id = str(uuid.uuid4())
    builder = MagicMock()
    builder.embedded_page_metric_fields = frozenset({"num_sessions"})
    builder.build_requested_page_metric_queries.return_value = []

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics_cls:
        metrics = manager._read_page_metrics(
            [{"end_user_id": end_user_id}],
            builder,
            ReadDeadline.start(9_500),
        )

    assert metrics == {}
    builder.build_requested_page_metric_queries.assert_called_once_with(
        [end_user_id], frozenset()
    )
    analytics_cls.assert_not_called()


def test_exact_cursor_builder_embeds_requested_num_sessions_in_usage_replay():
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    candidate = _candidate(0, now=now)
    candidate_id = str(candidate["end_user_id"])
    manager = _manager(requested_columns=["num_sessions"])
    analytics = MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[])

    with (
        patch(
            "tracer.services.users_list_manager.V2AnalyticsQueryService",
            return_value=analytics,
        ),
        patch.object(manager, "_enrich_rows"),
        patch(
            "tracer.services.users_list_manager.UserListQueryBuilderV2"
        ) as builder_cls,
    ):
        builder = builder_cls.return_value
        builder.build_candidate_page_query.return_value = ("SELECT 1", {})
        builder.format_rows.return_value = {
            "table": [{"end_user_id": candidate_id, "num_sessions": 2}],
            "total_count": 1,
        }
        rows = manager._read_exact_candidate_rows(
            candidate_ids=[candidate_id],
            candidate_scan_ids=[candidate_id],
            candidate_end_user_id_map={candidate_id: candidate_id},
            frozen_filters=[],
            window_start=now - timedelta(days=365),
            window_end=now,
            deadline=ReadDeadline.start(9_500),
        )

    assert builder_cls.call_args.kwargs["include_num_sessions"] is True
    assert analytics.execute_ch_query.call_args.kwargs["settings"]["max_threads"] == 8
    assert rows[0]["num_sessions_is_approximate"] is True


@pytest.mark.parametrize("filter_column", ["num_sessions", "avg_session_duration"])
def test_session_metric_filter_disables_approximate_embedded_session_count(
    filter_column,
):
    manager = _manager(
        requested_columns=["num_sessions"],
        filters=[
            {
                "column_id": filter_column,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                },
            }
        ],
    )

    assert manager.approximate_num_sessions is False
    builder = manager._exact_candidate_builder(
        candidate_ids=[str(uuid.uuid4())],
        candidate_scan_ids=None,
        candidate_end_user_id_map=None,
        frozen_filters=[],
    )
    assert builder.include_num_sessions is False
    assert builder.embedded_page_metric_fields == frozenset()


def test_avg_session_duration_metric_keeps_exact_time_state():
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
    )

    queries = builder.build_requested_page_metric_queries(
        [str(uuid.uuid4())], {"avg_session_duration"}
    )

    assert len(queries) == 1
    sql, _, fields = queries[0]
    assert fields == ("avg_session_duration",)
    assert "argMax(tuple(end_time), _version)" in sql
    assert "argMax(start_time, _version)" in sql
    assert "duration_seconds" in sql
    assert "round(avg(duration_seconds), 2) AS avg_session_duration" in sql


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
