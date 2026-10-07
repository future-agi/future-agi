"""SQL-string checks for Observe count-mode eval reads.

These do not execute ClickHouse. They fail if the count query uses FINAL,
moves the live predicate inside the version collapse, or drops the count
columns the Observe pivots read.
"""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import django

django.setup()

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.trace_detail_reads import TraceDetailReadBuilder


def _assert_live_outside_collapse(sql):
    collapsed = " ".join(sql.split())
    assert "FINAL" not in collapsed
    assert "LIMIT 1 BY id" in collapsed or "GROUP BY id" in collapsed
    where_at = collapsed.rfind("WHERE latest_")
    limit_at = collapsed.find("LIMIT 1 BY id")
    group_at = collapsed.find("GROUP BY id")
    collapse_at = limit_at if limit_at != -1 else group_at
    assert collapse_at != -1
    assert where_at > collapse_at, sql


def test_observe_span_count_sql_projects_counts_without_final():
    builder = SpanListQueryBuilder(
        project_id="00000000-0000-4000-8000-000000000001",
        eval_config_ids=["cfg-1"],
    )
    sql, params = builder.build_eval_query(
        ["span-1"],
        span_entities=[("trace-1", "span-1")],
        count_mode=True,
    )
    assert sql
    assert "pass_count" in sql
    assert "fail_count" in sql
    assert "AS target_type" in sql
    assert params["span_ids"] == ("span-1",)
    _assert_live_outside_collapse(sql)

    percentage_sql, _ = builder.build_eval_query(
        ["span-1"],
        span_entities=[("trace-1", "span-1")],
        count_mode=False,
    )
    assert "AS pass_count" not in percentage_sql


def test_observe_trace_count_sql_projects_counts_without_final():
    builder = TraceListQueryBuilderV2(
        project_id="00000000-0000-4000-8000-000000000001",
        eval_config_ids=["cfg-1"],
    )
    sql, _params = builder.build_eval_query(["trace-1"], count_mode=True)
    assert sql
    assert "pass_count" in sql
    assert "fail_count" in sql
    _assert_live_outside_collapse(sql)


def test_detail_eval_sql_projects_selection_fields_without_final():
    builder = TraceDetailReadBuilder(
        trace_id="trace-1",
        project_ids=["00000000-0000-4000-8000-000000000001"],
    )
    sql, params = builder.build_eval_query(
        project_id="00000000-0000-4000-8000-000000000001",
        span_ids=["span-1"],
        eval_config_ids=["cfg-1"],
    )
    assert "created_at" in sql
    assert "target_type" in sql
    assert "log_id" in sql
    assert "FINAL" not in sql
    assert "latest_is_deleted = 0" in sql
    assert params["detail_eval_trace_id"] == "trace-1"
    _assert_live_outside_collapse(sql)
