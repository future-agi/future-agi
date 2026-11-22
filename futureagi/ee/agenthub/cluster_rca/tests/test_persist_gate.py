"""The deterministic last-resort text must never become a stored diagnosis.

The cluster-RCA agent always ends with a synthesis so the Analyse tab is never
dead-air. Two very different texts can be the last one:

  - a synthesis the *model* wrote — normally through ``submit_synthesis``, or
    through ``_force_synthesis``'s forced call after a budget or cost exit.
    It carries the model's own text, confidence and evidence: that is a
    conclusion, and a diagnosis even when the run was pushed to close.
  - ``_fallback_synthesis``'s deterministic text, written when the model
    produced nothing at all. It restates the gathered evidence without
    concluding it.

The bridge used to persist whatever was non-null, so a run that never concluded
overwrote an earlier completed diagnosis and posted its restatement to the
linked Linear issue as a finished result. These tests pin that only the second
kind is refused, against a real ``TraceErrorGroup`` row, and drive the real
persist step the worker thread calls — not just the gate helper.
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

GOOD = ClusterSynthesis(
    synthesis="Auth retries exhaust the shared pool; the 30s client timeout "
    "fires before the second attempt can acquire a connection.",
    fix="Raise the pool size above peak concurrency, or drop the client "
    "timeout below the retry backoff.",
    confidence=Confidence.HIGH,
    evidence_trace_ids=["7f4d2a18-0000-4000-8000-000000000001"],
)

# What _fallback_synthesis produces when the model concluded nothing at all.
SALVAGED = ClusterSynthesis(
    synthesis="A recurring failure pattern was observed across the cluster. The "
    "investigation reached its turn budget before a single root cause was "
    "confirmed; this is the strongest pattern observed across the cluster.",
    fix="Inspect the cited traces directly to confirm the pattern before acting.",
    confidence=Confidence.LOW,
)

# What _force_synthesis yields when the model *does* answer the forced call:
# its own text and its own confidence, which the prompt never forces to LOW.
FORCED_BY_MODEL = ClusterSynthesis(
    synthesis="Auth retries exhaust the shared pool; the second attempt cannot "
    "acquire a connection before the client timeout fires.",
    fix="Raise the pool size above peak concurrency.",
    confidence=Confidence.MEDIUM,
    evidence_trace_ids=["7f4d2a18-0000-4000-8000-000000000003"],
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
        rca_trace=[{"type": "synthesis", "synthesis": GOOD.synthesis}],
    )


def _result(synthesis, *, terminated_reason, last_resort, complete=False):
    return ClusterAnalysisResult(
        cluster_id="E-test",
        synthesis=synthesis,
        terminated_reason=terminated_reason,
        cost_usd=0.02,
        investigation_complete=complete,
        synthesis_is_last_resort=last_resort,
    )


class TestLastResortTextIsNotADiagnosis:
    """The deterministic fallback must not be stored, whatever the exit reason."""

    @pytest.mark.parametrize(
        "terminated_reason",
        # Every reason an incomplete run can actually carry once a synthesis
        # exists: a resource exit with the model-authored forced attempt
        # succeeding leaves "synthesis_forced"; an exception leaves "error".
        ["synthesis_forced", "error"],
    )
    def test_last_resort_leaves_every_stored_column_alone(
        self, cluster, terminated_reason
    ):
        before = {
            "rca_synthesis": cluster.rca_synthesis,
            "rca_fix": cluster.rca_fix,
            "rca_confidence": cluster.rca_confidence,
            "rca_at": cluster.rca_at,
            "rca_failures_at_run": cluster.rca_failures_at_run,
            "rca_trace": cluster.rca_trace,
        }

        persisted = falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(SALVAGED, terminated_reason=terminated_reason, last_resort=True),
            error_count_at_run=12,
            trace=[{"type": "x"}],
        )

        assert persisted is False
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == before["rca_synthesis"]
        assert cluster.rca_fix == before["rca_fix"]
        assert cluster.rca_confidence == before["rca_confidence"]
        assert cluster.rca_at == before["rca_at"], "the staleness signal was reset"
        assert cluster.rca_failures_at_run == before["rca_failures_at_run"]
        assert cluster.rca_trace == before["rca_trace"], "the replay trail was replaced"

    def test_last_resort_is_not_posted_to_the_linked_issue(self, cluster, monkeypatch):
        cluster.external_issue_id = "TH-3102"
        cluster.save(update_fields=["external_issue_id"])

        posted = []
        monkeypatch.setattr(
            "tracer.utils.feed.post_rca_comment_to_linked_issue",
            lambda cluster_pk: posted.append(cluster_pk) or True,
        )

        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(SALVAGED, terminated_reason="synthesis_forced", last_resort=True),
            error_count_at_run=12,
            trace=None,
        )

        assert posted == [], "a degraded fallback reached the linked ticket"

    def test_first_run_on_an_unanalysed_cluster_stays_unanalysed(self, db):
        """No prior diagnosis: a last-resort-only run must not land a fake one."""
        cluster = _fresh_cluster_without_diagnosis(db)

        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(SALVAGED, terminated_reason="synthesis_forced", last_resort=True),
            error_count_at_run=3,
            trace=None,
        )

        cluster.refresh_from_db()
        assert cluster.rca_synthesis is None
        assert cluster.rca_at is None


class TestModelAuthoredConclusionsStillPublish:
    """A synthesis the model wrote is a diagnosis — even after a budget exit."""

    def test_forced_but_model_authored_synthesis_is_stored(self, cluster):
        persisted = falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(
                FORCED_BY_MODEL,
                terminated_reason="synthesis_forced",
                last_resort=False,
            ),
            error_count_at_run=12,
            trace=[{"type": "x"}],
        )

        assert persisted is True
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == FORCED_BY_MODEL.synthesis
        assert cluster.rca_confidence == "M"
        assert cluster.rca_failures_at_run == 12
        assert cluster.rca_trace == [{"type": "x"}]

    def test_forced_but_model_authored_synthesis_notifies_the_linked_issue(
        self, cluster, monkeypatch
    ):
        cluster.external_issue_id = "TH-3102"
        cluster.save(update_fields=["external_issue_id"])

        posted = []
        monkeypatch.setattr(
            "tracer.utils.feed.post_rca_comment_to_linked_issue",
            lambda cluster_pk: posted.append(cluster_pk) or True,
        )

        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(
                FORCED_BY_MODEL,
                terminated_reason="synthesis_forced",
                last_resort=False,
            ),
            error_count_at_run=12,
            trace=None,
        )

        assert posted == [str(cluster.id)]

    def test_completed_low_confidence_run_still_persists(self, cluster):
        """Confidence is not the gate — the maintainer's stated carve-out."""
        low = ClusterSynthesis(
            synthesis="Two of the sampled traces time out on auth, but the third "
            "returns a provider 429, so the cluster may mix two causes.",
            fix="Split the cluster before concluding, then re-run the analysis.",
            confidence=Confidence.LOW,
            evidence_trace_ids=["7f4d2a18-0000-4000-8000-000000000002"],
        )

        persisted = falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(
                low,
                terminated_reason="synthesis_submitted",
                last_resort=False,
                complete=True,
            ),
            error_count_at_run=12,
            trace=[{"type": "x"}],
        )

        assert persisted is True
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == low.synthesis
        assert cluster.rca_confidence == "L"


