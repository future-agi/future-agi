"""Regression tests for the retry signal reaching the flagged tier.

Two independent defects, both silent:

1. `structural_prefilter` appended `s["id"]` inside a loop indexed by `i`, where `s`
   was the leftover variable from the error-span loop above it. Every detected retry
   recorded the LAST span's id. The appends still happened — on one real trace the
   loop fired 249 times — so the signal looked alive in any count-based view. But
   `anomalous_ids` is a set, so N copies of one id collapse to a single entry.

2. `build_trace_payload` takes `retry_ids` as an explicit parameter and the only
   production caller does not pass it, so it defaulted to empty and the
   correctly-computed set under `_retry_ids` never reached `flagged`.

Measured over 2,107 real traces before the fix: 844 carried at least one retry; 566
of those (67%) had the signal collapse, and 556 (66%) had production's flagged set
contain none of their retry spans. Retries cluster at the tail of long agentic
traces, so an unflagged retry loses the structural emphasis the prefilter exists
to provide.

A trace here uses repeated same-name same-depth siblings, which is the detector's own
definition of a retry.
"""

from ee.agenthub.trace_scanner.compress import (
    build_trace_payload,
    structural_prefilter,
    structural_prefilter_with_ids,
)


def _span(sid, name, out="ok"):
    return {
        "span_id": sid,
        "span_name": name,
        "duration": "PT1S",
        "status_code": "Ok",
        "span_attributes": {
            "span.kind": "Tool",
            "input.value": f"call {name}",
            "output.value": out,
        },
        "child_spans": [],
    }


def _trace_with_retries(n=4):
    """One root with n identical-name sibling children — n-1 detected retries."""
    root = {
        "span_id": "root",
        "span_name": "agent",
        "duration": "PT9S",
        "status_code": "Ok",
        "span_attributes": {"span.kind": "CHAIN", "input.value": "do the thing",
                            "output.value": "done"},
        "child_spans": [_span(f"s{i}", "fetch_quote", f"attempt {i}") for i in range(n)],
    }
    return {"trace_id": "t1", "spans": [root]}


def test_each_retry_span_is_recorded_not_just_one():
    """The set-collapse bug: N appends of one id yield a single flagged span."""
    trace = _trace_with_retries(5)
    ext = structural_prefilter_with_ids(trace)
    retry_ids = set(ext.get("_retry_ids") or ())
    assert len(retry_ids) >= 3, "fixture should produce several retries"

    anomalous = set(structural_prefilter(trace).get("anomalous_span_ids") or [])
    # every retry the detector found must be individually present
    assert retry_ids <= anomalous, (
        f"retry spans missing from anomalous_span_ids: {retry_ids - anomalous}"
    )


def test_retry_ids_are_real_span_ids_not_the_last_span():
    """The stale-`s` signature: all recorded ids equal to the final span's."""
    trace = _trace_with_retries(5)
    ids = set(structural_prefilter_with_ids(trace).get("_retry_ids") or ())
    assert len(ids) > 1, "a single distinct id is the collapse signature"


def test_payload_flags_retries_without_being_passed_them():
    """Production calls build_trace_payload(trace, prefilter) with no third argument.

    Asserts EVERY retry is flagged, not merely one. Asserting `flagged & retry_ids`
    passed on the unfixed code: the stale id was the last span, which in a run of
    identical siblings is itself a retry, so the bug flagged one real retry by
    accident. That accident is not rare — on 2,107 real traces the broken signal
    still overlapped a genuine retry 34% of the time — so an `any` assertion does
    not separate fixed from broken.
    """
    trace = _trace_with_retries(5)
    prefilter = structural_prefilter_with_ids(trace)
    out = build_trace_payload(trace, prefilter)  # deliberately no retry_ids
    flagged = {s["id"] for s in (out.get("spans") or []) if s.get("flagged")}
    retry_ids = set(prefilter.get("_retry_ids") or ())
    assert retry_ids <= flagged, (
        f"retry spans absent from the flagged tier: {retry_ids - flagged}"
    )


def test_explicit_retry_ids_still_honoured():
    """The parameter must keep working for callers that do pass it."""
    trace = _trace_with_retries(5)
    prefilter = structural_prefilter_with_ids(trace)
    out = build_trace_payload(trace, prefilter, retry_ids=["s1"])
    flagged = {s["id"] for s in (out.get("spans") or []) if s.get("flagged")}
    assert "s1" in flagged


def test_trace_without_retries_flags_no_retries():
    """Guard against the fix flagging everything."""
    root = {
        "span_id": "root", "span_name": "agent", "duration": "PT1S",
        "status_code": "Ok",
        "span_attributes": {"span.kind": "CHAIN", "input.value": "hi",
                            "output.value": "hello"},
        "child_spans": [_span("a", "alpha"), _span("b", "beta"), _span("c", "gamma")],
    }
    ext = structural_prefilter_with_ids({"trace_id": "t2", "spans": [root]})
    assert not (ext.get("_retry_ids") or ()), "distinct names are not retries"
