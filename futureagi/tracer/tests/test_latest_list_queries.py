"""Focused regressions for latest-state trace/span list queries."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.span_reader import merge_span_attributes
from tracer.views.observation_span import _execute_bounded_span_filter_prefix

_START = datetime(2026, 7, 30, 11, tzinfo=UTC)
_END = datetime(2026, 7, 30, 13, tzinfo=UTC)


def _time_filter():
    return {
        "column_id": "start_time",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [_START, _END],
        },
    }


def _attr(key, value):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _session_filter(value):
    return {
        "column_id": "trace_session_id",
        "filter_config": {
            "col_type": "NORMAL",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": str(value),
        },
    }


def test_span_latest_page_compiles_same_row_and_tombstone_predicates():
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("a", "x"), _attr("b", "y")],
        page_size=25,
    )

    sql, params = builder.build_latest_attribute_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
        before_start_time=_END - timedelta(minutes=1),
        before_id="span-b",
    )

    assert "FINAL" not in sql
    assert "GROUP BY id" in sql
    assert "latest_attr_exists_0" in sql
    assert "latest_attr_exists_1" in sql
    assert "latest_is_deleted = 0" in sql
    assert "latest_start_time = %(keyset_start_time)s" in sql
    assert "grouped_id < %(keyset_id)s" in sql
    assert "project_id = %(project_id)s" in sql
    assert params["project_id"] == "11111111-1111-1111-1111-111111111111"


def test_span_list_prefix_keeps_slice_aggregation_skinny_and_stable():
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
        page_size=25,
    )

    sql, params = builder.build_latest_attribute_list_ids(
        slice_start=_START,
        slice_end=_END,
        limit=50,
        before_start_time=_END - timedelta(minutes=1),
        before_id="span-b",
    )

    assert "FINAL" not in sql
    assert "grouped_id AS id" in sql
    assert "latest_start_time AS start_time" in sql
    assert "argMax(tuple(start_time), _version).1" in sql
    assert "latest_is_deleted = 0" in sql
    assert "latest_attr_value_0" in sql
    assert "ORDER BY latest_start_time DESC, grouped_id DESC" in sql
    assert "latest_start_time = %(keyset_start_time)s" in sql
    # Display/content states must never be retained for every id in the slice.
    assert "argMax(trace_id" not in sql
    assert "argMax(name" not in sql
    assert "argMax(input" not in sql
    assert "argMax(output" not in sql
    assert params["limit"] == 50


def test_span_candidate_seed_uses_safe_raw_prefilter_and_stable_keyset():
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
        page_size=25,
    )

    sql, params = builder.build_latest_attribute_candidate_seed_page(
        slice_start=_START,
        slice_end=_END,
        limit=50,
        before_start_time=_END - timedelta(minutes=1),
        before_id="span-b",
    )

    assert "FINAL" not in sql
    assert "GROUP BY" not in sql
    assert "argMax(" not in sql
    assert "latest_is_deleted" not in sql
    assert "latest_attr_" not in sql
    assert "project_version_id" not in sql
    assert "span_attr_str" in sql or "attrs_string" in sql
    assert "final_status" in sql
    assert "SELECT\n            id,\n            start_time" in sql
    assert "start_time >= %(candidate_slice_start)s" in sql
    assert "start_time < %(candidate_slice_end)s" in sql
    assert "start_time < %(candidate_before_start_time)s" in sql
    assert "id < %(candidate_before_id)s" in sql
    assert "ORDER BY start_time DESC, id DESC" in sql
    assert params["candidate_seed_limit"] == 50


def test_span_candidate_classifier_uses_full_request_latest_state():
    project_version_id = "22222222-2222-2222-2222-222222222222"
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        project_version_id=project_version_id,
        filters=[_time_filter(), _attr("final_status", "approved")],
        page_size=25,
    )

    sql, params = builder.build_latest_attribute_candidate_matches(
        ["span-a", "span-b", "span-a"]
    )

    assert "FINAL" not in sql
    assert "id IN %(candidate_span_ids)s" in sql
    assert "start_time >= %(start_date)s" in sql
    assert "start_time < %(end_date)s" in sql
    assert "candidate_slice_start" not in sql
    assert "GROUP BY id" in sql
    assert "latest_is_deleted = 0" in sql
    assert "latest_attr_exists_0" in sql
    assert "latest_attr_value_0" in sql
    assert "argMax(tuple(project_version_id), _version).1" in sql
    assert "latest_project_version_id = %(project_version_id)s" in sql
    assert "ORDER BY start_time DESC, id DESC" in sql
    assert params["candidate_span_ids"] == ("span-a", "span-b")
    assert params["start_date"] == _START.replace(tzinfo=None)
    assert params["end_date"] == _END.replace(tzinfo=None)


def test_span_candidate_classifier_combines_scalar_and_annotation_filters():
    label_id = "33333333-3333-3333-3333-333333333333"
    annotation_filter = {
        "column_id": label_id,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "approved",
        },
    }
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved"), annotation_filter],
        annotation_label_ids=[label_id],
    )

    assert builder.supports_latest_attribute_page() is False
    assert builder.supports_latest_candidate_page() is True

    seed_sql, _ = builder.build_latest_attribute_candidate_seed_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )
    match_sql, match_params = builder.build_latest_attribute_candidate_matches(
        ["span-a", "span-b"]
    )

    assert "model_hub_score" not in seed_sql
    assert "latest_attr_value_0" in match_sql
    assert "FROM model_hub_score AS s FINAL" in match_sql
    assert "candidate_span_ids" in match_sql
    assert match_params["candidate_span_ids"] == ("span-a", "span-b")
    assert "use_skip_indexes_if_final = 0" in match_sql
    assert "use_skip_indexes_if_final = 1" not in match_sql


def test_trace_candidate_classifier_combines_attribute_and_trace_tag():
    tag_filter = {
        "column_id": "tags",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "production",
        },
    }
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("customer_tier", "enterprise"), tag_filter],
    )

    assert builder.supports_latest_filter_match() is True
    sql, params = builder.build_latest_filter_match_query(["trace-a", "trace-b"])

    assert "latest_attr_value_0" in sql
    assert "dictGetOrDefault('trace_dict', 'tags'" in sql
    assert "candidate_trace_ids" in sql
    assert params["candidate_trace_ids"] == ("trace-a", "trace-b")
    assert "use_skip_indexes_if_final = 0" in sql
    assert "use_skip_indexes_if_final = 1" not in sql


def test_trace_candidate_classifier_rejects_unsafe_unknown_metric():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[
            _time_filter(),
            {
                "column_id": "unsafe') OR 1 = 1 --",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "x",
                },
            },
        ],
    )

    with pytest.raises(UnsupportedFilterShapeError):
        builder.build_latest_filter_match_query(["trace-a"])


def test_span_point_content_hydrates_light_columns_for_only_page_ids():
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter()],
    )

    sql, params = builder.build_content_query(["span-a", "span-b"])

    assert "id IN %(content_span_ids)s" in sql
    assert "GROUP BY id" in sql
    assert "latest_is_deleted = 0" in sql
    for column in (
        "trace_id",
        "name",
        "observation_type",
        "status",
        "start_time",
        "latency_ms",
        "created_at",
    ):
        assert f"AS {column}" in sql
    assert params["content_span_ids"] == ("span-a", "span-b")


def test_span_preview_hydrates_only_selected_typed_attributes():
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
    )

    sql, params = builder.build_preview_hydration_query(["span-a"])

    assert "id IN %(preview_span_ids)s" in sql
    assert "mapFilter(" in sql
    assert "AS attrs_string" in sql
    assert "AS attrs_number" not in sql
    assert "AS attrs_bool" not in sql
    assert "argMax(input" not in sql
    assert "argMax(output" not in sql
    assert "attributes_extra" not in sql
    assert params["preview_text_keys"] == ("final_status",)
    assert params["preview_span_ids"] == ("span-a",)


def test_span_latest_id_page_applies_sampling_exclusion_and_keyset_before_limit():
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
    )

    sql, params = builder.build_latest_attribute_id_page(
        slice_start=_START,
        slice_end=_START + timedelta(minutes=1),
        limit=25,
        sampling_salt="task-1",
        sampling_rate=50,
        exclude_span_ids={"span-a"},
        after_span_id="span-b",
    )

    assert "FINAL" not in sql
    assert "GROUP BY id" in sql
    assert "latest_is_deleted = 0" in sql
    assert "final_status" in sql
    assert "grouped_id NOT IN %(latest_span_excluded_ids)s" in sql
    assert "grouped_id > %(latest_span_after_id)s" in sql
    assert sql.index("latest_is_deleted = 0") < sql.index("LIMIT %(latest_span_limit)s")
    assert params["latest_span_sampling_salt"] == "task-1"
    assert params["latest_span_sampling_rate"] == 50.0
    assert params["latest_span_excluded_ids"] == ("span-a",)
    assert params["latest_span_after_id"] == "span-b"
    assert sql.upper().count("PREWHERE") == 1


def test_trace_latest_root_id_page_selects_canonical_root_before_final_status():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
    )

    sql, params = builder.build_latest_root_id_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
        sampling_salt="task-1",
        sampling_rate=100,
    )

    assert "FINAL" not in sql
    assert "LIMIT 1 BY grouped_trace_id" in sql
    assert sql.index("LIMIT 1 BY grouped_trace_id") < sql.rindex("latest_attr_exists_0")
    assert "argMax(tuple(parent_span_id), _version).1" in sql
    assert "argMax(tuple(start_time), _version).1" in sql
    assert params["latest_root_limit"] == 25


def test_trace_root_candidate_seed_is_ch25_projection_compatible_and_keyset_stable():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
    )
    slice_start = _END - timedelta(minutes=5)
    marker_time = _END - timedelta(minutes=2)

    sql, params = builder.build_root_candidate_seed_page(
        slice_start=slice_start,
        slice_end=_END,
        limit=100,
        before_start_time=marker_time,
        before_trace_id="trace-b",
    )

    select_clause = sql.split("FROM", 1)[0]
    assert "trace_id" in select_clause
    assert "start_time" in select_clause
    assert "attrs_string" not in select_clause
    assert "FINAL" not in sql
    assert "GROUP BY" not in sql
    assert "PREWHERE project_id = %(project_id)s" in sql
    assert "is_deleted = 0" in sql
    assert "parent_span_id = ''" in sql
    assert "parent_span_id IS NULL" not in sql
    assert "start_time >= %(root_seed_slice_start)s" in sql
    assert "start_time < %(root_seed_slice_end)s" in sql
    assert "start_time < %(root_seed_before_start_time)s" in sql
    assert "trace_id < %(root_seed_before_trace_id)s" in sql
    assert "ORDER BY start_time DESC, trace_id DESC" in sql
    assert "LIMIT %(root_seed_limit)s" in sql
    assert "optimize_use_projections = 1" in sql
    assert params["root_seed_slice_start"] == slice_start.replace(tzinfo=None)
    assert params["root_seed_slice_end"] == _END.replace(tzinfo=None)
    assert params["root_seed_before_start_time"] == marker_time.replace(tzinfo=None)
    assert params["root_seed_before_trace_id"] == "trace-b"
    assert params["root_seed_limit"] == 100


def test_trace_filtered_root_seed_is_a_bounded_physical_superset():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved")],
    )
    slice_start = _END - timedelta(minutes=5)

    sql, params = builder.build_filtered_root_candidate_seed_page(
        slice_start=slice_start,
        slice_end=_END,
        limit=100,
    )

    assert "FINAL" not in sql
    assert "argMax(" not in sql
    assert "GROUP BY" not in sql
    assert "parent_span_id = ''" in sql
    assert "mapContains(attrs_string, 'final_status')" in sql
    assert "start_time >= %(root_seed_slice_start)s" in sql
    assert "start_time < %(root_seed_slice_end)s" in sql
    assert "ORDER BY start_time DESC, trace_id DESC" in sql
    assert params["root_seed_slice_start"] == slice_start.replace(tzinfo=None)
    assert params["root_seed_slice_end"] == _END.replace(tzinfo=None)
    assert params["root_seed_limit"] == 100


def test_trace_filtered_root_seed_rejects_any_span_predicates():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("arbitrary_child_attribute", "approved")],
    )

    with pytest.raises(ValueError, match="root-only"):
        builder.build_filtered_root_candidate_seed_page(
            slice_start=_END - timedelta(minutes=5),
            slice_end=_END,
            limit=100,
        )


def test_trace_matcher_keeps_root_and_any_span_semantics_separate():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _attr("final_status", "approved"), _attr("a", "x")],
    )

    sql, params = builder.build_latest_filter_match_query(
        ["trace-a", "trace-b"],
        filters=[_attr("final_status", "approved"), _attr("a", "x")],
    )

    assert "FINAL" not in sql
    assert "LIMIT 1 BY grouped_trace_id" in sql
    assert sql.index("LIMIT 1 BY grouped_trace_id") < sql.rindex("latest_attr_exists_0")
    assert "trace_id IN (" in sql
    assert "GROUP BY trace_id, id" in sql
    assert "created_at >= %(candidate_start_date)s - INTERVAL 1 DAY" in sql
    assert "start_time >= %(candidate_start_date)s - INTERVAL 1 DAY" in sql
    assert "start_time < %(candidate_end_date)s + INTERVAL 1 DAY" in sql
    assert params["candidate_trace_ids"] == ("trace-a", "trace-b")
    assert params["project_id"] == "11111111-1111-1111-1111-111111111111"


def test_trace_matcher_revalidates_session_on_canonical_root():
    session_id = uuid.uuid4()
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter(), _session_filter(session_id), _attr("a", "x")],
    )

    sql, params = builder.build_latest_filter_match_query(
        ["trace-a"],
        filters=[_session_filter(session_id), _attr("a", "x")],
    )

    assert "FINAL" not in sql
    assert "argMax(tuple(trace_session_id), _version).1 AS latest_root_value_0" in sql
    assert "LIMIT 1 BY grouped_trace_id" in sql
    assert params["latest_root_value_param_0"] == str(session_id)


def test_trace_hydration_selects_canonical_latest_live_root():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter()],
    )
    sql, params = builder.build_candidate_hydration_query(["trace-a"])

    assert "FINAL" not in sql
    assert "GROUP BY trace_id, id" in sql
    assert "latest_is_deleted = 0" in sql
    assert "LIMIT 1 BY grouped_trace_id" in sql
    assert "ORDER BY latest_start_time DESC" in sql
    assert "latest_attrs_string AS attrs_string" in sql
    assert "argMax(attrs_string, _version) AS latest_attrs_string" in sql
    assert params["candidate_start_date"] == _START.replace(tzinfo=None)
    assert params["candidate_end_date"] == _END.replace(tzinfo=None)


def test_span_latest_page_keeps_project_version_in_scalar_latest_state():
    project_version_id = "22222222-2222-2222-2222-222222222222"
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        project_version_id=project_version_id,
        filters=[_time_filter(), _attr("final_status", "approved")],
        page_size=25,
    )

    sql, params = builder.build_latest_attribute_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )

    assert "FINAL" not in sql
    assert "argMax(tuple(project_version_id), _version).1" in sql
    assert "latest_project_version_id = %(project_version_id)s" in sql
    assert params["project_version_id"] == project_version_id


def test_trace_attribute_hydration_keeps_request_time_pruning():
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=[_time_filter()],
    )
    builder.build()

    sql, params = builder.build_span_attributes_query(["trace-a"])

    assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in sql
    assert "start_time < %(end_date)s + INTERVAL 1 DAY" in sql
    assert "groupArrayIf(128)(tuple(" in sql
    assert "AS root_attribute_row" in sql
    assert "AS attribute_row_count" in sql
    assert "GROUP BY grouped_trace_id" in sql
    assert params["start_date"] == _START.replace(tzinfo=None)
    assert params["end_date"] == _END.replace(tzinfo=None)


def test_mixed_overflow_typed_and_bool_attributes_have_explicit_precedence():
    merged = merge_span_attributes(
        {"typed_string": "yes", "shared": "typed"},
        {"typed_number": 3.5},
        {"typed_bool": 1},
        '{"overflow": "yes", "shared": "overflow"}',
    )

    assert merged == {
        "typed_string": "yes",
        "typed_number": 3.5,
        "typed_bool": True,
        "overflow": "yes",
        "shared": "overflow",
    }


pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def latest_query_ch():
    """Isolated CH25 table for semantic parity; skip on CH-less lanes."""

    if os.environ.get("FUTUREAGI_TEST_ALLOW_LOCAL_CH_DDL") != "1":
        pytest.skip("local ClickHouse DDL integration test requires explicit opt-in")
    clickhouse_connect = pytest.importorskip("clickhouse_connect")
    host = os.environ.get("CH25_HOST") or os.environ.get("CH_HOST") or "localhost"
    if host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("local ClickHouse DDL test refuses a non-loopback host")
    port = int(
        os.environ.get("CH25_HTTP_PORT") or os.environ.get("CH_HTTP_PORT") or 18124
    )
    database = f"test_latest_lists_{uuid.uuid4().hex[:8]}"
    try:
        admin = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=os.environ.get("CH_USER", "default"),
            password=os.environ.get("CH_PASSWORD", ""),
        )
        admin.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"ClickHouse not available: {exc!r}")

    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        username=os.environ.get("CH_USER", "default"),
        password=os.environ.get("CH_PASSWORD", ""),
        database=database,
    )
    client.command(
        """
        CREATE TABLE spans (
            project_id UUID,
            trace_id String,
            id String,
            parent_span_id String DEFAULT '',
            trace_name String DEFAULT '',
            name String DEFAULT '',
            observation_type String DEFAULT '',
            status String DEFAULT '',
            start_time DateTime64(6, 'UTC'),
            end_time Nullable(DateTime64(6, 'UTC')),
            latency_ms Int32 DEFAULT 0,
            cost Float64 DEFAULT 0,
            total_tokens Int32 DEFAULT 0,
            prompt_tokens Int32 DEFAULT 0,
            completion_tokens Int32 DEFAULT 0,
            model String DEFAULT '',
            provider String DEFAULT '',
            end_user_id Nullable(UUID),
            trace_session_id Nullable(UUID),
            project_version_id Nullable(UUID),
            created_at DateTime64(6, 'UTC'),
            input String DEFAULT '',
            output String DEFAULT '',
            attributes_extra String DEFAULT '{}',
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            is_deleted UInt8 DEFAULT 0,
            _version UInt64
        ) ENGINE = MergeTree
        ORDER BY (project_id, start_time, trace_id, id, _version)
        """
    )
    try:
        yield client
    finally:
        client.close()
        admin.command(f"DROP DATABASE IF EXISTS {database}")
        admin.close()


def _insert(client, rows):
    columns = [
        "project_id",
        "trace_id",
        "id",
        "parent_span_id",
        "trace_name",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "created_at",
        "trace_session_id",
        "project_version_id",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "is_deleted",
        "_version",
    ]
    client.insert("spans", rows, column_names=columns)


def _query(client, sql, params):
    result = client.query(sql, parameters=params)
    return [
        dict(zip(result.column_names, row, strict=False)) for row in result.result_rows
    ]


class _LocalClickHouseAnalytics:
    """Small adapter for exercising the real bounded-list executor locally."""

    def __init__(self, client):
        self.client = client
        self.calls: list[tuple[str, dict]] = []

    def execute_ch_query(self, query, params, timeout_ms, settings):
        self.calls.append((query, dict(params)))
        rows = _query(self.client, query, params)
        return QueryResult(rows, len(rows), "clickhouse", 0)


@pytest.mark.integration
def test_span_bounded_executor_does_not_resurface_cross_slice_stale_match(
    latest_query_ch,
):
    """Newer nonmatches/key-clears/tombstones suppress older slice matches."""

    project = uuid.uuid4()
    older_time = _START + timedelta(minutes=5)
    newer_time = _END - timedelta(minutes=5)

    def row(span_id, when, attrs, *, deleted=0, version=1):
        return [
            project,
            span_id,
            span_id,
            "",
            "cross-slice",
            "span",
            "llm",
            "OK",
            when,
            when,
            when,
            None,
            None,
            attrs,
            {},
            {},
            deleted,
            version,
        ]

    stale_ids = {
        "cross-slice-key-clear",
        "cross-slice-nonmatch",
        "cross-slice-tombstone",
    }
    live_id = "cross-slice-current-match"
    rows = [
        row(span_id, older_time, {"final_status": "approved"}, version=1)
        for span_id in stale_ids
    ]
    rows.extend(
        [
            row("cross-slice-key-clear", newer_time, {}, version=2),
            row(
                "cross-slice-nonmatch",
                newer_time - timedelta(seconds=1),
                {"final_status": "rejected"},
                version=2,
            ),
            row(
                "cross-slice-tombstone",
                newer_time - timedelta(seconds=2),
                {"final_status": "approved"},
                deleted=1,
                version=2,
            ),
            row(
                live_id,
                newer_time - timedelta(seconds=3),
                {"final_status": "approved"},
                version=2,
            ),
        ]
    )
    _insert(latest_query_ch, rows)
    builder = SpanListQueryBuilderV2(
        project_id=str(project),
        page_number=0,
        page_size=1,
        filters=[_time_filter(), _attr("final_status", "approved")],
    )
    analytics = _LocalClickHouseAnalytics(latest_query_ch)

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        builder,
        analytics,
        budget_ms=30_000,
        max_slices=16,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == [live_id]
    assert stale_ids.isdisjoint(row["id"] for row in result.data)
    classified_ids = [
        span_id
        for _, params in analytics.calls
        for span_id in params.get("candidate_span_ids", ())
    ]
    # Every raw seed is classified locally. The three mutable ids then appear
    # in the skinny cross-slice history probe and receive one additional exact
    # full-window classification; the direct-write live id needs only local
    # classification.
    assert all(classified_ids.count(span_id) == 2 for span_id in stale_ids)
    assert classified_ids.count(live_id) == 1
    assert complete is True
    assert full_window is True


@pytest.mark.integration
def test_trace_root_candidate_seed_real_ch_is_ordered_safe_superset(latest_query_ch):
    client = latest_query_ch
    project = uuid.uuid4()
    other_project = uuid.uuid4()
    same_time = _END - timedelta(minutes=1)
    older_time = _END - timedelta(minutes=2)

    def row(pid, trace_id, span_id, when, *, parent="", deleted=0, version=1):
        return [
            pid,
            trace_id,
            span_id,
            parent,
            f"trace-{trace_id}",
            "root",
            "llm",
            "OK",
            when,
            when,
            when,
            None,
            None,
            {"final_status": "approved"},
            {},
            {},
            deleted,
            version,
        ]

    _insert(
        client,
        [
            row(project, "trace-z", "root-z", same_time),
            row(project, "trace-a", "root-a", same_time),
            row(project, "trace-old", "root-old", older_time),
            # A non-FINAL ``is_deleted = 0`` seed may include this historical
            # live version. Full-window hydration must remove the tombstone.
            row(project, "trace-deleted", "root-deleted", older_time, version=1),
            row(
                project,
                "trace-deleted",
                "root-deleted",
                older_time,
                deleted=1,
                version=2,
            ),
            row(
                project,
                "trace-child-only",
                "child",
                same_time,
                parent="parent",
            ),
            row(other_project, "trace-other-tenant", "root-other", same_time),
        ],
    )

    builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("final_status", "approved")],
    )
    first_sql, first_params = builder.build_root_candidate_seed_page(
        slice_start=_START,
        slice_end=_END,
        limit=2,
    )
    first_page = _query(client, first_sql, first_params)
    assert [row["trace_id"] for row in first_page] == ["trace-z", "trace-a"]

    second_sql, second_params = builder.build_root_candidate_seed_page(
        slice_start=_START,
        slice_end=_END,
        limit=10,
        before_start_time=first_page[-1]["start_time"],
        before_trace_id=first_page[-1]["trace_id"],
    )
    second_page = _query(client, second_sql, second_params)
    assert [row["trace_id"] for row in second_page] == [
        "trace-old",
        "trace-deleted",
    ]
    assert "trace-child-only" not in {row["trace_id"] for row in second_page}
    assert "trace-other-tenant" not in {row["trace_id"] for row in second_page}

    hydration_sql, hydration_params = builder.build_candidate_hydration_query(
        ["trace-deleted"]
    )
    assert _query(client, hydration_sql, hydration_params) == []


@pytest.mark.integration
def test_trace_attribute_enrichment_is_bounded_to_one_result_row_per_trace(
    latest_query_ch,
):
    """A >2k-span trace must stay below the endpoint result-row guard."""

    client = latest_query_ch
    project = uuid.uuid4()
    trace_id = "large-trace"
    when = _START + timedelta(hours=1)
    rows = [
        [
            project,
            trace_id,
            f"large-span-{index}",
            "" if index == 0 else "large-span-0",
            "large-trace",
            "span",
            "llm",
            "OK",
            when + timedelta(microseconds=index),
            when + timedelta(microseconds=index),
            when + timedelta(microseconds=index),
            None,
            None,
            {"attribute": f"value-{index}"},
            {},
            {},
            0,
            1,
        ]
        for index in range(2001)
    ]
    _insert(client, rows)
    builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter()],
    )
    builder.build()
    sql, params = builder.build_span_attributes_query([trace_id])

    result = client.query(
        sql,
        parameters=params,
        settings={
            "max_result_rows": 2000,
            "result_overflow_mode": "throw",
        },
    )

    assert len(result.result_rows) == 1
    root_attribute_row = result.result_rows[0][1]
    root_attribute_count = result.result_rows[0][2]
    attribute_rows = result.result_rows[0][3]
    attribute_row_count = result.result_rows[0][4]
    assert root_attribute_count == 1
    assert root_attribute_row[1] == {"attribute": "value-0"}
    assert len(attribute_rows) == 128
    assert attribute_row_count == 2000


@pytest.mark.integration
def test_session_candidate_final_is_exact_with_stopped_merges(latest_query_ch):
    """Candidate preview dedups updates/key-clears/tombstones before filtering."""

    client = latest_query_ch
    table = f"session_candidate_spans_{uuid.uuid4().hex[:8]}"
    client.command(
        f"""
        CREATE TABLE {table} (
            project_id UUID,
            trace_id String,
            id String,
            parent_span_id String DEFAULT '',
            start_time DateTime64(6, 'UTC'),
            end_time Nullable(DateTime64(6, 'UTC')),
            cost Float64 DEFAULT 0,
            total_tokens Int32 DEFAULT 0,
            trace_session_id Nullable(UUID),
            created_at DateTime64(6, 'UTC'),
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            is_deleted UInt8 DEFAULT 0,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, toStartOfHour(start_time), trace_id, id)
        """
    )
    client.command(
        """
        CREATE TABLE IF NOT EXISTS trace_session_id_remap (
            old_id UUID,
            new_id UUID,
            version DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC')
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY old_id
        """
    )
    # Every version stays in a separate part. FINAL must be what makes the
    # result exact; a background merge cannot accidentally make the test pass.
    client.command(f"SYSTEM STOP MERGES {table}")

    project = uuid.uuid4()
    old_session = uuid.uuid4()
    new_session = uuid.uuid4()
    client.insert(
        "trace_session_id_remap",
        [[old_session, new_session]],
        column_names=["old_id", "new_id"],
    )
    columns = [
        "project_id",
        "trace_id",
        "id",
        "parent_span_id",
        "start_time",
        "end_time",
        "cost",
        "total_tokens",
        "trace_session_id",
        "created_at",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "is_deleted",
        "_version",
    ]
    base_time = _START + timedelta(minutes=20)

    def row(
        trace_id,
        *,
        session_id=old_session,
        attrs=None,
        cost=0,
        tokens=0,
        deleted=0,
        version=1,
        offset=0,
    ):
        when = base_time + timedelta(seconds=offset)
        return [
            project,
            trace_id,
            f"root-{trace_id}",
            "",
            when,
            when + timedelta(seconds=1),
            cost,
            tokens,
            session_id,
            when,
            attrs or {},
            {},
            {},
            deleted,
            version,
        ]

    # Separate inserts create distinct parts while merges are stopped.
    client.insert(
        table,
        [
            row("updated", attrs={"final_status": "Rechazado"}, cost=100, tokens=100),
            row(
                "cleared",
                attrs={"final_status": "Rechazado"},
                cost=10,
                tokens=10,
                offset=1,
            ),
            row(
                "changed",
                attrs={"final_status": "Rechazado"},
                cost=20,
                tokens=20,
                offset=2,
            ),
            row(
                "deleted",
                attrs={"final_status": "Rechazado"},
                cost=30,
                tokens=30,
                offset=3,
            ),
            row(
                "new-alias",
                session_id=new_session,
                attrs={"final_status": "Rechazado"},
                cost=3,
                tokens=4,
                offset=4,
            ),
            row(
                "moved-alias",
                attrs={"final_status": "Rechazado"},
                cost=40,
                tokens=40,
                offset=5,
            ),
        ],
        column_names=columns,
    )
    client.insert(
        table,
        [
            row(
                "updated",
                attrs={"final_status": "Rechazado"},
                cost=1,
                tokens=2,
                version=2,
            ),
            row("cleared", attrs={}, cost=99, tokens=99, version=2, offset=1),
            row(
                "changed",
                attrs={"final_status": "Aceptado"},
                cost=99,
                tokens=99,
                version=2,
                offset=2,
            ),
            row(
                "deleted",
                attrs={"final_status": "Rechazado"},
                cost=30,
                tokens=30,
                deleted=1,
                version=2,
                offset=3,
            ),
            row(
                "moved-alias",
                session_id=new_session,
                attrs={"final_status": "Rechazado"},
                cost=5,
                tokens=6,
                version=2,
                offset=5,
            ),
        ],
        column_names=columns,
    )

    CandidateBuilder = type(
        "CandidateSessionListQueryBuilderV2",
        (SessionListQueryBuilderV2,),
        {"TABLE": table},
    )
    builder = CandidateBuilder(
        project_id=str(project),
        filters=[_time_filter(), _attr("final_status", "Rechazado")],
        page_number=0,
        page_size=10,
        candidate_session_ids=[str(old_session), str(new_session)],
    )
    sql, params = builder.build()

    raw_count = client.query(f"SELECT count() FROM {table}").result_rows[0][0]
    final_count = client.query(f"SELECT count() FROM {table} FINAL").result_rows[0][0]
    rows = _query(client, sql, params)

    assert raw_count == 11
    assert final_count == 5
    assert "use_skip_indexes_if_final = 0" in sql
    assert "is_deleted = 0" not in sql
    assert len(rows) == 1
    assert str(rows[0]["session_id"]) == str(old_session)
    assert rows[0]["total_cost"] == pytest.approx(9.0)
    assert rows[0]["total_tokens"] == 12
    assert rows[0]["traces_count"] == 3


@pytest.mark.integration
def test_latest_list_queries_real_ch_semantics(latest_query_ch):
    client = latest_query_ch
    project = uuid.uuid4()
    other_project = uuid.uuid4()
    root_old = _START + timedelta(minutes=10)
    root_new = _START + timedelta(minutes=20)
    same_time = _START + timedelta(minutes=30)
    stale_session = uuid.uuid4()
    current_session = uuid.uuid4()
    selected_project_version = uuid.uuid4()
    other_project_version = uuid.uuid4()

    def row(
        pid,
        trace_id,
        span_id,
        when,
        attrs,
        *,
        parent="",
        deleted=0,
        version=1,
        name="span",
        session=None,
        project_version=None,
    ):
        return [
            pid,
            trace_id,
            span_id,
            parent,
            f"trace-{trace_id}",
            name,
            "llm",
            "OK",
            when,
            when,
            when,
            session,
            project_version,
            attrs,
            {},
            {},
            deleted,
            version,
        ]

    _insert(
        client,
        [
            # Canonical-root contract: the newer root is approved. The older
            # rejected root must not make final_status=rejected pass.
            row(
                project,
                "multi-root",
                "root-old",
                root_old,
                {"final_status": "rejected"},
                name="old",
            ),
            row(
                project,
                "multi-root",
                "root-new",
                root_new,
                {"final_status": "approved"},
                name="new",
            ),
            # The filtered physical root seed may retain these historical
            # matches. Full-window classification must reject all three.
            row(
                project,
                "root-status-change",
                "root-status-change",
                root_old,
                {"final_status": "approved"},
                version=1,
            ),
            row(
                project,
                "root-status-change",
                "root-status-change",
                root_old,
                {"final_status": "rejected"},
                version=2,
            ),
            row(
                project,
                "root-status-cleared",
                "root-status-cleared",
                root_old,
                {"final_status": "approved"},
                version=1,
            ),
            row(
                project,
                "root-status-cleared",
                "root-status-cleared",
                root_old,
                {},
                version=2,
            ),
            row(
                project,
                "root-status-deleted",
                "root-status-deleted",
                root_old,
                {"final_status": "approved"},
                version=1,
            ),
            row(
                project,
                "root-status-deleted",
                "root-status-deleted",
                root_old,
                {"final_status": "approved"},
                deleted=1,
                version=2,
            ),
            # Generic attributes may live on independent child spans.
            row(project, "generic", "generic-root", root_old, {}, name="generic-root"),
            row(
                project,
                "generic",
                "child-a",
                root_old,
                {"a": "x"},
                parent="generic-root",
            ),
            row(
                project,
                "generic",
                "child-b",
                root_old,
                {"b": "y"},
                parent="generic-root",
            ),
            # Span same-row AND and equal-timestamp keyset fixtures.
            row(project, "span-a", "span-a", same_time, {"a": "x", "b": "y"}),
            row(project, "span-b", "span-b", same_time, {"a": "x", "b": "no"}),
            row(project, "span-c", "span-c", same_time, {"a": "no", "b": "y"}),
            row(
                project,
                "versioned-a",
                "versioned-a",
                same_time,
                {"a": "x"},
                project_version=selected_project_version,
            ),
            row(
                project,
                "versioned-b",
                "versioned-b",
                same_time,
                {"a": "x"},
                project_version=other_project_version,
            ),
            # argMax over Nullable skips NULL unless the value is tuple-wrapped.
            # The latest version clearing project_version must not resurrect v1.
            row(
                project,
                "version-cleared",
                "version-cleared",
                same_time,
                {"a": "x"},
                version=1,
                project_version=selected_project_version,
            ),
            row(
                project,
                "version-cleared",
                "version-cleared",
                same_time,
                {"a": "x"},
                version=2,
                project_version=None,
            ),
            # Latest tombstone wins over an older live matching version.
            row(
                project,
                "deleted",
                "span-deleted",
                same_time,
                {"a": "x", "b": "y"},
                version=1,
            ),
            row(
                project,
                "deleted",
                "span-deleted",
                same_time,
                {"a": "x", "b": "y"},
                deleted=1,
                version=2,
            ),
            # A historical seed can carry the old session. The matcher must
            # resolve the canonical root's latest session before accepting it.
            row(
                project,
                "session-change",
                "session-root",
                root_old,
                {"a": "x"},
                version=1,
                session=stale_session,
            ),
            row(
                project,
                "session-change",
                "session-root",
                root_old,
                {"a": "x"},
                version=2,
                session=current_session,
            ),
            row(
                project,
                "session-cleared",
                "session-cleared-root",
                root_old,
                {"a": "x"},
                version=1,
                session=stale_session,
            ),
            row(
                project,
                "session-cleared",
                "session-cleared-root",
                root_old,
                {"a": "x"},
                version=2,
                session=None,
            ),
            # Same values in another tenant must never leak.
            row(other_project, "other", "other-span", same_time, {"a": "x", "b": "y"}),
        ],
    )

    span_builder = SpanListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("a", "x"), _attr("b", "y")],
        page_size=25,
    )
    span_sql, span_params = span_builder.build_latest_attribute_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )
    span_rows = _query(client, span_sql, span_params)
    assert {row["id"] for row in span_rows} == {"span-a"}

    list_id_sql, list_id_params = span_builder.build_latest_attribute_list_ids(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )
    assert _query(client, list_id_sql, list_id_params) == [
        {
            "id": "span-a",
            "start_time": same_time.replace(tzinfo=None),
            "created_at": same_time.replace(tzinfo=None),
        }
    ]

    content_sql, content_params = span_builder.build_content_query(["span-a"])
    hydrated = _query(client, content_sql, content_params)
    assert len(hydrated) == 1
    assert hydrated[0]["id"] == "span-a"
    assert hydrated[0]["trace_id"] == "span-a"
    assert hydrated[0]["attrs_string"]["a"] == "x"

    preview_sql, preview_params = span_builder.build_preview_hydration_query(["span-a"])
    preview = _query(client, preview_sql, preview_params)
    assert len(preview) == 1
    assert preview[0]["attrs_string"] == {"a": "x", "b": "y"}
    assert "input" not in preview[0]

    span_id_sql, span_id_params = span_builder.build_latest_attribute_id_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
        sampling_salt="task-real-ch",
        sampling_rate=100,
    )
    assert {row["id"] for row in _query(client, span_id_sql, span_id_params)} == {
        "span-a"
    }

    keyset_sql, keyset_params = span_builder.build_latest_attribute_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
        before_start_time=same_time,
        before_id="span-b",
    )
    assert [row["id"] for row in _query(client, keyset_sql, keyset_params)] == [
        "span-a"
    ]

    version_builder = SpanListQueryBuilderV2(
        project_id=str(project),
        project_version_id=str(selected_project_version),
        filters=[_time_filter(), _attr("a", "x")],
        page_size=25,
    )
    version_sql, version_params = version_builder.build_latest_attribute_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )
    assert {row["id"] for row in _query(client, version_sql, version_params)} == {
        "versioned-a"
    }

    approved_builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("final_status", "approved")],
    )
    seed_sql, seed_params = approved_builder.build_root_candidate_seed_page(
        slice_start=_START,
        slice_end=_END,
        limit=100,
    )
    seed_rows = _query(client, seed_sql, seed_params)
    seed_ids = {row["trace_id"] for row in seed_rows}
    # The non-FINAL physical seed is intentionally a superset: an older live
    # version of a tombstoned root may appear, but child spans and another
    # tenant may not. The full-window hydration below removes that stale row.
    assert "deleted" in seed_ids
    assert "other" not in seed_ids
    assert seed_rows == sorted(
        seed_rows,
        key=lambda row: (row["start_time"], row["trace_id"]),
        reverse=True,
    )
    filtered_seed_sql, filtered_seed_params = (
        approved_builder.build_filtered_root_candidate_seed_page(
            slice_start=_START,
            slice_end=_END,
            limit=100,
        )
    )
    filtered_seed_ids = {
        row["trace_id"]
        for row in _query(client, filtered_seed_sql, filtered_seed_params)
    }
    assert {
        "multi-root",
        "root-status-change",
        "root-status-cleared",
        "root-status-deleted",
    }.issubset(filtered_seed_ids)
    filtered_match_sql, filtered_match_params = (
        approved_builder.build_latest_filter_match_query(
            sorted(filtered_seed_ids),
            filters=[_attr("final_status", "approved")],
        )
    )
    assert _query(client, filtered_match_sql, filtered_match_params) == [
        {"trace_id": "multi-root"}
    ]
    stale_hydration_sql, stale_hydration_params = (
        approved_builder.build_candidate_hydration_query(["deleted"])
    )
    assert _query(client, stale_hydration_sql, stale_hydration_params) == []

    approved_sql, approved_params = approved_builder.build_latest_filter_match_query(
        ["multi-root"],
        filters=[_attr("final_status", "approved")],
    )
    assert _query(client, approved_sql, approved_params) == [{"trace_id": "multi-root"}]
    approved_id_sql, approved_id_params = approved_builder.build_latest_root_id_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )
    assert _query(client, approved_id_sql, approved_id_params) == [
        {
            "trace_id": "multi-root",
            "eval_order_start_time": root_new.replace(tzinfo=None),
        }
    ]

    rejected_builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("final_status", "rejected")],
    )
    rejected_sql, rejected_params = rejected_builder.build_latest_filter_match_query(
        ["multi-root"],
        filters=[_attr("final_status", "rejected")],
    )
    assert _query(client, rejected_sql, rejected_params) == []
    rejected_id_sql, rejected_id_params = rejected_builder.build_latest_root_id_page(
        slice_start=_START,
        slice_end=_END,
        limit=25,
    )
    assert {
        row["trace_id"] for row in _query(client, rejected_id_sql, rejected_id_params)
    } == {"root-status-change"}

    generic_builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("a", "x"), _attr("b", "y")],
    )
    generic_sql, generic_params = generic_builder.build_latest_filter_match_query(
        ["generic"],
        filters=[_attr("a", "x"), _attr("b", "y")],
    )
    assert _query(client, generic_sql, generic_params) == [{"trace_id": "generic"}]

    stale_builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _session_filter(stale_session), _attr("a", "x")],
    )
    stale_sql, stale_params = stale_builder.build_latest_filter_match_query(
        ["session-change"],
        filters=[_session_filter(stale_session), _attr("a", "x")],
    )
    assert _query(client, stale_sql, stale_params) == []

    cleared_sql, cleared_params = stale_builder.build_latest_filter_match_query(
        ["session-cleared"],
        filters=[_session_filter(stale_session), _attr("a", "x")],
    )
    assert _query(client, cleared_sql, cleared_params) == []

    current_builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _session_filter(current_session), _attr("a", "x")],
    )
    current_sql, current_params = current_builder.build_latest_filter_match_query(
        ["session-change"],
        filters=[_session_filter(current_session), _attr("a", "x")],
    )
    assert _query(client, current_sql, current_params) == [
        {"trace_id": "session-change"}
    ]

    hydration_sql, hydration_params = approved_builder.build_candidate_hydration_query(
        ["multi-root"]
    )
    hydrated = _query(client, hydration_sql, hydration_params)
    assert len(hydrated) == 1
    assert hydrated[0]["span_name"] == "new"
    assert hydrated[0]["start_time"].replace(tzinfo=UTC) == root_new
    assert hydrated[0]["attrs_string"]["final_status"] == "approved"

    attr_sql, attr_params = approved_builder.build_span_attributes_query(["multi-root"])
    attribute_rows = _query(client, attr_sql, attr_params)
    assert len(attribute_rows) == 1
    assert attribute_rows[0]["trace_id"] == "multi-root"
    assert attribute_rows[0]["root_attribute_count"] == 2
    assert attribute_rows[0]["root_attribute_row"][1] == {"final_status": "approved"}
    assert attribute_rows[0]["attribute_rows"] == []
    assert attribute_rows[0]["attribute_row_count"] == 0


@pytest.mark.integration
def test_continuous_final_status_real_ch_uses_changed_ids_and_latest_state(
    latest_query_ch,
):
    """A cursor finds writes, while argMax classifies their complete current row."""

    client = latest_query_ch
    project = uuid.uuid4()
    other_project = uuid.uuid4()
    old_root_time = _START + timedelta(minutes=10)
    new_root_time = _START + timedelta(minutes=20)
    span_time = _START + timedelta(minutes=30)
    cursor_version = 100

    def row(
        pid,
        trace_id,
        span_id,
        when,
        final_status,
        *,
        parent="",
        deleted=0,
        version=90,
    ):
        return [
            pid,
            trace_id,
            span_id,
            parent,
            f"trace-{trace_id}",
            "root",
            "llm",
            "OK",
            when,
            when,
            when,
            None,
            None,
            ({"final_status": final_status} if final_status is not None else {}),
            {},
            {},
            deleted,
            version,
        ]

    _insert(
        client,
        [
            # Span transitions after the cursor: only the latest matching live
            # value is selected. An unchanged old match is not a tail candidate.
            row(
                project,
                "span-match",
                "span-match",
                span_time,
                "rejected",
                parent="span-parent",
            ),
            row(
                project,
                "span-match",
                "span-match",
                span_time,
                "approved",
                parent="span-parent",
                version=110,
            ),
            row(
                project,
                "span-stale",
                "span-stale",
                span_time,
                "approved",
                parent="span-parent",
            ),
            row(
                project,
                "span-stale",
                "span-stale",
                span_time,
                "rejected",
                parent="span-parent",
                version=110,
            ),
            row(
                project,
                "span-deleted",
                "span-deleted",
                span_time,
                "approved",
                parent="span-parent",
            ),
            row(
                project,
                "span-deleted",
                "span-deleted",
                span_time,
                "approved",
                parent="span-parent",
                deleted=1,
                version=110,
            ),
            row(
                project,
                "span-key-cleared",
                "span-key-cleared",
                span_time,
                "approved",
                parent="span-parent",
            ),
            row(
                project,
                "span-key-cleared",
                "span-key-cleared",
                span_time,
                None,
                parent="span-parent",
                version=110,
            ),
            row(
                project,
                "span-unchanged",
                "span-unchanged",
                span_time,
                "approved",
                parent="span-parent",
            ),
            row(
                project,
                "span-before-task",
                "span-before-task",
                _START - timedelta(minutes=1),
                "approved",
                parent="span-parent",
                version=110,
            ),
            row(
                other_project,
                "span-other",
                "span-other",
                span_time,
                "approved",
                parent="span-parent",
                version=110,
            ),
            # A changed older matching root makes the trace a candidate, but the
            # newer unchanged non-matching root remains canonical.
            row(project, "trace-multi", "root-old", old_root_time, "approved"),
            row(
                project,
                "trace-multi",
                "root-old",
                old_root_time,
                "rejected",
                version=110,
            ),
            row(project, "trace-multi", "root-new", new_root_time, "approved"),
            # A changed canonical root can enter the task; a latest tombstone
            # cannot resurrect its older matching version.
            row(project, "trace-match", "trace-match-root", new_root_time, "approved"),
            row(
                project,
                "trace-match",
                "trace-match-root",
                new_root_time,
                "rejected",
                version=110,
            ),
            row(
                project,
                "trace-deleted",
                "trace-deleted-root",
                new_root_time,
                "rejected",
            ),
            row(
                project,
                "trace-deleted",
                "trace-deleted-root",
                new_root_time,
                "rejected",
                deleted=1,
                version=110,
            ),
            # Deleting the newest root promotes the previous live root. The
            # promoted root is then the canonical row whose status is tested.
            row(
                project,
                "trace-promoted",
                "trace-promoted-old",
                old_root_time,
                "rejected",
            ),
            row(
                project,
                "trace-promoted",
                "trace-promoted-new",
                new_root_time,
                "approved",
            ),
            row(
                project,
                "trace-promoted",
                "trace-promoted-new",
                new_root_time,
                "approved",
                deleted=1,
                version=110,
            ),
            # Equal-time roots use the same deterministic id-desc tie-break as
            # trace hydration. Updating the non-canonical root must not win.
            row(
                project,
                "trace-equal-time",
                "root-z",
                new_root_time,
                "approved",
            ),
            row(
                project,
                "trace-equal-time",
                "root-a",
                new_root_time,
                "rejected",
                version=110,
            ),
            row(
                project,
                "trace-key-cleared",
                "trace-key-cleared-root",
                new_root_time,
                "rejected",
            ),
            row(
                project,
                "trace-key-cleared",
                "trace-key-cleared-root",
                new_root_time,
                None,
                version=110,
            ),
            row(
                project,
                "trace-before-task",
                "trace-before-task-root",
                _START - timedelta(minutes=1),
                "rejected",
                version=110,
            ),
            row(
                project,
                "trace-unchanged",
                "trace-unchanged-root",
                new_root_time,
                "rejected",
            ),
            row(
                other_project,
                "trace-other",
                "trace-other-root",
                new_root_time,
                "rejected",
                version=110,
            ),
        ],
    )

    span_builder = SpanListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("final_status", "approved")],
    )
    span_sql, span_params = span_builder.build_latest_attribute_id_page(
        slice_start=_START,
        slice_end=_END,
        limit=None,
        sampling_salt="continuous-span",
        sampling_rate=100,
        changed_since_version=cursor_version,
    )
    assert "FINAL" not in span_sql
    assert "_version >= %(latest_span_changed_since_version)s" in span_sql
    assert "latest_span_limit" not in span_params
    assert {row["id"] for row in _query(client, span_sql, span_params)} == {
        "span-match"
    }

    trace_builder = TraceListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _attr("final_status", "rejected")],
    )
    trace_sql, trace_params = trace_builder.build_latest_root_id_page(
        slice_start=_START,
        slice_end=_END,
        limit=None,
        sampling_salt="continuous-trace",
        sampling_rate=100,
        changed_since_version=cursor_version,
    )
    assert "FINAL" not in trace_sql
    assert "_version >= %(latest_root_changed_since_version)s" in trace_sql
    assert "latest_root_limit" not in trace_params
    expected_trace_ids = {"trace-match", "trace-promoted"}
    assert {
        row["trace_id"] for row in _query(client, trace_sql, trace_params)
    } == expected_trace_ids
    # Re-reading the overlap is idempotent: the same write-version candidates
    # classify to the same current trace set.
    assert {
        row["trace_id"] for row in _query(client, trace_sql, trace_params)
    } == expected_trace_ids
