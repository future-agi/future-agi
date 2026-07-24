"""
Pin the v2 SpanList builder's output: legacy column refs in the SQL it
produces are rewritten to the new CH 25.3 schema.

These tests don't hit a real ClickHouse — they assert the COMPILED SQL
STRING contains only v2 column names. End-to-end parity (same SQL, same
rows) is enforced by the parity-shadow harness when v1 and v2 run in
production side-by-side.
"""

from __future__ import annotations

from django.test import override_settings

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-2222-2222-222222222222"


def _make_builder(filters=None, sort_params=None, eval_config_ids=None):
    return SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=50,
        filters=filters or [],
        sort_params=sort_params or [],
        eval_config_ids=eval_config_ids or [],
        annotation_label_ids=[],
    )


def test_build_main_query_uses_v2_columns():
    sql, params = _make_builder().build()
    # No legacy column references
    for legacy in (
        "_peerdb_is_deleted",
        "_peerdb_version",
        "span_attr_str",
        "span_attr_num",
        "span_attr_bool",
        "span_attributes_raw",
        "metadata_map",
    ):
        assert legacy not in sql, f"legacy column {legacy!r} leaked into v2 SQL"
    # And the canonical replacements ARE present where v1 would have used them
    assert "is_deleted" in sql, "v2 SQL must reference the is_deleted column"


def test_build_count_query_uses_v2_columns():
    sql, params = _make_builder().build_count_query()
    for legacy in ("_peerdb_is_deleted", "span_attr_str", "span_attr_num"):
        assert legacy not in sql
    assert "is_deleted" in sql


def test_build_content_query_uses_typed_json_overflow_column():
    # build_content_query reads span_attributes_raw in v1 — v2 must read the
    # typed JSON column (attributes_extra) via toJSONString() wrapping to keep
    # the row-key shape downstream Python expects: row["span_attributes_raw"]
    # still returns a JSON STRING (just sourced from the typed column).
    sql, params = _make_builder().build_content_query(span_ids=["sp1", "sp2"])

    # No legacy column REFERENCE — only legitimate AS alias is allowed
    assert "_peerdb_is_deleted" not in sql
    # The v2 typed column IS used directly (no toJSONString wrapping needed
    # for the span list builder since it returns the JSON column as-is)
    assert "attributes_extra" in sql
    # Pagination via parameterized id list (or literal in v1 base)
    assert len(params) > 0 or "%(content_span_ids)s" in sql


def test_filter_compiler_class_yields_v2_columns():
    # Mirrors the filter compiler test, but exercised via the SpanList path.
    # If the v1 base ever stops respecting the post-rewrite (e.g. emits SQL
    # that bypasses translate()), this test catches it.
    sql, _ = _make_builder(
        filters=[
            {
                "column_id": "model",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4o-mini",
                },
            }
        ],
    ).build()
    # The compiled query references the model column (not via the legacy
    # `span_attr_str['model']` form, which is what v1 would have produced
    # for an attribute-key match).
    assert "_peerdb_is_deleted" not in sql
    assert "span_attr_str" not in sql


def test_v2_builder_output_includes_critical_settings():
    """Every v2 builder's build*() output MUST end with the SETTINGS clause
    that enables use_skip_indexes_if_final, optimize_use_projections,
    optimize_aggregation_in_order. These are required for sub-second query
    behavior at trillion-row scale — see DECISIONS #026.
    """
    for method in ("build", "build_count_query"):
        sql, _ = getattr(_make_builder(), method)()
        assert "SETTINGS" in sql, (
            f"{method}() output missing SETTINGS clause — required for "
            f"trillion-row scale (use_skip_indexes_if_final etc.)"
        )
        assert "use_skip_indexes_if_final = 1" in sql
        assert "optimize_use_projections = 1" in sql
        assert "optimize_aggregation_in_order = 1" in sql

    # build_content_query takes args
    sql, _ = _make_builder().build_content_query(span_ids=["s1"])
    assert "use_skip_indexes_if_final = 1" in sql


