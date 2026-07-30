"""
Lock in the eval-metric filter compilation, the v1→v2 SQL rewrite, and the
eval-logger soft-delete predicate for the Observe list endpoints.

These cover GAPS left by the existing suites:
  - test_ch25_filter_compiler.py     — rewrite cases + end-user dim swap
  - test_metric_filters_comprehensive.py — has_eval / has_annotation shape
  - test_filter_operator_matrix.py   — eval ops only asserted "well-formed",
    NOT the value scaling / bool mapping / OR-join parens / not-deleted pred

Everything here is pure query-string building (no DB): the EVAL_METRIC path is
exercised with the DB-facing managers monkeypatched (mirrors the fakes in
test_filter_operator_matrix.py), so the tests stay @pytest.mark.unit.

The RECENT FIXES pinned here:
  1. eval_logger_source() legacy predicate = "(deleted = 0 OR deleted IS NULL)"
     (NOT _peerdb_is_deleted); v2 table -> "is_deleted = 0".
  2. rewrite_v1_sql_to_v2 renames _peerdb_is_deleted->is_deleted but leaves a
     bare "deleted" untouched — so a v2-translated EVAL_METRIC filter never
     emits a broken "is_deleted" against tracer_eval_logger.
  3. _build_eval_condition multi-value CHOICE OR-join is wrapped in parens so
     the config/deleted/error guards scope ALL values (precedence fix).
  4. SCORE value/100 scaling; PASS_FAIL Passed/Failed -> output_bool.
"""
from __future__ import annotations

import uuid

import pytest

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    rewrite_v1_sql_to_v2,
)


# ---------------------------------------------------------------------------
# Fakes so the EVAL_METRIC path resolves config ids + output type without a DB.
# Same shape as tracer/tests/test_filter_operator_matrix.py.
# ---------------------------------------------------------------------------


class _FakeValuesList:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _FakeConfigQuerySet:
    def __init__(self, config_ids, template_id, exists=True):
        self.config_ids = config_ids
        self.template_id = template_id
        self._exists = exists

    def exists(self):
        return self._exists

    def filter(self, **kwargs):
        return self

    def values_list(self, field, flat=False):
        if field == "id":
            return _FakeValuesList(list(self.config_ids))
        if field == "eval_template_id":
            return _FakeValuesList([self.template_id])
        return _FakeValuesList([])


class _FakeConfigManager:
    def __init__(self, config_ids, template_id, exists=True):
        self.queryset = _FakeConfigQuerySet(config_ids, template_id, exists)

    def filter(self, **kwargs):
        return self.queryset


class _FakeEvalTemplateManager:
    def __init__(self, output_type):
        self.output_type = output_type

    def filter(self, **kwargs):
        return self

    def values(self, *fields):
        return self

    def first(self):
        return {"config": {"output": self.output_type}}


def _patch_eval(monkeypatch, output_type, *, config_ids=None, exists=True):
    """Point EVAL_METRIC resolution at fake managers; return the eval_id used."""
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    eval_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())
    if config_ids is None:
        config_ids = [str(uuid.uuid4())]
    monkeypatch.setattr(
        CustomEvalConfig,
        "objects",
        _FakeConfigManager(config_ids, template_id, exists),
    )
    monkeypatch.setattr(
        EvalTemplate,
        "no_workspace_objects",
        _FakeEvalTemplateManager(output_type),
    )
    return eval_id, config_ids


def _eval_filter(eval_id, filter_op, filter_value=None):
    config = {
        "col_type": ClickHouseFilterBuilder.EVAL_METRIC,
        "filter_op": filter_op,
    }
    if filter_value is not None:
        config["filter_value"] = filter_value
    return [{"column_id": eval_id, "filter_config": config}]


def _translate(builder_cls, filters, **kwargs):
    return builder_cls(project_id="p1", **kwargs).translate(filters)


# ===========================================================================
# 1. eval_logger_source() — the not-deleted predicate per configured table.
# ===========================================================================


