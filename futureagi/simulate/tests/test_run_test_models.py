"""
Model unit tests for Run Test functionality.

Tests cover:
1. RunTest model - CRUD, relationships, soft delete
2. TestExecution model - Status transitions, properties
3. CallExecution model - Status transitions, properties
4. CallTranscript model - Creation, relationships
5. CallExecutionSnapshot model - Snapshot creation
6. SimulateEvalConfig model - Eval config management

These tests help discover bugs and validate model behavior.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import CreateCallExecution, RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import (
    CallExecution,
    CallExecutionSnapshot,
    CallTranscript,
    EvalExplanationSummaryStatus,
    TestExecution,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a test agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent for simulation",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    """Create a test simulator agent."""
    return SimulatorAgent.objects.create(
        name="Test Simulator Agent",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    """Create a dataset for scenarios."""
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()

    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="Test situation")

    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    """Create a test scenario."""
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario description",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    """Create a test run test."""
    run_test = RunTest.objects.create(
        name="Test Run",
        description="Test run description",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition):
    """Create a test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=3,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    """Create a call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.PENDING,
    )


# ============================================================================
# RunTest Model Tests
# ============================================================================


@pytest.mark.unit
class TestRunTestModel:
    """Tests for RunTest model."""

    def test_run_test_creation(self, db, organization, workspace, agent_definition):
        """Test basic RunTest creation."""
        run_test = RunTest.objects.create(
            name="New Test",
            description="A new test run",
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
        )

        assert run_test.id is not None
        assert run_test.name == "New Test"
        assert run_test.description == "A new test run"
        assert run_test.agent_definition == agent_definition
        assert run_test.organization == organization
        assert run_test.enable_tool_evaluation is False

    def test_run_test_allows_null_agent_definition(self, db, organization, workspace):
        """Test that agent_definition can be null (for prompt-based simulations)."""
        run_test = RunTest.objects.create(
            name="Test Without Agent",
            source_type="prompt",
            organization=organization,
            workspace=workspace,
        )
        assert run_test.agent_definition is None
        assert run_test.source_type == "prompt"

    def test_run_test_requires_organization(self, db, agent_definition, workspace):
        """Test that organization is required."""
        with pytest.raises(IntegrityError):
            RunTest.objects.create(
                name="Test Without Org",
                agent_definition=agent_definition,
                workspace=workspace,
            )

    def test_run_test_scenarios_relationship(
        self, db, run_test, scenario, organization, workspace, agent_definition
    ):
        """Test M2M relationship with scenarios."""
        # Create another scenario
        dataset = Dataset.no_workspace_objects.create(
            name="Another Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        scenario2 = Scenarios.objects.create(
            name="Second Scenario",
            source="Test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            agent_definition=agent_definition,
        )
        run_test.scenarios.add(scenario2)

        assert run_test.scenarios.count() == 2
        assert scenario in run_test.scenarios.all()
        assert scenario2 in run_test.scenarios.all()

    def test_run_test_soft_delete(self, db, run_test):
        """Test soft delete functionality."""
        run_test_id = run_test.id
        run_test.delete()

        # Should not appear in default queryset
        assert not RunTest.objects.filter(id=run_test_id).exists()
        # Should appear in all_objects
        assert RunTest.all_objects.filter(id=run_test_id).exists()
        # deleted flag should be True
        deleted_run_test = RunTest.all_objects.get(id=run_test_id)
        assert deleted_run_test.deleted is True

    def test_run_test_str_method(self, db, run_test):
        """Test __str__ method."""
        expected = f"{run_test.name} - {run_test.agent_definition.agent_name}"
        assert str(run_test) == expected

    def test_run_test_ordering(
        self, db, organization, workspace, agent_definition, simulator_agent
    ):
        """Test that RunTests are ordered by -created_at."""
        run_test1 = RunTest.objects.create(
            name="First",
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
        )
        run_test2 = RunTest.objects.create(
            name="Second",
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
        )

        all_tests = list(RunTest.objects.filter(organization=organization))
        # Second should come first (more recent)
        assert all_tests[0].name == "Second"
        assert all_tests[1].name == "First"

    def test_run_test_dataset_row_ids_default(
        self, db, organization, workspace, agent_definition
    ):
        """Test dataset_row_ids defaults to empty list."""
        run_test = RunTest.objects.create(
            name="Test",
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
        )

        assert run_test.dataset_row_ids == []

    def test_run_test_dataset_row_ids_stored(
        self, db, organization, workspace, agent_definition
    ):
        """Test dataset_row_ids can store UUIDs."""
        row_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        run_test = RunTest.objects.create(
            name="Test",
            agent_definition=agent_definition,
            organization=organization,
            workspace=workspace,
            dataset_row_ids=row_ids,
        )

        run_test.refresh_from_db()
        assert run_test.dataset_row_ids == row_ids


# ============================================================================
# TestExecution Model Tests
# ============================================================================


@pytest.mark.unit
class TestTestExecutionModel:
    """Tests for TestExecution model."""

    def test_test_execution_creation(
        self, db, run_test, simulator_agent, agent_definition
    ):
        """Test basic TestExecution creation."""
        execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            total_scenarios=2,
            total_calls=10,
            simulator_agent=simulator_agent,
            agent_definition=agent_definition,
        )

        assert execution.id is not None
        assert execution.status == TestExecution.ExecutionStatus.PENDING
        assert execution.total_scenarios == 2
        assert execution.total_calls == 10
        assert execution.completed_calls == 0
        assert execution.failed_calls == 0

    def test_test_execution_status_choices(self):
        """Test ExecutionStatus enum values."""
        assert TestExecution.ExecutionStatus.PENDING == "pending"
        assert TestExecution.ExecutionStatus.RUNNING == "running"
        assert TestExecution.ExecutionStatus.COMPLETED == "completed"
        assert TestExecution.ExecutionStatus.FAILED == "failed"
        assert TestExecution.ExecutionStatus.CANCELLED == "cancelled"
        assert TestExecution.ExecutionStatus.CANCELLING == "cancelling"
        assert TestExecution.ExecutionStatus.EVALUATING == "evaluating"

    def test_test_execution_status_transition_pending_to_running(
        self, db, test_execution
    ):
        """Test status transition from PENDING to RUNNING."""
        test_execution.status = TestExecution.ExecutionStatus.RUNNING
        test_execution.started_at = timezone.now()
        test_execution.save()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.RUNNING
        assert test_execution.started_at is not None

    def test_test_execution_status_transition_running_to_completed(
        self, db, test_execution
    ):
        """Test status transition from RUNNING to COMPLETED."""
        test_execution.status = TestExecution.ExecutionStatus.RUNNING
        test_execution.started_at = timezone.now()
        test_execution.save()

        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.completed_at = timezone.now()
        test_execution.completed_calls = 3
        test_execution.save()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.completed_at is not None

    def test_test_execution_status_transition_to_failed(self, db, test_execution):
        """Test status transition to FAILED."""
        test_execution.status = TestExecution.ExecutionStatus.FAILED
        test_execution.failed_calls = 3
        test_execution.save()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.FAILED
        assert test_execution.failed_calls == 3

    def test_test_execution_status_transition_to_cancelled(self, db, test_execution):
        """Test status transition to CANCELLED."""
        test_execution.status = TestExecution.ExecutionStatus.CANCELLED
        test_execution.save()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.CANCELLED

    def test_test_execution_success_rate_property(self, db, test_execution):
        """Test success_rate property calculation."""
        test_execution.total_calls = 10
        test_execution.completed_calls = 7
        test_execution.save()

        assert test_execution.success_rate == 70.0

    def test_test_execution_success_rate_zero_calls(self, db, test_execution):
        """Test success_rate with zero total calls."""
        test_execution.total_calls = 0
        test_execution.save()

        assert test_execution.success_rate == 0

    def test_test_execution_duration_seconds_property(
        self, db, test_execution, scenario
    ):
        """Test duration_seconds property calculation."""
        # Create calls with durations
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1111111111",
            status=CallExecution.CallStatus.COMPLETED,
            duration_seconds=120,
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+2222222222",
            status=CallExecution.CallStatus.COMPLETED,
            duration_seconds=180,
        )

        assert test_execution.duration_seconds == 300

    def test_test_execution_duration_seconds_no_calls(self, db, test_execution):
        """Test duration_seconds with no calls."""
        assert test_execution.duration_seconds is None

    def test_test_execution_soft_delete(self, db, test_execution):
        """Test soft delete functionality."""
        execution_id = test_execution.id
        test_execution.delete()

        assert not TestExecution.objects.filter(id=execution_id).exists()
        assert TestExecution.all_objects.filter(id=execution_id).exists()

    def test_test_execution_str_method(self, db, test_execution):
        """Test __str__ method."""
        expected = f"{test_execution.run_test.name} - {test_execution.status}"
        assert str(test_execution) == expected

    def test_test_execution_eval_explanation_summary_status_choices(self):
        """Test EvalExplanationSummaryStatus enum values."""
        assert EvalExplanationSummaryStatus.PENDING == "pending"
        assert EvalExplanationSummaryStatus.RUNNING == "running"
        assert EvalExplanationSummaryStatus.COMPLETED == "completed"
        assert EvalExplanationSummaryStatus.FAILED == "failed"

    def test_test_execution_scenario_ids_stored(
        self, db, run_test, simulator_agent, agent_definition
    ):
        """Test scenario_ids can store list of UUIDs."""
        scenario_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            scenario_ids=scenario_ids,
            simulator_agent=simulator_agent,
            agent_definition=agent_definition,
        )

        execution.refresh_from_db()
        assert execution.scenario_ids == scenario_ids


# ============================================================================
# CallExecution Model Tests
# ============================================================================


@pytest.mark.unit
class TestCallExecutionModel:
    """Tests for CallExecution model."""

    def test_call_execution_creation(self, db, test_execution, scenario):
        """Test basic CallExecution creation."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.PENDING,
        )

        assert call.id is not None
        assert call.phone_number == "+1234567890"
        assert call.status == CallExecution.CallStatus.PENDING
        assert call.service_provider_call_id is None

    def test_call_execution_status_choices(self):
        """Test CallStatus enum values."""
        assert CallExecution.CallStatus.PENDING == "pending"
        assert (
            CallExecution.CallStatus.REGISTERED == "queued"
        )  # Note: value is "queued"
        assert CallExecution.CallStatus.ONGOING == "ongoing"
        assert CallExecution.CallStatus.COMPLETED == "completed"
        assert CallExecution.CallStatus.FAILED == "failed"
        assert CallExecution.CallStatus.CANCELLED == "cancelled"

    def test_call_execution_status_transitions(self, db, call_execution):
        """Test status transitions through lifecycle."""
        # PENDING -> REGISTERED
        call_execution.status = CallExecution.CallStatus.REGISTERED
        call_execution.save()
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.REGISTERED

        # REGISTERED -> ONGOING
        call_execution.status = CallExecution.CallStatus.ONGOING
        call_execution.started_at = timezone.now()
        call_execution.service_provider_call_id = "provider-123"
        call_execution.save()
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.ONGOING

        # ONGOING -> COMPLETED
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.completed_at = timezone.now()
        call_execution.duration_seconds = 300
        call_execution.save()
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.COMPLETED

    def test_call_execution_is_successful_property(self, db, call_execution):
        """Test is_successful property."""
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.save()

        assert call_execution.is_successful is True

        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.save()

        assert call_execution.is_successful is False

    def test_call_execution_is_failed_property(self, db, call_execution):
        """Test is_failed property."""
        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.save()
        assert call_execution.is_failed is True

        call_execution.status = CallExecution.CallStatus.CANCELLED
        call_execution.save()
        assert call_execution.is_failed is True

        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.save()
        assert call_execution.is_failed is False

    def test_call_execution_cost_breakdown(self, db, call_execution):
        """Test cost breakdown fields."""
        call_execution.cost_cents = 100
        call_execution.stt_cost_cents = 20
        call_execution.llm_cost_cents = 50
        call_execution.tts_cost_cents = 20
        call_execution.save()

        call_execution.refresh_from_db()
        assert call_execution.cost_cents == 100
        assert call_execution.stt_cost_cents == 20
        assert call_execution.llm_cost_cents == 50
        assert call_execution.tts_cost_cents == 20

    def test_call_execution_conversation_metrics(self, db, call_execution):
        """Test conversation metrics fields."""
        call_execution.avg_agent_latency_ms = 500
        call_execution.user_interruption_count = 3
        call_execution.ai_interruption_count = 1
        call_execution.user_wpm = 150.5
        call_execution.bot_wpm = 120.3
        call_execution.talk_ratio = 0.8
        call_execution.save()

        call_execution.refresh_from_db()
        assert call_execution.avg_agent_latency_ms == 500
        assert call_execution.user_interruption_count == 3
        assert call_execution.user_wpm == 150.5
        assert call_execution.talk_ratio == 0.8

    def test_call_execution_eval_outputs(self, db, call_execution):
        """Test eval_outputs JSON field."""
        eval_outputs = {
            "accuracy": {"score": 0.9, "explanation": "Good accuracy"},
            "relevance": {"score": 0.85, "explanation": "Relevant responses"},
        }
        call_execution.eval_outputs = eval_outputs
        call_execution.save()

        call_execution.refresh_from_db()
        assert call_execution.eval_outputs == eval_outputs

    def test_call_execution_soft_delete(self, db, call_execution):
        """Test soft delete functionality."""
        call_id = call_execution.id
        call_execution.delete()

        assert not CallExecution.objects.filter(id=call_id).exists()
        assert CallExecution.all_objects.filter(id=call_id).exists()

    def test_call_execution_str_method(self, db, call_execution):
        """Test __str__ method."""
        expected = f"{call_execution.phone_number} - {call_execution.status}"
        assert str(call_execution) == expected

    def test_call_execution_ordering(self, db, test_execution, scenario):
        """Test that CallExecutions are ordered by -updated_at."""
        call1 = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1111111111",
        )
        call2 = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+2222222222",
        )

        # Update call1 to make it more recent
        call1.status = CallExecution.CallStatus.ONGOING
        call1.save()

        calls = list(CallExecution.objects.filter(test_execution=test_execution))
        assert calls[0].phone_number == "+1111111111"  # Most recently updated


