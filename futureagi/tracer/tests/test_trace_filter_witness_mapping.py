from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.utils import eval as eval_utils

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
TRACE_ID = "00000000-0000-4000-8000-000000000002"
START = datetime(2026, 7, 30, tzinfo=UTC)


def _span(span_id: str, offset: int, **attributes):
    return SimpleNamespace(
        id=span_id,
        start_time=START + timedelta(seconds=offset),
        span_attributes=attributes,
        eval_attributes={},
    )


def _trace_and_spans():
    trace = SimpleNamespace(id=TRACE_ID, project_id=PROJECT_ID)
    spans = [
        _span("root", 0, final_status="Approved", customer_tier="free"),
        _span("status-child", 1, final_status="Rejected"),
        _span("tier-child", 2, customer_tier="vip"),
    ]
    return trace, spans


def _witness(span, ordinal: int, column_id: str) -> dict:
    return {
        "filter_ordinal": ordinal,
        "column_id": column_id,
        "col_type": "SPAN_ATTRIBUTE",
        "project_id": PROJECT_ID,
        "trace_id": TRACE_ID,
        "span_id": span.id,
        "start_time": span.start_time.isoformat(),
    }


def _resolve(trace, spans, witnesses, path: str):
    witness_token = eval_utils._task_filter_witnesses.set(tuple(witnesses))
    memo_token = eval_utils._trace_span_memo.set(
        {f"{trace.project_id}:{trace.id}": spans}
    )
    try:
        return eval_utils._resolve_trace_path(trace, path)
    finally:
        eval_utils._trace_span_memo.reset(memo_token)
        eval_utils._task_filter_witnesses.reset(witness_token)


def test_saved_positional_mapping_reads_the_child_that_matched_the_filter() -> None:
    trace, spans = _trace_and_spans()
    witnesses = [_witness(spans[1], 0, "final_status")]

    # The old positional resolver would read root/Approved here. A unique
    # filter key now binds the saved path to the physical matching child.
    assert _resolve(trace, spans, witnesses, "spans.0.final_status") == "Rejected"


def test_multiple_filter_leaves_can_bind_to_different_children() -> None:
    trace, spans = _trace_and_spans()
    witnesses = [
        _witness(spans[1], 0, "final_status"),
        _witness(spans[2], 1, "customer_tier"),
    ]

    assert (
        _resolve(trace, spans, witnesses, "filter_spans.0.final_status") == "Rejected"
    )
    assert _resolve(trace, spans, witnesses, "filter_spans.1.customer_tier") == "vip"


def test_duplicate_filter_key_mapping_fails_closed_instead_of_guessing() -> None:
    trace, spans = _trace_and_spans()
    witnesses = [
        _witness(spans[0], 0, "final_status"),
        _witness(spans[1], 1, "final_status"),
    ]

    with pytest.raises(ValueError, match="could not be resolved safely"):
        _resolve(trace, spans, witnesses, "spans.0.final_status")


def test_stale_or_reused_span_identity_fails_closed() -> None:
    trace, spans = _trace_and_spans()
    stale = _witness(spans[1], 0, "final_status")
    stale["start_time"] = (spans[1].start_time + timedelta(seconds=1)).isoformat()

    with pytest.raises(ValueError, match="could not be resolved safely"):
        _resolve(trace, spans, [stale], "spans.0.final_status")


def test_witness_from_another_project_or_trace_fails_closed() -> None:
    trace, spans = _trace_and_spans()
    foreign = _witness(spans[1], 0, "final_status")
    foreign["project_id"] = "another-project"

    with pytest.raises(ValueError, match="could not be resolved safely"):
        _resolve(trace, spans, [foreign], "spans.0.final_status")
