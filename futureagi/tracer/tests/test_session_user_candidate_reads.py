"""Candidate-first session/user list regression and CH25 execution coverage."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders.user_list import (
    UnsupportedBoundedUserListQuery,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)


def _window(now: datetime) -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (now - timedelta(days=1)).isoformat(),
                    (now + timedelta(days=1)).isoformat(),
                ],
            },
        }
    ]


def _has_eval_filter(value: bool | str) -> dict:
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _has_annotation_filter(value: bool | str) -> dict:
    return {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


@pytest.mark.unit
def test_user_default_page_replays_latest_state_before_pagination():
    builder = UserListQueryBuilderV2(
        organization_id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        limit=25,
        offset=50,
        filters=[],
    )

    page_sql, params = builder.build_candidate_page_query()
    physical_sql, physical_params = builder.build_physical_user_presence_query()
    metrics_sql, metrics_params = builder.build_page_metrics_query([str(uuid.uuid4())])
    combined_sql, _ = builder.build()

    assert builder.supports_candidate_first_page() is True
    assert "FROM spans" in physical_sql
    assert "argMax" not in physical_sql
    assert "LIMIT 1" in physical_sql
    assert physical_params["project_id"] == builder.project_id
    assert "candidate_users AS" in page_sql
    assert "FROM spans AS sp FINAL" in page_sql
    assert "candidate_span_identities AS" not in page_sql
    assert "latest_candidate_spans AS" not in page_sql
    assert "sp.is_deleted = 0" in page_sql
    assert "span_user_rollup" not in page_sql
    assert "span_user_rollup" not in combined_sql
    cursor_seed_sql, cursor_seed_params = builder.build_dimension_candidate_query(
        limit=26
    )
    assert "FROM span_user_rollup AS rollup" in cursor_seed_sql
    assert (
        cursor_seed_params["candidate_window_start"]
        < cursor_seed_params["candidate_window_end"]
    )
    assert "LIMIT %(limit)s OFFSET %(offset)s" in page_sql
    assert params["limit"] == 25
    assert params["offset"] == 50
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in metrics_sql
    assert "(project_id, trace_id, id, start_time) IN" in metrics_sql
    assert "eu_survivor_map" in metrics_sql
    assert "ts_survivor_map" in metrics_sql
    assert len(metrics_params["candidate_end_user_ids"]) == 1


@pytest.mark.unit
def test_user_detail_metrics_uses_only_target_and_touched_remap_groups():
    end_user_id = str(uuid.uuid4())
    builder = UserListQueryBuilderV2(
        organization_id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        end_user_id=end_user_id,
        filters=[],
    )

    sql, params = builder.build()
    compact_sql = " ".join(sql.split())

    assert params["end_user_id"] == end_user_id
    assert "eval_eu_ids" not in params
    assert "%(eval_eu_ids)s" not in sql
    assert compact_sql.index("filtered_end_users_raw AS") < compact_sql.index(
        "eu_survivor_map AS"
    )
    assert "candidate_trace_session_ids AS" in compact_sql
    assert "WHERE old_id IN ( SELECT end_user_id FROM filtered_end_users_raw )" in (
        compact_sql
    )
    assert (
        "WHERE old_id IN ( SELECT trace_session_id "
        "FROM candidate_trace_session_ids )" in compact_sql
    )
    assert "OVER (PARTITION BY new_id)" not in compact_sql


@pytest.mark.unit
def test_user_raw_metric_sort_fails_closed_instead_of_running_legacy_scan():
    builder = UserListQueryBuilderV2(
        organization_id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        limit=25,
        offset=0,
        sort_params=[{"column_id": "num_sessions", "direction": "desc"}],
    )

    assert builder.supports_candidate_first_page() is False
    with pytest.raises(UnsupportedBoundedUserListQuery, match="bounded query path"):
        builder.build()


@pytest.mark.unit
def test_session_candidate_page_is_physical_latest_and_page_metrics_are_scoped():
    project_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=project_id,
        page_number=3,
        page_size=25,
        filters=[],
    )

    page_sql, page_params = builder.build_candidate_page_query()
    metrics_sql, metrics_params = builder.build_page_metrics_query([session_id])

    assert builder.supports_candidate_first_page() is True
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in page_sql
    assert "count() OVER() AS total_count" in page_sql
    assert "ORDER BY session_start DESC, session_id DESC" in page_sql
    assert "candidate_root_raw_session_ids AS" in page_sql
    assert "candidate_root_session_group_ids AS" in page_sql
    assert "FROM trace_session_id_remap AS remap_match FINAL" in page_sql
    assert "SELECT raw_session_id FROM candidate_root_raw_session_ids" in page_sql
    assert "WHERE remap.new_id IN (" in page_sql
    assert "OVER (PARTITION BY new_id)" not in page_sql
    assert page_params["limit"] == 26
    assert page_params["offset"] == 75
    assert metrics_params["candidate_session_ids"] == (session_id,)
    assert metrics_params["candidate_filter_session_id_array"] == [session_id]
    assert "candidate_root_identities AS" in metrics_sql
    assert "(project_id, trace_id, id, start_time) IN" in metrics_sql
    assert "trace_session_id_remap" in metrics_sql
    assert "PREWHERE old_id IN (" in metrics_sql
    assert "WHERE new_id IN (" in metrics_sql
    assert "OVER (PARTITION BY new_id)" not in metrics_sql

    count_sql, _ = builder.build_candidate_count_query()
    assert "SELECT count() AS total" in count_sql
    assert "sum(cost)" not in count_sql
    assert "candidate_root_raw_session_ids AS" in count_sql
    assert "OVER (PARTITION BY new_id)" not in count_sql


@pytest.mark.unit
def test_default_session_cursor_seeds_from_session_rollup_before_exact_replay():
    now = datetime(2026, 8, 11, 12, 0)
    project_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=project_id,
        page_size=25,
        filters=_window(now),
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=now - timedelta(days=1),
        slice_end=now + timedelta(days=1),
        limit=26,
        before_start_time=now,
        before_id=str(uuid.uuid4()),
    )

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.filter_candidate_seed_proves_result_order() is True
    assert builder.filter_candidate_seed_is_sampled() is True
    assert builder.recommended_filter_cursor_seed_batch_size() == 101
    assert builder.recommended_filter_initial_slice_width() == timedelta(days=2)
    assert "FROM spans_per_session" in sql
    assert "minMerge(first_seen) AS start_time" in sql
    assert "GROUP BY trace_session_id" in sql
    assert "ORDER BY start_time DESC, toString(session_id) DESC" in sql
    assert "FROM spans AS" not in sql
    assert "trace_session_id_remap" not in sql
    assert "max_rows_to_read" not in sql
    assert params["project_id"] == project_id
    assert params["filter_seed_limit"] == 26
    assert params["filter_slice_start_us"] < params["filter_slice_end_us"]
    assert "filter_before_start_time_us" in params


@pytest.mark.unit
def test_default_session_rollup_classifier_preserves_finite_raw_seed_order():
    now = datetime(2026, 8, 11, 12, 0)
    raw_session_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        page_size=25,
        filters=_window(now),
        bounded_internal_scan=True,
    )
    seed_rows = [
        {
            "session_id": raw_session_ids[0],
            "start_time": now - timedelta(hours=1),
        },
        {
            "session_id": raw_session_ids[1],
            "start_time": now - timedelta(hours=2),
        },
    ]

    sql, params = builder.build_filter_match_query_from_seed_rows(seed_rows)

    assert "candidate_seed_order_rows AS" in sql
    assert "candidate_group_rollup_order_rows AS" in sql
    assert "FROM spans_per_session" in sql
    assert "SELECT any_id FROM ts_survivor_map" in sql
    assert "resolved_candidate_seed_order AS" in sql
    assert "LEFT JOIN ts_survivor_map AS seed_ts_remap" in sql
    assert "INNER JOIN candidate_seed_order USING (session_id)" in sql
    assert "AS _seed_order_start" in sql
    assert "AS _seed_order_id" in sql
    assert "ORDER BY _seed_order_start DESC, _seed_order_id DESC" in sql
    assert "PREWHERE old_id IN (" in sql
    assert "WHERE new_id IN (" in sql
    assert "OVER (PARTITION BY new_id)" not in sql
    assert "max_rows_to_read" not in sql
    assert params["candidate_seed_order_ids"] == raw_session_ids
    assert len(params["candidate_seed_order_start_us"]) == 2
    assert builder.bounded_filter_row_order_token(seed_rows[0]) == raw_session_ids[0]
    classified_row = {
        "session_id": str(uuid.uuid4()),
        "_seed_order_id": raw_session_ids[0],
    }
    assert builder.bounded_filter_row_order_token(classified_row) == raw_session_ids[0]


@pytest.mark.unit
def test_session_aggregate_filter_and_sort_use_narrow_exact_candidate_shape():
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 10,
                },
            }
        ],
        sort_params=[{"column_id": "total_tokens", "direction": "desc"}],
    )

    sql, params = builder.build_candidate_page_query()

    assert builder.supports_candidate_first_page() is True
    assert "argMax(tuple(total_tokens), _version).1 AS latest_total_tokens" in sql
    assert "sum(total_tokens) AS total_tokens" in sql
    assert "HAVING total_tokens > %(having_" in sql
    assert "ORDER BY total_tokens DESC, session_id DESC" in sql
    assert 10 in params.values()


@pytest.mark.unit
def test_arbitrary_span_filter_remains_controlled_unsupported():
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-5",
                },
            }
        ],
    )

    assert builder.supports_candidate_first_page() is False
    with pytest.raises(ValueError, match="not candidate-page safe"):
        builder.build_candidate_page_query()


@pytest.mark.unit
def test_attribute_bulk_filter_uses_bounded_seed_and_latest_candidate_classifier():
    now = datetime(2026, 7, 31, 12, 0)
    session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["Rejected"],
                },
            },
        ],
        bounded_internal_scan=True,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=now - timedelta(minutes=5),
        slice_end=now,
        limit=200,
    )
    match_sql, match_params = builder.build_filter_match_query([session_id])

    assert builder.supports_bounded_filter_scan() is True
    assert seed_params["filter_seed_limit"] == 200
    assert "SELECT session_id, start_time" in seed_sql
    assert "LIMIT %(filter_seed_limit)s" in seed_sql
    assert "trace_session_id_remap" not in seed_sql
    assert " FINAL" not in seed_sql
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in match_sql
    assert "argMax(mapContains(attrs_string" in match_sql
    assert "latest_attr_value_0" in match_sql
    assert "latest_filter_key_0" not in seed_params
    assert "mapKeys(attrs_string)" not in seed_sql
    candidate_roots = match_sql.split("candidate_root_identities AS (", 1)[1].split(
        "latest_roots AS (", 1
    )[0]
    assert "mapKeys(attrs_string)" not in candidate_roots
    assert "candidate_scalar_span_identities AS" in match_sql
    assert "latest_candidate_scalar_spans AS" in match_sql
    assert "resolved_candidate_scalar_spans AS" in match_sql
    assert "matching_scalar_sessions AS" in match_sql
    # Root replay remains the source of session ordering/aggregates. Scalar
    # filters replay every span in the finite candidate sessions, then roll
    # each independently up to session membership.
    assert "latest_attr_exists_0 AND" in match_sql
    assert "HAVING countIf(latest_attr_exists_0 AND" in match_sql
    assert (
        "session_id IN (SELECT session_id FROM matching_scalar_sessions)" in match_sql
    )
    assert "min(start_time) AS session_start" in match_sql
    assert "candidate_filter_session_id_array" in match_sql
    assert match_params["candidate_filter_session_ids"] == (session_id,)
    assert match_params["candidate_filter_session_id_array"] == [session_id]
    assert "candidate_filter_sessions AS" in match_sql
    assert "candidate_raw_session_id = candidate_ts_remap.any_id" in match_sql
    assert "SELECT session_id FROM candidate_filter_sessions" in match_sql
    assert "WHERE survivor_id IN (" in match_sql
    assert ("rejected",) in match_params.values()


@pytest.mark.unit
def test_raw_new_session_seed_classifier_expands_group_and_keeps_all_filters():
    now = datetime(2026, 7, 31, 12, 0)
    new_session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rejected",
                },
            },
            {
                "column_id": "customer.region",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "US",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=now - timedelta(minutes=5),
        slice_end=now,
        limit=200,
    )
    match_sql, match_params = builder.build_filter_match_query([new_session_id])

    # The per-slice query is an ordered root superset: no remap FINAL or scalar
    # witness can hide a session whose qualifying value lives on a child span.
    assert "trace_session_id_remap" not in seed_sql
    assert "attrs_string" not in seed_sql
    assert "latest_filter_key_0" not in seed_params
    assert "latest_filter_key_1" not in seed_params

    # Exact classification resolves a raw new/old candidate to its survivor,
    # expands that survivor back to every group member, and evaluates both
    # customer filters against latest state before returning the canonical ID.
    assert "trace_session_id_remap FINAL" in match_sql
    assert "argMin(old_id, toString(old_id)) OVER" not in match_sql
    assert match_sql.count("FROM trace_session_id_remap FINAL") == 2
    assert "FROM trace_sessions FINAL" not in match_sql
    assert "candidate_target_new_ids AS" in match_sql
    assert "PREWHERE old_id IN (" in match_sql
    assert "WHERE new_id IN (" in match_sql
    assert "arrayConcat(groupArray(old_id), [new_id])" in match_sql
    assert "SELECT arrayJoin(group_ids) AS any_id" in match_sql
    assert "AS candidate_session_pairs" in match_sql
    assert "SELECT arrayJoin(candidate_session_pairs) AS pair" in match_sql
    assert "AS Array(UUID)" in match_sql
    assert "candidate_filter_sessions AS" in match_sql
    assert "candidate_raw_session_id = candidate_ts_remap.any_id" in match_sql
    assert "SELECT any_id" in match_sql
    assert "SELECT session_id FROM candidate_filter_sessions" in match_sql
    assert "latest_attr_exists_0 AND" in match_sql
    assert "latest_attr_exists_1 AND" in match_sql
    assert "countIf(latest_attr_exists_0 AND" in match_sql
    assert "countIf(latest_attr_exists_1 AND" in match_sql
    assert (
        "session_id IN (SELECT session_id FROM matching_scalar_sessions)" in match_sql
    )
    assert match_params["candidate_filter_session_id_array"] == [new_session_id]
    assert match_params["latest_filter_param_0"] == "rejected"
    assert match_params["latest_filter_param_1"] == "us"


@pytest.mark.unit
def test_session_scalar_filters_match_any_latest_span_independently():
    now = datetime(2026, 7, 31, 12, 0)
    session_id = str(uuid.uuid4())
    filters = [
        *_window(now),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "categorical",
                "filter_op": "equals",
                "filter_value": "OK",
            },
        },
        {
            "column_id": "node_type",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "categorical",
                "filter_op": "equals",
                "filter_value": "llm",
            },
        },
        {
            "column_id": "retries",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 2,
            },
        },
    ]
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=filters,
        bounded_internal_scan=True,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=now - timedelta(minutes=5),
        slice_end=now,
        limit=200,
    )
    match_sql, match_params = builder.build_filter_match_query([session_id])

    assert "status" not in seed_sql
    assert "attrs_number" not in seed_sql
    assert not any(key.startswith("latest_filter_") for key in seed_params)
    root_scan = match_sql.split("candidate_root_identities AS (", 1)[1].split(
        "latest_roots AS (", 1
    )[0]
    assert "attrs_number" not in root_scan
    scalar_scan = match_sql.split("latest_candidate_scalar_spans AS (", 1)[1].split(
        "resolved_candidate_scalar_spans AS (", 1
    )[0]
    assert "argMax(tuple(status), _version).1 AS latest_column_value_0" in scalar_scan
    assert "argMax(observation_type, _version) AS latest_column_value_1" in scalar_scan
    assert "argMax(mapContains(attrs_number" in scalar_scan
    assert "HAVING countIf(" in match_sql
    assert match_sql.count("countIf(") == 3
    assert (
        "session_id IN (SELECT session_id FROM matching_scalar_sessions)" in match_sql
    )
    assert match_params["latest_filter_param_0"] == "ok"
    assert match_params["latest_filter_param_1"] == "llm"
    assert match_params["latest_filter_param_2"] == 2.0


@pytest.mark.unit
def test_session_positive_attribute_exposes_bounded_any_span_anchor():
    now = datetime(2026, 7, 31, 12, 0)
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rejected",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    assert builder.supports_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() == 900
    assert builder.recommended_filter_anchor_probe_strata() == 4
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() == (
        192 * 1024 * 1024
    )

    sql, params = builder.build_filter_anchor_probe(
        limit=64,
        slice_start=now - timedelta(days=1),
        slice_end=now,
    )
    assert "parent_span_id" not in sql
    assert "LIMIT 1 BY trace_session_id" in sql
    assert "LIMIT %(filter_anchor_limit)s" in sql
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in sql
    assert params["filter_anchor_limit"] == 64
    assert params["latest_filter_key_0"] == "final_status"
    assert params["latest_filter_param_0"] == "rejected"

    # A partitioned 64-row probe can enter its final stratum with one shared
    # sentinel slot left.  That one-row statement is valid and reaching it
    # forces the exact ordered fallback.
    _one_row_sql, one_row_params = builder.build_filter_anchor_probe(
        limit=1,
        slice_start=now - timedelta(hours=1),
        slice_end=now,
    )
    assert one_row_params["filter_anchor_limit"] == 1


@pytest.mark.unit
def test_sparse_session_any_span_anchor_exhausts_then_classifies_exactly():
    now = datetime(2026, 7, 31, 12, 0)
    session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rejected",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    class _SparseAnchorExecutor:
        def __init__(self):
            self.anchor_calls = 0
            self.seed_calls = 0
            self.classify_calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if "LIMIT 1 BY trace_session_id" in query:
                self.anchor_calls += 1
                rows = (
                    [{"session_id": session_id, "start_time": now - timedelta(hours=1)}]
                    if self.anchor_calls == 1
                    else []
                )
                return SimpleNamespace(data=rows)
            if "matching_scalar_sessions AS" in query:
                self.classify_calls += 1
                assert params["candidate_filter_session_ids"] == (session_id,)
                return SimpleNamespace(
                    data=[
                        {
                            "session_id": session_id,
                            "start_time": now - timedelta(hours=2),
                        }
                    ]
                )
            if "WITH seed_sessions AS" in query:
                self.seed_calls += 1
            raise AssertionError("sparse exhaustive anchor must not run broad roots")

    executor = _SparseAnchorExecutor()
    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=builder.filters,
        key_field="session_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_candidates=200,
        classify_batch_size=50,
    )

    assert page.complete is True
    assert [row["session_id"] for row in page.rows] == [session_id]
    assert executor.anchor_calls == 4
    assert executor.classify_calls == 1
    assert executor.seed_calls == 0


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["not_equals", "is_null"])
def test_session_negative_attribute_does_not_use_raw_anchor(operation):
    now = datetime(2026, 7, 31, 12, 0)
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": operation,
    }
    if operation != "is_null":
        config["filter_value"] = "Rejected"
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {"column_id": "final_status", "filter_config": config},
        ],
        bounded_internal_scan=True,
    )

    assert builder.supports_filter_anchor_probe() is False
    assert builder.recommended_filter_anchor_probe_limit() is None


@pytest.mark.unit
def test_session_sampled_internal_lane_does_not_anchor_raw_aliases():
    now = datetime(2026, 7, 31, 12, 0)
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rejected",
                },
            },
        ],
        bounded_internal_scan=True,
        bounded_sampling_salt="stable-salt",
        bounded_sampling_rate=50,
    )

    assert builder.supports_filter_anchor_probe() is False


class _RawAliasSessionBuilder:
    def __init__(self, rows, canonical_rows, *, start, end):
        self.rows = rows
        self.canonical_rows = canonical_rows
        self.start = start
        self.end = end

    def parse_time_range(self, _filters):
        return self.start, self.end

    @staticmethod
    def filter_seed_proves_result_order():
        return True

    @staticmethod
    def recommended_filter_classify_batch_size():
        return 2

    def build_filter_seed_page(
        self,
        *,
        slice_start,
        slice_end,
        limit,
        before_start_time=None,
        before_id=None,
    ):
        return "seed", {
            "slice_start": slice_start,
            "slice_end": slice_end,
            "limit": limit,
            "before_start_time": before_start_time,
            "before_id": before_id,
        }

    @staticmethod
    def build_filter_match_query(candidate_ids):
        return "match", {"candidate_ids": tuple(candidate_ids)}


class _RawAliasSessionExecutor:
    def __init__(self, builder):
        self.builder = builder
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params))
        if query == "seed":
            rows = [
                row
                for row in self.builder.rows
                if params["slice_start"] <= row["start_time"] < params["slice_end"]
            ]
            before = params["before_start_time"]
            if before is not None:
                boundary = (before, str(params["before_id"]))
                rows = [
                    row
                    for row in rows
                    if (row["start_time"], str(row["session_id"])) < boundary
                ]
            rows = sorted(
                rows,
                key=lambda row: (row["start_time"], str(row["session_id"])),
                reverse=True,
            )[: params["limit"]]
        else:
            rows_by_canonical = {}
            for raw_id in params["candidate_ids"]:
                row = self.builder.canonical_rows.get(raw_id)
                if row is not None:
                    rows_by_canonical[row["session_id"]] = row
            rows = list(rows_by_canonical.values())
        return SimpleNamespace(data=rows)


@pytest.mark.unit
def test_raw_alias_duplicates_keep_page_one_and_page_n_disjoint():
    end = datetime(2026, 7, 31, 12, 0)
    start = end - timedelta(hours=1)
    canonical = {
        "new-a": {"session_id": "old-a", "start_time": end - timedelta(minutes=1)},
        "old-a": {"session_id": "old-a", "start_time": end - timedelta(minutes=1)},
        "session-b": {
            "session_id": "session-b",
            "start_time": end - timedelta(minutes=2),
        },
        "session-c": {
            "session_id": "session-c",
            "start_time": end - timedelta(minutes=3),
        },
        "session-d": {
            "session_id": "session-d",
            "start_time": end - timedelta(minutes=4),
        },
    }
    builder = _RawAliasSessionBuilder(
        [
            {"session_id": "new-a", "start_time": end - timedelta(seconds=30)},
            *canonical.values(),
        ],
        canonical,
        start=start,
        end=end,
    )

    pages = []
    for page_number in (0, 1):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=_RawAliasSessionExecutor(builder),
            filters=_window(end),
            key_field="session_id",
            page_number=page_number,
            page_size=2,
            deadline_ms=5_000,
            max_candidates=200,
        )
        assert page.complete is True
        pages.append([row["session_id"] for row in page.rows])

    assert pages == [["old-a", "session-b"], ["session-c", "session-d"]]
    assert set(pages[0]).isdisjoint(pages[1])


@pytest.mark.unit
def test_raw_session_seed_crosses_empty_recent_slices_to_late_match():
    end = datetime(2026, 7, 31, 12, 0)
    start = end - timedelta(days=365)
    late = {"session_id": "late-session", "start_time": start + timedelta(days=20)}
    builder = _RawAliasSessionBuilder(
        [late],
        {"late-session": late},
        start=start,
        end=end,
    )
    executor = _RawAliasSessionExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=_window(end),
        key_field="session_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_candidates=200,
    )

    seed_calls = [params for query, params in executor.calls if query == "seed"]
    assert page.complete is True
    assert [row["session_id"] for row in page.rows] == ["late-session"]
    assert len(seed_calls) > 1
    assert seed_calls[-1]["slice_start"] == start
    assert all(
        newer["slice_start"] == older["slice_end"]
        for newer, older in zip(seed_calls, seed_calls[1:], strict=False)
    )


@pytest.mark.unit
def test_session_eval_seed_allows_shared_512_rows_but_classifier_stays_at_200():
    now = datetime(2026, 7, 31, 12, 0)
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[*_window(now)],
        bounded_internal_scan=True,
    )

    _, seed_params = builder.build_filter_seed_page(
        slice_start=now - timedelta(minutes=5),
        slice_end=now,
        limit=512,
    )

    assert seed_params["filter_seed_limit"] == 512
    with pytest.raises(ValueError, match="between 1 and 512"):
        builder.build_filter_seed_page(
            slice_start=now - timedelta(minutes=5),
            slice_end=now,
            limit=513,
        )
    with pytest.raises(ValueError, match="exceeds bounded limit"):
        builder.build_filter_match_query([str(uuid.uuid4()) for _ in range(201)])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_value", "membership_op"),
    [(False, "NOT IN"), ("false", "NOT IN"), (True, "IN")],
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_session_has_eval_is_finite_latest_state_and_page_n_safe(
    filter_value: bool | str,
    membership_op: str,
):
    now = datetime(2026, 7, 31, 12, 0)
    candidate_session_id = str(uuid.uuid4())
    eval_config_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[*_window(now), _has_eval_filter(filter_value)],
        eval_config_ids=[eval_config_id],
        bounded_internal_scan=True,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=now - timedelta(days=1),
        slice_end=now + timedelta(days=1),
        limit=50,
        before_start_time=now,
        before_id=str(uuid.uuid4()),
    )
    sql, params = builder.build_filter_match_query([candidate_session_id])

    assert builder.supports_bounded_filter_scan() is True
    assert builder.bounded_filter_degraded_error_code() is None
    assert "tracer_eval_logger" not in seed_sql
    assert seed_params["filter_before_start_time"] == now
    assert "candidate_filter_sessions AS" in sql
    assert "candidate_eval_trace_ids AS" in sql
    assert "SELECT DISTINCT trace_id" in sql
    assert "WHERE isNotNull(trace_id)" in sql
    assert "toUUIDOrNull(trace_id)" not in sql
    assert "FROM tracer_eval_logger_v2 AS eval_scan" in sql
    assert "eval_scan.custom_eval_config_id IN" in sql
    assert params["session_project_eval_config_ids"] == (eval_config_id,)
    assert (
        "eval_scan.trace_id IN (\n                SELECT trace_id "
        "FROM candidate_eval_trace_ids" in sql
    )
    assert (
        "eval_scan.created_at >= "
        "fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC') - INTERVAL 7 DAY" in sql
    )
    assert "ORDER BY eval_scan._version DESC" in sql
    assert "LIMIT 1 BY eval_scan.id" in sql
    assert "latest_eval.is_deleted = 0" in sql
    assert "tracer_eval_logger_v2 FINAL" not in sql
    assert (
        f"trace_id {membership_op} "
        "(SELECT trace_id FROM live_candidate_eval_trace_ids)" in sql
    )
    if membership_op == "IN":
        assert (
            "trace_id NOT IN (SELECT trace_id FROM live_candidate_eval_trace_ids)"
            not in sql
        )
    assert params["candidate_filter_session_ids"] == (candidate_session_id,)
    assert params["bounded_match_limit"] == 1


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
def test_session_has_eval_false_combines_before_exact_session_aggregation():
    now = datetime(2026, 7, 31, 12, 0)
    candidate_session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        eval_config_ids=[str(uuid.uuid4())],
        filters=[
            *_window(now),
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rejected",
                },
            },
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 10,
                },
            },
            _has_eval_filter(False),
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query([candidate_session_id])

    sessions_cte = sql.split("sessions AS (", 1)[1]
    assert "attrs_string" in sql
    assert (
        "trace_id NOT IN (SELECT trace_id FROM live_candidate_eval_trace_ids)"
        in sessions_cte
    )
    assert sessions_cte.index("trace_id NOT IN") < sessions_cte.index(
        "GROUP BY session_id"
    )
    assert "HAVING total_tokens >" in sessions_cte
    assert "eval_scan._peerdb_version" in sql
    assert "eval_scan._version" not in sql
    assert "latest_eval._peerdb_is_deleted = 0" in sql
    assert "rejected" in params.values()


@pytest.mark.unit
def test_session_has_eval_unknown_configs_are_resolved_once_for_all_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy/public session callers cannot repeat one PG lookup per batch."""

    from tracer.models.custom_eval_config import CustomEvalConfig

    project_id = str(uuid.uuid4())
    config_id = str(uuid.uuid4())
    filter_calls = []

    class _ConfigQuery:
        @staticmethod
        def values_list(*_args, **_kwargs):
            return [config_id]

    def config_filter(**kwargs):
        filter_calls.append(kwargs)
        return _ConfigQuery()

    monkeypatch.setattr(CustomEvalConfig.objects, "filter", config_filter)
    builder = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=[_has_eval_filter(True)],
        bounded_internal_scan=True,
    )

    for candidate_id in (str(uuid.uuid4()), str(uuid.uuid4())):
        sql, params = builder.build_filter_match_query([candidate_id])
        assert "eval_scan.custom_eval_config_id IN" in sql
        assert params["session_project_eval_config_ids"] == (config_id,)

    assert filter_calls == [
        {"project_id__in": [project_id], "deleted": False},
    ]


