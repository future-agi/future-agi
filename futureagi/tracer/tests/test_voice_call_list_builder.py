"""Unit tests for VoiceCallListQueryBuilder (query-string building, no DB).

Covers the multi-phase voice-call list query strategy:
  build() (root conversation spans, project scope, ORDER BY, pagination),
  build_count_query, build_id_query, build_content_query, build_eval_query,
  build_annotation_query, build_child_spans_query, empty-input guards,
  the simulation-filter no-op, and filters embedded via the filter builder.

The builder builds SQL STRINGS only — nothing here touches ClickHouse.
"""

import re

import pytest

from tracer.services.clickhouse.query_builders.voice_call_list import (
    VAPI_PHONE_NUMBERS,
    VoiceCallListQueryBuilder,
)

PROJECT_ID = "proj-123"


def _squash(sql: str) -> str:
    """Collapse whitespace so multi-line SQL substrings match reliably."""
    return re.sub(r"\s+", " ", sql).strip()


# ---------------------------------------------------------------------------
# build() — Phase 1 paginated root conversation spans
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_selects_from_spans_table():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    assert "FROM spans" in _squash(sql)


@pytest.mark.unit
def test_build_root_conversation_span_predicate():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    s = _squash(sql)
    # Root span: no parent
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in s
    # Voice calls are conversation-type roots
    assert "observation_type = 'conversation'" in s


@pytest.mark.unit
def test_build_scopes_to_single_project():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build()
    s = _squash(sql)
    assert "project_id = %(project_id)s" in s
    assert "is_deleted = 0" in s
    assert params["project_id"] == PROJECT_ID


@pytest.mark.unit
def test_build_scopes_to_multiple_projects():
    qb = VoiceCallListQueryBuilder(project_id=None, project_ids=["p1", "p2"])
    sql, params = qb.build()
    s = _squash(sql)
    assert "project_id IN %(project_ids)s" in s
    assert params["project_ids"] == ("p1", "p2")


@pytest.mark.unit
def test_build_orders_by_start_time_desc():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    s = _squash(sql)
    assert "ORDER BY start_time DESC" in s
    # Deduplicate to one row per call
    assert "LIMIT 1 BY trace_id" in s


@pytest.mark.unit
def test_build_selects_light_columns_not_heavy_attrs():
    """build() must not pull span_attributes_raw (heavy blob → CH OOM)."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    s = _squash(sql)
    assert "span_attributes_raw" not in s
    for col in ("trace_id", "id AS span_id", "status", "latency_ms", "provider"):
        assert col in s


@pytest.mark.unit
def test_build_pagination_default_page():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, page_number=0, page_size=10)
    sql, params = qb.build()
    s = _squash(sql)
    assert "LIMIT %(limit)s" in s
    assert "OFFSET %(offset)s" in s
    # Fetch one extra row for has_more detection.
    assert params["limit"] == 11
    assert params["offset"] == 0


@pytest.mark.unit
def test_build_pagination_computes_offset_from_page():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, page_number=3, page_size=25)
    _, params = qb.build()
    assert params["offset"] == 75  # page_number * page_size
    assert params["limit"] == 26  # page_size + 1


@pytest.mark.unit
def test_build_sets_time_window_params():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build()
    s = _squash(sql)
    assert "start_time >= %(start_date)s" in s
    assert "start_time < %(end_date)s" in s
    # created_at pre-window widens partition pruning by one day.
    assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in s
    assert params["start_date"] is not None
    assert params["end_date"] is not None


@pytest.mark.unit
def test_build_no_filters_omits_filter_fragment():
    """With no frontend filters there must be no dangling `AND` fragment."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=[])
    sql, _ = qb.build()
    s = _squash(sql)
    assert "AND  ORDER BY" not in s
    assert "AND ORDER BY" not in s


# ---------------------------------------------------------------------------
# Simulation filter — SQL is a no-op (filtering happens in Python)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulation_filter_sql_is_noop():
    """_build_simulation_filter emits nothing regardless of the flag."""
    qb_on = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID, remove_simulation_calls=True
    )
    qb_off = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID, remove_simulation_calls=False
    )
    assert qb_on._build_simulation_filter() == ""
    assert qb_off._build_simulation_filter() == ""