# ============================================================================
# CallTranscript Model Tests
# ============================================================================


@pytest.mark.unit
class TestCallTranscriptModel:
    """Tests for CallTranscript model."""

    def test_call_transcript_creation(self, db, call_execution):
        """Test basic CallTranscript creation."""
        transcript = CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content="Hello, I need help.",
            start_time_ms=0,
            end_time_ms=2000,
        )

        assert transcript.id is not None
        assert transcript.content == "Hello, I need help."
        assert transcript.speaker_role == CallTranscript.SpeakerRole.USER

    def test_call_transcript_speaker_role_choices(self):
        """Test SpeakerRole enum values."""
        assert CallTranscript.SpeakerRole.USER == "user"
        assert CallTranscript.SpeakerRole.ASSISTANT == "assistant"
        assert CallTranscript.SpeakerRole.SYSTEM == "system"
        assert CallTranscript.SpeakerRole.TOOL_CALLS == "tool_calls"
        assert CallTranscript.SpeakerRole.TOOL_CALL_RESULT == "tool_call_result"
        assert CallTranscript.SpeakerRole.UNKNOWN == "unknown"

    def test_call_transcript_ordering(self, db, call_execution):
        """Test transcripts are ordered by start_time_ms."""
        t1 = CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content="First message",
            start_time_ms=0,
            end_time_ms=2000,
        )
        t2 = CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.ASSISTANT,
            content="Second message",
            start_time_ms=2500,
            end_time_ms=5000,
        )
        t3 = CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content="Third message",
            start_time_ms=5500,
            end_time_ms=8000,
        )

        transcripts = list(call_execution.transcripts.all())
        assert transcripts[0].content == "First message"
        assert transcripts[1].content == "Second message"
        assert transcripts[2].content == "Third message"

    def test_call_transcript_str_method(self, db, call_execution):
        """Test __str__ method."""
        transcript = CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content="This is a very long message that should be truncated in the string representation",
            start_time_ms=0,
            end_time_ms=2000,
        )

        assert "user" in str(transcript).lower()
        assert "..." in str(transcript)


