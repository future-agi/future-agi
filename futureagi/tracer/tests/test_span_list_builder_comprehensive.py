"""Comprehensive unit tests for the v1 ``SpanListQueryBuilder``.

Covers GAPS not already exercised by ``test_ch25_span_list_builder.py`` (which
pins the *v2* subclass) or ``test_span_list_custom_columns.py`` (content-query
typed maps + flatten helper):

  * the v1 builder's ``build`` / ``build_count_query`` / ``build_id_query``
    SQL shape (project scope, time window, pagination, ordering),
  * ``build_eval_query`` semantics under the DEFAULT (legacy) eval-logger
    table — grouping, per-status averaging, and (being rewrite-EXCLUDED) both
    delete guards ``_peerdb_is_deleted = 0 AND (deleted = 0 OR deleted IS NULL)``,
  * ``build_annotation_query`` (model_hub_score, still peerdb-gated),
  * empty-input ``("", {})`` contracts for all three helpers,
  * both static pivots: ``pivot_eval_results`` (SCORE ×100, PASS_FAIL
    pass_rate, CHOICES per-choice %, error / non-terminal markers, empty)
    and ``pivot_annotation_results`` (numeric / star / thumbs / categorical /
    text / raw).

These are pure query-string / pivot-logic tests — NO ClickHouse, NO DB.
"""
from __future__ import annotations

import pytest
from django.test import override_settings

from tracer.services.clickhouse.query_builders.span_list import (
    SpanListQueryBuilder,
)

pytestmark = pytest.mark.unit


PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-2222-2222-222222222222"
LABEL_ID = "33333333-3333-3333-3333-333333333333"


def _make_builder(
    filters=None,
    sort_params=None,
    eval_config_ids=None,
    annotation_label_ids=None,
    project_id=PROJECT_ID,
    project_ids=None,
    page_number=0,
    page_size=50,
    end_user_id=None,
    project_version_id=None,
):
    return SpanListQueryBuilder(
        project_id=project_id,
        project_ids=project_ids,
        page_number=page_number,
        page_size=page_size,
        filters=filters or [],
        sort_params=sort_params or [],
        eval_config_ids=eval_config_ids or [],
        annotation_label_ids=annotation_label_ids or [],
        end_user_id=end_user_id,
        project_version_id=project_version_id,
    )


# --------------------------------------------------------------------------- #
# build() — main paginated span list
# --------------------------------------------------------------------------- #
class TestBuild:
    def test_selects_from_spans_light_columns(self):
        sql, params = _make_builder().build()
        assert "FROM spans" in sql
        # Light columns only; content columns fetched separately.
        assert "input" not in sql.split("FROM spans")[0]
        assert "id," in sql
        assert "trace_id," in sql

    def test_project_scope_and_soft_delete(self):
        sql, _ = _make_builder().build()
        assert "project_id = %(project_id)s" in sql
        assert "is_deleted = 0" in sql

    def test_multi_project_scope(self):
        sql, params = _make_builder(
            project_id=None, project_ids=[PROJECT_ID, EVAL_CONFIG_ID]
        ).build()
        assert "project_id IN %(project_ids)s" in sql
        assert params["project_ids"] == (PROJECT_ID, EVAL_CONFIG_ID)

    def test_time_window_bounds(self):
        sql, params = _make_builder().build()
        assert "start_time >= %(start_date)s" in sql
        assert "start_time < %(end_date)s" in sql
        # created_at pre-filter widened by 1 day.
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in sql
        assert params.get("start_date") is not None
        assert params.get("end_date") is not None

    def test_default_ordering_is_start_time_desc(self):
        sql, _ = _make_builder().build()
        assert "ORDER BY start_time DESC" in sql

    def test_pagination_params_and_offset(self):
        sql, params = _make_builder(page_number=2, page_size=25).build()
        assert "LIMIT %(limit)s" in sql
        assert "OFFSET %(offset)s" in sql
        assert params["limit"] == 25
        assert params["offset"] == 50  # page 2 * size 25
        assert "LIMIT 1 BY id" in sql

    def test_sort_param_maps_latency_alias(self):
        sql, _ = _make_builder(
            sort_params=[{"column_id": "latency", "direction": "asc"}]
        ).build()
        # SORT_FIELD_MAP maps latency -> latency_ms.
        assert "latency_ms" in sql
        assert "start_time DESC" not in sql  # custom order replaced default

    def test_filter_fragment_embedded(self):
        sql, params = _make_builder(
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
            ]
        ).build()
        # A compiled filter injects the value into params and appends an
        # `AND ...` predicate (model is an always-selected column, so its bare
        # presence proves nothing — the compiled predicate + param value do).
        # `model` is a case-insensitive column, so equals compiles to
        # `lower(model) = %(...)s` and the value is lower-cased.
        assert "gpt-4o-mini" in params.values()
        assert "lower(model) = %(" in sql

    def test_project_version_fragment(self):
        sql, params = _make_builder(
            project_version_id="pv-1"
        ).build()
        assert "project_version_id = %(project_version_id)s" in sql
        assert params["project_version_id"] == "pv-1"

    def test_no_project_version_fragment_when_absent(self):
        sql, _ = _make_builder().build()
        assert "project_version_id = %(project_version_id)s" not in sql

    def test_end_user_id_takes_remap_branch(self):
        sql, params = _make_builder(end_user_id="eu-1").build()
        # The remap branch resolves end_user_id new->old and re-projects it.
        assert "resolved_end_user_id" in sql
        assert "end_user_id_remap" in sql
        assert params["end_user_id"] == "eu-1"

    def test_no_end_user_id_uses_bare_scan(self):
        sql, _ = _make_builder().build()
        assert "resolved_end_user_id" not in sql
        assert "end_user_id_remap" not in sql


