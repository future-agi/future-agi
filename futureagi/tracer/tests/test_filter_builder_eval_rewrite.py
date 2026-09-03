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
  1. Candidate-scoped ``ORDER BY version DESC LIMIT 1 BY id`` resolves the
     newest physical eval row before live/error/value predicates are applied.
  2. Legacy reads enforce both PeerDB and app tombstones after version collapse;
     v2 reads enforce ``is_deleted`` after version collapse.
  3. The v2 whole-fragment rewriter protects legacy eval-table
     ``_peerdb_version``/``_peerdb_is_deleted`` while still rewriting spans.
  4. _build_eval_condition multi-value CHOICE OR-join is wrapped in parens so
     the config/deleted/error guards scope ALL values (precedence fix).
  5. SCORE value/100 scaling; PASS_FAIL Passed/Failed -> output_bool.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import DatabaseError

from tracer.services.clickhouse.eval_logger_table import (
    eval_logger_live_state_columns,
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
    resolve_eval_filter_metadata,
)
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

    def test_unknown_table_name_is_rejected_before_sql_generation(self, settings):
        from django.core.exceptions import ImproperlyConfigured

        for unsupported in (
            "",
            "tracer_eval_logger_shadow",
            "tracer_eval_logger; DROP TABLE spans",
            ["tracer_eval_logger"],
        ):
            settings.CH25_EVAL_LOGGER_TABLE = unsupported
            for resolver in (
                eval_logger_source,
                eval_logger_version_column,
                eval_logger_live_state_columns,
            ):
                with pytest.raises(
                    ImproperlyConfigured, match="supported eval-logger table"
                ):
                    resolver()

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
        assert pred == ("_peerdb_is_deleted = 0 AND (deleted = 0 OR deleted IS NULL)")

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
        assert "FROM tracer_eval_logger " in where
        # PERF guard (filters.py:1464): no table-level FINAL — it would merge
        # the WHOLE eval table before the config filter (same OOM class the
        # span-list Phase-2 rewrite removed). A regression re-adding it must
        # turn this red, so pin its absence rather than just its presence.
        assert "FINAL" not in where
        assert "latest_eval._peerdb_is_deleted = 0" in where
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where
        assert "ORDER BY eval_scan._peerdb_version DESC" in where
        assert "LIMIT 1 BY eval_scan.id" in where
        # errored eval rows always excluded from value-match filters.
        assert "AND error = 0" in where

        # Candidate/config/date work happens before collapse; state/value work
        # happens after it. This ordering prevents stale matching/live rows
        # from being resurrected by a newer value, error, or tombstone.
        collapse = where.index("LIMIT 1 BY eval_scan.id")
        latest = where.index(") AS latest_eval", collapse)
        assert where.index("eval_scan.custom_eval_config_id") < collapse
        assert where.index("latest_eval._peerdb_is_deleted") > latest
        assert where.index("AND error = 0") > latest
        assert where.index("output_float >") > latest

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
        where, _ = _translate(ClickHouseFilterBuilder, _eval_filter(eval_id, "is_null"))
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
        where, _ = _translate(ClickHouseFilterBuilder, _eval_filter(eval_id, "is_null"))
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
        assert "latest_eval._peerdb_is_deleted = 0" in where
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where

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
        where, _ = _translate(ClickHouseFilterBuilder, _eval_filter(eval_id, "is_null"))
        assert "notEmpty(" in where
        assert "output_str IS NOT NULL" in where
        assert "NOT IN (" in where


# ===========================================================================
# 6. EVAL_METRIC — span vs trace mode column selection + no-config sentinel.
# ===========================================================================