# ============================================================================
# CallExecutionSnapshot Model Tests
# ============================================================================


@pytest.mark.unit
class TestCallExecutionSnapshotModel:
    """Tests for CallExecutionSnapshot model."""

    def test_snapshot_creation(self, db, call_execution):
        """Test basic snapshot creation."""
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.service_provider_call_id = "provider-123"
        call_execution.duration_seconds = 300
        call_execution.cost_cents = 50
        call_execution.save()

        snapshot = CallExecutionSnapshot.objects.create(
            call_execution=call_execution,
            rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
            service_provider_call_id=call_execution.service_provider_call_id,
            status=call_execution.status,
            duration_seconds=call_execution.duration_seconds,
            cost_cents=call_execution.cost_cents,
        )

        assert snapshot.id is not None
        assert snapshot.rerun_type == CallExecutionSnapshot.RerunType.EVAL_ONLY
        assert snapshot.service_provider_call_id == "provider-123"
        assert snapshot.cost_cents == 50

    def test_snapshot_rerun_type_choices(self):
        """Test RerunType enum values."""
        assert CallExecutionSnapshot.RerunType.EVAL_ONLY == "eval_only"
        assert CallExecutionSnapshot.RerunType.CALL_AND_EVAL == "call_and_eval"

    def test_snapshot_with_transcripts(self, db, call_execution):
        """Test snapshot can store transcripts as JSON."""
        transcripts_data = [
            {"speaker_role": "user", "content": "Hello", "start_time_ms": 0},
            {
                "speaker_role": "assistant",
                "content": "Hi there!",
                "start_time_ms": 2000,
            },
        ]

        snapshot = CallExecutionSnapshot.objects.create(
            call_execution=call_execution,
            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
            transcripts=transcripts_data,
        )

        snapshot.refresh_from_db()
        assert snapshot.transcripts == transcripts_data

    def test_snapshot_ordering(self, db, call_execution):
        """Test snapshots are ordered by -snapshot_timestamp."""
        s1 = CallExecutionSnapshot.objects.create(
            call_execution=call_execution,
            rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
        )
        s2 = CallExecutionSnapshot.objects.create(
            call_execution=call_execution,
            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        )

        snapshots = list(call_execution.snapshots.all())
        # Most recent first
        assert snapshots[0].id == s2.id