# --------------------------------------------------------------------------- #
# build_count_query()
# --------------------------------------------------------------------------- #
class TestBuildCountQuery:
    def test_uses_uniqexact_over_spans(self):
        sql, _ = _make_builder().build_count_query()
        assert "uniqExact(id) AS total" in sql
        assert "FROM spans" in sql

    def test_same_time_window_as_build(self):
        sql, _ = _make_builder().build_count_query()
        assert "start_time >= %(start_date)s" in sql
        assert "start_time < %(end_date)s" in sql
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in sql

    def test_no_pagination_or_order(self):
        sql, _ = _make_builder().build_count_query()
        assert "LIMIT %(limit)s" not in sql
        assert "ORDER BY" not in sql

    def test_end_user_remap_branch_mirrors_build(self):
        sql, params = _make_builder(end_user_id="eu-9").build_count_query()
        assert "resolved_end_user_id = %(end_user_id)s" in sql
        assert "end_user_id_remap" in sql
        assert params["end_user_id"] == "eu-9"

    def test_filter_and_version_embedded(self):
        sql, params = _make_builder(
            project_version_id="pv-2",
        ).build_count_query()
        assert "project_version_id = %(project_version_id)s" in sql
        assert params["project_version_id"] == "pv-2"


# --------------------------------------------------------------------------- #
# build_id_query()
# --------------------------------------------------------------------------- #
class TestBuildIdQuery:
    def test_selects_only_id_no_pagination(self):
        sql, _ = _make_builder().build_id_query()
        assert "SELECT id" in sql
        assert "FROM spans" in sql
        assert "LIMIT 1 BY id" in sql
        # No page LIMIT / OFFSET / ORDER — it mirrors the filter window only.
        assert "LIMIT %(limit)s" not in sql
        assert "OFFSET %(offset)s" not in sql
        assert "ORDER BY" not in sql

    def test_same_time_window_and_project(self):
        sql, _ = _make_builder().build_id_query()
        assert "project_id = %(project_id)s" in sql
        assert "start_time >= %(start_date)s" in sql
        assert "start_time < %(end_date)s" in sql

    def test_project_version_fragment(self):
        sql, params = _make_builder(
            project_version_id="pv-3"
        ).build_id_query()
        assert "project_version_id = %(project_version_id)s" in sql
        assert params["project_version_id"] == "pv-3"


# --------------------------------------------------------------------------- #
# build_content_query() — empty contract only (typed maps covered elsewhere)
# --------------------------------------------------------------------------- #
class TestBuildContentQuery:
    def test_empty_span_ids_returns_empty(self):
        sql, params = _make_builder().build_content_query(span_ids=[])
        assert sql == ""
        assert params == {}

    def test_prewhere_id_list_and_soft_delete(self):
        sql, params = _make_builder().build_content_query(span_ids=["s1", "s2"])
        assert "PREWHERE id IN %(content_span_ids)s" in sql
        assert "is_deleted = 0" in sql
        assert params["content_span_ids"] == ("s1", "s2")


