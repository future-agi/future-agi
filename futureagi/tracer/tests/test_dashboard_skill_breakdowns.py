"""Skill cohort metrics and dashboard dimensions; cloud-only execution."""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
    _prefix_spans_columns,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import ClickHouseFilterBuilderV2
from tracer.tests.test_skill_filters import (
    DIGEST, END, OTHER_VERSION, PROJECT, SKILL, START, VERSION,
    insert_rows, skill, span_row,
)
from tracer.tests.test_skill_filters import skill_query_client as skill_query_client


def catalog(project=PROJECT):
    return {
        "identities": (
            (project, SKILL, VERSION, DIGEST),
            (project, SKILL, OTHER_VERSION, DIGEST),
        ),
        "skill": {SKILL: "refund"},
        "skill_version": {VERSION: "refund v3", OTHER_VERSION: "refund v2"},
    }


@pytest.fixture
def cohort_catalog(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.filters.resolve_skill_cohort_catalog",
        lambda projects: catalog(str(projects[0])),
    )


def dashboard(dimension="skill_version", **overrides):
    return DashboardQueryBuilder({
        "project_ids": [PROJECT], "granularity": "hour",
        "time_range": {"custom_start": START.isoformat(), "custom_end": END.isoformat()},
        "breakdowns": [{"type": "system_metric", "name": dimension}],
        **overrides,
    })


@pytest.mark.parametrize("dimension", ["skill", "skill_version", "skill_agent"])
def test_skill_dimensions_use_trace_cohorts_not_load_span_averages(dimension, cohort_catalog):
    builder = dashboard(dimension)
    resolved = builder._resolve_all_breakdowns({})
    assert resolved == [{"type": "skill_cohort", "expr": "cohort_key", "join": None}]
    sql, params = builder.build_metric_query({
        "type": "system_metric", "id": "latency", "aggregation": "p95",
    })
    assert "latency_p95 AS value" in sql
    assert "quantileExactIf(0.95)(t.root_latency, t.has_root)" in sql
    assert "GROUP BY project_id, trace_id, cohort_key, time_bucket" in sql
    assert "skill_digest) IN" in sql
    assert catalog()["identities"] in params.values()
    assert "skill_trace_metrics AS" in sql
    assert "INNER JOIN skill_trace_metrics" in sql


def test_cohort_source_does_not_split_into_unselected_versions():
    compiler = ClickHouseFilterBuilderV2(project_id=PROJECT)
    sql, params = compiler.skill_cohort_rows_sql(
        [skill("version_id", VERSION)], dimension="skill_version",
        verified_identities=catalog()["identities"],
    )
    assert "skill_version_id = %(cohort_skill_1)s" in sql
    assert params["cohort_skill_1"] == VERSION
    assert "(toString(project_id), skill_id, skill_version_id, skill_digest)" in sql


def test_unverified_catalog_is_never_silently_accepted():
    compiler = ClickHouseFilterBuilderV2(project_id=PROJECT)
    with pytest.raises(ValueError, match="authoritative"):
        compiler.skill_cohort_rows_sql([], dimension="skill")
    sql, _ = compiler.skill_cohort_rows_sql([], dimension="skill", verified_identities=())
    assert sql.endswith("WHERE 0 = 1")


def test_graph_split_preserves_all_skill_items_and_returns_nonadditive_series(cohort_catalog):
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT, observe_type="trace", split_by="skill_version",
        start_date=START, end_date=END,
        filters=[skill("id", SKILL), skill("version", 3), skill("agent", "A")],
    )
    sql, params = builder.build()
    assert "skill_trace_metrics AS" in sql
    assert "failed_request_rate" in sql
    assert "HAVING (max(graph_row_match_0) = 1)" in sql
    assert "avg(t.trace_cost) AS cost_per_trace" in sql
    assert "toString(project_id)" in sql
    assert params["start_date"] == START and params["end_date"] == END
    result = builder.format_result([
        {"cohort_key": VERSION, "time_bucket": START, "traces": 2,
         "incomplete": 2, "latency_p95": float("nan"), "latency_p50": float("nan")},
    ], [])
    assert result["totals_note"] == "cohorts overlap; not additive"
    assert result["series"][0]["label"] == "refund v3"
    assert result["series"][0]["data"][0]["latency_p95"] is None


def test_dashboard_filter_qualifier_does_not_rewrite_nested_scope():
    builder = dashboard(breakdowns=[])
    filters = [skill("id", SKILL), skill("version", 3)]
    clauses, params = builder._build_where_clauses("spans", "start_time", filters, {})
    clause = next(clause for clause in clauses if clause.startswith("/* skill membership */"))
    assert clause.count("FROM skill_trace_links FINAL") == 1
    qualified = _prefix_spans_columns(clause)
    assert "(s.project_id, s.trace_id) IN (SELECT project_id, trace_id FROM" in qualified
    assert "FROM skill_trace_links FINAL WHERE project_id IN %(project_ids)s" in qualified
    assert "s.start_time" not in qualified
    assert SKILL in params.values()


