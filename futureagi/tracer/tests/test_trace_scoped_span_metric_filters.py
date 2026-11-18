"""Trace-scoped identity contracts for span-grid eval/annotation filters."""

from __future__ import annotations

import uuid

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


class _Values:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _Configs:
    def __init__(self, config_id: str, template_id: str):
        self.config_id = config_id
        self.template_id = template_id

    def filter(self, **_kwargs):
        return self

    def exists(self):
        return True

    def values_list(self, field, flat=False):
        del flat
        if field == "id":
            return _Values([self.config_id])
        if field == "eval_template_id":
            return _Values([self.template_id])
        return _Values([])


class _Template:
    def filter(self, **_kwargs):
        return self

    def values(self, *_fields):
        return self

    def first(self):
        return {"config": {"output": "SCORE"}}


def _span_builder() -> ClickHouseFilterBuilder:
    return ClickHouseFilterBuilder(
        table="spans",
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        project_id="00000000-0000-4000-8000-000000000001",
        candidate_ids_param="candidate_span_ids",
        candidate_entities_param="candidate_span_entities",
    )


@pytest.mark.unit
def test_span_eval_filter_matches_trace_and_span_pair(monkeypatch):
    from model_hub.models.evals_metric import EvalTemplate

    from tracer.models.custom_eval_config import CustomEvalConfig

    eval_id = str(uuid.uuid4())
    config_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())
    monkeypatch.setattr(
        CustomEvalConfig,
        "objects",
        _Configs(config_id, template_id),
    )
    monkeypatch.setattr(EvalTemplate, "no_workspace_objects", _Template())

    where, _ = _span_builder().translate(
        [
            {
                "column_id": eval_id,
                "filter_config": {
                    "col_type": ClickHouseFilterBuilder.EVAL_METRIC,
                    "filter_op": "greater_than",
                    "filter_value": 50,
                },
            }
        ]
    )

    assert "tuple(trace_id, id) IN (" in where
    assert (
        "tuple(toString(latest_eval.trace_id), "
        "toString(latest_eval.observation_span_id))" in where
    )
    assert "AND NOT isNull(eval_scan.trace_id)" in where
    assert (
        "(toString(eval_scan.trace_id), "
        "toString(eval_scan.observation_span_id)) "
        "IN %(candidate_span_entities)s" in where
    )
    assert "id IN (SELECT observation_span_id" not in where


@pytest.mark.unit
def test_span_has_eval_filter_joins_and_returns_exact_pair():
    where, _ = _span_builder().translate(
        [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
    )

    assert "tuple(trace_id, id) IN (" in where
    assert (
        "toString(latest_eval.trace_id), "
        "toString(latest_eval.observation_span_id)" in where
    )
    assert "ON sp.trace_id = toString(latest_eval.trace_id)" in where
    assert "AND sp.id = toString(latest_eval.observation_span_id)" in where
    assert "WHERE NOT isNull(eval_scan.trace_id)" in where
    assert "IN %(candidate_span_entities)s" in where
    assert "LIMIT 1 BY eval_scan.id" in where


@pytest.mark.unit
@pytest.mark.parametrize("present", [True, False])
def test_span_annotation_filter_resolves_span_backed_scores_to_exact_pair(present):
    where, _ = _span_builder().translate(
        [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": present,
                },
            }
        ]
    )

    expected_op = "IN" if present else "NOT IN"
    assert f"tuple(trace_id, id) {expected_op} (" in where
    assert "scored_sp.id = s.observation_span_id" in where
    assert "scored_sp.trace_id" in where
    assert "ifNull(s.observation_span_id, '') != ''" in where
    # Score.trace_id may legitimately be NULL for inline/span annotations.
    assert "AND NOT isNull(s.trace_id)" not in where
    assert "IN %(candidate_span_entities)s" in where