@pytest.mark.unit
def test_session_has_annotation_uses_only_candidate_session_trace_ids():
    now = datetime(2026, 7, 31, 12, 0)
    candidate_session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[*_window(now), _has_annotation_filter(False)],
        bounded_internal_scan=True,
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=now - timedelta(days=1),
        slice_end=now + timedelta(days=1),
        limit=50,
    )
    sql, params = builder.build_filter_match_query([candidate_session_id])

    assert builder.supports_bounded_filter_scan() is True
    assert builder.bounded_filter_degraded_error_code() is None
    assert "model_hub_score" not in seed_sql
    assert "candidate_relational_trace_ids AS" in sql
    assert "SELECT DISTINCT toString(trace_id) AS trace_id" in sql
    assert "FROM model_hub_score AS s FINAL" in sql
    assert "(SELECT trace_id FROM candidate_relational_trace_ids)" in sql
    assert "%(session_relational_trace_ids)s" not in sql
    sessions_cte = sql.split("sessions AS (", 1)[1]
    assert "trace_id NOT IN" in sessions_cte
    assert sessions_cte.index("trace_id NOT IN") < sessions_cte.index(
        "GROUP BY session_id"
    )
    assert params["candidate_filter_session_ids"] == (candidate_session_id,)


@pytest.mark.unit
def test_session_annotation_leaves_combine_with_namespaced_params():
    now = datetime(2026, 7, 31, 12, 0)
    label_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            _has_annotation_filter(True),
            {
                "column_id": label_id,
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "helpful",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query([str(uuid.uuid4())])

    assert sql.count("(SELECT trace_id FROM candidate_relational_trace_ids)") >= 2
    assert sql.count("FROM model_hub_score AS s FINAL") >= 2
    assert "%(session_relational_trace_ids)s" not in sql
    assert label_id in params.values()
    assert "helpful" in params.values()
    assert any(key.startswith("session_relational_1_ann_label_") for key in params)
    assert any(key.startswith("session_relational_1_ann_") for key in params)


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_session_eval_value_filter_uses_candidate_trace_cte(monkeypatch):
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    class _Values(list):
        def first(self):
            return self[0] if self else None

    config_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())
    config_query = mock.Mock()
    config_query.exists.return_value = True
    config_query.filter.return_value = config_query
    config_query.values_list.side_effect = lambda field, **_kwargs: _Values(
        [template_id] if field == "eval_template_id" else [config_id]
    )
    config_manager = mock.Mock()
    config_manager.filter.return_value = config_query

    template_values = mock.Mock()
    template_values.first.return_value = {"config": {"output": "SCORE"}}
    template_query = mock.Mock()
    template_query.values.return_value = template_values
    template_manager = mock.Mock()
    template_manager.filter.return_value = template_query
    monkeypatch.setattr(CustomEvalConfig, "objects", config_manager)
    monkeypatch.setattr(EvalTemplate, "no_workspace_objects", template_manager)

    now = datetime(2026, 7, 31, 12, 0)
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": config_id,
                "filter_config": {
                    "col_type": "EVAL_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 50,
                },
            },
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query([str(uuid.uuid4())])

    assert "candidate_relational_trace_ids AS" in sql
    assert "FROM tracer_eval_logger_v2 AS eval_scan" in sql
    assert "toString(eval_scan.trace_id) IN (" in sql
    assert "SELECT trace_id FROM candidate_relational_trace_ids" in sql
    assert "%(session_relational_trace_ids)s" not in sql
    assert params["session_relational_0_eval_cfg_1"] == (config_id,)
    assert params["session_relational_0_eval_2"] == 0.5