@pytest.mark.unit
class TestEvalLoggerSource:
    def test_legacy_table_uses_deleted_or_null_predicate(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        table, pred = eval_logger_source()
        assert table == "tracer_eval_logger"
        assert pred == "(deleted = 0 OR deleted IS NULL)"
        # The legacy table lacks these — must NOT appear.
        assert "_peerdb_is_deleted" not in pred
        assert "is_deleted" not in pred

    def test_code_default_is_legacy_when_setting_absent(self, settings):
        # The code default (getattr fallback) is the legacy table — a
        # peerdb-backed deployment keeps it without any env var set.
        del settings.CH25_EVAL_LOGGER_TABLE
        table, pred = eval_logger_source()
        assert table == "tracer_eval_logger"
        assert pred == "(deleted = 0 OR deleted IS NULL)"

    def test_any_non_v2_table_name_uses_deleted_predicate(self, settings):
        # Only a `_v2` suffix flips to is_deleted; anything else is legacy.
        # Use a novel (non-canonical) name so the else-branch generality is
        # actually exercised, not just the canonical legacy table again.
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_shadow"
        table, pred = eval_logger_source()
        assert table == "tracer_eval_logger_shadow"
        assert pred == "(deleted = 0 OR deleted IS NULL)"

    def test_legacy_alias_prefixes_deleted_column(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        table, pred = eval_logger_source("e")
        assert table == "tracer_eval_logger"
        assert pred == "(e.deleted = 0 OR e.deleted IS NULL)"

    def test_v2_table_uses_is_deleted_predicate(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        table, pred = eval_logger_source()
        assert table == "tracer_eval_logger_v2"
        assert pred == "is_deleted = 0"
        assert "deleted = 0 OR" not in pred

    def test_v2_table_alias_prefixes_is_deleted(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        _, pred = eval_logger_source("el")
        assert pred == "el.is_deleted = 0"

    def test_default_omits_cdc_tombstone_guard(self, settings):
        # Default stays rewrite-safe: `deleted`-only, no `_peerdb_is_deleted`
        # (the v2 rewriter renames it, so rewritten fragments must not carry it).
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        _, pred = eval_logger_source()
        assert pred == "(deleted = 0 OR deleted IS NULL)"
        assert "_peerdb_is_deleted" not in pred

    def test_cdc_tombstone_guard_flag_emits_both_predicates(self, settings):
        # Rewrite-EXCLUDED callers keep both guards: the version-only legacy
        # engine's FINAL does not drop CDC tombstones.
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        _, pred = eval_logger_source(include_cdc_tombstone_guard=True)
        assert pred == (
            "_peerdb_is_deleted = 0 AND (deleted = 0 OR deleted IS NULL)"
        )

    def test_cdc_tombstone_guard_flag_respects_alias(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        _, pred = eval_logger_source("e", include_cdc_tombstone_guard=True)
        assert pred == (
            "e._peerdb_is_deleted = 0 AND (e.deleted = 0 OR e.deleted IS NULL)"
        )

    def test_cdc_tombstone_guard_flag_noop_on_v2(self, settings):
        # v2 has no CDC columns — the flag must not inject `_peerdb_is_deleted`.
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        _, pred = eval_logger_source(include_cdc_tombstone_guard=True)
        assert pred == "is_deleted = 0"
        assert "_peerdb_is_deleted" not in pred


# ===========================================================================
# 2. rewrite_v1_sql_to_v2 — the soft-delete rename must leave bare "deleted".
# ===========================================================================


@pytest.mark.unit
class TestRewriteLeavesDeletedUntouched:
    def test_bare_deleted_word_is_not_renamed(self):
        # The legacy eval-logger not-deleted predicate uses the app `deleted`
        # column. The v2 rewriter must NOT touch it — only `_peerdb_is_deleted`
        # is in the rename map.
        v1 = "WHERE (deleted = 0 OR deleted IS NULL)"
        assert rewrite_v1_sql_to_v2(v1) == v1

    def test_peerdb_is_deleted_renamed_but_deleted_survives(self):
        v1 = "WHERE _peerdb_is_deleted = 0 AND (deleted = 0 OR deleted IS NULL)"
        out = rewrite_v1_sql_to_v2(v1)
        assert "is_deleted = 0" in out
        assert "(deleted = 0 OR deleted IS NULL)" in out
        # `_peerdb_is_deleted` gone; bare `deleted` predicate intact.
        assert "_peerdb_is_deleted" not in out

    def test_deleted_is_not_a_substring_target(self):
        # "deleted" as a whole word survives; "_peerdb_is_deleted" is the only
        # deleted-family token that gets renamed.
        v1 = "AND deleted = false"
        assert rewrite_v1_sql_to_v2(v1) == "AND deleted = false"


# ===========================================================================
# 3. EVAL_METRIC — SCORE numeric path (value/100 scaling on output_float).
# ===========================================================================


@pytest.mark.unit
class TestEvalScoreCompilation:
    def test_score_subquery_uses_eval_logger_not_deleted_predicate(
        self, monkeypatch, settings
    ):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "greater_than", 50)
        )
        assert "FROM tracer_eval_logger FINAL" in where
        assert "(deleted = 0 OR deleted IS NULL)" in where
        assert "_peerdb_is_deleted" not in where
        # errored eval rows always excluded from value-match filters.
        assert "AND error = 0" in where

    def test_score_greater_than_divides_value_by_100(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "greater_than", 50)
        )
        assert "output_float >" in where
        # UI 0-100, storage 0-1 → 50/100 = 0.5.
        assert 0.5 in params.values()

    def test_score_equals_divides_value_by_100(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "equals", 80)
        )
        assert "output_float =" in where
        assert 0.8 in params.values()

    def test_score_between_scales_both_bounds(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "between", [10, 20])
        )
        assert "output_float BETWEEN" in where
        vals = set(params.values())
        assert 0.1 in vals and 0.2 in vals

    def test_score_not_between_uses_not_between(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "not_between", [10, 20])
        )
        assert "output_float NOT BETWEEN" in where
        assert 0.1 in params.values() and 0.2 in params.values()

    def test_score_in_scales_each_value(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "in", [50, 100])
        )
        assert "output_float IN" in where
        assert (0.5, 1.0) in params.values()

    def test_score_not_in_negates_membership(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "not_in", [50])
        )
        assert "output_float NOT IN" in where

    def test_score_is_null_checks_output_float_absence(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "is_null")
        )
        # is_null → NOT IN a subquery of rows that HAVE a value.
        assert "output_float IS NOT NULL" in where
        assert "NOT IN (" in where

    def test_score_is_not_null_checks_presence(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "is_not_null")
        )
        assert "output_float IS NOT NULL" in where
        assert "trace_id IN (" in where
        assert "NOT IN" not in where