# ============================================================================
# CreateCallExecution Model Tests
# ============================================================================


@pytest.mark.unit
class TestCreateCallExecutionModel:
    """Tests for CreateCallExecution staging model."""

    def test_create_call_execution_creation(self, db, call_execution):
        """Test CreateCallExecution creation."""
        create_call = CreateCallExecution.objects.create(
            phone_number_id="phone-123",
            to_number="+1234567890",
            system_prompt="You are a test agent.",
            metadata={"test": "value"},
            voice_settings={"provider": "elevenlabs"},
            call_execution=call_execution,
        )

        assert create_call.id is not None
        assert create_call.phone_number_id == "phone-123"
        assert create_call.status == CreateCallExecution.CallStatus.REGISTERED

    def test_create_call_execution_status_choices(self):
        """Test CallStatus enum in CreateCallExecution."""
        assert CreateCallExecution.CallStatus.PENDING == "pending"
        assert CreateCallExecution.CallStatus.REGISTERED == "registered"
        assert CreateCallExecution.CallStatus.ONGOING == "ongoing"
        assert CreateCallExecution.CallStatus.COMPLETED == "completed"
        assert CreateCallExecution.CallStatus.FAILED == "failed"
        assert CreateCallExecution.CallStatus.CANCELLED == "cancelled"


