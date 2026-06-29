"""Pin the RCA-trail and pattern-insight evidence serializers to the full key
union their producers emit. A too-narrow nested serializer silently drops
undeclared keys on output, so these round-trip assertions guard the contract.
"""

from django.test import SimpleTestCase

from tracer.serializers.feed import (
    PatternInsightEvidenceSerializer,
    RcaTrailStepSerializer,
)


class RcaTrailStepSerializerTests(SimpleTestCase):
    """One representative dict per rca_trace frame type; assert every producer
    key survives output with its value intact. Declared-but-absent optional
    fields surface as null (DRF emits all fields) — that's the populated
    contract, not key-dropping, so we assert the producer keys are a subset.
    """

    def _assert_kept(self, frame):
        out = RcaTrailStepSerializer(frame).data
        self.assertTrue(
            set(frame) <= set(out),
            f"dropped keys: {set(frame) - set(out)}",
        )
        for key, val in frame.items():
            self.assertEqual(out[key], val, f"value changed for {key!r}")
        return out

    def test_reasoning_frame_round_trips(self):
        self._assert_kept({"type": "reasoning", "text": "Looking at failures."})

    def test_step_start_frame_keeps_args_map(self):
        # args is an open map consumed by attribute on the FE — not stringified.
        self._assert_kept(
            {
                "type": "step_start",
                "call_id": "c1",
                "tool": "aggregate",
                "args": {"group_by": "model", "metric": "score"},
            }
        )

    def test_step_result_frame_keeps_result_map(self):
        self._assert_kept(
            {
                "type": "step_result",
                "call_id": "c1",
                "tool": "aggregate",
                "result": {"buckets": [{"key": "gpt-4", "pct": 80}]},
            }
        )

    def test_synthesis_frame_round_trips(self):
        self._assert_kept(
            {
                "type": "synthesis",
                "synthesis": "The agent skips the lookup step.",
                "fix": "Force a tool call before answering.",
                "confidence": "high",
            }
        )

    def test_args_result_tolerate_pre_stringified_json(self):
        # args/result are JSONField so a producer emitting them as objects OR as
        # pre-stringified JSON both round-trip — a DictField would crash on the
        # string, a CharField would repr the object.
        self._assert_kept(
            {
                "type": "step_start",
                "tool": "search",
                "args": '{"query": "foo"}',
            }
        )
        self._assert_kept(
            {
                "type": "step_result",
                "tool": "search",
                "result": '{"items": []}',
            }
        )


class PatternInsightEvidenceSerializerTests(SimpleTestCase):
    """One evidence dict per insight builder; every key must survive output."""

    def _round_trip(self, evidence):
        out = PatternInsightEvidenceSerializer(evidence).data
        self.assertEqual(
            set(out),
            set(evidence),
            f"dropped keys: {set(evidence) - set(out)}",
        )
        return out

    def test_topic_log_odds(self):
        self._round_trip(
            {
                "test": "log-odds w/ Dirichlet prior (Monroe)",
                "z": 4.2,
                "fail_pct": 70,
                "baseline_pct": 12,
                "baseline": "31 KNN-passing traces",
            }
        )

    def test_brief_phrase(self):
        self._round_trip(
            {
                "test": "log-odds w/ Dirichlet prior (Monroe)",
                "z": 3.1,
                "hits": 5,
                "total": 9,
                "baseline": "other clusters' briefs in this project",
            }
        )

    def test_ks_distribution_shift(self):
        out = self._round_trip(
            {
                "test": "KS two-sample",
                "p_value": 0.0123,
                "ks_stat": 0.412,
                "fail_median": 8200.0,
                "baseline_median": 3100.0,
                "baseline": "44 KNN-passing traces",
            }
        )
        # The KS block is the float-heavy one most at risk of silent dropping.
        self.assertEqual(
            {"p_value", "ks_stat", "fail_median", "baseline_median"} & set(out),
            {"p_value", "ks_stat", "fail_median", "baseline_median"},
        )

    def test_missing_tool(self):
        self._round_trip(
            {
                "tool": "search_kb",
                "missing_in": 6,
                "traces_with_tools": 8,
            }
        )

    def test_judge_phrase(self):
        self._round_trip(
            {
                "test": "log-odds w/ Dirichlet prior (Monroe)",
                "z": 2.7,
                "hits": 4,
                "total": 7,
            }
        )
