"""Trace-name projections must resolve roots even before trace_dict has loaded."""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from ai_tools.tools.web.trace_explorer import TraceExplorerTool
from tracer.services.clickhouse import cluster_rca_spans
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.trace_name_sql import TRACE_NAME_SQL
from tracer.services.clickhouse.v2.query_builders.observe_usage import (
    ObserveTraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.span_reader import (
    _DATA_KEYS,
    _PROJECTION_SQL,
    _READ_COLUMNS,
)


@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilder, TraceListQueryBuilderV2]
)
def test_trace_list_search_sort_and_hydration_use_root_expression(builder_cls):
    project = str(uuid.uuid4())
    builder = builder_cls(
        project_id=project,
        sort_params=[{"column_id": "name", "direction": "asc"}],
    )
    # Arbitrary trace-list sorting is fail-closed in the public builder;
    # verify its mapped ORDER BY expression separately from that guard.
    with pytest.raises(ValueError, match="unsupported_filter_modifiers"):
        builder.build()
    order = ClickHouseFilterBuilder().translate_sort(
        builder.sort_params, field_map=builder.SORT_FIELD_MAP
    )
    assert order == f"ORDER BY {TRACE_NAME_SQL} ASC"
    assert builder.SORT_FIELD_MAP["trace_name"] == TRACE_NAME_SQL
    # A full-window search must use the bounded selector rather than build();
    # its root predicate is compiled by latest_filter_predicates (filters lane).
    search_builder = builder_cls(project_id=project, search="Executor")
    assert search_builder.supports_bounded_filter_scan()
    sql, _ = search_builder.build_id_query()
    assert f"{TRACE_NAME_SQL} ILIKE %(search)s" in sql


def test_v1_content_hydration_resolves_legacy_empty_name():
    builder = TraceListQueryBuilder(project_id=str(uuid.uuid4()))
    sql, _ = builder.build_filter_page_hydration_query(
        [
            {
                "project_id": builder.project_id,
                "trace_id": str(uuid.uuid4()),
                "root_span_id": str(uuid.uuid4()),
                "start_time": datetime(2026, 7, 1, tzinfo=UTC),
            }
        ]
    )
    assert f"argMax({TRACE_NAME_SQL}, _peerdb_version)" in sql


def test_v2_root_replay_projects_resolved_name():
    builder = TraceListQueryBuilderV2(project_id=str(uuid.uuid4()))
    sql, _ = builder._root_replay_query(
        [(builder.project_id, "trace", "root", 1_000_000, "unknown", "", 0, 1)],
        content=False,
    )
    assert "argMax(tuple(start_time, parent_span_id, is_deleted, " in sql
    assert TRACE_NAME_SQL in sql
    assert "AS trace_name" in sql


def test_observe_usage_root_tuple_resolves_stored_blank_name():
    builder = ObserveTraceListQueryBuilderV2(project_id=str(uuid.uuid4()))
    sql, _ = builder.build_usage_sorted_query(
        [{"column_id": "cost", "direction": "asc"}]
    )
    assert f"({TRACE_NAME_SQL})" in sql
    assert "root.6 AS trace_name" in sql


def test_span_reader_projection_keeps_positional_decode():
    assert len(_READ_COLUMNS) == len(_DATA_KEYS)
    assert _PROJECTION_SQL["trace_name"] == f"{TRACE_NAME_SQL} AS trace_name"


def test_cluster_rca_raw_reads_resolve_root_and_keep_alias():
    assert f"{TRACE_NAME_SQL} AS trace_name" in cluster_rca_spans._SPAN_COLS
    queries = []

    def capture(sql, params):
        queries.append(sql)
        return []

    with patch.object(cluster_rca_spans, "_execute_read", side_effect=capture):
        cluster_rca_spans.trace_roots("project", ["trace"])
        cluster_rca_spans.traces_in_session("project", "session")
    assert len(queries) == 2
    for sql in queries:
        assert f"anyIf({TRACE_NAME_SQL}, parent_span_id = '')" in sql
        assert "parent_span_id, name" in sql


def test_trace_explorer_falls_back_on_blank_name():
    # The fallback must test truthiness, not just key presence: a child with
    # stored trace_name='' should still display the span's own name.
    import inspect

    assert (
        "data.get('trace_name') or data.get('name') or 'unnamed'"
        in inspect.getsource(TraceExplorerTool)
    )
