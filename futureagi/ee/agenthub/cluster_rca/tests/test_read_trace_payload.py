"""read(trace) renders the span tree itself, deterministically and bounded.

The payload used to be assembled by a cheaper model: every read(trace) posted
the WHOLE span tree to a lite model and waited on the answer. That call had no
span-count cap and no total cap, so its latency was unbounded on exactly the
traces worth reading, and a raise from it discarded the root I/O, skeleton,
attributes and eval joins the read had already computed.

These tests pin the replacement. Each fails if the LLM step comes back or if a
budget stops being enforced:

  * no gateway call on the read path, at any depth
  * a whole-trace ceiling, not a per-field cap that span count can multiply
  * the FAILING span keeps its I/O even when it is last of a thousand
  * a failure at the END of an over-long field is not truncated away
  * what was withheld is stated, never silently dropped
"""

import threading
from unittest.mock import MagicMock, patch

from ee.agenthub.cluster_rca.agent import (
    _MAX_SPANS_RENDERED,
    _TRACE_IO_BUDGET,
    ClusterAnalysisAgent,
    _budgeted_field,
)

TRACE_UUID = "11111111-1111-1111-1111-111111111111"


def _span(n: int, *, status="OK", inp="", out="", msg=None) -> dict:
    return {
        "span_id": f"span-{n:04d}",
        "parent_span_id": None if n == 0 else "span-0000",
        "name": f"step_{n}",
        "observation_type": "chain",
        "status": status,
        "status_message": msg,
        "latency_ms": 5,
        "input": inp,
        "output": out,
        "trace_name": "checkout",
        "trace_session_id": None,
        "tags": None,
    }


def _agent(spans: list[dict]) -> ClusterAnalysisAgent:
    """Agent wired with just enough to run _read_trace, no DB and no gateway."""
    a = ClusterAnalysisAgent.__new__(ClusterAnalysisAgent)
    a.project_id = "proj-1"
    a.cluster_id = "E-CLUSTER1"
    a.cluster_uuid = "cluster-uuid-1"
    a._alias_to_uuid = {"T01": TRACE_UUID}
    a._uuid_to_alias = {}
    a._alias_counters = {}
    a._counter_lock = threading.Lock()
    a._trace_summary_cache = {}
    a.on_event = lambda *args, **kwargs: None
    # Present so a reintroduced LLM call would have something to reach for —
    # the assertion is that nothing ever touches it.
    a._gateway_client = MagicMock()
    a._spans_for_trace = lambda _uuid: spans
    return a


def _read(agent, depth="summary"):
    with patch(
        "ee.agenthub.cluster_rca.agent.list_attributes_for_trace", return_value=[]
    ), patch(
        "ee.agenthub.cluster_rca.selectors.trace_eval_results", return_value=[]
    ):
        return agent._read_trace("T01", depth)


def _io_chars(payload: dict) -> int:
    total = 0
    for s in payload["spans"]:
        for key in ("input", "output", "error"):
            if key in s:
                total += len(s[key]["value"])
    return total


class TestTheReadPathMakesNoLlmCall:
    """The whole point of the change: reading a trace is a DB read, not a
    round-trip to a weaker model."""

    def test_no_gateway_call_at_any_depth(self):
        spans = [_span(n, out="x" * 500) for n in range(50)]
        agent = _agent(spans)
        for depth in ("summary", "spans", "full"):
            agent._trace_summary_cache.clear()
            payload = _read(agent, depth)
            assert "error" not in payload, payload
        assert not agent._gateway_client.method_calls, (
            "read(trace) called the gateway — the LLM step is back"
        )


class TestThePayloadIsBounded:
    def test_a_thousand_chatty_spans_stay_under_the_trace_budget(self):
        # Each span alone is far past any per-field cap; only a whole-trace
        # ceiling keeps this payload sane.
        spans = [_span(n, inp="a" * 40_000, out="b" * 40_000) for n in range(1000)]
        payload = _read(_agent(spans))

        assert _io_chars(payload) <= _TRACE_IO_BUDGET * 1.1, (
            f"inline I/O was {_io_chars(payload)} chars — budget not enforced"
        )
        assert len(payload["spans"]) == _MAX_SPANS_RENDERED
        assert payload["spans_elided"] == 1000 - _MAX_SPANS_RENDERED

    def test_what_was_withheld_is_declared(self):
        spans = [_span(n, out="b" * 40_000) for n in range(1000)]
        payload = _read(_agent(spans))
        # Silent elision reads as "that is the whole trace", which is exactly
        # how a reader concludes nothing went wrong.
        assert payload["spans_without_io"] > 0
        assert "io_budget_note" in payload

    def test_spans_depth_carries_no_payloads(self):
        spans = [_span(n, out="b" * 5_000) for n in range(20)]
        payload = _read(_agent(spans), depth="spans")
        assert _io_chars(payload) == 0
        assert len(payload["spans"]) == 20


class TestTheFailureSurvivesTheBudget:
    def test_the_failing_span_keeps_its_io_when_it_is_last_of_a_thousand(self):
        """Execution order would spend the whole budget before reaching it."""
        spans = [_span(n, inp="a" * 40_000, out="b" * 40_000) for n in range(999)]
        spans.append(
            _span(
                999,
                status="ERROR",
                out="RateLimitReached: quota exceeded",
                msg="429 RateLimitReached",
            )
        )
        payload = _read(_agent(spans))

        failing = [s for s in payload["spans"] if s["status"] == "ERROR"]
        assert failing, "the failing span was elided entirely"
        assert "error" in failing[0], "the failing span lost its I/O to the budget"
        assert "RateLimitReached" in failing[0]["error"]["value"]

    def test_an_error_at_the_end_of_a_long_field_is_not_truncated_away(self):
        """Head-truncation alone drops the one thing the read exists to find."""
        text = "filler " * 5_000 + "Traceback: ValueError boom"
        field = _budgeted_field(text, 100)

        assert field["truncated"] is True
        assert "Traceback" in field["value"], (
            "a failure past the cut was dropped — head-only truncation is back"
        )
        assert field["full_chars"] == len(text)

    def test_a_short_field_is_returned_whole(self):
        field = _budgeted_field("all good", 100)
        assert field == {"value": "all good", "truncated": False, "full_chars": 8}

    def test_an_exhausted_budget_yields_nothing_not_everything(self):
        """A caller that has spent its budget can hand this a cap <= 0, and
        `s[:negative]` slices from the END — returning almost the whole field,
        the exact opposite of a budget. The helper has to be safe on its own."""
        field = _budgeted_field("x" * 10_000, -5)
        assert len(field["value"]) < 100, "a spent budget returned the field anyway"
        assert field["truncated"] is True
        assert field["full_chars"] == 10_000