@pytest.mark.unit
def test_build_does_not_embed_phone_numbers_in_sql():
    """Phone numbers live in the heavy JSON blob; must not leak into SQL."""
    qb = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID, remove_simulation_calls=True
    )
    sql, _ = qb.build()
    for phone in VAPI_PHONE_NUMBERS:
        assert phone not in sql


@pytest.mark.unit
def test_is_simulator_call_vapi_match():
    attrs = {"raw_log": {"customer": {"number": VAPI_PHONE_NUMBERS[0]}}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "vapi") is True


@pytest.mark.unit
def test_is_simulator_call_vapi_non_match():
    attrs = {"raw_log": {"customer": {"number": "+10000000000"}}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "vapi") is False


@pytest.mark.unit
def test_is_simulator_call_retell_match():
    attrs = {"raw_log": {"from_number": VAPI_PHONE_NUMBERS[1]}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "retell") is True


@pytest.mark.unit
def test_is_simulator_call_unknown_provider():
    attrs = {"raw_log": {"customer": {"number": VAPI_PHONE_NUMBERS[0]}}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "twilio") is False


@pytest.mark.unit
def test_is_simulator_call_missing_raw_log():
    assert VoiceCallListQueryBuilder.is_simulator_call({}, "vapi") is False


# ---------------------------------------------------------------------------
# build_count_query — Phase-1 total
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_count_query_uses_uniq_exact_trace_id():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    qb.build()  # populate start/end params consumed by count query
    sql, params = qb.build_count_query()
    s = _squash(sql)
    assert "uniqExact(trace_id) AS total" in s
    assert "FROM spans" in s
    # Same conversation-root predicate as build()
    assert "observation_type = 'conversation'" in s
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in s
    # No pagination on a count query.
    assert "LIMIT" not in s
    assert "OFFSET" not in s


@pytest.mark.unit
def test_count_query_respects_project_scope():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    qb.build()
    _, params = qb.build_count_query()
    assert params["project_id"] == PROJECT_ID


# ---------------------------------------------------------------------------
# build_id_query — same predicate/window, no pagination/order limit params
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_id_query_selects_only_ids():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build_id_query()
    s = _squash(sql)
    assert "SELECT id FROM spans" in s
    assert "observation_type = 'conversation'" in s
    assert "LIMIT 1 BY trace_id" in s
    # No page-limit/offset — resolver wants the full matched id set.
    assert "%(limit)s" not in s
    assert "%(offset)s" not in s


# ---------------------------------------------------------------------------
# build_content_query — heavy attribute columns for a page of span ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_content_query_fetches_heavy_columns():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    qb.build()
    sql, params = qb.build_content_query(["s1", "s2"])
    s = _squash(sql)
    assert "span_attributes_raw" in s
    assert "PREWHERE id IN %(content_span_ids)s" in s
    assert "project_id = %(project_id)s AND is_deleted = 0" in s
    assert params["content_span_ids"] == ("s1", "s2")


@pytest.mark.unit
def test_content_query_empty_span_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_content_query([])
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# build_eval_query — Phase 2 eval scores (NOT rewritten by v2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_eval_query_groups_by_trace_and_config():
    qb = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID, eval_config_ids=["c1", "c2"]
    )
    sql, params = qb.build_eval_query(["t1", "t2"])
    s = _squash(sql)
    assert "GROUP BY trace_id, custom_eval_config_id" in s
    assert params["trace_ids"] == ("t1", "t2")
    assert params["eval_config_ids"] == ("c1", "c2")


@pytest.mark.unit
def test_eval_query_averages_across_all_spans():
    """avgIf/countIf/groupArrayIf aggregate over every eval row in the group,
    not just the root span."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "avgIf(" in s
    assert "groupArrayIf(" in s
    assert "output_str_list" in s
    assert "pass_rate" in s
    assert "avg_score" in s


@pytest.mark.unit
def test_eval_query_keeps_both_delete_predicates():
    """Voice build_eval_query is in the v2 rewrite-exclude set, so it keeps the
    legacy `_peerdb_is_deleted = 0` guard AND the `(deleted = 0 OR deleted IS
    NULL)` clause — the exact predicate must not be dropped/renamed."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "_peerdb_is_deleted = 0" in s
    assert "(deleted = 0 OR deleted IS NULL)" in s
    assert "FROM tracer_eval_logger FINAL" in s