@pytest.mark.unit
def test_org_session_has_annotation_branches_by_project_and_disjoint_labels():
    now = datetime(2026, 7, 31, 12, 0)
    project_a = str(uuid.uuid4())
    project_b = str(uuid.uuid4())
    label_a = str(uuid.uuid4())
    label_b = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_ids=[project_a, project_b],
        annotation_label_ids_by_project={
            project_a: [label_a],
            project_b: [label_b],
        },
        filters=[*_window(now), _has_annotation_filter(True)],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query([str(uuid.uuid4())])

    assert builder.supports_bounded_filter_scan() is True
    assert builder.bounded_filter_degraded_error_code() is None
    assert "SELECT DISTINCT project_id, toString(trace_id) AS trace_id" in sql
    assert sql.count("FROM model_hub_score AS s FINAL") >= 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert (
        sql.count(
            "SELECT trace_id FROM candidate_relational_trace_ids WHERE project_id ="
        )
        >= 2
    )
    assert params["session_relational_0_0_lbl_1"] == label_a
    assert params["session_relational_1_0_lbl_1"] == label_b
    assert "session_relational_0_0_lbl_2" not in params
    assert "session_relational_1_0_lbl_2" not in params
    assert params["session_relational_0_0_project_id"] == project_a
    assert params["session_relational_1_0_project_id"] == project_b
    assert params["session_relational_0_outer_project_id"] == project_a
    assert params["session_relational_1_outer_project_id"] == project_b
    assert "candidate_session_project_counts AS" in sql
    assert "uniqExact(project_id) AS project_count" in sql
    assert "max(project_count) AS project_count" in sql
    assert "any(project_id) AS project_id" in sql
    assert "SELECT session_id, session_start AS start_time" in sql
    assert ", project_id, project_count" in sql
    # The OR-ed project branches are one membership unit and cannot bypass
    # session/user predicates through SQL AND/OR precedence.
    assert "AND ((project_id =" in sql