def test_dashboard_adapter_canonical_filter_retains_skill_group():
    builder = dashboard(breakdowns=[])
    canonical = skill("version_id", VERSION, group=2)
    adapted = {"metric_type": "system_metric", "metric_name": "skill.version_id",
               "operator": "equal_to", "value": VERSION, "canonical_filter": canonical}
    clauses, _ = builder._build_where_clauses("spans", "start_time", [adapted], {})
    assert any("FROM skill_trace_links FINAL" in clause for clause in clauses)


def test_unsupported_cohort_combinations_fail_instead_of_averaging_percentiles():
    with pytest.raises(InvalidMetricCombinationError, match="one project"):
        dashboard(project_ids=[PROJECT, str(uuid4())]).build_metric_query({"id": "latency", "aggregation": "p95"})
    with pytest.raises(InvalidMetricCombinationError):
        dashboard().build_metric_query({"type": "eval_metric", "id": str(uuid4())})
    with pytest.raises(InvalidMetricCombinationError):
        dashboard().build_metric_query({"id": "error_rate", "aggregation": "avg"})
    with pytest.raises(ValueError, match="trace grain"):
        TimeSeriesQueryBuilder(project_id=PROJECT, split_by="skill_version").build()


@pytest.mark.integration
def test_live_overlapping_cohorts_count_roots_not_loads_or_child_errors(skill_query_client, cohort_catalog):
    client = skill_query_client
    project = str(uuid4())
    rows = [
        span_row(project, "ok"), span_row(project, "ok"),
        span_row(project, "ok", version=OTHER_VERSION),
        span_row(project, "bad"), span_row(project, "incomplete"),
        span_row(project, "ok", name="request", parent_span_id="", latency_ms=100,
                 cost=1, total_tokens=10, span_attributes={}),
        span_row(project, "bad", name="request", parent_span_id="", status="ERROR",
                 latency_ms=200, cost=2, total_tokens=20, span_attributes={}),
        span_row(project, "ok", name="tool", status="ERROR", latency_ms=900,
                 cost=3, total_tokens=30, span_attributes={}),
    ]
    mismatch = span_row(project, "mismatch")
    mismatch["span_attributes"]["fi.skill.digest"] = "sha256:" + "b" * 64
    rows.append(mismatch)
    insert_rows(client, rows)
    builder = TimeSeriesQueryBuilder(
        project_id=project, observe_type="trace", split_by="skill_version",
        start_date=START, end_date=END,
    )
    sql, params = builder.build()
    result = {row["cohort_key"]: row for row in client.query(sql, parameters=params).named_results()}
    assert result[VERSION]["traces"] == 3
    assert result[VERSION]["failed_requests"] == 1
    assert result[VERSION]["incomplete"] == 1
    assert result[VERSION]["failed_request_rate"] == pytest.approx(0.5)
    assert result[VERSION]["latency_p95"] == 200
    assert result[OTHER_VERSION]["traces"] == 1
    assert result[OTHER_VERSION]["failed_requests"] == 0
    assert result[OTHER_VERSION]["latency_p95"] == 100


@pytest.mark.integration
def test_cohort_metrics_include_whole_trace_across_window_boundaries(skill_query_client, cohort_catalog):
    client = skill_query_client
    project = str(uuid4())
    before = START - timedelta(seconds=1)
    after = END + timedelta(seconds=1)
    insert_rows(client, [
        span_row(project, "straddles", cost=0),
        span_row(project, "straddles", name="request", parent_span_id="", status="ERROR",
                 start_time=before, created_at=before, latency_ms=500, cost=2, span_attributes={}),
        span_row(project, "straddles", name="tool", start_time=after, created_at=after,
                 cost=3, span_attributes={}),
    ])
    sql, params = TimeSeriesQueryBuilder(
        project_id=project, observe_type="trace", split_by="skill_version",
        start_date=START, end_date=END,
    ).build()
    rows = list(client.query(sql, parameters=params).named_results())
    assert len(rows) == 1
    assert rows[0]["incomplete"] == 0
    assert rows[0]["failed_requests"] == 1
    assert rows[0]["cost_per_trace"] == pytest.approx(5)


@pytest.mark.integration
def test_live_selected_version_split_and_dashboard_share_source(skill_query_client, cohort_catalog):
    client = skill_query_client
    project = str(uuid4())
    insert_rows(client, [
        span_row(project, "both"), span_row(project, "both", version=OTHER_VERSION),
        span_row(project, "both", name="request", parent_span_id="", span_attributes={}),
    ])
    builder = TimeSeriesQueryBuilder(
        project_id=project, observe_type="trace", split_by="skill_version",
        start_date=START, end_date=END, filters=[skill("version_id", VERSION)],
    )
    sql, params = builder.build()
    result = list(client.query(sql, parameters=params).named_results())
    assert len(result) == 1 and result[0]["cohort_key"] == VERSION
    widget = dashboard(project_ids=[project], filters=[skill("version_id", VERSION)])
    sql, params = widget.build_metric_query({"id": "trace_count", "aggregation": "count_distinct"})
    rows = list(client.query(sql, parameters=params).named_results())
    assert len(rows) == 1 and rows[0]["value"] == result[0]["traces"]
