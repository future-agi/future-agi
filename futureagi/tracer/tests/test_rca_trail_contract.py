"""Pin RcaTrailStepSerializer to the rca_trace frames the cluster-RCA agent
actually emits.

The trail frames are built in the falcon bridge's ``on_event`` worker
(``ee/agenthub/cluster_rca/falcon_bridge.py``) and persisted to
``TraceErrorGroup.rca_trace``, then read back through this serializer and
consumed by the FE (clusterAnalyzeSocket buildMessagesFromFrames). If the
agent's emitted shape and the serializer drift, the FE silently loses fields.

Two assertions per variant:
  - ``is_valid()`` on the EXACT emitted frame — proves the producer shape is
    accepted as input (the union variants below mirror the four ``trace.append``
    blocks in the bridge verbatim, including realistic ``rca-N`` call ids,
    single-letter confidence, and open tool-dependent ``args``/``result`` maps).
  - an anti-key-drop ``.data`` round-trip over the serializer-declared union
    keys — a too-narrow nested serializer drops undeclared keys on output, so
    this guards the persisted-frame contract the same way
    ``test_feed_rca_serializers`` does.
"""

from django.test import SimpleTestCase

from tracer.serializers.feed import RcaTrailStepSerializer


class RcaTrailStepContractTests(SimpleTestCase):
    """One representative frame per union variant, built exactly as the bridge
    emits it. No DB — pure serialization.
    """

    def _assert_valid(self, frame):
        """The emitted shape must be accepted as input."""
        ser = RcaTrailStepSerializer(data=frame)
        self.assertTrue(ser.is_valid(), ser.errors)

    def _assert_no_key_drop(self, frame):
        """Every serializer-declared key the producer sets must survive output
        with its value intact. Keys the serializer does not declare are dropped
        silently on output (DRF behavior) — see the synthesis note below.
        """
        out = RcaTrailStepSerializer(frame).data
        declared = set(RcaTrailStepSerializer().fields)
        kept = set(frame) & declared
        self.assertTrue(
            kept <= set(out),
            f"declared keys dropped on output: {kept - set(out)}",
        )
        for key in kept:
            self.assertEqual(
                out[key], frame[key], f"value changed for {key!r}"
            )

    def _assert_contract(self, frame):
        self._assert_valid(frame)
        self._assert_no_key_drop(frame)

    def test_reasoning_frame(self):
        # falcon_bridge on_event "reasoning" → {"type","text"} (text capped 4000).
        self._assert_contract(
            {"type": "reasoning", "text": "Most failures cluster on the older version."}
        )

    def test_step_start_frame(self):
        # falcon_bridge on_event "tool_call" → {"type","call_id","tool","args"}.
        # call_id is the bridge-minted "rca-<N>"; args is json.loads of the
        # tool_call arguments (an open map → JSONField).
        self._assert_contract(
            {
                "type": "step_start",
                "call_id": "rca-1",
                "tool": "aggregate",
                "args": {
                    "metric": "trace_count",
                    "group_by": "version",
                    "filter": {"cluster_id": "E-1B23E5E9"},
                },
            }
        )

    def test_step_result_frame(self):
        # falcon_bridge on_event "tool_result" → {"type","call_id","tool","result"}.
        # result is the raw tool-handler return dict (open map → JSONField).
        self._assert_contract(
            {
                "type": "step_result",
                "call_id": "rca-1",
                "tool": "aggregate",
                "result": {
                    "buckets": [{"key": "Ver01", "count": 18, "pct": 90.0}],
                    "total": 20,
                    "group_by": "version",
                    "metric": "trace_count",
                },
            }
        )

    def test_synthesis_frame(self):
        # falcon_bridge on_event "synthesis" → projection of asdict(ClusterSynthesis):
        # {"type","synthesis","fix","confidence","suggested_questions"}.
        # confidence is the Confidence enum value — single letter "H"|"M"|"L",
        # NOT a word. suggested_questions is emitted by the bridge but is NOT a
        # declared serializer field, so it is dropped on output today; the
        # round-trip below only asserts the declared keys survive. If the
        # serializer ever grows a suggested_questions field, this frame already
        # carries it and the contract tightens for free.
        self._assert_contract(
            {
                "type": "synthesis",
                "synthesis": "The agent answers before calling the lookup tool, so "
                "stale context drives the wrong response.",
                "fix": "Require the lookup tool to run before the final answer.",
                "confidence": "H",
                "suggested_questions": [
                    "Which versions skip the lookup most?",
                    "Does the skip correlate with input length?",
                ],
            }
        )

    def test_args_result_tolerate_pre_stringified_json(self):
        # args/result are JSONField, so a producer emitting them as pre-stringified
        # JSON (not just objects) still validates and round-trips — a DictField
        # would reject the string, a CharField would repr the object.
        self._assert_contract(
            {"type": "step_start", "tool": "search", "args": '{"query": "foo"}'}
        )
        self._assert_contract(
            {"type": "step_result", "tool": "search", "result": '{"items": []}'}
        )