@pytest.mark.unit
def test_default_org_session_page_exposes_global_collision_guard():
    now = datetime(2026, 7, 31, 12, 0)
    builder = SessionListQueryBuilderV2(
        project_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        filters=_window(now),
    )

    page_sql, _ = builder.build_candidate_page_query()
    count_sql, _ = builder.build_candidate_count_query()

    assert "candidate_session_project_counts AS" in page_sql
    assert "uniqExact(project_id) AS project_count" in page_sql
    assert "any(project_id) AS project_id" in page_sql
    assert "max(project_count) OVER() AS max_project_count" in page_sql
    assert "max(project_count) AS max_project_count" in count_sql


@pytest.mark.unit
def test_org_session_has_annotation_without_project_label_map_fails_closed():
    now = datetime(2026, 7, 31, 12, 0)
    builder = SessionListQueryBuilderV2(
        project_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        filters=[*_window(now), _has_annotation_filter(True)],
        bounded_internal_scan=True,
    )

    assert builder.supports_bounded_filter_scan() is False
    assert (
        builder.bounded_filter_degraded_error_code()
        == "unsupported_relational_session_filter"
    )
    with pytest.raises(ValueError, match="unsupported bounded session filter scan"):
        builder.build_filter_match_query([str(uuid.uuid4())])