class TestNoSynthesisIsANoop:
    def test_none_synthesis_never_persists(self, cluster):
        falcon_bridge.persist_result_if_diagnosis(
            str(cluster.id),
            _result(None, terminated_reason="stopped", last_resort=False),
            error_count_at_run=12,
            trace=None,
        )
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == GOOD.synthesis


class TestPersistRunResultIsTheCallSite:
    """The worker thread calls ``persist_run_result``; drive that, not the gate.

    Reverting ``run_agent``'s call site back to ``_persist_synthesis`` is the
    exact production bug. A test that only calls ``persist_result_if_diagnosis``
    cannot see it, so these go through the function ``run_agent`` actually calls.
    """

    def test_last_resort_run_is_not_persisted_through_the_call_site(self, cluster):
        persisted = falcon_bridge.persist_run_result(
            str(cluster.id),
            _result(SALVAGED, terminated_reason="synthesis_forced", last_resort=True),
            12,
            [{"type": "x"}],
        )
        assert persisted is False
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == GOOD.synthesis

    def test_authored_run_is_persisted_through_the_call_site(self, cluster):
        persisted = falcon_bridge.persist_run_result(
            str(cluster.id),
            _result(
                FORCED_BY_MODEL,
                terminated_reason="synthesis_forced",
                last_resort=False,
            ),
            12,
            [{"type": "x"}],
        )
        assert persisted is True
        cluster.refresh_from_db()
        assert cluster.rca_synthesis == FORCED_BY_MODEL.synthesis


class TestAgentFlagsTheTextItProduced:
    """The gate reads the flags, so the agent must set them honestly."""

    @staticmethod
    def _loop_agent():
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
        agent._synthesis_is_last_resort = False
        agent.on_event = lambda *a, **k: None
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

    def test_model_submitted_run_is_complete_and_not_last_resort(self):
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
        assert result.synthesis_is_last_resort is False

    def test_deterministic_fallback_is_last_resort(self):
        agent = self._loop_agent()
        agent._force_synthesis = lambda messages, turn: agent._fallback_synthesis()

        with patch(
            "ee.agenthub.cluster_rca.agent.call_llm_raw",
            return_value=self._reply(finish="stop"),
        ):
            result = agent.run()

        assert result.synthesis is not None, "the stream still needs an answer"
        assert result.terminated_reason == "synthesis_forced"
        assert result.synthesis_is_last_resort is True, (
            "the deterministic text is the thing the gate must refuse"
        )
        assert result.investigation_complete is False

    def test_model_authored_forced_synthesis_is_not_last_resort(self):
        """The model answering the forced call is a conclusion, not a fallback."""
        agent = self._loop_agent()
        agent._execute_tool = lambda name, args: agent._tool_submit_synthesis(**args)
        forced_call = SimpleNamespace(
            id="call-forced",
            function=SimpleNamespace(
                name="submit_synthesis",
                arguments=(
                    '{"synthesis": "Auth pool exhaustion causes the timeout.", '
                    '"fix": "Enlarge the pool.", "confidence": "M"}'
                ),
            ),
        )
        replies = [
            self._reply(finish="stop"),
            self._reply(tool_calls=[forced_call]),
        ]

        def _fake(_client, **kwargs):
            return replies.pop(0)

        with patch("ee.agenthub.cluster_rca.agent.call_llm_raw", side_effect=_fake):
            result = agent.run()

        assert result.synthesis is not None
        assert result.synthesis.confidence == Confidence.MEDIUM
        assert result.synthesis_is_last_resort is False, (
            "a model-authored synthesis must persist even after a budget exit"
        )

    def test_stopped_run_carries_no_synthesis(self):
        agent = self._loop_agent()
        agent._stop_event = _AlreadySet()

        result = agent.run()

        assert result.synthesis is None
        assert result.terminated_reason == "stopped"
        assert result.synthesis_is_last_resort is False


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
    return TraceErrorGroup.objects.create(
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
