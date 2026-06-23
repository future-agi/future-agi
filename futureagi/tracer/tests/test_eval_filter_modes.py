"""Eval-filter SQL verification for the count-mode-touched list endpoints.

Confirms the eval filter path (``ClickHouseFilterBuilder._build_eval_condition``)
behaves correctly for the APIs whose display was changed to count-mode:

* ``list_spans_observe`` (SpanListQueryBuilder, ``query_mode=span``) — each
  span is matched on its OWN eval row: ``id IN (SELECT observation_span_id …)``.
* ``list_voice_calls`` (VoiceCallListQueryBuilder, default ``query_mode=trace``)
  and ``list_traces_of_session`` — a trace matches if ANY span has the value:
  ``trace_id IN (SELECT trace_id …)``.

Pass/Fail → ``output_bool``, Choices → ``output_str_list``/``output_str``,
Score → ``output_float`` (UI 0-100 ÷ 100). These tests assert SQL shape only
(no CH execution needed); they lock the behaviour the count-mode display
changes must not disturb.
"""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


def _eval_filter(cid, op, value, *, filter_type="text"):
    return {
        "column_id": str(cid),
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": op,
            "filter_value": value,
            "col_type": "EVAL_METRIC",
        },
    }


def _set_output(cfg, output, choices=None):
    cfg.eval_template.config = {"output": output}
    if choices is not None:
        cfg.eval_template.choices = choices
        cfg.eval_template.save(update_fields=["config", "choices"])
    else:
        cfg.eval_template.save(update_fields=["config"])


def _span_builder(cfg):
    return ClickHouseFilterBuilder(
        project_ids=[str(cfg.project_id)],
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
    )


def _trace_builder(cfg):
    return ClickHouseFilterBuilder(project_ids=[str(cfg.project_id)])


# ---------------------------------------------------------------------------
# list_spans_observe — span mode: match each span on its own eval row.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSpanModeEvalFilters:
    def test_passfail_targets_span_and_output_bool(self, custom_eval_config):
        _set_output(custom_eval_config, "Pass/Fail")
        where, params = _span_builder(custom_eval_config).translate(
            [_eval_filter(custom_eval_config.id, "equals", ["Passed"])]
        )
        assert "id IN (SELECT observation_span_id FROM tracer_eval_logger" in where
        assert "output_bool IN" in where
        assert "trace_id IN" not in where

    def test_choices_contains_parses_choice_array(self, custom_eval_config):
        _set_output(custom_eval_config, "choices", ["Accurate", "Inaccurate"])
        where, params = _span_builder(custom_eval_config).translate(
            [_eval_filter(custom_eval_config.id, "contains", "Accurate")]
        )
        assert "SELECT observation_span_id FROM tracer_eval_logger" in where
        assert "arrayExists" in where and "output_str ILIKE" in where
        assert "%Accurate%" in params.values()

    def test_choices_equals_uses_has_membership(self, custom_eval_config):
        _set_output(custom_eval_config, "choices", ["Accurate", "Inaccurate"])
        where, params = _span_builder(custom_eval_config).translate(
            [_eval_filter(custom_eval_config.id, "equals", "Accurate")]
        )
        assert "SELECT observation_span_id" in where
        assert "has(" in where and "output_str =" in where

    def test_score_scales_and_targets_output_float(self, custom_eval_config):
        _set_output(custom_eval_config, "score")
        where, params = _span_builder(custom_eval_config).translate(
            [
                _eval_filter(
                    custom_eval_config.id, "greater_than", 75, filter_type="number"
                )
            ]
        )
        assert "id IN (SELECT observation_span_id FROM tracer_eval_logger" in where
        assert "output_float >" in where
        assert 0.75 in params.values()


# ---------------------------------------------------------------------------
# list_voice_calls / list_traces_of_session — trace mode: any span matches.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTraceModeEvalFilters:
    def test_passfail_any_span_in_trace(self, custom_eval_config):
        _set_output(custom_eval_config, "Pass/Fail")
        where, params = _trace_builder(custom_eval_config).translate(
            [_eval_filter(custom_eval_config.id, "equals", ["Failed"])]
        )
        assert "trace_id IN (SELECT trace_id FROM tracer_eval_logger" in where
        assert "output_bool IN" in where
        assert (0,) in params.values()

    def test_choices_any_span_in_trace(self, custom_eval_config):
        _set_output(custom_eval_config, "choices", ["Accurate", "Inaccurate"])
        where, params = _trace_builder(custom_eval_config).translate(
            [_eval_filter(custom_eval_config.id, "equals", "Inaccurate")]
        )
        assert "trace_id IN (SELECT trace_id FROM tracer_eval_logger" in where
        assert "has(" in where

    def test_score_between_scales_to_raw(self, custom_eval_config):
        _set_output(custom_eval_config, "score")
        where, params = _trace_builder(custom_eval_config).translate(
            [
                _eval_filter(
                    custom_eval_config.id, "between", [60, 90], filter_type="number"
                )
            ]
        )
        assert "trace_id IN (SELECT trace_id FROM tracer_eval_logger" in where
        assert "output_float BETWEEN" in where
        assert 0.6 in params.values() and 0.9 in params.values()

    def test_not_equals_passfail_negates_in_subquery(self, custom_eval_config):
        _set_output(custom_eval_config, "Pass/Fail")
        where, params = _trace_builder(custom_eval_config).translate(
            [_eval_filter(custom_eval_config.id, "not_equals", ["Passed"])]
        )
        assert "trace_id IN (SELECT trace_id FROM tracer_eval_logger" in where
        assert "output_bool NOT IN" in where