# ===========================================================================
# 4. EVAL_METRIC — PASS_FAIL path (Passed/Failed -> output_bool).
# ===========================================================================


@pytest.mark.unit
class TestEvalPassFailCompilation:
    def test_passed_maps_to_output_bool_one(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "PASS_FAIL")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "equals", "Passed")
        )
        assert "output_bool IN" in where
        assert (1,) in params.values()

    def test_failed_maps_to_output_bool_zero(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "PASS_FAIL")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "equals", "Failed")
        )
        assert "output_bool IN" in where
        assert (0,) in params.values()

    def test_pass_fail_multi_value_in(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "PASS_FAIL")
        where, params = _translate(
            ClickHouseFilterBuilder,
            _eval_filter(eval_id, "in", ["Passed", "Failed"]),
        )
        assert "output_bool IN" in where
        # order-preserving dedup → (1, 0).
        assert (1, 0) in params.values()

    def test_pass_fail_not_equals_negates(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "PASS_FAIL")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "not_equals", "Passed")
        )
        assert "output_bool NOT IN" in where

    def test_pass_fail_unrecognized_value_matches_nothing(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "PASS_FAIL")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "equals", "maybe")
        )
        assert where == "0 = 1"

    def test_pass_fail_is_null_uses_output_bool_presence(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "PASS_FAIL")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "is_null")
        )
        assert "output_bool IS NOT NULL" in where
        assert "NOT IN (" in where