@pytest.mark.unit
class TestEvalModeAndConfig:
    @pytest.mark.parametrize(
        ("output_type", "filter_op", "filter_value"),
        [
            ("SCORE", "greater_than", 50),
            ("PASS_FAIL", "equals", "Passed"),
            ("CHOICE", "in", ["a", "b"]),
            ("CHOICES", "equals", "yes"),
        ],
    )
    def test_authoritative_metadata_is_byte_identical_without_an_orm_fallback(
        self,
        monkeypatch,
        output_type,
        filter_op,
        filter_value,
    ):
        eval_id, config_ids = _patch_eval(monkeypatch, output_type)
        filters = _eval_filter(eval_id, filter_op, filter_value)
        expected = _translate(ClickHouseFilterBuilder, filters)

        monkeypatch.setattr(
            "tracer.services.clickhouse.query_builders.filters.resolve_eval_filter_metadata",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("authoritative metadata must suppress ORM resolution")
            ),
        )
        actual = _translate(
            ClickHouseFilterBuilder,
            filters,
            eval_filter_metadata={
                eval_id: EvalFilterMetadata(tuple(config_ids), output_type)
            },
        )

        assert actual == expected

    @pytest.mark.parametrize("metadata_present", [True, False])
    def test_authoritative_empty_metadata_is_a_known_no_match(
        self,
        monkeypatch,
        metadata_present,
    ):
        eval_id = str(uuid.uuid4())
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_builders.filters.resolve_eval_filter_metadata",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("explicit metadata must not fall back to the ORM")
            ),
        )

        where, params = _translate(
            ClickHouseFilterBuilder,
            _eval_filter(eval_id, "equals", 50),
            eval_filter_metadata=(
                {eval_id: EvalFilterMetadata((), "SCORE")} if metadata_present else {}
            ),
        )

        assert where == (
            "trace_id IN (SELECT toUUID('00000000-0000-0000-0000-000000000000'))"
        )
        assert params == {}

    def test_authoritative_resolution_applies_project_fence_before_config_read(
        self,
        monkeypatch,
    ):
        from unittest import mock

        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig

        eval_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        template_id = str(uuid.uuid4())
        config_id = str(uuid.uuid4())
        unscoped = mock.Mock()
        scoped = mock.Mock()
        manager = mock.Mock()
        manager.filter.return_value = unscoped
        unscoped.exists.return_value = True
        unscoped.filter.return_value = scoped
        scoped.values_list.side_effect = lambda field, flat=False: _FakeValuesList(
            [config_id] if field == "id" else [template_id]
        )
        monkeypatch.setattr(CustomEvalConfig, "objects", manager)
        monkeypatch.setattr(
            EvalTemplate,
            "no_workspace_objects",
            _FakeEvalTemplateManager("PASS_FAIL"),
        )

        metadata = resolve_eval_filter_metadata(eval_id, [project_id])

        manager.filter.assert_called_once_with(id=eval_id, deleted=False)
        unscoped.filter.assert_called_once_with(project_id__in=[project_id])
        assert metadata == EvalFilterMetadata((config_id,), "PASS_FAIL")

    def test_malformed_identifier_resolves_to_authoritative_no_match(
        self,
        monkeypatch,
    ):
        from django.core.exceptions import ValidationError

        from tracer.models.custom_eval_config import CustomEvalConfig

        class InvalidIdentifierManager:
            @staticmethod
            def filter(**_kwargs):
                raise ValidationError("invalid UUID")

        monkeypatch.setattr(
            CustomEvalConfig,
            "objects",
            InvalidIdentifierManager(),
        )

        metadata = resolve_eval_filter_metadata("not-a-uuid", [str(uuid.uuid4())])

        assert metadata == EvalFilterMetadata((), "SCORE")

    def test_trace_mode_matches_trace_id(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder,
            _eval_filter(eval_id, "greater_than", 50),
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        )
        assert where.startswith("trace_id IN (")
        assert "SELECT toString(latest_eval.trace_id) FROM" in where

    def test_span_mode_matches_span_id(self, monkeypatch):
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilder,
            _eval_filter(eval_id, "greater_than", 50),
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        )
        assert where.startswith("tuple(trace_id, id) IN (")
        assert (
            "SELECT tuple(toString(latest_eval.trace_id), "
            "toString(latest_eval.observation_span_id)) FROM" in where
        )
        assert "AND NOT isNull(eval_scan.trace_id)" in where

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
        eval_id, _ = _patch_eval(monkeypatch, "SCORE", config_ids=[], exists=False)
        where, _ = _translate(
            ClickHouseFilterBuilder, _eval_filter(eval_id, "greater_than", 50)
        )
        assert where == (
            "trace_id IN (SELECT toUUID('00000000-0000-0000-0000-000000000000'))"
        )

    def test_metadata_database_failure_propagates_instead_of_false_empty(
        self,
        monkeypatch,
    ):
        from tracer.models.custom_eval_config import CustomEvalConfig

        class _UnavailableConfigManager:
            @staticmethod
            def filter(**_kwargs):
                raise DatabaseError("metadata backend unavailable")

        monkeypatch.setattr(
            CustomEvalConfig,
            "objects",
            _UnavailableConfigManager(),
        )
        with pytest.raises(DatabaseError, match="metadata backend unavailable"):
            _translate(
                ClickHouseFilterBuilder,
                _eval_filter(str(uuid.uuid4()), "greater_than", 50),
            )


# ===========================================================================
# 7. V2 translate() of an EVAL_METRIC filter — no broken is_deleted rename.
# ===========================================================================


