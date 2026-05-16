import uuid

import pytest
from django.db.models import Q

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.utils.constants import LIST_OPS, NO_VALUE_OPS, RANGE_OPS
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS
from tracer.utils.filters import ColType, FilterEngine


def _value_for(filter_type, filter_op):
    if filter_op in NO_VALUE_OPS:
        return None
    if filter_op in RANGE_OPS:
        if filter_type == "datetime":
            return ["2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"]
        if filter_type == "number":
            return [10, 20]
        return ["alpha", "omega"]
    if filter_op in LIST_OPS:
        if filter_type == "annotator":
            return [str(uuid.uuid4()), str(uuid.uuid4())]
        if filter_type == "thumbs":
            return ["Thumbs Up", "Thumbs Down"]
        return ["alpha", "beta"]
    if filter_type == "number":
        return 10
    if filter_type == "boolean":
        return True
    if filter_type == "datetime":
        return "2026-01-01T00:00:00.000Z"
    if filter_type == "annotator":
        return str(uuid.uuid4())
    if filter_type == "thumbs":
        return "Thumbs Up"
    return "alpha"


def _api_filter(column_id, col_type, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _translate(filter_item, *, query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE):
    builder = ClickHouseFilterBuilder(query_mode=query_mode)
    return builder.translate([filter_item])


def _assert_sql_shape(where):
    assert where
    assert "IS NULL %(" not in where
    assert "IS NOT NULL %(" not in where
    assert "IN ()" not in where
    assert "NOT IN ()" not in where


class _FakeValuesList:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _FakeConfigQuerySet:
    def __init__(self, config_id, template_id):
        self.config_id = config_id
        self.template_id = template_id

    def exists(self):
        return True

    def filter(self, **kwargs):
        return self

    def values_list(self, field, flat=False):
        if field == "id":
            return _FakeValuesList([self.config_id])
        if field == "eval_template_id":
            return _FakeValuesList([self.template_id])
        return _FakeValuesList([])


class _FakeConfigManager:
    def __init__(self, config_id, template_id):
        self.queryset = _FakeConfigQuerySet(config_id, template_id)

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


def _patch_eval_template_output(monkeypatch, output_type):
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    config_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())
    monkeypatch.setattr(
        CustomEvalConfig,
        "objects",
        _FakeConfigManager(config_id, template_id),
    )
    monkeypatch.setattr(
        EvalTemplate,
        "no_workspace_objects",
        _FakeEvalTemplateManager(output_type),
    )
    return template_id


class TestClickHouseFilterOperatorMatrix:
    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
    def test_system_number_metrics_accept_every_number_operator(self, filter_op):
        where, _ = _translate(
            _api_filter(
                "latency_ms",
                ClickHouseFilterBuilder.SYSTEM_METRIC,
                "number",
                filter_op,
                _value_for("number", filter_op),
            )
        )

        _assert_sql_shape(where)

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["text"]))
    def test_system_text_metrics_accept_every_text_operator(self, filter_op):
        where, params = _translate(
            _api_filter(
                "model",
                ClickHouseFilterBuilder.SYSTEM_METRIC,
                "text",
                filter_op,
                _value_for("text", filter_op),
            )
        )

        _assert_sql_shape(where)
        if filter_op == "contains":
            assert "%alpha%" in params.values()
        if filter_op == "starts_with":
            assert "alpha%" in params.values()
        if filter_op == "ends_with":
            assert "%alpha" in params.values()

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
    def test_voice_numeric_expression_metrics_accept_every_number_operator(
        self, filter_op
    ):
        where, _ = _translate(
            _api_filter(
                "agent_talk_percentage",
                ClickHouseFilterBuilder.SYSTEM_METRIC,
                "number",
                filter_op,
                _value_for("number", filter_op),
            )
        )

        _assert_sql_shape(where)

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["text"]))
    def test_voice_text_expression_metrics_accept_every_text_operator(self, filter_op):
        where, params = _translate(
            _api_filter(
                "call_type",
                ClickHouseFilterBuilder.SYSTEM_METRIC,
                "text",
                filter_op,
                _value_for("text", filter_op),
            )
        )

        _assert_sql_shape(where)
        if filter_op == "in":
            assert ("alpha", "beta") in params.values()
        if filter_op == "not_in":
            assert ("alpha", "beta") in params.values()

    @pytest.mark.parametrize(
        "filter_type,filter_op",
        [
            (filter_type, filter_op)
            for filter_type in (
                "number",
                "text",
                "categorical",
                "thumbs",
                "annotator",
                "boolean",
                "array",
            )
            for filter_op in sorted(FILTER_TYPE_ALLOWED_OPS[filter_type])
        ],
    )
    def test_annotation_metrics_accept_every_operator_for_their_value_type(
        self, filter_type, filter_op
    ):
        where, _ = _translate(
            _api_filter(
                str(uuid.uuid4()),
                ClickHouseFilterBuilder.ANNOTATION,
                filter_type,
                filter_op,
                _value_for(filter_type, filter_op),
            )
        )

        _assert_sql_shape(where)
        if filter_type == "boolean" and filter_op == "not_equals":
            assert "!=" in where
        if filter_type == "annotator" and filter_op in ("not_equals", "not_in"):
            assert " NOT IN " in where

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["annotator"]))
    def test_global_annotator_filter_accepts_every_annotator_operator(self, filter_op):
        where, _ = _translate(
            _api_filter(
                "annotator",
                ClickHouseFilterBuilder.NORMAL,
                "annotator",
                filter_op,
                _value_for("annotator", filter_op),
            )
        )

        _assert_sql_shape(where)
        if filter_op in ("not_equals", "not_in", "is_null"):
            assert " NOT IN " in where

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
    def test_eval_score_metrics_accept_every_number_operator(
        self, monkeypatch, filter_op
    ):
        eval_id = _patch_eval_template_output(monkeypatch, "SCORE")

        where, _ = _translate(
            _api_filter(
                eval_id,
                ClickHouseFilterBuilder.EVAL_METRIC,
                "number",
                filter_op,
                _value_for("number", filter_op),
            )
        )

        _assert_sql_shape(where)

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["text"]))
    def test_eval_choice_metrics_accept_every_text_operator(
        self, monkeypatch, filter_op
    ):
        eval_id = _patch_eval_template_output(monkeypatch, "CHOICE")

        where, _ = _translate(
            _api_filter(
                eval_id,
                ClickHouseFilterBuilder.EVAL_METRIC,
                "text",
                filter_op,
                _value_for("text", filter_op),
            )
        )

        _assert_sql_shape(where)

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["boolean"]))
    def test_eval_pass_fail_metrics_accept_every_boolean_operator(
        self, monkeypatch, filter_op
    ):
        eval_id = _patch_eval_template_output(monkeypatch, "PASS_FAIL")

        where, _ = _translate(
            _api_filter(
                eval_id,
                ClickHouseFilterBuilder.EVAL_METRIC,
                "boolean",
                filter_op,
                _value_for("boolean", filter_op),
            )
        )

        _assert_sql_shape(where)