@pytest.mark.unit
def test_org_session_has_eval_branches_colliding_trace_ids_by_project(monkeypatch):
    from tracer.models.custom_eval_config import CustomEvalConfig

    now = datetime(2026, 7, 31, 12, 0)
    project_a = str(uuid.uuid4())
    project_b = str(uuid.uuid4())
    config_by_project = {
        project_a: str(uuid.uuid4()),
        project_b: str(uuid.uuid4()),
    }

    class _ConfigQuery:
        def __init__(self, config_ids):
            self.config_ids = config_ids

        def values_list(self, *_args, **_kwargs):
            return list(self.config_ids)

    def _filter_configs(**kwargs):
        project_ids = tuple(str(value) for value in kwargs["project_id__in"])
        return _ConfigQuery([config_by_project[value] for value in project_ids])

    monkeypatch.setattr(CustomEvalConfig.objects, "filter", _filter_configs)
    builder = SessionListQueryBuilderV2(
        project_ids=[project_a, project_b],
        filters=[*_window(now), _has_eval_filter(True)],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query([str(uuid.uuid4())])

    assert "candidate_eval_trace_ids AS" not in sql
    assert sql.count("INNER JOIN spans AS sp") == 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["session_relational_0_0_project_eval_cfg_1"] == (
        config_by_project[project_a],
    )
    assert params["session_relational_1_0_project_eval_cfg_1"] == (
        config_by_project[project_b],
    )
    assert params["session_relational_0_0_project_id"] == project_a
    assert params["session_relational_1_0_project_id"] == project_b


@pytest.mark.unit
def test_session_relational_source_without_candidate_guard_is_rejected():
    class _UnsafeRelationalCompiler:
        QUERY_MODE_TRACE = "trace"

        def __init__(self, **_kwargs):
            pass

        def translate(self, _filters):
            return (
                "trace_id IN (SELECT trace_id FROM model_hub_score)",
                {},
            )

    class _UnsafeSessionBuilder(SessionListQueryBuilderV2):
        _FILTER_BUILDER_CLS = _UnsafeRelationalCompiler

    now = datetime(2026, 7, 31, 12, 0)
    builder = _UnsafeSessionBuilder(
        project_id=str(uuid.uuid4()),
        filters=[
            *_window(now),
            {
                "column_id": str(uuid.uuid4()),
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "helpful",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    with pytest.raises(ValueError, match="missing finite candidate scope"):
        builder.build_filter_match_query([str(uuid.uuid4())])


@pytest.mark.unit
def test_negated_end_user_bulk_filter_is_candidate_session_scoped():
    session_id = str(uuid.uuid4())
    end_user_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_in",
                    "filter_value": [end_user_id],
                },
            }
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query([session_id])

    assert builder.supports_bounded_filter_scan() is True
    assert params["candidate_filter_session_ids"] == (session_id,)
    assert params["candidate_filter_session_id_array"] == [session_id]
    assert params["eu_remap_1"] == (end_user_id,)
    assert "end_user_id NOT IN %(eu_remap_1)s" in sql
    assert "candidate_filter_sessions AS" in sql
    assert sql.count("SELECT session_id FROM candidate_filter_sessions") >= 2
    assert "WHERE survivor_id IN (" in sql
    assert "session_id IN (SELECT session_id FROM matching_user_sessions)" in sql


@pytest.mark.unit
def test_session_message_filter_and_sort_are_applied_before_page():
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "first_message",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "needle",
                },
            }
        ],
        sort_params=[{"column_id": "first_message", "direction": "asc"}],
    )

    sql, params = builder.build_candidate_page_query()

    assert "argMax(tuple(input), _version).1 AS latest_input" in sql
    assert "argMin(input, start_time) AS first_message" in sql
    assert "HAVING first_message ILIKE %(having_" in sql
    assert "ORDER BY first_message ASC, session_id ASC" in sql
    assert "%needle%" in params.values()


@pytest.mark.unit
def test_session_candidate_page_preserves_ascending_time_sort():
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        sort_params=[{"column_id": "created_at", "direction": "asc"}],
    )

    sql, _ = builder.build_candidate_page_query()

    assert "ORDER BY session_start ASC, session_id ASC" in sql


@pytest.mark.unit
def test_session_identity_filters_stay_on_bounded_candidate_path():
    session_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [session_id],
                },
            }
        ],
    )

    sql, params = builder.build_candidate_page_query()

    assert builder.supports_candidate_first_page() is True
    assert params["candidate_filter_session_ids"] == (session_id,)
    assert params["candidate_sess_1"] == (session_id,)
    assert "candidate_root_identities AS" in sql
    assert "session_id IN %(candidate_sess_1)s" in sql


@pytest.mark.unit
def test_public_trace_and_session_filters_share_the_9_5_second_statement_ceiling():
    project_id = str(uuid.uuid4())
    status_filter = {
        "column_id": "status",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["UNSET"],
        },
    }

    trace_builder = TraceListQueryBuilderV2(
        project_id=project_id,
        filters=[status_filter],
    )
    session_builder = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=[status_filter],
    )

    assert trace_builder.recommended_filter_query_timeout_ms() == 9_500
    assert session_builder.recommended_filter_query_timeout_ms() == 9_500


@pytest.mark.unit
def test_cross_project_user_detail_session_query_bounds_both_remaps():
    end_user_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [end_user_id],
                },
            }
        ],
    )

    sql, params = builder.build_candidate_page_query()

    assert builder.supports_candidate_first_page() is True
    assert builder.recommended_filter_query_timeout_ms() == 9_500
    assert builder.supports_filter_anchor_probe() is False
    assert params["candidate_filter_user_ids"] == (end_user_id,)
    assert params["eu_remap_1"] == (end_user_id,)
    assert "OVER (PARTITION BY new_id)" not in sql
    assert "matching_user_raw_sessions AS" in sql
    assert "candidate_user_session_group_ids AS" in sql
    assert "WHERE remap.new_id IN (" in sql
    assert "SELECT new_id FROM candidate_user_session_group_ids" in sql
    assert "old_id IN %(candidate_filter_user_ids)s" in sql
    assert "new_id IN %(candidate_filter_user_ids)s" in sql
    assert "candidate_user_span_identities AS" in sql
    assert "latest_user_spans AS" in sql
    assert "matching_user_sessions AS" in sql
    assert "session_id IN (SELECT session_id FROM matching_user_sessions)" in sql
    assert sql.index("matching_user_sessions AS") < sql.index(
        "candidate_root_identities AS"
    )
    root_seed_sql = sql.split("candidate_root_identities AS (", 1)[1].split(
        "),\n        latest_roots AS (", 1
    )[0]
    assert "trace_session_id IN (" in root_seed_sql
    assert "SELECT session_id FROM matching_user_root_ids" in root_seed_sql
    assert "matching_user_root_ids AS" in sql
    assert "groupUniqArray(user_session_aliases.any_id)" in sql
    assert "LEFT JOIN ts_survivor_map AS user_session_aliases" in sql


@pytest.mark.unit
def test_cross_project_user_detail_trace_query_bounds_dimension_remap():
    now = datetime(2026, 8, 11, 12, 0)
    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    filters = [
        *_window(now),
        {
            "column_id": "user_id",
            "filter_config": {
                "col_type": "TRACE_END_USER",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "cross-project-user",
            },
        },
    ]
    builder = TraceListQueryBuilderV2(
        project_ids=project_ids,
        page_size=25,
        filters=filters,
    )

    seed_sql, seed_params = builder.build_filter_ordered_seed_page(
        slice_start=now - timedelta(days=1),
        slice_end=now + timedelta(days=1),
        limit=26,
    )
    match_sql, match_params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": project_ids[0],
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "start_time": now,
            }
        ]
    )

    assert builder.recommended_filter_query_timeout_ms() == 9_500
    assert builder.supports_filter_anchor_probe() is False
    for sql in (seed_sql, match_sql):
        assert "matching_end_user_ids AS" in sql
        assert "matching_end_user_group_ids AS" in sql
        assert "WHERE remap.new_id IN (" in sql
        assert "SELECT new_id FROM matching_end_user_group_ids" in sql
        assert "OVER (PARTITION BY new_id)" not in sql
    assert tuple(project_ids) in seed_params.values()
    assert tuple(project_ids) in match_params.values()


@pytest.mark.unit
def test_user_detail_selector_uses_deadline_without_row_read_cap():
    now = datetime(2026, 8, 11, 12, 0)

    class _UserDetailBuilder:
        @staticmethod
        def recommended_filter_query_timeout_ms():
            return 30_000

        @staticmethod
        def parse_time_range(_filters):
            return now - timedelta(minutes=5), now

        @staticmethod
        def build_filter_seed_page(**_kwargs):
            return "user detail seed", {}

        @staticmethod
        def build_filter_match_query(_candidate_ids):
            return "user detail classify", {}

    class _Recorder:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, params, timeout_ms, settings))
            return SimpleNamespace(data=[])

    recorder = _Recorder()
    page = read_bounded_filter_page(
        builder=_UserDetailBuilder(),
        analytics=recorder,
        filters=[],
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=30_000,
    )

    assert page.complete is True
    assert len(recorder.calls) == 1
    _, _, timeout_ms, settings = recorder.calls[0]
    assert 3_000 < timeout_ms <= 30_000
    assert "max_rows_to_read" not in settings
    assert settings["max_bytes_to_read"] == django_settings.OBSERVABILITY_LIST_MAX_BYTES
    assert (
        settings["max_memory_usage"]
        == django_settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES
    )
    assert 0 < settings["max_result_rows"] <= 512
    assert settings["max_threads"] == 1


@pytest.mark.unit
def test_positive_end_user_cursor_uses_exact_stable_keyset_query():
    end_user_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    before_session_id = str(uuid.uuid4())
    before_start = datetime(2026, 7, 31, 12, 0, 0, 123456, tzinfo=UTC)
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        page_size=25,
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": end_user_ids,
                },
            }
        ],
        bounded_internal_scan=True,
    )

    first_sql, first_params = builder.build_candidate_cursor_page_query()
    next_sql, next_params = builder.build_candidate_cursor_page_query(
        before_start_time=before_start,
        before_session_id=before_session_id,
    )

    assert builder.supports_candidate_cursor_page() is True
    assert first_params["limit"] == 26
    assert first_params["candidate_filter_user_ids"] == tuple(end_user_ids)
    assert "matching_user_sessions AS" in first_sql
    assert "count() OVER() AS remaining_count" in first_sql
    assert "ORDER BY session_start DESC, session_id DESC" in first_sql
    assert "cursor_before_start_us" not in first_params
    assert "cursor_before_session_id" not in first_params

    assert next_params["cursor_before_start_us"] == 1785499200123456
    assert next_params["cursor_before_session_id"] == before_session_id
    assert "session_start < fromUnixTimestamp64Micro(" in next_sql
    assert "session_id < toUUID(%(cursor_before_session_id)s)" in next_sql
    assert "OFFSET" not in next_sql


