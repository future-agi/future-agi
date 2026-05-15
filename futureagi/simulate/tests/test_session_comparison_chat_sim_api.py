"""
API + view-level unit tests for session comparison chat simulation endpoint.

Endpoint:
GET /simulate/call-executions/<uuid:call_execution_id>/session-comparison/
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

import simulate.models.test_execution as test_execution_models
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset, Row
from simulate.models import AgentDefinition, CallExecution, Scenarios
from simulate.models.chat_message import ChatMessageModel
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.views.session_comparison_chat_sim import SessionComparisonChatSimView
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

# ============================================================================
# Fixtures (minimal graph to satisfy view + session_comparison utils)
# ============================================================================


@pytest.fixture
def agent_definition_text(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Session Comparison Chat Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+1234567890",
        inbound=True,
        description="Agent for session comparison tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Session Comparison Simulator",
        prompt="You are a simulator agent.",
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4o-mini",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset(db, organization, user, workspace):
    return Dataset.no_workspace_objects.create(
        name="Session Comparison Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )


@pytest.fixture
def scenario(
    db, organization, workspace, dataset, agent_definition_text, simulator_agent
):
    return Scenarios.objects.create(
        name="Session Comparison Scenario",
        description="Scenario for session comparison",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition_text,
        simulator_agent=simulator_agent,
    )


@pytest.fixture
def run_test(
    db, organization, workspace, agent_definition_text, simulator_agent, scenario
):
    run_test = RunTest.objects.create(
        name="Session Comparison Run",
        description="Run for session comparison tests",
        agent_definition=agent_definition_text,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def test_execution(db, organization, workspace, run_test, agent_definition_text):
    return test_execution_models.TestExecution.objects.create(
        run_test=run_test,
        status=test_execution_models.TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition_text,
    )


@pytest.fixture
def tracer_project(db, organization, workspace, user):
    return Project.objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        name=f"Session Comparison Project {uuid.uuid4()}",
        trace_type="observe",
    )


@pytest.fixture
def trace_session(db, tracer_project):
    return TraceSession.objects.create(project=tracer_project)


@pytest.fixture
def base_trace(db, tracer_project, trace_session):
    return Trace.objects.create(
        project=tracer_project,
        session=trace_session,
        input="hello",
        output="hi there",
        metadata={},
        name="test-trace",
    )


@pytest.fixture
def observation_spans(db, tracer_project, base_trace):
    start = timezone.now() - timedelta(seconds=10)
    end = timezone.now()

    # At least one span is required to compute start/end, tokens, and tools_count.
    ObservationSpan.objects.create(
        id=f"span-{uuid.uuid4()}",
        project=tracer_project,
        trace=base_trace,
        name="conversation",
        observation_type="conversation",
        start_time=start,
        end_time=end,
        total_tokens=100,
    )
    ObservationSpan.objects.create(
        id=f"span-{uuid.uuid4()}",
        project=tracer_project,
        trace=base_trace,
        name="tool-call",
        observation_type="tool",
        start_time=start,
        end_time=end,
        total_tokens=0,
    )


@pytest.fixture
def dataset_row(db, dataset, trace_session):
    # View expects Row.metadata["session_id"].
    return Row.objects.create(
        dataset=dataset,
        order=0,
        metadata={"session_id": str(trace_session.id)},
    )


@pytest.fixture
def completed_text_call_execution(
    db,
    test_execution,
    scenario,
    organization,
    workspace,
    dataset_row,
):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={},
        row_id=dataset_row.id,
        conversation_metrics_data={
            "avg_latency_ms": 5000,
            "output_tokens": 50,
            "turn_count": 1,
        },
    )


@pytest.fixture
def assistant_chat_message(db, completed_text_call_execution, organization, workspace):
    # Adds tool_calls in content to exercise tools_count calculation.
    return ChatMessageModel.objects.create(
        call_execution=completed_text_call_execution,
        role=ChatMessageModel.RoleChoices.ASSISTANT,
        messages=["hi there"],
        content=[
            {
                "role": "assistant",
                "content": "hi there",
                "tool_calls": [{"id": "tool_1"}, {"id": "tool_2"}],
            }
        ],
        session_id="vapi-session-123",
        organization=organization,
        workspace=workspace,
        tool_calls=[{"id": "tool_1"}, {"id": "tool_2"}],
    )


# ============================================================================
# Integration tests (DB + endpoint wiring)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestSessionComparisonChatSimAPI:
    def test_success(
        self,
        auth_client,
        completed_text_call_execution,
        dataset_row,
        base_trace,
        observation_spans,
        assistant_chat_message,
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{completed_text_call_execution.id}/session-comparison/"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        assert "comparison_metrics" in data["result"]
        assert "comparison_transcripts" in data["result"]

        metrics = data["result"]["comparison_metrics"]
        assert isinstance(metrics, list)
        # Should include some metrics (duration/turn_count/tokens/tools_count)
        assert any(m["metric"] == "duration" for m in metrics)

        transcripts = data["result"]["comparison_transcripts"]
        assert "base_session_transcripts" in transcripts
        assert "comparison_call_transcripts" in transcripts
        assert isinstance(transcripts["base_session_transcripts"], list)
        assert isinstance(transcripts["comparison_call_transcripts"], list)

    def test_unauthenticated(self, api_client, completed_text_call_execution):
        response = api_client.get(
            f"/simulate/call-executions/{completed_text_call_execution.id}/session-comparison/"
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_bad_request_when_voice_execution_has_no_replay_baseline(
        self, auth_client, completed_text_call_execution
    ):
        completed_text_call_execution.simulation_call_type = (
            CallExecution.SimulationCallType.VOICE
        )
        completed_text_call_execution.save()

        response = auth_client.get(
            f"/simulate/call-executions/{completed_text_call_execution.id}/session-comparison/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["result"] == "Comparison is only available for replay sessions"

    def test_bad_request_when_call_execution_not_completed(
        self, auth_client, completed_text_call_execution
    ):
        completed_text_call_execution.status = CallExecution.CallStatus.ONGOING
        completed_text_call_execution.save()

        response = auth_client.get(
            f"/simulate/call-executions/{completed_text_call_execution.id}/session-comparison/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["result"] == "Call execution is not completed yet"

    def test_bad_request_when_row_id_missing(
        self, auth_client, completed_text_call_execution
    ):
        completed_text_call_execution.row_id = None
        completed_text_call_execution.save()

        response = auth_client.get(
            f"/simulate/call-executions/{completed_text_call_execution.id}/session-comparison/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["result"] == "Row ID is not associated which is required"

    def test_bad_request_when_session_id_missing(
        self, auth_client, completed_text_call_execution, dataset_row
    ):
        dataset_row.metadata = {}
        dataset_row.save()

        response = auth_client.get(
            f"/simulate/call-executions/{completed_text_call_execution.id}/session-comparison/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["result"] == "No session ID found for comparison"


# ============================================================================
# View-level unit tests (mock dependencies, focus on branching)
# ============================================================================


@pytest.mark.unit
class TestSessionComparisonChatSimViewUnit:
    def test_get_success_calls_comparison_helpers(self, user):
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        row_id = uuid.uuid4()

        fake_call_exec = SimpleNamespace(
            id=call_exec_id,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            status=CallExecution.CallStatus.COMPLETED,
            row_id=row_id,
        )
        fake_row = SimpleNamespace(metadata={"session_id": "session-123"})

        with (
            patch(
                "simulate.views.session_comparison_chat_sim.get_object_or_404"
            ) as mock_get_obj,
            patch(
                "simulate.views.session_comparison_chat_sim.fetch_comparison_metrics"
            ) as mock_metrics,
            patch(
                "simulate.views.session_comparison_chat_sim.fetch_comparison_transcripts"
            ) as mock_transcripts,
        ):
            mock_get_obj.side_effect = [fake_call_exec, fake_row]
            mock_metrics.return_value = [{"metric": "duration", "value": 1}]
            mock_transcripts.return_value = {
                "base_session_transcripts": [],
                "comparison_call_transcripts": [],
            }

            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["status"] is True
        assert "comparison_metrics" in data["result"]
        assert "comparison_transcripts" in data["result"]
        mock_metrics.assert_called_once_with(fake_call_exec, "session-123")
        mock_transcripts.assert_called_once_with(fake_call_exec, "session-123")

    def test_get_voice_execution_returns_recordings(self, user):
        """Voice call executions are supported and include comparison_recordings."""
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        row_id = uuid.uuid4()
        fake_call_exec = SimpleNamespace(
            id=call_exec_id,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            status=CallExecution.CallStatus.COMPLETED,
            row_id=row_id,
        )
        fake_row = SimpleNamespace(metadata={"trace_id": "trace-456"})

        with (
            patch(
                "simulate.views.session_comparison_chat_sim.get_object_or_404"
            ) as mock_get_obj,
            patch(
                "simulate.views.session_comparison_chat_sim.fetch_voice_trace_comparison_metrics"
            ) as mock_metrics,
            patch(
                "simulate.views.session_comparison_chat_sim.fetch_voice_trace_comparison_transcripts"
            ) as mock_transcripts,
            patch(
                "simulate.views.session_comparison_chat_sim.fetch_comparison_recordings"
            ) as mock_recordings,
            patch(
                "simulate.views.session_comparison_chat_sim.fetch_voice_conversation_span"
            ) as mock_span,
        ):
            mock_get_obj.side_effect = [fake_call_exec, fake_row]
            mock_span.return_value = object()
            mock_metrics.return_value = [{"metric": "duration", "value": 3}]
            mock_transcripts.return_value = {
                "base_session_transcripts": [],
                "comparison_call_transcripts": [],
            }
            mock_recordings.return_value = {
                "baseline": {"stereo": "https://example.com/baseline.wav"},
                "simulated": {"stereo": "https://example.com/simulated.wav"},
            }

            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["status"] is True
        assert "comparison_metrics" in data["result"]
        assert "comparison_transcripts" in data["result"]
        assert "comparison_recordings" in data["result"]
        mock_span.assert_called_once_with("trace-456")
        span = mock_span.return_value
        mock_metrics.assert_called_once_with(fake_call_exec, "trace-456", _span=span)
        mock_transcripts.assert_called_once_with(
            fake_call_exec, "trace-456", _span=span
        )
        mock_recordings.assert_called_once_with(fake_call_exec, "trace-456", _span=span)

    def test_get_rejects_not_completed_execution(self, user):
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        fake_call_exec = SimpleNamespace(
            id=call_exec_id,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            status=CallExecution.CallStatus.ONGOING,
            row_id=uuid.uuid4(),
        )

        with patch(
            "simulate.views.session_comparison_chat_sim.get_object_or_404"
        ) as mock_get_obj:
            mock_get_obj.return_value = fake_call_exec
            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["result"] == "Call execution is not completed yet"

    def test_get_rejects_missing_row_id(self, user):
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        fake_call_exec = SimpleNamespace(
            id=call_exec_id,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            status=CallExecution.CallStatus.COMPLETED,
            row_id=None,
        )

        with patch(
            "simulate.views.session_comparison_chat_sim.get_object_or_404"
        ) as mock_get_obj:
            mock_get_obj.return_value = fake_call_exec
            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["result"] == "Row ID is not associated which is required"

    def test_get_rejects_missing_session_id_for_text(self, user):
        """Text calls without session_id in metadata return 400."""
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        fake_call_exec = SimpleNamespace(
            id=call_exec_id,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            status=CallExecution.CallStatus.COMPLETED,
            row_id=uuid.uuid4(),
        )
        fake_row = SimpleNamespace(metadata={})

        with patch(
            "simulate.views.session_comparison_chat_sim.get_object_or_404"
        ) as mock_get_obj:
            mock_get_obj.side_effect = [fake_call_exec, fake_row]
            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["result"] == "No session ID found for comparison"

    def test_get_rejects_non_replay_voice_execution(self, user):
        """Voice calls without a replay baseline return 400."""
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        fake_call_exec = SimpleNamespace(
            id=call_exec_id,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            status=CallExecution.CallStatus.COMPLETED,
            row_id=uuid.uuid4(),
            test_execution=None,
        )
        fake_row = SimpleNamespace(metadata={"intent_id": None})

        with patch(
            "simulate.views.session_comparison_chat_sim.get_object_or_404"
        ) as mock_get_obj:
            mock_get_obj.side_effect = [fake_call_exec, fake_row]
            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert (
            response.data["result"]
            == "Comparison is only available for replay sessions"
        )

    def test_get_returns_500_on_unexpected_exception(self, user):
        factory = APIRequestFactory()
        request = factory.get("/simulate/call-executions/x/session-comparison/")
        force_authenticate(request, user=user)

        call_exec_id = uuid.uuid4()
        with patch(
            "simulate.views.session_comparison_chat_sim.get_object_or_404",
            side_effect=Exception("boom"),
        ):
            response = SessionComparisonChatSimView.as_view()(
                request, call_execution_id=call_exec_id
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to compare session chat simulations" in response.data["result"]