# ===========================================================================
# 5. EVAL_METRIC — CHOICE/CHOICES path (OR-join wrapped in parens).
# ===========================================================================


@pytest.mark.unit
class TestEvalChoiceCompilation:
    def test_choice_equals_uses_has_on_parsed_array(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "equals", "yes")
        )
        assert "JSONExtract(output_str_list, 'Array(String)')" in where
        assert "has(" in where
        assert "output_str = " in where
        assert "yes" in params.values()

    def test_multi_value_choice_or_join_is_wrapped_in_parens(self, monkeypatch):
        # Precedence fix: the OR-join across values is wrapped so the
        # config/deleted/error guards scope ALL values, not just the first.
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "in", ["a", "b", "c"])
        )
        assert " OR " in where
        # The combined membership block sits inside the AND-guarded subquery,
        # so the guards precede a parenthesised OR group.
        assert "AND error = 0 AND ((" in where
        # Three membership checks OR-joined.
        assert where.count("has(") == 3

    def test_choice_subquery_uses_eval_logger_not_deleted_predicate(
        self, monkeypatch, settings
    ):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "in", ["a", "b"])
        )
        assert "(deleted = 0 OR deleted IS NULL)" in where
        assert "_peerdb_is_deleted" not in where

    def test_choice_contains_uses_ilike(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "contains", "part")
        )
        assert "ILIKE" in where
        assert "%part%" in params.values()

    def test_choice_starts_with(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "starts_with", "pre")
        )
        assert "ILIKE" in where
        assert "pre%" in params.values()

    def test_choice_ends_with(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "ends_with", "suf")
        )
        assert "ILIKE" in where
        assert "%suf" in params.values()

    def test_choice_not_in_uses_not_wrapped_group(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "not_in", ["a", "b"])
        )
        # Negation: exists-guard AND NOT (…OR…). The NOT scopes the whole group.
        assert "AND NOT (" in where
        assert "notEmpty(" in where

    def test_choices_output_type_alias_behaves_like_choice(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICES")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "equals", "yes")
        )
        assert "JSONExtract(output_str_list, 'Array(String)')" in where

    def test_choice_is_null_checks_choice_presence(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "is_null")
        )
        assert "notEmpty(" in where
        assert "output_str IS NOT NULL" in where
        assert "NOT IN (" in where


# ===========================================================================
# 6. EVAL_METRIC — span vs trace mode column selection + no-config sentinel.
# ===========================================================================


@pytest.mark.unit
class TestEvalModeAndConfig:
    def test_trace_mode_matches_trace_id(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder,
            _eval_filter(eval_id, "greater_than", 50),
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        )
        assert where.startswith("trace_id IN (")
        assert "SELECT trace_id FROM" in where

    def test_span_mode_matches_span_id(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder,
            _eval_filter(eval_id, "greater_than", 50),
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        )
        assert where.startswith("id IN (")
        assert "SELECT observation_span_id FROM" in where

    def test_config_ids_bound_as_param_tuple(self, monkeypatch):
        cfg_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        eval_id, _ = _patch_eval(monkeypatch, "SCORE", config_ids=cfg_ids)
        where, params = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "greater_than", 50)
        )
        assert "custom_eval_config_id IN %(" in where
        assert tuple(cfg_ids) in params.values()

    def test_no_matching_config_returns_impossible_sentinel(self, monkeypatch):
        # Empty config resolution → a filter that matches nothing (rather than
        # silently dropping the eval filter).
        eval_id, _ = _patch_eval(
            monkeypatch, "SCORE", config_ids=[], exists=False
        )
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "greater_than", 50)
        )
        assert where == (
            "trace_id IN "
            "(SELECT toUUID('00000000-0000-0000-0000-000000000000'))"
        )