@pytest.mark.unit
def test_positive_session_cursor_uses_finite_session_seed():
    session_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        page_size=25,
        filters=[
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": session_ids,
                },
            }
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_candidate_cursor_page_query()

    assert builder.supports_candidate_cursor_page() is True
    assert params["candidate_filter_session_id_array"] == session_ids
    assert params["candidate_sess_1"] == tuple(session_ids)
    assert "candidate_filter_sessions AS" in sql
    assert "candidate_root_raw_session_ids AS" not in sql
    assert "count() OVER() AS remaining_count" in sql


@pytest.mark.unit
def test_positive_session_cursor_can_intersect_user_membership():
    session_id = str(uuid.uuid4())
    end_user_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        page_size=25,
        filters=[
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": session_id,
                },
            },
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [end_user_id],
                },
            },
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_candidate_cursor_page_query()

    assert builder.supports_candidate_cursor_page() is True
    assert params["candidate_filter_session_id_array"] == [session_id]
    assert params["candidate_filter_user_ids"] == (end_user_id,)
    assert "candidate_filter_sessions AS" in sql
    assert "matching_user_sessions AS" in sql
    assert "session_id IN (SELECT session_id FROM matching_user_sessions)" in sql


@pytest.mark.unit
@pytest.mark.parametrize("operator", ["not_equals", "not_in", "is_null", "is_not_null"])
def test_non_positive_session_cursor_keeps_bounded_path(operator):
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": operator,
                    "filter_value": [str(uuid.uuid4())],
                },
            }
        ],
        bounded_internal_scan=True,
    )

    assert builder.supports_candidate_cursor_page() is False


@pytest.mark.unit
@pytest.mark.parametrize("operator", ["not_equals", "not_in", "is_null", "is_not_null"])
def test_non_positive_end_user_cursor_keeps_bounded_path(operator):
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": operator,
                    "filter_value": [str(uuid.uuid4())],
                },
            }
        ],
        bounded_internal_scan=True,
    )

    assert builder.supports_candidate_cursor_page() is False


@pytest.mark.unit
def test_positive_end_user_cursor_with_another_filter_keeps_bounded_path():
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [str(uuid.uuid4())],
                },
            },
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "completed",
                },
            },
        ],
        bounded_internal_scan=True,
    )

    assert builder.supports_candidate_cursor_page() is False


@pytest.mark.unit
def test_negated_end_user_filter_uses_exact_time_scoped_membership():
    excluded_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_in",
                    "filter_value": [excluded_id],
                },
            }
        ],
    )

    sql, params = builder.build_candidate_page_query()

    assert builder.supports_candidate_first_page() is True
    assert params["eu_remap_1"] == (excluded_id,)
    assert "end_user_id NOT IN %(eu_remap_1)s" in sql
    assert "session_id IN (SELECT session_id FROM matching_user_sessions)" in sql
    # A negated predicate must not preseed only the excluded user IDs.
    assert "candidate_filter_user_ids" not in params
    root_seed_sql = sql.split("candidate_root_identities AS (", 1)[1].split(
        "),\n        latest_roots AS (", 1
    )[0]
    assert "matching_user_sessions" not in root_seed_sql


@pytest.mark.unit
def test_session_page_enrichments_replay_tombstones_and_resolve_remaps():
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[],
    )
    session_id = str(uuid.uuid4())

    metrics_sql, metrics_params = builder.build_page_metrics_query([session_id])
    content_sql, content_params = builder.build_content_query([session_id])
    attrs_sql, attrs_params = builder.build_span_attributes_query([session_id])

    for params in (metrics_params, content_params, attrs_params):
        assert params["candidate_filter_session_id_array"] == [session_id]
    # One primary-key old-ID probe plus one authoritative reverse new-ID pass.
    # The scalar tuple-array wrapper executes those source arms once even though
    # content hydration consumes the tiny map in multiple CTE stages.
    for sql in (metrics_sql, content_sql, attrs_sql):
        assert sql.count("FROM trace_session_id_remap FINAL") == 2
        assert "WHERE new_id IN (" in sql
        assert "candidate_target_new_ids AS" in sql
        assert "PREWHERE old_id IN (" in sql
        assert "AS candidate_session_pairs" in sql
        assert "SELECT arrayJoin(candidate_session_pairs) AS pair" in sql
        assert "OVER (PARTITION BY new_id)" not in sql
    assert "trace_session_id IN %(content_session_ids)s" in content_sql
    assert "if(ts_remap.survivor_id IS NULL OR ts_remap.survivor_id = " in content_sql

    for sql in (metrics_sql, content_sql, attrs_sql):
        candidate_sql = sql.split("candidate_root_identities AS (", 1)[1].split(
            "),\n        latest_roots AS (", 1
        )[0]
        assert "candidate_root_identities AS" in sql
        # A latest live root always has at least its latest raw root row, so
        # this is a safe candidate witness. Root-to-child corrections and
        # tombstones are still rejected by the exact latest-state phase below.
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in candidate_sql
        assert "trace_session_id_remap" in sql
        assert (
            "argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id" in sql
        )
        assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql
        assert "latest_is_deleted = 0" in sql
        assert "(latest_parent_span_id IS NULL OR latest_parent_span_id = '')" in sql


def _ch25_client():
    host = os.getenv("CH25_HOST")
    port = int(
        os.getenv("CH25_NATIVE_PORT")
        or os.getenv("CH25_TCP_PORT")
        or os.getenv("CH_PORT")
        or "9000"
    )
    database = os.getenv("CH25_DATABASE") or os.getenv("CH_DATABASE") or "test_tfc"
    if not host:
        pytest.skip("CH25_HOST is not configured")
    try:
        from clickhouse_driver import Client

        client = Client(host=host, port=port, database=database)
        client.execute("SELECT 1")
        return client
    except Exception as exc:
        pytest.skip(f"disposable ClickHouse 25 is unavailable: {type(exc).__name__}")


def _dict_rows(rows, columns):
    names = [column[0] for column in columns]
    return [dict(zip(names, row, strict=True)) for row in rows]


@pytest.mark.integration
def test_user_trace_and_session_candidates_execute_with_alias_remaps():
    """Execute exact CH25 user trace/session reads across both ID cutovers."""

    client = _ch25_client()
    original_database = client.execute("SELECT currentDatabase()")[0][0]
    database = f"_test_user_candidates_{uuid.uuid4().hex}"
    project_id = str(uuid.uuid4())
    old_user_id, new_user_id = str(uuid.uuid4()), str(uuid.uuid4())
    old_session_id, new_session_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    start, end = now - timedelta(days=1), now + timedelta(days=1)
    trace_ids = (str(uuid.uuid4()), str(uuid.uuid4()))

    try:
        client.execute(f"CREATE DATABASE {database}")
        client.execute(f"USE {database}")
        client.execute(
            """
            CREATE TABLE spans (
                project_id UUID,
                trace_id String,
                id String,
                start_time DateTime64(6, 'UTC'),
                parent_span_id Nullable(String),
                trace_session_id Nullable(UUID),
                end_user_id Nullable(UUID),
                is_deleted UInt8,
                _version UInt64
            ) ENGINE = ReplacingMergeTree(_version)
            PARTITION BY toDate(start_time)
            ORDER BY (project_id, toStartOfHour(start_time), trace_id, id, start_time)
            """
        )
        client.execute(
            """
            CREATE TABLE end_users (
                project_id UUID,
                end_user_id UUID,
                user_id String,
                user_id_type String,
                is_deleted UInt8,
                version DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree(version)
            ORDER BY end_user_id
            """
        )
        for table in ("end_user_id_remap", "trace_session_id_remap"):
            client.execute(
                f"""
                CREATE TABLE {table} (
                    old_id UUID,
                    new_id UUID,
                    version DateTime64(6, 'UTC')
                ) ENGINE = ReplacingMergeTree(version)
                ORDER BY old_id
                """
            )

        client.execute(
            "INSERT INTO end_users VALUES",
            [(project_id, old_user_id, "guest-e3dce503", "custom", 0, now)],
        )
        client.execute(
            "INSERT INTO end_user_id_remap VALUES",
            [(old_user_id, new_user_id, now)],
        )
        client.execute(
            "INSERT INTO trace_session_id_remap VALUES",
            [(old_session_id, new_session_id, now)],
        )
        span_ids: list[str] = []
        span_rows = []
        for index, (trace_id, end_user_id, session_id) in enumerate(
            zip(
                trace_ids,
                (old_user_id, new_user_id),
                (old_session_id, new_session_id),
                strict=True,
            )
        ):
            span_id = f"root-{index}-{uuid.uuid4()}"
            span_ids.append(span_id)
            span_rows.append(
                (
                    project_id,
                    trace_id,
                    span_id,
                    now - timedelta(minutes=2 - index),
                    "",
                    session_id,
                    end_user_id,
                    0,
                    index + 1,
                )
            )
        client.execute("INSERT INTO spans VALUES", span_rows)

        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start.isoformat(), end.isoformat()],
                },
            },
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": "TRACE_END_USER",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "guest-e3dce503",
                },
            },
        ]
        builder = TraceListQueryBuilderV2(
            project_id=project_id,
            filters=filters,
            bounded_identity_only=True,
        )
        seed_sql, seed_params = builder.build_filter_seed_page(
            slice_start=start,
            slice_end=end,
            limit=25,
        )
        seed_raw, seed_columns = client.execute(
            seed_sql, seed_params, with_column_types=True
        )
        seed_rows = _dict_rows(seed_raw, seed_columns)
        assert {row["trace_id"] for row in seed_rows} == set(trace_ids)

        match_sql, match_params = builder.build_filter_match_query_from_seed_rows(
            seed_rows
        )
        match_raw, match_columns = client.execute(
            match_sql, match_params, with_column_types=True
        )
        matches = _dict_rows(match_raw, match_columns)
        assert {row["trace_id"] for row in matches} == set(trace_ids)

        session_builder = SessionListQueryBuilderV2(
            project_id=project_id,
            page_size=25,
            filters=[
                filters[0],
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": [old_user_id],
                    },
                },
            ],
        )
        session_sql, session_params = session_builder.build_candidate_page_query()
        session_raw, session_columns = client.execute(
            session_sql, session_params, with_column_types=True
        )
        sessions = _dict_rows(session_raw, session_columns)
        assert len(sessions) == 1
        assert str(sessions[0]["session_id"]) == old_session_id
        assert sessions[0]["total_count"] == 1
    finally:
        client.execute(f"USE {original_database}")
        client.execute(f"DROP DATABASE IF EXISTS {database}")