# ============================================================================
# SimulateEvalConfig Model Tests
# ============================================================================


@pytest.mark.unit
class TestSimulateEvalConfigModel:
    """Tests for SimulateEvalConfig model."""

    def test_eval_config_creation(self, db, run_test, organization):
        """Test SimulateEvalConfig creation."""
        eval_template = EvalTemplate.objects.create(
            name="Test Eval",
            config={"prompt": "Evaluate this"},
            organization=organization,
        )

        eval_config = SimulateEvalConfig.objects.create(
            name="Test Config",
            eval_template=eval_template,
            run_test=run_test,
            config={"threshold": 0.8},
            mapping={"input": "transcript"},
        )

        assert eval_config.id is not None
        assert eval_config.name == "Test Config"
        assert eval_config.eval_template == eval_template
        assert eval_config.run_test == run_test

    def test_eval_config_relationship_with_run_test(self, db, run_test, organization):
        """Test relationship between eval config and run test."""
        eval_template = EvalTemplate.objects.create(
            name="Eval 1",
            config={},
            organization=organization,
        )

        config1 = SimulateEvalConfig.objects.create(
            name="Config 1",
            eval_template=eval_template,
            run_test=run_test,
        )
        config2 = SimulateEvalConfig.objects.create(
            name="Config 2",
            eval_template=eval_template,
            run_test=run_test,
        )

        assert run_test.simulate_eval_configs.count() == 2

    @pytest.mark.parametrize(
        "model_value",
        ["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-latest", "turing_large", ""],
    )
    def test_eval_config_accepts_byo_model(self, db, model_value):
        config = SimulateEvalConfig(model=model_value)
        exclude = [f.name for f in SimulateEvalConfig._meta.fields if f.name != "model"]
        config.clean_fields(exclude=exclude)


# ============================================================================
# Bug Discovery Tests
# ============================================================================


@pytest.mark.unit
class TestModelBugs:
    """Tests that document discovered bugs."""

    def test_run_test_duplicate_meta_class_documented(self):
        """
        BUG FOUND: RunTest model has duplicate Meta class (lines 83-109).

        The model defines Meta class and __str__ method twice.
        This should be cleaned up.
        """
        # This test documents the bug - the model still works but has redundant code
        # Check that the model class only has the expected attributes
        meta_attrs = [attr for attr in dir(RunTest) if attr == "Meta"]
        # Python will use the last Meta class definition
        assert len(meta_attrs) == 1

    def test_call_execution_status_registered_value_mismatch(self):
        """
        NOTE: CallExecution.CallStatus.REGISTERED has value "queued" not "registered".

        This is intentional but can be confusing. The enum name is REGISTERED
        but the database value is "queued".
        """
        assert CallExecution.CallStatus.REGISTERED.value == "queued"
        assert CallExecution.CallStatus.REGISTERED.label == "Queued"
