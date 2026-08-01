from types import SimpleNamespace

import pytest
from rest_framework import serializers

from tracer.constants.eval_tasks import MAX_BOUNDED_HISTORICAL_SPAN_TRACE_ROWS
from tracer.models.eval_task import RowType, RunType
from tracer.serializers.eval_task import EvalTaskSerializer


@pytest.mark.parametrize("row_type", [RowType.SPANS, RowType.TRACES])
def test_historical_span_trace_serializer_rejects_unbounded_limit(row_type):
    serializer = EvalTaskSerializer()

    with pytest.raises(serializers.ValidationError) as exc_info:
        serializer.validate(
            {
                "run_type": RunType.HISTORICAL,
                "row_type": row_type,
                "spans_limit": MAX_BOUNDED_HISTORICAL_SPAN_TRACE_ROWS + 1,
            }
        )

    assert "spans_limit" in exc_info.value.detail


@pytest.mark.parametrize("row_type", [RowType.SPANS, RowType.TRACES])
def test_historical_span_trace_serializer_accepts_exact_bounded_limit(row_type):
    serializer = EvalTaskSerializer()
    attrs = {
        "run_type": RunType.HISTORICAL,
        "row_type": row_type,
        "spans_limit": MAX_BOUNDED_HISTORICAL_SPAN_TRACE_ROWS,
    }

    assert serializer.validate(attrs) == attrs


def test_historical_session_serializer_retains_million_row_contract():
    serializer = EvalTaskSerializer()
    attrs = {
        "run_type": RunType.HISTORICAL,
        "row_type": RowType.SESSIONS,
        "spans_limit": 1_000_000,
    }

    assert serializer.validate(attrs) == attrs


def test_partial_update_rejects_legacy_oversized_span_task():
    task = SimpleNamespace(
        run_type=RunType.HISTORICAL,
        row_type=RowType.SPANS,
        spans_limit=MAX_BOUNDED_HISTORICAL_SPAN_TRACE_ROWS + 1,
    )
    serializer = EvalTaskSerializer(instance=task, partial=True)

    with pytest.raises(serializers.ValidationError) as exc_info:
        serializer.validate({"name": "renamed"})

    assert "spans_limit" in exc_info.value.detail