# --------------------------------------------------------------------------- #
# build_eval_query() — Phase 2
# --------------------------------------------------------------------------- #
class TestBuildEvalQuery:
    def test_empty_span_ids_returns_empty(self):
        sql, params = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=[])
        assert sql == "" and params == {}

    def test_empty_eval_config_ids_returns_empty(self):
        sql, params = _make_builder(eval_config_ids=[]).build_eval_query(
            span_ids=["s1"]
        )
        assert sql == "" and params == {}

    def test_params_are_fresh_dict(self):
        # A fresh dict is built — only span_ids + eval_config_ids, no project_id.
        _, params = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1", "s2"])
        assert set(params.keys()) == {"span_ids", "eval_config_ids"}
        assert params["span_ids"] == ("s1", "s2")
        assert params["eval_config_ids"] == (EVAL_CONFIG_ID,)

    def test_groups_by_span_and_config(self):
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        assert "GROUP BY observation_span_id, custom_eval_config_id" in sql

    def test_averages_only_completed_non_errored_rows(self):
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        assert "avgIf(" in sql
        # non-terminal / skipped / errored excluded from the aggregate guard.
        assert (
            "status NOT IN ('pending', 'running', 'skipped', 'errored')" in sql
        )
        # NULL-safe output_str comparison.
        assert "ifNull(output_str, '') != 'ERROR'" in sql

    def test_pass_rate_case_and_str_lists(self):
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        assert "CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END" in sql
        assert "pass_rate" in sql
        assert "groupArrayIf(" in sql
        assert "str_lists" in sql

    def test_per_status_counts_present(self):
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        for col in (
            "success_count",
            "error_count",
            "skipped_count",
            "running_count",
            "pending_count",
        ):
            assert col in sql

    def test_external_group_by_settings(self):
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        assert "max_bytes_before_external_group_by = 1073741824" in sql

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
    def test_legacy_table_keeps_both_delete_guards(self):
        # build_eval_query is rewrite-EXCLUDED, so it keeps BOTH the CDC
        # tombstone (`_peerdb_is_deleted`) and the app `deleted` soft-delete
        # guards, matching the display queries. The version-only legacy engine's
        # FINAL does not drop tombstones, so a hard-deleted row would otherwise
        # leak into eval filters. Override is required: the test stack defaults
        # CH25_EVAL_LOGGER_TABLE to `tracer_eval_logger_v2`.
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        assert "tracer_eval_logger FINAL" in sql
        assert "_peerdb_is_deleted = 0 AND (deleted = 0 OR deleted IS NULL)" in sql

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_v2_table_uses_is_deleted(self):
        sql, _ = _make_builder(
            eval_config_ids=[EVAL_CONFIG_ID]
        ).build_eval_query(span_ids=["s1"])
        assert "tracer_eval_logger_v2 FINAL" in sql
        assert "is_deleted = 0" in sql


# --------------------------------------------------------------------------- #
# build_annotation_query() — Phase 3
# --------------------------------------------------------------------------- #
class TestBuildAnnotationQuery:
    def test_empty_span_ids_returns_empty(self):
        sql, params = _make_builder(
            annotation_label_ids=[LABEL_ID]
        ).build_annotation_query(span_ids=[])
        assert sql == "" and params == {}

    def test_empty_label_ids_returns_empty(self):
        sql, params = _make_builder(
            annotation_label_ids=[]
        ).build_annotation_query(span_ids=["s1"])
        assert sql == "" and params == {}

    def test_reads_model_hub_score_grouped(self):
        sql, params = _make_builder(
            annotation_label_ids=[LABEL_ID]
        ).build_annotation_query(span_ids=["s1", "s2"])
        assert "FROM model_hub_score FINAL" in sql
        assert "GROUP BY observation_span_id, label_id" in sql
        assert "anyLast(value) AS value" in sql
        assert params["span_ids"] == ("s1", "s2")
        assert params["label_ids"] == (LABEL_ID,)

    def test_annotation_soft_delete_uses_peerdb(self):
        # model_hub_score is still peerdb-gated (distinct from the eval fix).
        sql, _ = _make_builder(
            annotation_label_ids=[LABEL_ID]
        ).build_annotation_query(span_ids=["s1"])
        assert "_peerdb_is_deleted = 0" in sql
        assert "deleted = false" in sql