@pytest.mark.unit
class TestEvalMetricThroughV2:
    def test_v2_eval_metric_uses_authoritative_legacy_named_table(
        self, monkeypatch, settings
    ):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilderV2, _eval_filter(eval_id, "greater_than", 50)
        )
        assert "FROM tracer_eval_logger " in where
        # No table-level FINAL survives the v2 rewrite either (OOM guard).
        assert "FINAL" not in where
        assert "ORDER BY eval_scan._peerdb_version DESC" in where
        assert "latest_eval._peerdb_is_deleted = 0" in where
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where
        assert "FROM tracer_eval_logger_v2 " not in where

    def test_v2_choice_eval_metric_uses_configured_eval_table(
        self, monkeypatch, settings
    ):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        eval_id, _ = _patch_eval(monkeypatch, "CHOICE")
        where, _ = _translate(
            ClickHouseFilterBuilderV2, _eval_filter(eval_id, "in", ["a", "b"])
        )
        assert "FROM tracer_eval_logger " in where
        assert "ORDER BY eval_scan._peerdb_version DESC" in where
        assert "latest_eval._peerdb_is_deleted = 0" in where
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where

    def test_v2_eval_table_uses_native_latest_state_columns(
        self, monkeypatch, settings
    ):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        eval_id, _ = _patch_eval(monkeypatch, "SCORE")
        where, _ = _translate(
            ClickHouseFilterBuilderV2, _eval_filter(eval_id, "equals", 80)
        )

        assert "FROM tracer_eval_logger_v2 AS eval_scan" in where
        assert "ORDER BY eval_scan._version DESC" in where
        assert "LIMIT 1 BY eval_scan.id" in where
        assert "latest_eval.is_deleted = 0" in where
        assert "_peerdb_version" not in where
        assert "_peerdb_is_deleted" not in where

        latest = where.index(") AS latest_eval")
        assert where.index("latest_eval.is_deleted = 0") > latest
        assert where.index("AND error = 0") > latest
        assert where.index("output_float =") > latest


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
        assert "FROM tracer_eval_logger AS eval_scan " in where
        assert "FINAL" not in where
        assert "ORDER BY eval_scan._peerdb_version DESC" in where
        assert "LIMIT 1 BY eval_scan.id" in where
        assert "latest_eval._peerdb_is_deleted = 0" in where
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where
        # spans-side scoping keeps this from matching every project.
        assert "sp.is_deleted = 0" in where
        assert "sp.project_id" in where

    def test_has_eval_v2_uses_authoritative_legacy_named_table(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        where, _ = ClickHouseFilterBuilderV2(project_id="p1").translate(
            self._bool_filter("has_eval", True)
        )
        assert "FROM tracer_eval_logger AS eval_scan " in where
        assert "FINAL" not in where
        assert "ORDER BY eval_scan._peerdb_version DESC" in where
        assert "latest_eval._peerdb_is_deleted = 0" in where
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where

    def test_has_eval_v2_table_uses_is_deleted(self, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        where, _ = ClickHouseFilterBuilder(project_id="p1").translate(
            self._bool_filter("has_eval", True)
        )
        assert "FROM tracer_eval_logger_v2 AS eval_scan " in where
        assert "FINAL" not in where
        assert "ORDER BY eval_scan._version DESC" in where
        assert "latest_eval.is_deleted = 0" in where

    def test_has_eval_false_produces_candidate_scoped_anti_membership(self):
        where, _ = ClickHouseFilterBuilder(
            project_id="p1", candidate_ids_param="candidate_trace_ids"
        ).translate(self._bool_filter("has_eval", False))
        assert "trace_id NOT IN" in where
        assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in where

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

    @pytest.mark.parametrize(
        "column_id", ["has_eval", "has_annotation", "my_annotations"]
    )
    @pytest.mark.parametrize("filter_op", ["not_equals", "is_null", "is_not_null"])
    def test_boolean_meta_filters_reject_non_equals_operations(
        self,
        column_id: str,
        filter_op: str,
    ) -> None:
        filter_item = self._bool_filter(column_id, True)[0]
        filter_item["filter_config"]["filter_op"] = filter_op
        if column_id == "my_annotations":
            filter_item["filter_config"]["user_id"] = (
                "00000000-0000-4000-8000-000000000002"
            )

        with pytest.raises(ValueError, match="supports only the equals operation"):
            ClickHouseFilterBuilder(
                project_id="p1", candidate_ids_param="candidate_trace_ids"
            ).translate([filter_item])