# ===========================================================================
# 7. V2 translate() of an EVAL_METRIC filter — no broken is_deleted rename.
# ===========================================================================


@pytest.mark.unit
class TestEvalMetricThroughV2:
    def test_v2_eval_metric_keeps_deleted_predicate_intact(
        self, monkeypatch, settings
    ):
        # The legacy tracer_eval_logger lacks is_deleted. When the eval filter
        # is compiled by the v2 builder, the rewriter must NOT turn the
        # `(deleted = 0 OR deleted IS NULL)` predicate into `is_deleted`.
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilderV2, _eval_filter(eval_id, "greater_than", 50)
        )
        assert "FROM tracer_eval_logger FINAL" in where
        assert "(deleted = 0 OR deleted IS NULL)" in where
        # No stray is_deleted against the legacy table.
        assert "is_deleted" not in where
        assert "_peerdb_is_deleted" not in where

    def test_v2_choice_eval_metric_keeps_deleted_predicate(
        self, monkeypatch, settings
    ):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, _ = _translate(
            ClickHouseFilterBuilderV2, _eval_filter(eval_id, "in", ["a", "b"])
        )
        assert "(deleted = 0 OR deleted IS NULL)" in where
        assert "is_deleted" not in where


# ===========================================================================
# 8. has_eval / has_annotation subquery shape (eval-logger not-deleted pred).
# ===========================================================================


@pytest.mark.unit
class TestHasEvalHasAnnotationShape:
    @staticmethod
    def _bool_filter(col_id, value=True):
        return [
            {
                "column_id": col_id,
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": value,
                },
            }
        ]

    def test_has_eval_uses_aliased_deleted_predicate(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        where, _ = ClickHouseFilterBuilder(project_id="p1").translate(
            self._bool_filter("has_eval", True)
        )
        assert "FROM tracer_eval_logger AS el FINAL" in where
        assert "(el.deleted = 0 OR el.deleted IS NULL)" in where
        assert "_peerdb_is_deleted" not in where
        # spans-side scoping keeps this from matching every project.
        assert "sp.is_deleted = 0" in where
        assert "sp.project_id" in where

    def test_has_eval_v2_keeps_legacy_deleted_predicate(self, settings):
        # v2 builder + legacy eval table: the aliased deleted predicate must
        # survive the rewrite (el.deleted is NOT renamed to el.is_deleted).
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        where, _ = ClickHouseFilterBuilderV2(project_id="p1").translate(
            self._bool_filter("has_eval", True)
        )
        assert "FROM tracer_eval_logger AS el FINAL" in where
        assert "(el.deleted = 0 OR el.deleted IS NULL)" in where
        # spans-side `is_deleted` IS legitimately rewritten from _peerdb_*,
        # but the eval-logger `el.deleted` alias must stay bare.
        assert "el.is_deleted" not in where

    def test_has_eval_v2_table_uses_is_deleted(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        where, _ = ClickHouseFilterBuilder(project_id="p1").translate(
            self._bool_filter("has_eval", True)
        )
        assert "FROM tracer_eval_logger_v2 AS el FINAL" in where
        assert "el.is_deleted = 0" in where

    def test_has_eval_false_produces_no_condition(self):
        where, _ = ClickHouseFilterBuilder(project_id="p1").translate(
            self._bool_filter("has_eval", False)
        )
        assert where == ""

    def test_has_annotation_true_generates_in_subquery(self):
        where, _ = ClickHouseFilterBuilder(project_id="p1").translate(
            self._bool_filter("has_annotation", True)
        )
        assert "model_hub_score" in where
        assert "trace_id IN" in where
        assert "trace_id NOT IN" not in where

    def test_has_annotation_false_generates_not_in(self):
        where, _ = ClickHouseFilterBuilder(project_id="p1").translate(
            self._bool_filter("has_annotation", False)
        )
        assert "trace_id NOT IN" in where
