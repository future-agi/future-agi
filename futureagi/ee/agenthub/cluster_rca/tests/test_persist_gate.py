"""A run that never finished must not become a stored, published diagnosis.

The cluster-RCA agent always ends with a synthesis: when the loop dies on the
turn budget, the cost ceiling or an LLM error, ``_force_synthesis`` /
``_fallback_synthesis`` produce a LOW-confidence writeup from whatever was
gathered so the Analyse tab is never dead-air. That salvage is the correct close
for the *stream*. Persisting it is a different act, and the bridge used to do it
whenever ``result.synthesis is not None`` — so a failed run overwrote an earlier
completed diagnosis and posted its restatement to the linked Linear issue as a
finished result.

These tests pin the gate on the real bridge entry point, against a real
``TraceErrorGroup`` row, for the two cases the maintainer asked to see:

  - an incomplete run (forced fallback) never writes the row and never notifies,
    even when a good diagnosis is already stored;
  - a completed run that merely came out low-confidence still writes and
    notifies — confidence is not the gate, completion is.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from ee.agenthub.cluster_rca import falcon_bridge
from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent
from ee.agenthub.cluster_rca.constants import Confidence
from ee.agenthub.cluster_rca.types import ClusterAnalysisResult, ClusterSynthesis
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup


def _bare_agent() -> ClusterAnalysisAgent:
    """An agent wired past ``__init__`` (DB lookup + gateway client) with the
    loop state a run reads. Mirrors ``test_round1_regressions._bare_agent``."""
    agent = ClusterAnalysisAgent.__new__(ClusterAnalysisAgent)
    agent.project_id = "proj-1"
    agent.cluster_uuid = "cluster-uuid-1"
    agent.cluster_label = "E-CLUSTER1"
    agent.cluster_id = "E-CLUSTER1"
    agent._alias_to_uuid = {}
    agent._uuid_to_alias = {}
    agent._cluster_session_uuids_cache = {}
    agent._cluster_trace_uuids_cache = {}
    agent.findings = []
    agent.synthesis = None
    agent._investigation_complete = False
    agent.on_event = lambda *a, **k: None
    return agent


GOOD = ClusterSynthesis(
    synthesis="Auth retries exhaust the shared pool; the 30s client timeout "
    "fires before the second attempt can acquire a connection.",
    fix="Raise the pool size above peak concurrency, or drop the client "
    "timeout below the retry backoff.",
    confidence=Confidence.HIGH,
    evidence_trace_ids=["7f4d2a18-0000-4000-8000-000000000001"],
)

# What _fallback_synthesis produces when a run dies mid-investigation.
SALVAGED = ClusterSynthesis(
    synthesis="A recurring failure pattern was observed across the cluster. The "
    "investigation reached its turn budget before a single root cause was "
    "confirmed; this is the strongest pattern observed across the cluster.",
    fix="Inspect the cited traces directly to confirm the pattern before acting.",
    confidence=Confidence.LOW,
)


@pytest.fixture
def cluster(db):
    """One cluster row in a real tenant, carrying an earlier good diagnosis."""
    user = User.objects.create_user(
        email=f"rca-gate-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="x",
        name="Rca Gate",
    )
    org = Organization.objects.create(name=f"Rca Gate Org {uuid.uuid4().hex[:6]}")
    workspace = Workspace.objects.create(
        name="Rca Gate WS",
        organization=org,
        created_by=user,
        is_default=True,
        is_active=True,
    )
    project = Project.objects.create(
        name="Rca Gate Project",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    now = timezone.now()
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=f"E-{uuid.uuid4().hex[:8]}",
        error_type="auth-timeout",
        source=ClusterSource.SCANNER,
        title="auth timeout under load",
        first_seen=now - timedelta(hours=6),
        last_seen=now,
        error_count=12,
        unique_traces=9,
        # The completed diagnosis this cluster already earned.
        rca_synthesis=GOOD.synthesis,
        rca_fix=GOOD.fix,
        rca_confidence="H",
        rca_evidence_trace_ids=GOOD.evidence_trace_ids,
        rca_at=now - timedelta(hours=1),
        rca_failures_at_run=10,
    )


def _result(synthesis, *, terminated_reason, investigation_complete):
    return ClusterAnalysisResult(
        cluster_id="E-test",
        synthesis=synthesis,
        terminated_reason=terminated_reason,
        cost_usd=0.02,
        investigation_complete=investigation_complete,
    )


class TestIncompleteRunIsNotADiagnosis:
    """The forced fallback after an incomplete run must not be persisted."""

    @pytest.mark.parametrize(
        "terminated_reason",
        ["synthesis_forced", "max_turns", "cost_ceiling", "error", "converged"],
    )
    def test_forced_fallback_does_not_touch_the_stored_diagnosis(
        self, cluster, terminated_reason
    ):
        result = _result(
            SALVAGED, terminated_reason=terminated_reason, investigation_complete=False
        )

        persisted = falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id), result, error_count_at_run=12, trace=[{"type": "x"}]
        )

        assert persisted is False
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == GOOD.synthesis
        assert cluster.rca_fix == GOOD.fix
        assert cluster.rca_confidence == "H"

    def test_forced_fallback_is_not_posted_to_the_linked_issue(
        self, cluster, monkeypatch
    ):
        cluster.external_issue_id = "TH-3102"
        cluster.save(update_fields=["external_issue_id"])

        posted = []
        monkeypatch.setattr(
            "tracer.utils.feed.post_rca_comment_to_linked_issue",
            lambda cluster_pk: posted.append(cluster_pk) or True,
        )

        result = _result(
            SALVAGED, terminated_reason="synthesis_forced", investigation_complete=False
        )
        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id), result, error_count_at_run=12, trace=None
        )

        assert posted == [], "a degraded fallback reached the linked ticket"

    def test_first_run_on_an_unanalysed_cluster_stays_unanalysed(self, db):
        """No prior diagnosis: a failed first run must not land a fake one."""
        _, _, cluster = _fresh_cluster_without_diagnosis(db)

        result = _result(
            SALVAGED, terminated_reason="max_turns", investigation_complete=False
        )
        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id), result, error_count_at_run=3, trace=None
        )

        cluster.refresh_from_db()
        assert cluster.rca_synthesis is None
        assert cluster.rca_at is None


class TestCompletedLowConfidenceRunStillPublishes:
    """Low confidence is not the gate — a finished run persists as it always did."""

    def test_low_confidence_completed_case_replaces_the_stored_diagnosis(self, cluster):
        completed_low = ClusterSynthesis(
            synthesis="Two of the sampled traces time out on auth, but the third "
            "returns a provider 429, so the cluster may mix two causes.",
            fix="Split the cluster before concluding, then re-run the analysis.",
            confidence=Confidence.LOW,
            evidence_trace_ids=["7f4d2a18-0000-4000-8000-000000000002"],
        )
        result = _result(
            completed_low,
            terminated_reason="synthesis_submitted",
            investigation_complete=True,
        )

        persisted = falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id), result, error_count_at_run=12, trace=[{"type": "x"}]
        )

        assert persisted is True
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == completed_low.synthesis
        assert cluster.rca_confidence == "L"
        assert cluster.rca_failures_at_run == 12

    def test_low_confidence_completed_case_notifies_the_linked_issue(
        self, cluster, monkeypatch
    ):
        cluster.external_issue_id = "TH-3102"
        cluster.save(update_fields=["external_issue_id"])

        posted = []
        monkeypatch.setattr(
            "tracer.utils.feed.post_rca_comment_to_linked_issue",
            lambda cluster_pk: posted.append(cluster_pk) or True,
        )

        completed_low = ClusterSynthesis(
            synthesis="Cause is only partly isolated.",
            fix="Narrow the cluster first.",
            confidence=Confidence.LOW,
        )
        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(
                completed_low,
                terminated_reason="synthesis_submitted",
                investigation_complete=True,
            ),
            error_count_at_run=12,
            trace=None,
        )

        assert posted == [str(cluster.id)]


class TestNoSynthesisIsANoop:
    def test_none_synthesis_never_persists(self, cluster):
        result = _result(
            None, terminated_reason="stopped", investigation_complete=False
        )
        assert (
            falcon_bridge.persist_result_if_diagnosis(
                str(cluster.id), result, error_count_at_run=12, trace=None
            )
            is False
        )
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == GOOD.synthesis


class TestResultFlagsTheRun:
    """The bridge gate reads this field, so the agent must set it honestly.

    ``_fallback_synthesis`` sets ``_investigation_complete`` for its own reasons
    (the loop must stop looping), so that flag cannot be the gate — completion
    has to come from the run's own termination path.
    """

    @staticmethod
    def _loop_agent():
        agent = _bare_agent()
        agent.max_turns = 1
        agent.question = None
        agent.total_cost_usd = 0.0
        agent.model = "test-model"
        agent.temperature = 0.0
        agent.max_tokens = 256
        agent.thinking_budget = None
        agent._gateway_client = object()
        agent._stop_event = None
        agent._converged_at_turn = None
        agent._dominant_dims = {}
        agent._call_history = {}
        agent._span_index = {}
        agent._trace_summary_cache = {}
        agent._trace_spans_cache = {}
        agent._finding_counter = 0
        agent._initial_messages = lambda: []
        return agent

    @staticmethod
    def _reply(tool_calls=None, finish="tool_calls"):
        message = SimpleNamespace(
            content=None,
            tool_calls=tool_calls,
            model_dump=lambda: {"role": "assistant", "content": None},
        )
        return SimpleNamespace(
            response=SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason=finish)]
            ),
            cost_usd=0.0,
        )

    def test_model_submitted_run_is_complete(self):
        agent = self._loop_agent()
        submitted = []

        def _execute_tool(name, args):
            submitted.append(name)
            return agent._tool_submit_synthesis(**args)

        agent._execute_tool = _execute_tool
        call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="submit_synthesis",
                arguments=(
                    '{"synthesis": "Auth pool exhaustion causes the timeout.", '
                    '"fix": "Enlarge the pool.", "confidence": "M"}'
                ),
            ),
        )
        with patch(
            "ee.agenthub.cluster_rca.agent.call_llm_raw",
            return_value=self._reply(tool_calls=[call]),
        ):
            result = agent.run()

        assert submitted == ["submit_synthesis"]
        assert result.terminated_reason == "synthesis_submitted"
        assert result.investigation_complete is True

    def test_forced_fallback_is_not_complete(self):
        agent = self._loop_agent()
        agent._stop_event = None
        agent._force_synthesis = lambda messages, turn: agent._fallback_synthesis()

        with patch(
            "ee.agenthub.cluster_rca.agent.call_llm_raw",
            return_value=self._reply(finish="stop"),
        ):
            result = agent.run()

        assert result.synthesis is not None, "the stream still needs an answer"
        assert result.terminated_reason == "synthesis_forced"
        assert result.investigation_complete is False

    def test_stopped_run_is_not_complete(self):
        """An explicit Stop never reaches the backstop and is not a diagnosis."""
        agent = self._loop_agent()
        agent._stop_event = _AlreadySet()

        result = agent.run()

        assert result.synthesis is None
        assert result.terminated_reason == "stopped"
        assert result.investigation_complete is False


class _AlreadySet:
    """A stop signal the run sees set on its first check."""

    def is_set(self) -> bool:
        return True


def _fresh_cluster_without_diagnosis(db):
    user = User.objects.create_user(
        email=f"rca-blank-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="x",
        name="Rca Blank",
    )
    org = Organization.objects.create(name=f"Rca Blank Org {uuid.uuid4().hex[:6]}")
    workspace = Workspace.objects.create(
        name="Rca Blank WS",
        organization=org,
        created_by=user,
        is_default=True,
        is_active=True,
    )
    project = Project.objects.create(
        name="Rca Blank Project",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    now = timezone.now()
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id=f"E-{uuid.uuid4().hex[:8]}",
        error_type="blank-cluster",
        source=ClusterSource.SCANNER,
        title="never analysed",
        first_seen=now,
        last_seen=now,
        error_count=3,
        unique_traces=3,
    )
    return user, project, cluster