class TestDjangoFilterOperatorMatrix:
    @pytest.mark.parametrize(
        "filter_op",
        sorted(FILTER_TYPE_ALLOWED_OPS["number"]),
    )
    def test_pg_system_number_metrics_accept_every_number_operator(self, filter_op):
        q = FilterEngine.get_filter_conditions_for_system_metrics(
            [
                _api_filter(
                    "latency_ms",
                    ColType.SYSTEM_METRIC.value,
                    "number",
                    filter_op,
                    _value_for("number", filter_op),
                )
            ]
        )

        assert isinstance(q, Q)
        assert q.children

    @pytest.mark.parametrize(
        "filter_op",
        sorted(FILTER_TYPE_ALLOWED_OPS["text"]),
    )
    def test_pg_system_text_metrics_accept_every_text_operator(self, filter_op):
        q = FilterEngine.get_filter_conditions_for_system_metrics(
            [
                _api_filter(
                    "status",
                    ColType.SYSTEM_METRIC.value,
                    "text",
                    filter_op,
                    _value_for("text", filter_op),
                )
            ]
        )

        assert isinstance(q, Q)
        assert q.children

    @pytest.mark.parametrize(
        "filter_op",
        sorted(FILTER_TYPE_ALLOWED_OPS["number"]),
    )
    def test_pg_voice_numeric_metrics_accept_every_number_operator(self, filter_op):
        q, annotations = FilterEngine.get_filter_conditions_for_voice_system_metrics(
            [
                _api_filter(
                    "agent_talk_percentage",
                    ColType.SYSTEM_METRIC.value,
                    "number",
                    filter_op,
                    _value_for("number", filter_op),
                )
            ]
        )

        assert isinstance(q, Q)
        assert q.children
        assert "_voice_agent_talk_pct" in annotations

    @pytest.mark.parametrize(
        "filter_type,filter_op",
        [
            (filter_type, filter_op)
            for filter_type in (
                "number",
                "text",
                "categorical",
                "thumbs",
                "annotator",
                "boolean",
                "array",
            )
            for filter_op in sorted(FILTER_TYPE_ALLOWED_OPS[filter_type])
        ],
    )
    def test_voice_annotation_filters_accept_every_operator_for_their_value_type(
        self, filter_type, filter_op
    ):
        q, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            [
                _api_filter(
                    str(uuid.uuid4()),
                    ColType.ANNOTATION.value,
                    filter_type,
                    filter_op,
                    _value_for(filter_type, filter_op),
                )
            ]
        )

        assert isinstance(q, Q)
        assert q.children

    @pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["datetime"]))
    def test_in_memory_datetime_filter_accepts_every_datetime_operator(self, filter_op):
        engine = FilterEngine([])
        rows = [
            {"created_at": "2026-01-01T12:00:00.000Z"},
            {"created_at": "2026-01-03T12:00:00.000Z"},
            {"created_at": None},
        ]

        out = engine._filter_datetime(
            rows,
            "created_at",
            filter_op,
            _value_for("datetime", filter_op),
            ColType.NORMAL,
        )

        assert isinstance(out, list)

    def test_in_memory_filters_cover_multi_select_and_native_boolean_values(self):
        engine = FilterEngine([])

        assert engine._filter_text(
            [{"k": "alpha"}, {"k": "beta"}, {"k": "gamma"}],
            "k",
            "in",
            ["alpha", "beta"],
            ColType.NORMAL,
        ) == [{"k": "alpha"}, {"k": "beta"}]
        assert engine._filter_boolean(
            [{"b": True}, {"b": False}, {"b": "true"}],
            "b",
            True,
            ColType.NORMAL,
            "equals",
        ) == [{"b": True}]