# --------------------------------------------------------------------------- #
# pivot_eval_results() — static pivot
# --------------------------------------------------------------------------- #
class TestPivotEvalResults:
    def test_empty_rows(self):
        assert SpanListQueryBuilder.pivot_eval_results([]) == {}

    def test_score_eval_scaled_by_100(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": 0.9,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == 90.0

    def test_pass_fail_uses_pass_rate_no_scaling(self):
        # avg_score None -> falls to pass_rate branch, rounded, NOT ×100.
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": 75.0,
                "success_count": 2,
                "error_count": 0,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == 75.0

    def test_avg_score_zero_falls_through_to_marker(self):
        # avg_score == 0 (and != 0 guard) falls through; pass_rate None ->
        # score None -> non_terminal_eval_marker with all-zero counts -> None.
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": 0.0,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] is None

    def test_all_errored_surfaces_error_marker(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 0,
                "error_count": 3,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == {"error": True}

    def test_partial_error_still_renders_score(self):
        # success_count > 0 alongside errors -> error branch NOT taken.
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": 0.5,
                "pass_rate": None,
                "success_count": 2,
                "error_count": 1,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == 50.0

    def test_skipped_marker_with_reason(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 0,
                "error_count": 0,
                "skipped_count": 1,
                "skipped_reason": "no input",
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == {"status": "skipped", "skipped_reason": "no input"}

    def test_running_marker(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 0,
                "error_count": 0,
                "running_count": 2,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == {"status": "running"}

    def test_pending_marker(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 0,
                "error_count": 0,
                "pending_count": 1,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == {"status": "pending"}

    def test_no_data_at_all_renders_none(self):
        # No score, no error, no lifecycle counts -> None ("no eval run").
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 0,
                "error_count": 0,
                "str_lists": [],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] is None

    def test_choices_per_choice_percentage_list_form(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 3,
                "error_count": 0,
                "str_lists": [["A"], ["A"], ["B"]],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == {"A": 66.67, "B": 33.33}

    def test_choices_json_string_form(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 2,
                "error_count": 0,
                "str_lists": ['["A","B"]', '["A"]'],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        # Both rows contain A -> 100%; B in one of two -> 50%.
        assert out["s1"]["c1"] == {"A": 100.0, "B": 50.0}

    def test_empty_choice_lists_fall_through_to_score(self):
        # '[]' / empty inner lists must NOT be treated as CHOICES data;
        # they fall through to the avg_score branch.
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": 0.8,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": ["[]", []],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == 80.0

    def test_choices_dedup_within_single_row(self):
        # A single eval row's list is deduped via set() before counting.
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [["A", "A", "B"]],
            }
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        # One row, A and B each present once -> both 100%.
        assert out["s1"]["c1"] == {"A": 100.0, "B": 100.0}

    def test_per_span_keying_multiple_spans(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": 1.0,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [],
            },
            {
                "observation_span_id": "s2",
                "eval_config_id": "c1",
                "avg_score": 0.5,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [],
            },
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == 100.0
        assert out["s2"]["c1"] == 50.0

    def test_multiple_configs_per_span(self):
        rows = [
            {
                "observation_span_id": "s1",
                "eval_config_id": "c1",
                "avg_score": 1.0,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [],
            },
            {
                "observation_span_id": "s1",
                "eval_config_id": "c2",
                "avg_score": None,
                "pass_rate": 40.0,
                "success_count": 1,
                "error_count": 0,
                "str_lists": [],
            },
        ]
        out = SpanListQueryBuilder.pivot_eval_results(rows)
        assert out["s1"]["c1"] == 100.0
        assert out["s1"]["c2"] == 40.0


# --------------------------------------------------------------------------- #
# pivot_annotation_results() — static pivot
# --------------------------------------------------------------------------- #
class TestPivotAnnotationResults:
    def test_empty_rows(self):
        assert SpanListQueryBuilder.pivot_annotation_results([]) == {}

    def test_numeric_extracts_value_key(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": '{"value": 7}'}
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "NUMERIC"}
        )
        assert out["s1"]["l1"] == 7

    def test_star_extracts_rating_key(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": '{"rating": 4}'}
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "STAR"}
        )
        assert out["s1"]["l1"] == 4

    def test_thumbs_up_down_coerced_to_bool(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": '{"value": "up"}'},
            {"observation_span_id": "s2", "label_id": "l1", "value": '{"value": "down"}'},
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "THUMBS_UP_DOWN"}
        )
        assert out["s1"]["l1"] is True
        assert out["s2"]["l1"] is False

    def test_categorical_extracts_selected(self):
        rows = [
            {
                "observation_span_id": "s1",
                "label_id": "l1",
                "value": '{"selected": ["a", "b"]}',
            }
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "CATEGORICAL"}
        )
        assert out["s1"]["l1"] == ["a", "b"]

    def test_text_extracts_text(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": '{"text": "hello"}'}
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "TEXT"}
        )
        assert out["s1"]["l1"] == "hello"

    def test_unknown_type_returns_raw_parsed_value(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": '{"k": "v"}'}
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(rows, label_types={})
        assert out["s1"]["l1"] == {"k": "v"}

    def test_bad_json_value_becomes_empty_dict(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": "{not json"}
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(rows, label_types={})
        assert out["s1"]["l1"] == {}

    def test_dict_value_passed_through(self):
        rows = [
            {
                "observation_span_id": "s1",
                "label_id": "l1",
                "value": {"value": 3},
            }
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "NUMERIC"}
        )
        assert out["s1"]["l1"] == 3

    def test_per_span_keying(self):
        rows = [
            {"observation_span_id": "s1", "label_id": "l1", "value": '{"value": 1}'},
            {"observation_span_id": "s2", "label_id": "l1", "value": '{"value": 2}'},
        ]
        out = SpanListQueryBuilder.pivot_annotation_results(
            rows, label_types={"l1": "NUMERIC"}
        )
        assert out["s1"]["l1"] == 1
        assert out["s2"]["l1"] == 2
