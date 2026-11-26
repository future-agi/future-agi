"""Tests for the falcon_bridge — no duplicate reasoning, cost passthrough."""

from unittest.mock import patch

import pytest

from ee.agenthub.cluster_rca.constants import Confidence
from ee.agenthub.cluster_rca.falcon_bridge import _format_synthesis_message
from ee.agenthub.cluster_rca.types import ClusterSynthesis


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
        for conf, label in [
            (Confidence.HIGH, "High"),
            (Confidence.MEDIUM, "Medium"),
            (Confidence.LOW, "Low"),
        ]:
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


class TestRunClusterRcaPersistsThroughTheWorker:
    """Drive the whole bridge so the persist call site itself is exercised.

    Reverting ``run_agent``'s call site to write the synthesis directly is the
    original bug; a test that only calls the gate helper cannot see it, because
    the gate still exists and every row assertion goes through it. These go
    through ``run_cluster_rca``'s real worker thread, replacing only the agent
    class (its ``__init__`` needs a gateway client and a cluster lookup).
    """

    @staticmethod
    def _cluster():
        import uuid as _uuid
        from datetime import timedelta as _td

        from django.utils import timezone as _tz

        from accounts.models.organization import Organization as _Org
        from accounts.models.user import User as _User
        from accounts.models.workspace import Workspace as _Ws
        from model_hub.models.ai_model import AIModel as _AIModel
        from tracer.models.project import Project as _Project
        from tracer.models.trace_error_analysis import (
            ClusterSource as _CS,
        )
        from tracer.models.trace_error_analysis import (
            TraceErrorGroup as _TEG,
        )

        user = _User.objects.create_user(
            email=f"rca-e2e-{_uuid.uuid4().hex[:8]}@futureagi.com",
            password="x",
            name="Rca E2E",
        )
        org = _Org.objects.create(name=f"Rca E2E Org {_uuid.uuid4().hex[:6]}")
        ws = _Ws.objects.create(
            name="Rca E2E WS",
            organization=org,
            created_by=user,
            is_default=True,
            is_active=True,
        )
        project = _Project.objects.create(
            name="Rca E2E Project",
            organization=org,
            workspace=ws,
            model_type=_AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        now = _tz.now()
        return _TEG.objects.create(
            project=project,
            cluster_id=f"E-{_uuid.uuid4().hex[:8]}",
            error_type="auth-timeout",
            source=_CS.SCANNER,
            title="auth timeout under load",
            first_seen=now - _td(hours=6),
            last_seen=now,
            error_count=12,
            unique_traces=9,
            rca_synthesis="Earlier completed root cause.",
            rca_fix="Earlier fix.",
            rca_confidence="H",
            rca_at=now - _td(hours=1),
            rca_failures_at_run=10,
        )

    @staticmethod
    def _fake_agent_class(result):
        """Stand-in whose ``run()`` returns ``result`` without any LLM call."""

        class _FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def run(self):
                result.kwargs = self.kwargs
                return result

        return _FakeAgent

    def _drive(self, result):
        """Run ``run_cluster_rca`` for a resolved cluster and report what landed.

        Returns ``(cluster, rca_at_before, rca_trace_before, posted)``, where
        ``posted`` records every ``post_rca_comment_to_linked_issue`` call so
        a test can assert the linked-issue path itself, not just the row.
        """
        import asyncio
        from types import SimpleNamespace as _NS

        from ee.agenthub.cluster_rca import falcon_bridge as _fb

        cluster = self._cluster()
        before_at = cluster.rca_at
        before_trace = cluster.rca_trace
        posted: list[str] = []

        def _resolve(*a, **k):
            return str(cluster.id), str(cluster.project_id), 12

        async def send_callback(frame):
            return None

        ctx = _NS(organization=object(), workspace=object())

        with (
            patch.object(_fb, "_resolve_cluster", _resolve),
            # run_agent imports the class from the agent module at call time,
            # so the stand-in has to be patched there, not on the bridge.
            patch(
                "ee.agenthub.cluster_rca.agent.ClusterAnalysisAgent",
                self._fake_agent_class(result),
            ),
            patch(
                "tracer.utils.feed.post_rca_comment_to_linked_issue",
                lambda cluster_pk: posted.append(cluster_pk) or True,
            ),
        ):
            asyncio.run(
                _fb.run_cluster_rca(
                    tool_context=ctx,
                    context_info={
                        "entity_type": "error_cluster",
                        "entity_id": cluster.cluster_id,
                    },
                    user_message="/cluster-rca",
                    send_callback=send_callback,
                )
            )

        cluster.refresh_from_db()
        return cluster, before_at, before_trace, posted

    @pytest.mark.django_db(transaction=True)
    def test_last_resort_run_never_reaches_the_row(self):
        """The worker's persist call refuses the deterministic fallback."""
        from ee.agenthub.cluster_rca.constants import Confidence as _Conf
        from ee.agenthub.cluster_rca.types import (
            ClusterAnalysisResult as _Res,
        )
        from ee.agenthub.cluster_rca.types import (
            ClusterSynthesis as _Syn,
        )

        salvaged = _Syn(
            synthesis="A recurring failure pattern was observed across the cluster.",
            fix="Inspect the cited traces directly.",
            confidence=_Conf.LOW,
        )
        result = _Res(
            cluster_id="E-x",
            synthesis=salvaged,
            terminated_reason="synthesis_forced",
            investigation_complete=False,
            synthesis_is_last_resort=True,
        )

        cluster, before_at, before_trace, posted = self._drive(result)

        assert cluster.rca_synthesis == "Earlier completed root cause."
        assert cluster.rca_confidence == "H"
        assert cluster.rca_at == before_at, "the run reset the staleness signal"
        assert cluster.rca_trace == before_trace, "the replay trail was replaced"
        assert posted == [], "a last-resort run reached the linked ticket"

    @pytest.mark.django_db(transaction=True)
    def test_model_authored_run_reaches_the_row(self):
        from ee.agenthub.cluster_rca.constants import Confidence as _Conf
        from ee.agenthub.cluster_rca.types import (
            ClusterAnalysisResult as _Res,
        )
        from ee.agenthub.cluster_rca.types import (
            ClusterSynthesis as _Syn,
        )

        authored = _Syn(
            synthesis="Auth pool exhaustion causes the timeout.",
            fix="Enlarge the pool.",
            confidence=_Conf.MEDIUM,
        )
        result = _Res(
            cluster_id="E-x",
            synthesis=authored,
            terminated_reason="synthesis_submitted",
            investigation_complete=True,
            synthesis_is_last_resort=False,
        )

        cluster, _before_at, _before_trace, posted = self._drive(result)

        assert cluster.rca_synthesis == "Auth pool exhaustion causes the timeout."
        assert cluster.rca_confidence == "M"
        assert cluster.rca_failures_at_run == 12
        assert posted == [str(cluster.id)], "the linked ticket was not notified"

    @pytest.mark.django_db(transaction=True)
    def test_completed_low_confidence_run_still_reaches_the_row(self):
        """The maintainer's carve-out, driven through the real bridge.

        Confidence is not the gate: a completed analysis that only came out low
        still has to save and notify. The helper test pins the gate; this pins
        that the production path agrees.
        """
        from ee.agenthub.cluster_rca.constants import Confidence as _Conf
        from ee.agenthub.cluster_rca.types import (
            ClusterAnalysisResult as _Res,
        )
        from ee.agenthub.cluster_rca.types import (
            ClusterSynthesis as _Syn,
        )

        low = _Syn(
            synthesis="Two of the sampled traces time out on auth, but the third "
            "returns a provider 429, so the cluster may mix two causes.",
            fix="Split the cluster before concluding, then re-run the analysis.",
            confidence=_Conf.LOW,
        )
        result = _Res(
            cluster_id="E-x",
            synthesis=low,
            terminated_reason="synthesis_submitted",
            investigation_complete=True,
            synthesis_is_last_resort=False,
        )

        cluster, _before_at, _before_trace, posted = self._drive(result)

        assert cluster.rca_synthesis == low.synthesis
        assert cluster.rca_confidence == "L"
        assert cluster.rca_failures_at_run == 12
        assert posted == [str(cluster.id)], "the carve-out did not notify"