# ---------------------------------------------------------------------------
# Eval-logger table routing: the Phase-2 score query (build_eval_query) is
# excluded from the v2 rewrite, so it must follow CH25_EVAL_LOGGER_TABLE on its
# own — else a v2 deployment 500s on the dropped legacy tracer_eval_logger.
# (Discovery-query routing is covered in test_eval_config_ids_resolution.py.)
# ---------------------------------------------------------------------------


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_build_eval_query_routes_to_v2_table():
    sql, _ = _make_builder(eval_config_ids=[EVAL_CONFIG_ID]).build_eval_query(
        span_ids=["sp1", "sp2"]
    )
    assert "tracer_eval_logger_v2" in sql
    # PERF: no table-level FINAL — dedup happens on the page-scoped slice via
    # `ORDER BY _version DESC LIMIT 1 BY id` inside the subquery.
    assert "tracer_eval_logger_v2 FINAL" not in sql
    assert "LIMIT 1 BY id" in sql
    assert "is_deleted = 0" in sql
    assert "_peerdb_is_deleted" not in sql
    assert "deleted IS NULL" not in sql


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
def test_build_eval_query_keeps_legacy_table_and_predicate():
    sql, _ = _make_builder(eval_config_ids=[EVAL_CONFIG_ID]).build_eval_query(
        span_ids=["sp1", "sp2"]
    )
    assert "tracer_eval_logger" in sql
    assert "tracer_eval_logger FINAL" not in sql
    assert "LIMIT 1 BY id" in sql
    assert "tracer_eval_logger_v2" not in sql
    assert "_peerdb_is_deleted = 0" in sql
    assert "deleted = 0 OR deleted IS NULL" in sql


# ── perf-fix shapes: tiebreak, progressive slices, created_at bounds ─────────


def test_default_sort_has_id_tiebreak():
    """P0-1: ClickHouse's parallel sort is not stable — without an `id`
    tiebreak, equal-start_time rows permute between requests and the
    prefix-dedup pagination (page_dedup.py) can duplicate/skip rows across
    pages. The default ORDER BY must carry the deterministic tiebreak."""
    sql, _ = _make_builder().build()
    assert "ORDER BY start_time DESC, id DESC" in sql


def test_build_with_since_adds_slice_predicate_and_keeps_window():
    """P1-1: `build(since=…)` narrows the scan to the newest slice via an
    ADDITIONAL predicate; the regular start_date/end_date params stay bound to
    the full requested window so the count query still counts everything."""
    from datetime import datetime, timedelta

    builder = _make_builder()
    since = datetime.utcnow() - timedelta(days=7)
    sql, params = builder.build(since=since)
    assert "start_time >= %(slice_start)s" in sql
    assert params["slice_start"] == since
    # full-window params untouched by the slice
    assert params["start_date"] < since
    # the count query built afterwards must not inherit the slice narrowing
    count_sql, _ = builder.build_count_query()
    assert "slice_start" not in count_sql


def test_build_without_since_has_no_slice_predicate():
    sql, params = _make_builder().build()
    assert "slice_start" not in sql
    assert "slice_start" not in params


def test_eval_query_created_after_prunes_partitions():
    """P0-3: the eval table is PARTITION BY toYYYYMM(created_at); the page's
    oldest created_at (minus a 7-day margin) bounds the probe so it stops
    touching every monthly partition. Without the arg the shape is unchanged."""
    from datetime import datetime

    builder = _make_builder(eval_config_ids=[EVAL_CONFIG_ID])
    bound = datetime(2026, 7, 1)
    sql, params = builder.build_eval_query(["sp1"], created_after=bound)
    assert "created_at >= %(evals_created_after)s - INTERVAL 7 DAY" in sql
    assert params["evals_created_after"] == bound

    sql_unbounded, params_unbounded = builder.build_eval_query(["sp1"])
    assert "evals_created_after" not in sql_unbounded
    assert "evals_created_after" not in params_unbounded


def test_annotation_query_created_after_prunes_partitions():
    from datetime import datetime

    builder = _make_builder()
    builder.annotation_label_ids = ["33333333-3333-3333-3333-333333333333"]
    bound = datetime(2026, 7, 1)
    sql, params = builder.build_annotation_query(["sp1"], created_after=bound)
    assert "created_at >= %(anns_created_after)s - INTERVAL 7 DAY" in sql
    assert params["anns_created_after"] == bound

    sql_unbounded, params_unbounded = builder.build_annotation_query(["sp1"])
    assert "anns_created_after" not in sql_unbounded
    assert "anns_created_after" not in params_unbounded