@pytest.mark.integration
def test_user_pages_ignore_stale_rollup_tombstones_updates_and_reassignments():
    """Page membership/order comes from latest spans, never insert-only states."""

    client = _ch25_client()
    project_id = str(uuid.uuid4())
    organization_id = str(uuid.uuid4())
    user_ids = {name: str(uuid.uuid4()) for name in ("a", "b", "c", "d", "e")}
    trace_ids = {name: str(uuid.uuid4()) for name in ("a", "b", "c", "move")}
    now = datetime.now(UTC).replace(tzinfo=None)

    client.execute(
        "INSERT INTO end_users "
        "(project_id, end_user_id, organization_id, user_id, user_id_type, "
        "user_id_hash, metadata, first_seen, version, is_deleted) VALUES",
        [
            (
                project_id,
                end_user_id,
                organization_id,
                f"exact-user-{name}",
                "custom",
                f"hash-{name}",
                "{}",
                now - timedelta(days=1),
                now,
                0,
            )
            for name, end_user_id in user_ids.items()
        ],
    )

    columns = [
        "project_id",
        "observation_type",
        "service_name",
        "start_time",
        "trace_id",
        "id",
        "parent_span_id",
        "name",
        "end_time",
        "latency_ms",
        "org_id",
        "end_user_id",
        "trace_session_id",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
        "input",
        "output",
        "is_deleted",
        "_version",
    ]

    def _span(
        *,
        key: str,
        user: str,
        start_minutes: int,
        end_minutes: int,
        cost: float,
        deleted: int,
        version: int,
    ):
        start = now - timedelta(minutes=start_minutes)
        return (
            project_id,
            "llm",
            "user-exactness-test",
            start,
            trace_ids[key],
            f"span-{key}",
            "",
            f"span-{key}",
            now - timedelta(minutes=end_minutes),
            100,
            organization_id,
            user_ids[user],
            None,
            "OK",
            4,
            6,
            10,
            cost,
            {},
            {},
            {},
            "{}",
            "",
            "",
            deleted,
            version,
        )

    rows = [
        # A has only a tombstoned identity. Its insert-only rollup still has the
        # newest last_active value and would incorrectly put it on page 1.
        _span(
            key="a",
            user="a",
            start_minutes=20,
            end_minutes=1,
            cost=50,
            deleted=0,
            version=1,
        ),
        _span(
            key="a",
            user="a",
            start_minutes=20,
            end_minutes=1,
            cost=50,
            deleted=1,
            version=2,
        ),
        # B's corrected latest row reduces both cost and last_active.
        _span(
            key="b",
            user="b",
            start_minutes=10,
            end_minutes=2,
            cost=100,
            deleted=0,
            version=1,
        ),
        _span(
            key="b",
            user="b",
            start_minutes=10,
            end_minutes=8,
            cost=1,
            deleted=0,
            version=2,
        ),
        # One physical identity moves D -> E. D must disappear and E must own
        # exactly the latest contribution.
        _span(
            key="move",
            user="d",
            start_minutes=7,
            end_minutes=6,
            cost=20,
            deleted=0,
            version=1,
        ),
        _span(
            key="move",
            user="e",
            start_minutes=7,
            end_minutes=5,
            cost=2,
            deleted=0,
            version=2,
        ),
        _span(
            key="c",
            user="c",
            start_minutes=4,
            end_minutes=3,
            cost=5,
            deleted=0,
            version=1,
        ),
    ]
    client.execute(
        f"INSERT INTO spans ({', '.join(columns)}) VALUES",
        rows,
        types_check=True,
    )

    # Prove the fixture is adversarial: the append-only source is stale for A,
    # D, and B's corrected cost. A selector that still reads this table fails.
    stale_rollup = dict(
        client.execute(
            "SELECT toString(end_user_id), sumMerge(cost_sum) "
            "FROM span_user_rollup "
            "WHERE project_id = toUUID(%(project_id)s) GROUP BY end_user_id",
            {"project_id": project_id},
        )
    )
    assert stale_rollup[user_ids["a"]] == 50
    assert stale_rollup[user_ids["b"]] == 101
    assert stale_rollup[user_ids["d"]] == 20

    filters = _window(now)
    pages = []
    elapsed_ms = []
    for offset in range(3):
        builder = UserListQueryBuilderV2(
            organization_id=organization_id,
            project_id=project_id,
            limit=1,
            offset=offset,
            filters=filters,
        )
        query, params = builder.build_candidate_page_query()
        started = time.monotonic()
        raw, returned_columns = client.execute(query, params, with_column_types=True)
        elapsed_ms.append((time.monotonic() - started) * 1000)
        pages.extend(_dict_rows(raw, returned_columns))

    assert [str(row["end_user_id"]) for row in pages] == [
        user_ids["c"],
        user_ids["e"],
        user_ids["b"],
    ]
    assert [row["total_count"] for row in pages] == [3, 3, 3]
    assert [row["total_cost"] for row in pages] == [5, 2, 1]
    assert user_ids["a"] not in {str(row["end_user_id"]) for row in pages}
    assert user_ids["d"] not in {str(row["end_user_id"]) for row in pages}
    # This is a disposable correctness ceiling, not a production performance
    # claim. The API's tighter read deadline fails closed when exact replay is
    # not affordable on a heavy workspace.
    assert max(elapsed_ms) < 5_000


