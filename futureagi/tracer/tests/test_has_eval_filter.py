"""
Tests for FilterEngine.get_filter_conditions_for_has_eval

Tests the has_eval filter that uses Exists subqueries to filter
traces/spans that have at least one associated EvalLogger record.
"""

import uuid

import pytest
from django.db.models import Q

from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.utils.filters import FilterEngine


def _make_has_eval_filter(value=True):
    """Helper to build a has_eval filter payload."""
    return [
        {
            "column_id": "has_eval",
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": value,
            },
        }
    ]


@pytest.fixture
def eval_logger(db, trace, observation_span, custom_eval_config):
    """Create an EvalLogger linked to the given trace and span."""
    return EvalLogger.objects.create(
        trace=trace,
        observation_span=observation_span,
        custom_eval_config=custom_eval_config,
        output_bool=True,
        output_float=0.95,
    )


@pytest.fixture
def trace_without_eval(db, project, project_version):
    """Create a second trace that has no EvalLogger records."""
    return Trace.objects.create(
        project=project,
        project_version=project_version,
        name="Trace Without Eval",
        input={"prompt": "No eval"},
        output={"response": "None"},
    )


@pytest.fixture
def span_without_eval(db, project, trace_without_eval):
    """Create a span with no EvalLogger records."""
    span_id = f"no_eval_span_{uuid.uuid4().hex[:16]}"
    return ObservationSpan.objects.create(
        id=span_id,
        project=project,
        trace=trace_without_eval,
        name="Span Without Eval",
        observation_type="llm",
        status="OK",
    )


# ---------------------------------------------------------------------------
# Unit tests: Q object generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFilterConditionsForHasEvalUnit:
    """Unit tests for the Q-object returned by get_filter_conditions_for_has_eval."""

    def test_empty_filters_returns_empty_q(self):
        result = FilterEngine.get_filter_conditions_for_has_eval([])
        assert result == Q()

    def test_none_filters_returns_empty_q(self):
        result = FilterEngine.get_filter_conditions_for_has_eval(None)
        assert result == Q()

    def test_no_has_eval_column_returns_empty_q(self):
        filters = [
            {
                "column_id": "some_other_column",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "test",
                },
            }
        ]
        result = FilterEngine.get_filter_conditions_for_has_eval(filters)
        assert result == Q()

    def test_has_eval_true_returns_non_empty_q_for_trace(self):
        filters = _make_has_eval_filter(True)
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result != Q()

    def test_has_eval_true_returns_non_empty_q_for_span(self):
        filters = _make_has_eval_filter(True)
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="span"
        )
        assert result != Q()

    def test_has_eval_false_returns_empty_q(self):
        filters = _make_has_eval_filter(False)
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result == Q()

    def test_has_eval_string_true_returns_non_empty_q(self):
        filters = _make_has_eval_filter("true")
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result != Q()

    def test_has_eval_string_True_returns_non_empty_q(self):
        filters = _make_has_eval_filter("True")
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result != Q()

    def test_has_eval_string_false_returns_empty_q(self):
        filters = _make_has_eval_filter("false")
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result == Q()

    def test_default_observe_type_is_trace(self):
        """When observe_type is omitted it defaults to 'trace'."""
        filters = _make_has_eval_filter(True)
        result = FilterEngine.get_filter_conditions_for_has_eval(filters)
        assert result != Q()

    def test_camel_case_filter_params_are_not_part_of_backend_contract(self):
        filters = [
            {
                "columnId": "has_eval",
                "filterConfig": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result == Q()

    def test_mixed_filters_only_picks_has_eval(self):
        """Other filter items in the list are ignored."""
        filters = [
            {
                "column_id": "status",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "OK",
                },
            },
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ]
        result = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert result != Q()


# ---------------------------------------------------------------------------
# Integration tests: actual DB filtering
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHasEvalFilterTraceIntegration:
    """Integration tests: filter Trace queryset with has_eval."""

    def test_trace_with_eval_is_included(self, trace, trace_without_eval, eval_logger):
        filters = _make_has_eval_filter(True)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        qs = Trace.objects.filter(id__in=[trace.id, trace_without_eval.id]).filter(
            condition
        )

        assert list(qs.values_list("id", flat=True)) == [trace.id]

    def test_trace_without_eval_is_excluded(
        self, trace, trace_without_eval, eval_logger
    ):
        filters = _make_has_eval_filter(True)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        qs = Trace.objects.filter(id__in=[trace.id, trace_without_eval.id]).filter(
            condition
        )

        assert trace_without_eval.id not in qs.values_list("id", flat=True)

    def test_no_filter_returns_all_traces(self, trace, trace_without_eval):
        condition = FilterEngine.get_filter_conditions_for_has_eval([])
        qs = Trace.objects.filter(id__in=[trace.id, trace_without_eval.id]).filter(
            condition
        )

        assert qs.count() == 2

    def test_false_filter_returns_all_traces(
        self, trace, trace_without_eval, eval_logger
    ):
        filters = _make_has_eval_filter(False)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        qs = Trace.objects.filter(id__in=[trace.id, trace_without_eval.id]).filter(
            condition
        )

        assert qs.count() == 2

    def test_multiple_evals_on_same_trace_returns_one_row(
        self, trace, observation_span, custom_eval_config, eval_logger
    ):
        """Exists ensures no duplicate rows even with multiple EvalLoggers."""
        EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            output_bool=False,
        )
        filters = _make_has_eval_filter(True)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        qs = Trace.objects.filter(id=trace.id).filter(condition)

        assert qs.count() == 1


@pytest.mark.integration
class TestHasEvalFilterSpanIntegration:
    """Integration tests: filter ObservationSpan queryset with has_eval."""

    def test_span_with_eval_is_included(
        self, observation_span, span_without_eval, eval_logger
    ):
        filters = _make_has_eval_filter(True)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="span"
        )
        qs = ObservationSpan.objects.filter(
            id__in=[observation_span.id, span_without_eval.id]
        ).filter(condition)

        assert list(qs.values_list("id", flat=True)) == [observation_span.id]

    def test_span_without_eval_is_excluded(
        self, observation_span, span_without_eval, eval_logger
    ):
        filters = _make_has_eval_filter(True)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="span"
        )
        qs = ObservationSpan.objects.filter(
            id__in=[observation_span.id, span_without_eval.id]
        ).filter(condition)

        assert span_without_eval.id not in qs.values_list("id", flat=True)

    def test_no_filter_returns_all_spans(self, observation_span, span_without_eval):
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            [], observe_type="span"
        )
        qs = ObservationSpan.objects.filter(
            id__in=[observation_span.id, span_without_eval.id]
        ).filter(condition)

        assert qs.count() == 2

    def test_multiple_evals_on_same_span_returns_one_row(
        self, trace, observation_span, custom_eval_config, eval_logger
    ):
        """Exists ensures no duplicate rows even with multiple EvalLoggers."""
        EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            output_float=0.5,
        )
        filters = _make_has_eval_filter(True)
        condition = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="span"
        )
        qs = ObservationSpan.objects.filter(id=observation_span.id).filter(condition)

        assert qs.count() == 1
