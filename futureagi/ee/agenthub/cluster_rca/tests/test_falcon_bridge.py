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


class TestUnresolvedClusterDoesNotFallThrough:
    """An error-cluster request must never hand the turn to plain Falcon.

    Returning None from run_cluster_rca lets the caller fall through to the
    general Falcon loop, which answers "Help me with: Cluster RCA" into a hidden
    conversation nobody reads. The user is billed for that chat, the Fix tab is
    sent frames its handler ignores, and the run ends with an empty thread that
    renders as the pre-run empty state — the cluster looks like it was never
    analysed and the failure is reported to nobody. That is the reported
    "analysis not available for this cluster".

    Cluster resolution is workspace-scoped, so a cluster whose project sits
    outside the socket's workspace reproduces this every time for that cluster.
    """

    @staticmethod
    def _ctx():
        from types import SimpleNamespace

        return SimpleNamespace(organization=object(), workspace=object())

    @staticmethod
    async def _run(context_info, resolver):
        from unittest.mock import patch

        from ee.agenthub.cluster_rca import falcon_bridge

        frames = []

        async def send_callback(frame):
            frames.append(frame)

        with patch.object(falcon_bridge, "_resolve_cluster", resolver):
            result = await falcon_bridge.run_cluster_rca(
                tool_context=TestUnresolvedClusterDoesNotFallThrough._ctx(),
                context_info=context_info,
                user_message="/cluster-rca",
                send_callback=send_callback,
            )
        return result, frames

    async def test_unresolvable_cluster_fails_the_run_instead_of_falling_through(self):
        result, frames = await self._run(
            {"entity_type": "error_cluster", "entity_id": "S-71376955"},
            lambda *a, **kw: None,
        )
        assert result is not None, "fell through to plain Falcon"
        assert result["mode"] == "cluster_rca"
        assert [f for f in frames if f.get("type") == "error"], (
            "the user was told nothing"
        )

    async def test_missing_entity_id_fails_the_run(self):
        result, frames = await self._run(
            {"entity_type": "error_cluster"},
            lambda *a, **kw: None,
        )
        assert result is not None
        assert [f for f in frames if f.get("type") == "error"]

    async def test_non_cluster_context_still_falls_through(self):
        """Other callers of the skill keep the old behaviour."""
        result, frames = await self._run(
            {"entity_type": "trace", "entity_id": "S-71376955"},
            lambda *a, **kw: None,
        )
        assert result is None
        assert frames == []