@pytest.mark.unit
def test_eval_query_excludes_non_terminal_and_errored_rows():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "status NOT IN ('pending', 'running', 'skipped', 'errored')" in s
    assert "error = 0" in s


@pytest.mark.unit
def test_eval_query_empty_trace_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, params = qb.build_eval_query([])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_eval_query_no_eval_configs_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=[])
    sql, params = qb.build_eval_query(["t1"])
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# build_annotation_query — Phase 3
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_annotation_query_joins_spans_and_scopes():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query(["t1"], annotation_label_ids=["l1", "l2"])
    s = _squash(sql)
    assert "FROM model_hub_score AS s FINAL" in s
    assert "LEFT JOIN spans AS sp" in s
    assert "s.label_id IN %(label_ids)s" in s
    assert "s._peerdb_is_deleted = 0" in s
    assert "s.deleted = false" in s
    assert params["trace_ids"] == ("t1",)
    assert params["label_ids"] == ("l1", "l2")


@pytest.mark.unit
def test_annotation_query_empty_trace_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query([], annotation_label_ids=["l1"])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_annotation_query_no_label_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query(["t1"], annotation_label_ids=[])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_annotation_query_none_label_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query(["t1"], annotation_label_ids=None)
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# build_child_spans_query — Phase 4
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_child_spans_query_fetches_non_root_spans():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_child_spans_query(["t1", "t2"])
    s = _squash(sql)
    assert "FROM spans" in s
    assert "parent_span_id IS NOT NULL" in s
    assert "trace_id IN %(trace_ids)s" in s
    assert "project_id = %(project_id)s" in s
    assert "is_deleted = 0" in s
    assert "ORDER BY start_time ASC" in s
    assert params["trace_ids"] == ("t1", "t2")
    assert params["project_id"] == PROJECT_ID


@pytest.mark.unit
def test_child_spans_query_selects_heavy_columns():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build_child_spans_query(["t1"])
    s = _squash(sql)
    for col in ("span_attributes_raw", "input", "output", "metadata_map", "tags"):
        assert col in s


@pytest.mark.unit
def test_child_spans_query_empty_trace_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_child_spans_query([])
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# Filters embedded via ClickHouseFilterBuilder
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_embeds_frontend_filter_fragment():
    """A frontend filter must be compiled by the filter builder and spliced
    into build() with an `AND` prefix + bound param."""
    filters = [
        {
            "column_id": "status",
            "filter_config": {
                "filter_type": "string",
                "filter_op": "equals",
                "filter_value": "error",
                "col_type": "ATTRIBUTE",
            },
        }
    ]
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    sql, params = qb.build()
    s = _squash(sql)
    # Filter param value bound (not inlined literal in the ORDER BY tail).
    assert any(v == "error" for v in params.values())
    # status is a case-insensitive system-metric column: the compiled filter
    # emits a trace_id subquery comparing lower(status). That fragment (which
    # only the filter builder produces) must be spliced in before ORDER BY.
    assert "lower(status) =" in s
    assert s.index("lower(status) =") < s.index("ORDER BY")


@pytest.mark.unit
def test_count_query_embeds_same_filter():
    filters = [
        {
            "column_id": "status",
            "filter_config": {
                "filter_type": "string",
                "filter_op": "equals",
                "filter_value": "error",
                "col_type": "ATTRIBUTE",
            },
        }
    ]
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    qb.build()
    _, params = qb.build_count_query()
    assert any(v == "error" for v in params.values())


@pytest.mark.unit
def test_time_range_filter_narrows_window_params():
    filters = [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_op": "between",
                "filter_value": [
                    "2026-01-01T00:00:00Z",
                    "2026-01-31T00:00:00Z",
                ],
            },
        }
    ]
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    _, params = qb.build()
    assert params["start_date"].year == 2026 and params["start_date"].month == 1
    assert params["end_date"].day == 31
