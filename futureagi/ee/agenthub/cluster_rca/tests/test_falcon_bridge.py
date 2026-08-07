"""Tests for the falcon_bridge — no duplicate reasoning, cost passthrough."""

from ee.agenthub.cluster_rca.falcon_bridge import _format_synthesis_message
from ee.agenthub.cluster_rca.types import ClusterSynthesis
from ee.agenthub.cluster_rca.constants import Confidence


class TestFormatSynthesisMessage:
    def test_includes_synthesis_and_fix(self):
        s = ClusterSynthesis(
            synthesis="All traces fail on auth timeout.",
            fix="Increase the auth service timeout to 30s.",
            confidence=Confidence.HIGH,
        )
        msg = _format_synthesis_message(s)
        assert "auth timeout" in msg
        assert "Increase" in msg
        assert "High" in msg

    def test_no_fix_omits_fix_section(self):
        s = ClusterSynthesis(
            synthesis="Cause unclear.",
            fix="",
            confidence=Confidence.LOW,
        )
        msg = _format_synthesis_message(s)
        assert "**Fix:**" not in msg
        assert "Low" in msg

    def test_confidence_labels(self):
        for conf, label in [(Confidence.HIGH, "High"), (Confidence.MEDIUM, "Medium"), (Confidence.LOW, "Low")]:
            s = ClusterSynthesis(synthesis="x", fix="y", confidence=conf)
            assert label in _format_synthesis_message(s)
