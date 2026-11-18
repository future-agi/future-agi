"""Contract tests for the §10 cluster-RCA WebSocket event frames.

The bridge streams a fixed set of frames to the Analyze tab. The wire shape is
always ``{"type": <str>, "data": {...}}``. Each frame is a frozen dataclass in
``types.py`` whose fields are exactly the ``data`` payload. These tests pin the
discriminator string, the exact ``data`` key set, and that realistic producer
values survive ``to_frame()`` intact — so a drift between the dataclass and the
FE consumer (``clusterAnalyzeSocket``) fails here, not in the browser.
"""

from ee.agenthub.cluster_rca.types import (
    RcaErrorEvent,
    RcaReasoningEvent,
    RcaStatusEvent,
    RcaStepResultEvent,
    RcaStepStartEvent,
    RcaSynthesisEvent,
)

MSG_ID = "11111111-1111-1111-1111-111111111111"


def _frame(event):
    """Render and assert the envelope is well-formed, return the data payload."""
    frame = event.to_frame()
    assert set(frame) == {"type", "data"}
    assert frame["type"] == event.TYPE
    assert isinstance(frame["data"], dict)
    # The discriminator must never leak into the streamed payload.
    assert "TYPE" not in frame["data"]
    assert frame["data"]["message_id"] == MSG_ID
    return frame


class TestRcaStatusEvent:
    def test_shape_and_values(self):
        frame = _frame(
            RcaStatusEvent(
                message_id=MSG_ID, phase="investigating", detail="Investigating…"
            )
        )
        assert frame["type"] == "rca_status"
        assert set(frame["data"]) == {"message_id", "phase", "detail"}
        assert frame["data"]["phase"] == "investigating"
        assert frame["data"]["detail"] == "Investigating…"


class TestRcaReasoningEvent:
    def test_shape_and_values(self):
        frame = _frame(
            RcaReasoningEvent(
                message_id=MSG_ID,
                turn=2,
                reasoning="The cluster collapses on version Ver01.",
                content="Let me aggregate by version.",
            )
        )
        assert frame["type"] == "rca_reasoning"
        assert set(frame["data"]) == {"message_id", "turn", "reasoning", "content"}
        assert frame["data"]["turn"] == 2
        assert frame["data"]["reasoning"] == "The cluster collapses on version Ver01."
        assert frame["data"]["content"] == "Let me aggregate by version."

    def test_reasoning_optional(self):
        # Native thinking may be absent; the frame still serializes with None.
        frame = _frame(RcaReasoningEvent(message_id=MSG_ID, turn=1))
        assert frame["data"]["reasoning"] is None
        assert frame["data"]["content"] is None


class TestRcaStepStartEvent:
    def test_shape_and_values(self):
        # call_id format is rca-<N> (1-indexed); args is an open tool-dependent map.
        args = {
            "metric": "trace_count",
            "group_by": "version",
            "filter": {"cluster_id": "E-1B23E5E9"},
        }
        frame = _frame(
            RcaStepStartEvent(
                message_id=MSG_ID,
                call_id="rca-1",
                tool="aggregate",
                args=args,
                turn=1,
            )
        )
        assert frame["type"] == "rca_step_start"
        assert set(frame["data"]) == {
            "message_id",
            "call_id",
            "tool",
            "args",
            "turn",
        }
        assert frame["data"]["call_id"] == "rca-1"
        assert frame["data"]["tool"] == "aggregate"
        # Open map survives intact, including nested keys.
        assert frame["data"]["args"] == args
        assert frame["data"]["args"]["filter"]["cluster_id"] == "E-1B23E5E9"


class TestRcaStepResultEvent:
    def test_shape_and_values(self):
        # result is the raw tool handler return dict — an open map.
        result = {
            "buckets": [{"key": "Ver01", "count": 18, "pct": 90.0}],
            "total": 20,
            "group_by": "version",
            "metric": "trace_count",
        }
        frame = _frame(
            RcaStepResultEvent(
                message_id=MSG_ID,
                call_id="rca-1",
                tool="aggregate",
                result=result,
                turn=1,
            )
        )
        assert frame["type"] == "rca_step_result"
        assert set(frame["data"]) == {
            "message_id",
            "call_id",
            "tool",
            "result",
            "turn",
        }
        assert frame["data"]["result"] == result
        assert frame["data"]["result"]["buckets"][0]["pct"] == 90.0


class TestRcaSynthesisEvent:
    def test_shape_and_values(self):
        # confidence is the Confidence enum value: "H" | "M" | "L".
        frame = _frame(
            RcaSynthesisEvent(
                message_id=MSG_ID,
                synthesis="Root cause is a regression in Ver01.",
                fix="Roll back to the prior version.",
                confidence="H",
                evidence_trace_ids=["t-1", "t-2"],
                suggested_questions=["What changed in Ver01?", "Any deploy nearby?"],
            )
        )
        assert frame["type"] == "rca_synthesis"
        assert set(frame["data"]) == {
            "message_id",
            "synthesis",
            "fix",
            "confidence",
            "evidence_trace_ids",
            "suggested_questions",
        }
        assert frame["data"]["confidence"] == "H"
        assert frame["data"]["evidence_trace_ids"] == ["t-1", "t-2"]
        assert frame["data"]["suggested_questions"] == [
            "What changed in Ver01?",
            "Any deploy nearby?",
        ]

    def test_list_fields_default_empty(self):
        frame = _frame(
            RcaSynthesisEvent(
                message_id=MSG_ID,
                synthesis="x",
                fix="y",
                confidence="L",
            )
        )
        assert frame["data"]["evidence_trace_ids"] == []
        assert frame["data"]["suggested_questions"] == []

    def test_frozen_lists_are_independent(self):
        # Default-factory lists must not be shared across instances.
        a = RcaSynthesisEvent(message_id=MSG_ID, synthesis="a", fix="a", confidence="L")
        b = RcaSynthesisEvent(message_id=MSG_ID, synthesis="b", fix="b", confidence="L")
        assert a.evidence_trace_ids is not b.evidence_trace_ids


class TestRcaErrorEvent:
    def test_shape_and_values(self):
        frame = _frame(RcaErrorEvent(message_id=MSG_ID, error="boom"))
        # The error frame reuses the generic "error" type, not an rca_ prefix.
        assert frame["type"] == "error"
        assert set(frame["data"]) == {"message_id", "error"}
        assert frame["data"]["error"] == "boom"

    def test_default_error_message(self):
        frame = _frame(RcaErrorEvent(message_id=MSG_ID))
        assert frame["data"]["error"] == "Cluster analysis failed"


class TestFrozen:
    def test_events_are_immutable(self):
        import pytest
        from dataclasses import FrozenInstanceError

        evt = RcaStatusEvent(message_id=MSG_ID, phase="reading_context")
        with pytest.raises(FrozenInstanceError):
            evt.phase = "mutated"