@pytest.mark.integration
def test_candidate_reads_on_ch25_preserve_remap_and_tombstone_semantics():
    """Execute every new query against the disposable CH25 schema.

    The old physical root is tombstoned after insert; the live root carries the
    deterministic ids. All APIs must return the one canonical old session/user,
    and deleted or out-of-window content/attributes must stay absent.
    """

    client = _ch25_client()
    project_id = str(uuid.uuid4())
    organization_id = str(uuid.uuid4())
    old_user_id, new_user_id = str(uuid.uuid4()), str(uuid.uuid4())
    old_session_id, new_session_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)

    client.execute(
        "INSERT INTO end_users "
        "(project_id, end_user_id, organization_id, user_id, user_id_type, "
        "user_id_hash, metadata, first_seen, version, is_deleted) VALUES",
        [
            (
                project_id,
                old_user_id,
                organization_id,
                "candidate-user",
                "email",
                "candidate-hash",
                "{}",
                now - timedelta(hours=1),
                now,
                0,
            )
        ],
    )
    client.execute(
        "INSERT INTO end_user_id_remap (old_id, new_id, version) VALUES",
        [(old_user_id, new_user_id, now)],
    )
    client.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [(old_session_id, new_session_id, now)],
    )

    columns = [
        "project_id",
        "observation_type",
        "service_name",
        "start_time",
        "trace_id",
        "id",
        "parent_span_id",
        "name",
        "end_time",
        "latency_ms",
        "org_id",
        "project_version_id",
        "end_user_id",
        "trace_session_id",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
        "input",
        "output",
        "is_deleted",
        "_version",
    ]
    old_start = now - timedelta(minutes=10)
    live_start = now - timedelta(minutes=5)
    old_row = (
        project_id,
        "llm",
        "candidate-test",
        old_start,
        str(uuid.uuid4()),
        "candidate-root-old",
        "",
        "old-root",
        old_start + timedelta(seconds=2),
        100,
        organization_id,
        None,
        old_user_id,
        old_session_id,
        "OK",
        4,
        6,
        10,
        1.0,
        {"deleted_key": "gone"},
        {},
        {},
        '{"deleted_key":"gone"}',
        "deleted-message",
        "old-output",
        0,
        1,
    )
    live_row = (
        project_id,
        "llm",
        "candidate-test",
        live_start,
        str(uuid.uuid4()),
        "candidate-root-new",
        "",
        "live-root",
        live_start + timedelta(seconds=3),
        200,
        organization_id,
        None,
        new_user_id,
        new_session_id,
        "ERROR",
        8,
        12,
        20,
        2.0,
        {"live_key": "yes", "final_status": "Rejected"},
        {"score": 2.0},
        {},
        '{"live_key":"yes","final_status":"Rejected"}',
        "live-message",
        "live-output",
        0,
        1,
    )
    outside_start = now - timedelta(days=2)
    outside_row = list(live_row)
    outside_row[3] = outside_start
    outside_row[4] = str(uuid.uuid4())
    outside_row[5] = "candidate-root-outside-window"
    outside_row[7] = "outside-window-root"
    outside_row[8] = outside_start + timedelta(seconds=4)
    outside_row[13] = old_session_id
    outside_row[19] = {"outside_key": "must-not-hydrate"}
    outside_row[22] = '{"outside_key":"must-not-hydrate"}'
    outside_row[23] = "outside-window-message"
    outside_row[24] = "outside-window-output"
    client.execute(
        f"INSERT INTO spans ({', '.join(columns)}) VALUES",
        [old_row, live_row, tuple(outside_row)],
        types_check=True,
    )
    tombstone = list(old_row)
    tombstone[-2] = 1
    tombstone[-1] = 2
    client.execute(
        f"INSERT INTO spans ({', '.join(columns)}) VALUES",
        [tuple(tombstone)],
        types_check=True,
    )

    filters = _window(now)
    user_builder = UserListQueryBuilderV2(
        organization_id=organization_id,
        project_id=project_id,
        limit=25,
        offset=0,
        filters=filters,
    )
    user_sql, user_params = user_builder.build_candidate_page_query()
    started = time.monotonic()
    user_raw, user_columns = client.execute(
        user_sql, user_params, with_column_types=True
    )
    user_page_elapsed_ms = (time.monotonic() - started) * 1000
    users = _dict_rows(user_raw, user_columns)

    assert len(users) == 1
    assert str(users[0]["end_user_id"]) == old_user_id
    user_metrics_sql, user_metrics_params = user_builder.build_page_metrics_query(
        [old_user_id]
    )
    started = time.monotonic()
    user_metrics_raw, user_metrics_columns = client.execute(
        user_metrics_sql,
        user_metrics_params,
        with_column_types=True,
    )
    user_metrics_elapsed_ms = (time.monotonic() - started) * 1000
    user_metrics = _dict_rows(user_metrics_raw, user_metrics_columns)
    assert len(user_metrics) == 1
    assert str(user_metrics[0]["end_user_id"]) == old_user_id
    assert user_metrics[0]["num_llm_calls"] == 1
    assert user_metrics[0]["num_sessions"] == 1
    assert user_metrics[0]["num_traces_with_errors"] == 1

    session_builder = SessionListQueryBuilderV2(
        project_id=project_id,
        page_number=0,
        page_size=25,
        filters=filters,
    )
    page_sql, page_params = session_builder.build_candidate_page_query()
    page_raw, page_columns = client.execute(
        page_sql, page_params, with_column_types=True
    )
    page = _dict_rows(page_raw, page_columns)
    assert len(page) == 1
    assert str(page[0]["session_id"]) == old_session_id
    assert page[0]["total_count"] == 1

    count_sql, count_params = session_builder.build_candidate_count_query()
    count_raw = client.execute(count_sql, count_params)
    assert count_raw[0][0] == 1

    structural_filters = {
        "session_in": [
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [old_session_id],
                },
            }
        ],
        "user_in": [
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [old_user_id],
                },
            }
        ],
    }
    for structural_filter in structural_filters.values():
        filtered_builder = SessionListQueryBuilderV2(
            project_id=project_id,
            page_number=0,
            page_size=25,
            filters=[*filters, *structural_filter],
        )
        filtered_sql, filtered_params = filtered_builder.build_candidate_page_query()
        filtered_raw = client.execute(filtered_sql, filtered_params)
        assert len(filtered_raw) == 1
        assert str(filtered_raw[0][0]) == old_session_id
        filtered_count_sql, filtered_count_params = (
            filtered_builder.build_candidate_count_query()
        )
        assert client.execute(filtered_count_sql, filtered_count_params)[0][0] == 1

    excluded_builder = SessionListQueryBuilderV2(
        project_id=project_id,
        page_number=0,
        page_size=25,
        filters=[
            *filters,
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_in",
                    "filter_value": [old_session_id],
                },
            },
        ],
    )
    excluded_sql, excluded_params = excluded_builder.build_candidate_page_query()
    assert client.execute(excluded_sql, excluded_params) == []
    excluded_count_sql, excluded_count_params = (
        excluded_builder.build_candidate_count_query()
    )
    assert client.execute(excluded_count_sql, excluded_count_params)[0][0] == 0

    attribute_builder = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=[
            *filters,
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["Rejected"],
                },
            },
        ],
        bounded_internal_scan=True,
    )
    seed_sql, seed_params = attribute_builder.build_filter_seed_page(
        slice_start=now - timedelta(minutes=15),
        slice_end=now + timedelta(seconds=1),
        limit=200,
    )
    seed_raw, seed_columns = client.execute(
        seed_sql, seed_params, with_column_types=True
    )
    seed_rows = _dict_rows(seed_raw, seed_columns)
    # Seed acquisition deliberately scans raw non-deleted versions without a
    # latest-state replay.  Until the ReplacingMergeTree merges the old live
    # version with its tombstone, that old alias may remain as a bounded false
    # positive beside the deterministic new alias.  The live new alias must
    # always be present, and the finite classifier below is the authoritative
    # phase that collapses either seed shape to the canonical old survivor.
    seed_ids = {str(row["session_id"]) for row in seed_rows}
    assert new_session_id in seed_ids
    assert seed_ids <= {old_session_id, new_session_id}
    match_sql, match_params = attribute_builder.build_filter_match_query(
        [new_session_id]
    )
    match_raw = client.execute(match_sql, match_params)
    assert len(match_raw) == 1
    assert str(match_raw[0][0]) == old_session_id

    class _Executor:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            raw, returned_columns = client.execute(
                query,
                params,
                with_column_types=True,
                settings={
                    **settings,
                    "max_execution_time": max(1, timeout_ms // 1000),
                },
            )
            return SimpleNamespace(data=_dict_rows(raw, returned_columns))

    bounded_page = read_bounded_filter_page(
        builder=attribute_builder,
        analytics=_Executor(),
        filters=attribute_builder.filters,
        key_field="session_id",
        page_number=0,
        page_size=25,
        deadline_ms=5000,
    )
    assert bounded_page.complete is True, bounded_page
    assert [str(row["session_id"]) for row in bounded_page.rows] == [old_session_id]

    tombstoned_attribute_builder = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=[
            *filters,
            {
                "column_id": "deleted_key",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gone",
                },
            },
        ],
        bounded_internal_scan=True,
    )
    deleted_sql, deleted_params = tombstoned_attribute_builder.build_filter_match_query(
        [old_session_id]
    )
    assert client.execute(deleted_sql, deleted_params) == []

    derived_filters = {
        "tokens": (
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 15,
                },
            },
            {"column_id": "total_tokens", "direction": "desc"},
        ),
        "message": (
            {
                "column_id": "first_message",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "live-message",
                },
            },
            {"column_id": "first_message", "direction": "asc"},
        ),
    }
    for derived_filter, derived_sort in derived_filters.values():
        derived_builder = SessionListQueryBuilderV2(
            project_id=project_id,
            page_number=0,
            page_size=25,
            filters=[*filters, derived_filter],
            sort_params=[derived_sort],
        )
        derived_sql, derived_params = derived_builder.build_candidate_page_query()
        derived_raw = client.execute(derived_sql, derived_params)
        assert len(derived_raw) == 1
        assert str(derived_raw[0][0]) == old_session_id
        derived_count_sql, derived_count_params = (
            derived_builder.build_candidate_count_query()
        )
        assert client.execute(derived_count_sql, derived_count_params)[0][0] == 1

    phase_timings = []
    results = {}
    for name, (sql, params) in {
        "metrics": session_builder.build_page_metrics_query([old_session_id]),
        "content": session_builder.build_content_query([old_session_id]),
        "attributes": session_builder.build_span_attributes_query([old_session_id]),
    }.items():
        started = time.monotonic()
        raw, returned_columns = client.execute(sql, params, with_column_types=True)
        phase_timings.append((time.monotonic() - started) * 1000)
        results[name] = _dict_rows(raw, returned_columns)

    assert results["metrics"][0]["total_cost"] == 2.0
    assert results["metrics"][0]["total_tokens"] == 20
    assert results["metrics"][0]["traces_count"] == 1
    assert results["content"][0]["first_message"] == "live-message"
    assert results["content"][0]["last_message"] == "live-message"
    assert len(results["attributes"]) == 1
    assert results["attributes"][0]["attrs_string"] == {
        "live_key": "yes",
        "final_status": "Rejected",
    }
    assert "deleted_key" not in results["attributes"][0]["span_attributes_raw"]
    assert "outside_key" not in results["attributes"][0]["span_attributes_raw"]

    # Generous CI ceilings; local disposable runs are normally <1s for Users
    # and <100ms per Session phase. Production A/B remains a separate sealed
    # read-only gate and is not inferred from these local ceilings.
    assert user_page_elapsed_ms < 2000
    assert user_metrics_elapsed_ms < 2000
    assert sum(phase_timings) < 2000
