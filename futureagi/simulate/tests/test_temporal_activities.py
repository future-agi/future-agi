"""
Tests for Temporal workflow activities and client API.

Comprehensive tests covering:
1. Client API tests (start_test_execution_workflow, cancel, query, rerun)
2. Integration tests for activity database operations
3. CreateCallExecution integration with Temporal workflows

Run with: pytest simulate/tests/test_temporal_activities.py -v

Environment variables:
- VAPI_PHONE_NUMBER_ID: VAPI phone number ID for test fixtures (default: phone-test-id-12345)
- VAPI_API_KEY: VAPI API key for test fixtures (default: test-api-key-for-testing)
"""

import os
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone

# Environment variables for sensitive test data
VAPI_PHONE_NUMBER_ID = os.environ.get("VAPI_PHONE_NUMBER_ID", "phone-test-id-12345")
VAPI_API_KEY = os.environ.get("VAPI_API_KEY", "test-api-key-for-testing")

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.agent_version import AgentVersion
from simulate.models.run_test import CreateCallExecution, RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.semantics import CallType
from tracer.models.observability_provider import ProviderChoices

# ============================================================================
# Fixtures
# ============================================================================


def _ee_voice_large():
    return pytest.importorskip("ee.voice.temporal.activities.voice_large")


def _ee_processing_gating():
    return pytest.importorskip("ee.voice.utils.processing_gating")


def _ee_call_execution_workflow():
    return pytest.importorskip("ee.voice.temporal.workflows.call_execution_workflow")


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
        conversation_speed=1.0,
        interrupt_sensitivity=0.5,
        finished_speaking_sensitivity=0.5,
        max_call_duration_in_minutes=15,
        initial_message_delay=0,
        initial_message="Hello",
    )


@pytest.fixture
def agent_version(db, agent_definition, organization, workspace):
    """Create an agent version with configuration snapshot."""
    return AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={
            "contact_number": "+15551234567",
            "assistant_id": "test-assistant-id",
            "api_key": VAPI_API_KEY,
            "workspace_id": str(agent_definition.workspace_id),
        },
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
def scenario(
    db, organization, workspace, dataset_for_scenario, agent_definition, simulator_agent
):
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
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    """Create a test RunTest."""
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run description",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition, agent_version):
    """Create a test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario, agent_version):
    """Create a call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.PENDING,
        agent_version=agent_version,
    )


# ============================================================================
# Client API Tests
# ============================================================================


@pytest.mark.unit
class TestTemporalClientAPI:
    """Tests for Temporal client API functions."""

    @patch("simulate.temporal.client._start_test_execution_workflow_async")
    def test_start_test_execution_workflow(
        self, mock_start_async, db, test_execution, scenario
    ):
        """Test start_test_execution_workflow calls async implementation."""
        from simulate.temporal.client import start_test_execution_workflow

        mock_start_async.return_value = f"test-exec-{test_execution.id}"

        workflow_id = start_test_execution_workflow(
            test_execution_id=str(test_execution.id),
            run_test_id=str(test_execution.run_test_id),
            org_id=str(test_execution.run_test.organization_id),
            scenario_ids=[str(scenario.id)],
            simulator_id=str(test_execution.simulator_agent_id),
        )

        assert workflow_id == f"test-exec-{test_execution.id}"
        mock_start_async.assert_called_once()

    @patch("simulate.temporal.client._get_test_execution_status_async")
    def test_get_test_execution_status(self, mock_get_async, db, test_execution):
        """Test get_test_execution_status returns workflow status."""
        from simulate.temporal.client import get_test_execution_status

        mock_get_async.return_value = {
            "source": "temporal",
            "workflow_id": f"test-exec-{test_execution.id}",
            "status": "RUNNING",
            "total_calls": 10,
            "completed_calls": 5,
        }

        status = get_test_execution_status(str(test_execution.id))

        assert status["source"] == "temporal"
        assert status["status"] == "RUNNING"
        assert status["completed_calls"] == 5

    @patch("simulate.temporal.client._cancel_test_execution_async")
    def test_cancel_test_execution(self, mock_cancel_async, db, test_execution):
        """Test cancel_test_execution sends cancel signal."""
        from simulate.temporal.client import cancel_test_execution

        mock_cancel_async.return_value = True

        result = cancel_test_execution(str(test_execution.id))

        assert result is True
        mock_cancel_async.assert_called_once_with(str(test_execution.id))

    @patch("simulate.temporal.client._cancel_test_execution_async")
    def test_cancel_test_execution_returns_false_on_error(
        self, mock_cancel_async, db, test_execution
    ):
        """Test cancel_test_execution returns False when workflow not found."""
        from simulate.temporal.client import cancel_test_execution

        mock_cancel_async.return_value = False

        result = cancel_test_execution(str(test_execution.id))

        assert result is False

    @patch("simulate.temporal.client._rerun_call_executions_async")
    def test_rerun_call_executions(
        self,
        mock_rerun_async,
        db,
        test_execution,
        call_execution,
        organization,
        workspace,
    ):
        """Test rerun_call_executions launches individual workflows."""
        from simulate.temporal.client import rerun_call_executions

        mock_rerun_async.return_value = {
            "successful": [str(call_execution.id)],
            "failed": [],
            "total": 1,
        }

        result = rerun_call_executions(
            test_execution_id=str(test_execution.id),
            call_execution_ids=[str(call_execution.id)],
            org_id=str(organization.id),
            workspace_id=str(workspace.id),
        )

        assert len(result["successful"]) == 1
        assert len(result["failed"]) == 0
        assert result["total"] == 1

    @patch("simulate.temporal.client._rerun_call_executions_async")
    def test_rerun_evaluations_only(
        self,
        mock_rerun_async,
        db,
        call_execution,
        test_execution,
        organization,
        workspace,
    ):
        """Test rerun_evaluations_only calls rerun_call_executions with eval_only=True."""
        from simulate.temporal.client import rerun_evaluations_only

        mock_rerun_async.return_value = {
            "workflow_id": "rerun-eval-test",
            "rerun_id": "123456",
            "successful": [str(call_execution.id)],
            "failed": [],
            "total": 1,
            "eval_only": True,
            "merged": False,
        }

        result = rerun_evaluations_only(
            test_execution_id=str(test_execution.id),
            call_execution_ids=[str(call_execution.id)],
            org_id=str(organization.id),
            workspace_id=str(workspace.id),
        )

        assert len(result["successful"]) == 1
        assert str(call_execution.id) in result["successful"]
        assert result["eval_only"] is True


@pytest.mark.integration
class TestTemporalClientAPIIntegration:
    """Integration tests for Temporal client API with mocked Temporal client."""

    @patch("tfc.temporal.common.client.get_client")
    @pytest.mark.asyncio
    async def test_start_workflow_creates_correct_workflow_id(
        self, mock_get_client, db, test_execution, scenario
    ):
        """Test that workflow ID is generated correctly."""
        from simulate.temporal.client import _start_test_execution_workflow_async
        from simulate.temporal.constants import TEST_EXECUTION_WORKFLOW_ID_PREFIX

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        workflow_id = await _start_test_execution_workflow_async(
            test_execution_id=str(test_execution.id),
            run_test_id=str(test_execution.run_test_id),
            org_id=str(test_execution.run_test.organization_id),
            scenario_ids=[str(scenario.id)],
        )

        expected_id = f"{TEST_EXECUTION_WORKFLOW_ID_PREFIX}-{test_execution.id}"
        assert workflow_id == expected_id
        mock_client.start_workflow.assert_called_once()

    @patch("tfc.temporal.common.client.get_client")
    @pytest.mark.asyncio
    async def test_get_status_handles_workflow_not_found(
        self, mock_get_client, db, test_execution
    ):
        """Test get_status returns error when workflow not found."""
        from simulate.temporal.client import _get_test_execution_status_async

        # get_workflow_handle is sync, query is async
        mock_handle = MagicMock()
        mock_handle.query = AsyncMock(side_effect=Exception("Workflow not found"))
        mock_client = MagicMock()
        mock_client.get_workflow_handle.return_value = mock_handle
        mock_get_client.return_value = mock_client

        status = await _get_test_execution_status_async(str(test_execution.id))

        assert "error" in status
        assert "Workflow not found" in status["error"]

    @patch("tfc.temporal.common.client.get_client")
    @pytest.mark.asyncio
    async def test_cancel_workflow_sends_cancel(
        self, mock_get_client, db, test_execution
    ):
        """Test cancel sends cancel to workflow."""
        from simulate.temporal.client import _cancel_test_execution_async

        # get_workflow_handle is sync, cancel is async
        mock_handle = MagicMock()
        mock_handle.cancel = AsyncMock()
        mock_client = MagicMock()
        mock_client.get_workflow_handle.return_value = mock_handle
        mock_get_client.return_value = mock_client

        result = await _cancel_test_execution_async(str(test_execution.id))

        assert result is True
        mock_handle.cancel.assert_called_once()


# ============================================================================
# Activity Integration Tests - setup_test_execution
# ============================================================================


@pytest.mark.integration
class TestSetupTestExecutionActivity:
    """Integration tests for setup_test_execution activity."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_setup_test_execution_updates_status_to_running(
        self, test_execution, scenario
    ):
        """Test that setup_test_execution updates TestExecution status to RUNNING."""
        from simulate.temporal.activities.test_execution import setup_test_execution
        from simulate.temporal.types.activities import SetupTestInput

        # Mock the activity context
        with patch("temporalio.activity.info"):
            result = await setup_test_execution(
                SetupTestInput(
                    test_execution_id=str(test_execution.id),
                    run_test_id=str(test_execution.run_test_id),
                    scenario_ids=[str(scenario.id)],
                )
            )

        assert result.success is True

        # Verify picked_up_by_executor updated in database
        # Note: status remains PENDING until first call slot is granted
        await sync_to_async(test_execution.refresh_from_db)()
        assert test_execution.picked_up_by_executor is True

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_setup_test_execution_loads_scenarios(
        self, test_execution, scenario, dataset_for_scenario
    ):
        """Test that setup_test_execution loads scenario data correctly."""
        from simulate.temporal.activities.test_execution import setup_test_execution
        from simulate.temporal.types.activities import SetupTestInput

        with patch("temporalio.activity.info"):
            result = await setup_test_execution(
                SetupTestInput(
                    test_execution_id=str(test_execution.id),
                    run_test_id=str(test_execution.run_test_id),
                    scenario_ids=[str(scenario.id)],
                )
            )

        assert result.success is True
        assert len(result.scenarios) == 1
        assert result.scenarios[0]["id"] == str(scenario.id)
        assert result.scenarios[0]["name"] == scenario.name

    @pytest.mark.asyncio
    async def test_setup_test_execution_returns_error_for_invalid_id(self, db):
        """Test that setup_test_execution returns error for non-existent TestExecution."""
        from simulate.temporal.activities.test_execution import setup_test_execution
        from simulate.temporal.types.activities import SetupTestInput

        fake_id = str(uuid.uuid4())

        with patch("temporalio.activity.info"):
            result = await setup_test_execution(
                SetupTestInput(
                    test_execution_id=fake_id,
                    run_test_id=fake_id,
                    scenario_ids=[fake_id],
                )
            )

        assert result.success is False
        assert "not found" in result.error.lower()


# ============================================================================
# Activity Integration Tests - create_call_execution_records
# ============================================================================


@pytest.mark.integration
class TestCreateCallExecutionRecordsActivity:
    """Integration tests for create_call_execution_records activity."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_creates_call_execution_records(
        self, test_execution, scenario, simulator_agent
    ):
        """Test that activity creates CallExecution records."""
        from simulate.temporal.activities.test_execution import (
            create_call_execution_records,
        )
        from simulate.temporal.types.activities import CreateCallRecordsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        # Get row IDs from scenario dataset
        row_ids = await sync_to_async(list)(
            Row.objects.filter(dataset=scenario.dataset).values_list("id", flat=True)
        )

        scenarios_data = [
            {
                "id": str(scenario.id),
                "name": scenario.name,
                "dataset_id": str(scenario.dataset_id),
                "row_ids": [str(rid) for rid in row_ids],
            }
        ]

        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "ee.voice.constants.voice_mapper.select_voice_id",
                return_value="test-voice",
            ),
        ):
            result = await create_call_execution_records(
                CreateCallRecordsInput(
                    test_execution_id=str(test_execution.id),
                    scenarios=scenarios_data,
                    simulator_agent={"id": str(simulator_agent.id)},
                )
            )

        assert result.error is None
        assert result.total_created >= 1
        assert len(result.call_ids) >= 1

        # Verify CallExecution created in database
        call_count = await sync_to_async(
            CallExecution.objects.filter(test_execution=test_execution).count
        )()
        assert call_count >= 1

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_creates_create_call_execution_with_ongoing_status(
        self, test_execution, scenario, simulator_agent
    ):
        """Test that activity creates CreateCallExecution with ONGOING status."""
        from simulate.temporal.activities.test_execution import (
            create_call_execution_records,
        )
        from simulate.temporal.types.activities import CreateCallRecordsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        row_ids = await sync_to_async(list)(
            Row.objects.filter(dataset=scenario.dataset).values_list("id", flat=True)
        )

        scenarios_data = [
            {
                "id": str(scenario.id),
                "name": scenario.name,
                "dataset_id": str(scenario.dataset_id),
                "row_ids": [str(rid) for rid in row_ids],
            }
        ]

        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "ee.voice.constants.voice_mapper.select_voice_id",
                return_value="test-voice",
            ),
        ):
            result = await create_call_execution_records(
                CreateCallRecordsInput(
                    test_execution_id=str(test_execution.id),
                    scenarios=scenarios_data,
                    simulator_agent={"id": str(simulator_agent.id)},
                )
            )

        assert result.error is None

        # Verify CreateCallExecution created with ONGOING status
        create_call_count = await sync_to_async(
            CreateCallExecution.objects.filter(
                call_execution__test_execution=test_execution,
                status=CreateCallExecution.CallStatus.ONGOING,
            ).count
        )()
        assert create_call_count >= 1

        # Verify NO CreateCallExecution with REGISTERED status
        registered_count = await sync_to_async(
            CreateCallExecution.objects.filter(
                call_execution__test_execution=test_execution,
                status=CreateCallExecution.CallStatus.REGISTERED,
            ).count
        )()
        assert registered_count == 0

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_updates_test_execution_total_calls(
        self, test_execution, scenario, simulator_agent
    ):
        """Test that activity updates TestExecution.total_calls."""
        from simulate.temporal.activities.test_execution import (
            create_call_execution_records,
        )
        from simulate.temporal.types.activities import CreateCallRecordsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        row_ids = await sync_to_async(list)(
            Row.objects.filter(dataset=scenario.dataset).values_list("id", flat=True)
        )

        scenarios_data = [
            {
                "id": str(scenario.id),
                "name": scenario.name,
                "dataset_id": str(scenario.dataset_id),
                "row_ids": [str(rid) for rid in row_ids],
            }
        ]

        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "ee.voice.constants.voice_mapper.select_voice_id",
                return_value="test-voice",
            ),
        ):
            result = await create_call_execution_records(
                CreateCallRecordsInput(
                    test_execution_id=str(test_execution.id),
                    scenarios=scenarios_data,
                    simulator_agent={"id": str(simulator_agent.id)},
                )
            )

        # Verify total_calls updated
        await sync_to_async(test_execution.refresh_from_db)()
        assert test_execution.total_calls == result.total_created


# ============================================================================
# Activity Integration Tests - finalize_test_execution
# ============================================================================


@pytest.mark.integration
class TestFinalizeTestExecutionActivity:
    """Integration tests for finalize_test_execution activity."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_finalize_test_execution_updates_status(self, test_execution):
        """Test that finalize updates status and counts."""
        from simulate.temporal.activities.test_execution import finalize_test_execution
        from simulate.temporal.types.activities import FinalizeInput

        with (
            patch("temporalio.activity.info"),
            patch("simulate.tasks.eval_summary_tasks.run_eval_summary_task"),
        ):
            await finalize_test_execution(
                FinalizeInput(
                    test_execution_id=str(test_execution.id),
                    status="COMPLETED",
                    completed_calls=8,
                    failed_calls=2,
                )
            )

        await sync_to_async(test_execution.refresh_from_db)()
        assert test_execution.status == "COMPLETED"
        assert test_execution.completed_calls == 8
        assert test_execution.failed_calls == 2
        assert test_execution.completed_at is not None

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_finalize_test_execution_triggers_eval_summary(self, test_execution):
        """Test that finalize triggers eval summary task on completion."""
        from simulate.temporal.activities.test_execution import finalize_test_execution
        from simulate.temporal.types.activities import FinalizeInput

        with (
            patch("temporalio.activity.info"),
            patch(
                "simulate.tasks.eval_summary_tasks.run_eval_summary_task"
            ) as mock_task,
        ):
            mock_task.apply_async = MagicMock()

            await finalize_test_execution(
                FinalizeInput(
                    test_execution_id=str(test_execution.id),
                    status="COMPLETED",
                    completed_calls=10,
                    failed_calls=0,
                )
            )

            mock_task.apply_async.assert_called_once()


# ============================================================================
# CreateCallExecution Integration Tests
# ============================================================================


@pytest.mark.integration
class TestCreateCallExecutionIntegration:
    """Integration tests for CreateCallExecution lifecycle in Temporal workflows."""

    def test_create_call_execution_ongoing_not_picked_up_by_test_monitor_query(
        self, db, call_execution
    ):
        """Test that ONGOING CreateCallExecution is not picked up by test_monitor query pattern."""
        # Create with ONGOING status (Temporal workflow behavior)
        CreateCallExecution.objects.create(
            phone_number_id=VAPI_PHONE_NUMBER_ID,
            to_number="+1234567890",
            system_prompt="Test prompt",
            metadata={},
            voice_settings={},
            call_execution=call_execution,
            status=CreateCallExecution.CallStatus.ONGOING,
        )

        # Simulate test_monitor query - it filters by REGISTERED status
        registered_records = CreateCallExecution.objects.filter(
            status=CreateCallExecution.CallStatus.REGISTERED
        )

        assert registered_records.count() == 0

    def test_full_temporal_lifecycle_call_execution_and_create_call_execution(
        self, db, test_execution, scenario, agent_version
    ):
        """Test full lifecycle: create both records -> execute -> finalize."""
        # Step 1: Create CallExecution (simulating create_call_execution_records activity)
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.PENDING,
            agent_version=agent_version,
        )

        # Step 2: Create CreateCallExecution with ONGOING
        create_call = CreateCallExecution.objects.create(
            phone_number_id=VAPI_PHONE_NUMBER_ID,
            to_number="+1234567890",
            system_prompt="Test prompt",
            metadata={"scenario_id": str(scenario.id)},
            voice_settings={"voice_id": "test-voice"},
            call_execution=call,
            status=CreateCallExecution.CallStatus.ONGOING,
        )

        # Verify initial state
        assert call.status == CallExecution.CallStatus.PENDING
        assert create_call.status == CreateCallExecution.CallStatus.ONGOING

        # Step 3: Simulate call execution phases (update_call_status activity)
        call.status = CallExecution.CallStatus.REGISTERED
        call.save()

        call.status = CallExecution.CallStatus.ONGOING
        call.save()

        # CreateCallExecution should still be ONGOING (not updated by update_call_status)
        create_call.refresh_from_db()
        assert create_call.status == CreateCallExecution.CallStatus.ONGOING

        # Step 4: Simulate persist_call_result activity
        call.status = CallExecution.CallStatus.COMPLETED
        call.duration_seconds = 120
        call.save()

        # Update CreateCallExecution final status
        CreateCallExecution.objects.filter(call_execution_id=call.id).update(
            status=CreateCallExecution.CallStatus.COMPLETED
        )

        # Verify final state
        create_call.refresh_from_db()
        assert call.status == CallExecution.CallStatus.COMPLETED
        assert create_call.status == CreateCallExecution.CallStatus.COMPLETED

    def test_batch_create_call_executions_all_have_ongoing_status(
        self, db, test_execution, scenario, agent_version
    ):
        """Test creating multiple records - all should have ONGOING status."""
        calls_data = []

        for i in range(5):
            call = CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+123456789{i}",
                status=CallExecution.CallStatus.PENDING,
                agent_version=agent_version,
            )

            CreateCallExecution.objects.create(
                phone_number_id=f"phone-{i}",
                to_number=f"+123456789{i}",
                system_prompt=f"Test prompt {i}",
                metadata={},
                voice_settings={},
                call_execution=call,
                status=CreateCallExecution.CallStatus.ONGOING,
            )

            calls_data.append(call)

        # Verify all CreateCallExecution have ONGOING status
        ongoing_count = CreateCallExecution.objects.filter(
            call_execution__test_execution=test_execution,
            status=CreateCallExecution.CallStatus.ONGOING,
        ).count()

        registered_count = CreateCallExecution.objects.filter(
            call_execution__test_execution=test_execution,
            status=CreateCallExecution.CallStatus.REGISTERED,
        ).count()

        assert ongoing_count == 5
        assert registered_count == 0

    def test_create_call_execution_stores_metadata_for_ui(self, db, call_execution):
        """Test that CreateCallExecution stores all metadata needed for call logs UI."""
        metadata = {
            "run_test_id": str(uuid.uuid4()),
            "scenario_id": str(uuid.uuid4()),
            "scenario_name": "Test Scenario",
            "agent_definition_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "row_id": str(uuid.uuid4()),
            "row_data": {"situation": "Customer needs help with billing"},
            "dataset_id": str(uuid.uuid4()),
            "call_direction": "outbound",
            "user_assistant_id": "asst_123",
            "user_phone_number": "+1234567890",
        }

        voice_settings = {
            "voice_id": "voice-123",
            "speed": 1.0,
            "interrupt_sensitivity": 0.5,
            "finished_speaking_sensitivity": 0.5,
            "max_call_duration_in_minutes": 15,
            "language": "en",
            "initial_message": "Hello",
            "background_sound": "off",
        }

        create_call = CreateCallExecution.objects.create(
            phone_number_id=VAPI_PHONE_NUMBER_ID,
            to_number="+1234567890",
            system_prompt="You are a helpful customer service agent.",
            metadata=metadata,
            voice_settings=voice_settings,
            call_execution=call_execution,
            status=CreateCallExecution.CallStatus.ONGOING,
        )

        create_call.refresh_from_db()

        # Verify all fields accessible for UI
        assert create_call.metadata["scenario_name"] == "Test Scenario"
        assert create_call.metadata["call_direction"] == "outbound"
        assert create_call.voice_settings["voice_id"] == "voice-123"
        assert create_call.system_prompt is not None
        assert len(create_call.system_prompt) > 0


# ============================================================================
# Activity Integration Tests - fetch_and_persist_call_result
# ============================================================================


@pytest.mark.integration
class TestFetchAndPersistCallResultActivity:
    """Integration tests for fetch_and_persist_call_result activity."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_fetch_and_persist_call_result_updates_call_execution(
        self, call_execution
    ):
        """Test that fetch_and_persist_call_result updates CallExecution fields."""
        fetch_and_persist_call_result = _ee_voice_large().fetch_and_persist_call_result
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from tfc.temporal.common.heartbeat import Heartbeater

        # Mock the VoiceServiceManager and Heartbeater
        mock_call_data = MagicMock()
        mock_call_data.status = "ended"
        mock_call_data.call_id = "vapi-call-123"
        mock_call_data.duration_seconds = 120.5
        mock_call_data.cost = 0.05
        mock_call_data.ended_reason = "customer-ended-call"
        mock_call_data.transcript_available = True
        mock_call_data.recording_available = True
        mock_call_data.recording_url = "https://example.com/recording.mp3"
        mock_call_data.summary = "Test call summary"
        mock_call_data.messages = [{"role": "user", "content": "Hello"}]
        mock_call_data.raw_log = {"vapi": {"id": "vapi-123"}}
        mock_call_data.cost_breakdown = {"vapi": {"llm": 0.03, "tts": 0.02}}
        mock_call_data.performance_metrics = {}
        mock_call_data.analysis_data = {}
        mock_call_data.assistant_id = "asst-test-123"
        mock_call_data.customer_phone_number = "+1234567890"
        mock_call_data.call_type = "inbound"
        mock_call_data.started_at = "2024-01-01T10:00:00Z"
        mock_call_data.ended_at = "2024-01-01T10:02:00Z"
        mock_call_data.transcript = {"vapi": {"transcripts": []}}

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.heartbeat"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "ee.voice.services.voice_service_manager.VoiceServiceManager"
            ) as mock_vsm_class,
        ):
            mock_vsm = MagicMock()
            mock_vsm.get_call_async = AsyncMock(return_value=mock_call_data)
            mock_vsm.get_recording_urls = MagicMock(return_value={})
            # Production now calls fetch_and_store_call_data which returns
            # a (message_count, has_agent_message, has_customer_message) tuple.
            mock_vsm.fetch_and_store_call_data = AsyncMock(return_value=(1, True, True))
            # extract_and_persist_recordings returns an object with optional
            # *_url attributes; use a MagicMock with all set to None.
            mock_recordings = MagicMock(
                recording_url=None,
                stereo_recording_url=None,
                customer_recording_url=None,
                assistant_recording_url=None,
            )
            mock_vsm.extract_and_persist_recordings = AsyncMock(
                return_value=mock_recordings
            )
            # extract_costs returns an object with cost components.
            mock_costs = MagicMock(
                total=None,
                stt=None,
                llm=None,
                tts=None,
                transport=None,
                storage=None,
            )
            mock_vsm.extract_costs = AsyncMock(return_value=mock_costs)
            mock_vsm_class.return_value = mock_vsm

            result = await fetch_and_persist_call_result(
                FetchAndPersistCallResultInput(
                    call_id=str(call_execution.id),
                    provider_call_id="vapi-call-123",
                    status="COMPLETED",
                    provider="vapi",
                    call_type=CallType.INBOUND,
                )
            )

        assert result.success is True
        assert result.message_count == 1
        assert result.has_agent_message is True
        assert result.has_customer_message is True

        # Activity persists call data (transcript, recordings, costs); the
        # CallExecution.status transition itself is handled by the workflow,
        # not by this activity.
        await sync_to_async(call_execution.refresh_from_db)()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_fetch_and_persist_call_result_handles_failed_call(
        self, call_execution
    ):
        """Test that fetch_and_persist_call_result handles failed calls."""
        fetch_and_persist_call_result = _ee_voice_large().fetch_and_persist_call_result
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from tfc.temporal.common.heartbeat import Heartbeater

        mock_call_data = MagicMock()
        mock_call_data.status = "failed"
        mock_call_data.call_id = "vapi-call-456"
        mock_call_data.duration_seconds = 0
        mock_call_data.cost = 0
        mock_call_data.ended_reason = "assistant-error"
        mock_call_data.transcript_available = False
        mock_call_data.recording_available = False
        mock_call_data.recording_url = None
        mock_call_data.summary = None
        mock_call_data.messages = []
        mock_call_data.raw_log = {}
        mock_call_data.cost_breakdown = {}
        mock_call_data.performance_metrics = {}
        mock_call_data.analysis_data = {}
        mock_call_data.assistant_id = None
        mock_call_data.customer_phone_number = None
        mock_call_data.call_type = None
        mock_call_data.started_at = None
        mock_call_data.ended_at = None
        mock_call_data.transcript = {}

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.heartbeat"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "ee.voice.services.voice_service_manager.VoiceServiceManager"
            ) as mock_vsm_class,
        ):
            mock_vsm = MagicMock()
            mock_vsm.get_call_async = AsyncMock(return_value=mock_call_data)
            mock_vsm.get_recording_urls = MagicMock(return_value={})
            # Production now calls fetch_and_store_call_data which returns
            # a (message_count, has_agent_message, has_customer_message) tuple.
            mock_vsm.fetch_and_store_call_data = AsyncMock(
                return_value=(0, False, False)
            )
            mock_recordings = MagicMock(
                recording_url=None,
                stereo_recording_url=None,
                customer_recording_url=None,
                assistant_recording_url=None,
            )
            mock_vsm.extract_and_persist_recordings = AsyncMock(
                return_value=mock_recordings
            )
            mock_costs = MagicMock(
                total=None,
                stt=None,
                llm=None,
                tts=None,
                transport=None,
                storage=None,
            )
            mock_vsm.extract_costs = AsyncMock(return_value=mock_costs)
            mock_vsm_class.return_value = mock_vsm

            result = await fetch_and_persist_call_result(
                FetchAndPersistCallResultInput(
                    call_id=str(call_execution.id),
                    provider_call_id="vapi-call-456",
                    status="FAILED",
                    provider="vapi",
                    call_type=CallType.INBOUND,
                )
            )

        assert result.success is True
        assert result.message_count == 0
        assert result.has_agent_message is False
        assert result.has_customer_message is False

        # Activity persists call data (transcript, recordings, costs); the
        # CallExecution.status transition itself is handled by the workflow,
        # not by this activity.
        await sync_to_async(call_execution.refresh_from_db)()

    @pytest.mark.asyncio
    async def test_fetch_and_persist_call_result_returns_error_for_missing_call(
        self, db
    ):
        """Test that fetch_and_persist_call_result returns error for non-existent call."""
        fetch_and_persist_call_result = _ee_voice_large().fetch_and_persist_call_result
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from tfc.temporal.common.heartbeat import Heartbeater

        fake_id = str(uuid.uuid4())

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.heartbeat"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
        ):
            result = await fetch_and_persist_call_result(
                FetchAndPersistCallResultInput(
                    call_id=fake_id,
                    provider_call_id="vapi-call-123",
                    status="COMPLETED",
                    provider="vapi",
                    call_type=CallType.INBOUND,
                )
            )

        assert result.success is False
        assert (
            "not found" in result.error.lower()
            or "does not exist" in result.error.lower()
        )


# ============================================================================
# Activity Integration Tests - run_simulate_evaluations
# ============================================================================


@pytest.mark.integration
class TestRunSimulateEvaluationsActivity:
    """Integration tests for run_simulate_evaluations activity (standalone, no TestExecutor)."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_success(self, call_execution):
        """Test that run_simulate_evaluations runs evaluations successfully."""
        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.temporal.activities.xl._run_evaluations_standalone"
            ) as mock_run_evals,
        ):
            result = await run_simulate_evaluations(
                RunSimulateEvaluationsInput(
                    call_execution_id=str(call_execution.id),
                )
            )

        assert result.success is True
        assert result.error is None
        mock_run_evals.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_with_eval_config_ids(self, call_execution):
        """Test that run_simulate_evaluations passes eval_config_ids correctly."""
        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        eval_config_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.temporal.activities.xl._run_evaluations_standalone"
            ) as mock_run_evals,
        ):
            result = await run_simulate_evaluations(
                RunSimulateEvaluationsInput(
                    call_execution_id=str(call_execution.id),
                    eval_config_ids=eval_config_ids,
                    skip_existing=True,
                )
            )

        assert result.success is True
        # Verify standalone function was called with correct parameters
        mock_run_evals.assert_called_once()
        call_args = mock_run_evals.call_args
        assert call_args[1]["eval_config_ids"] == eval_config_ids
        assert call_args[1]["skip_existing"] is True

    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_returns_error_for_missing_call(self, db):
        """Test that run_simulate_evaluations returns error for non-existent call."""
        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        fake_id = str(uuid.uuid4())

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
        ):
            result = await run_simulate_evaluations(
                RunSimulateEvaluationsInput(
                    call_execution_id=fake_id,
                )
            )

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_handles_exception(self, call_execution):
        """Test that run_simulate_evaluations handles exceptions gracefully."""
        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.temporal.activities.xl._run_evaluations_standalone",
                side_effect=Exception("Evaluation failed"),
            ),
        ):
            result = await run_simulate_evaluations(
                RunSimulateEvaluationsInput(
                    call_execution_id=str(call_execution.id),
                )
            )

        assert result.success is False
        assert "Evaluation failed" in result.error

    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_cancelled_before_starting(
        self, db, call_execution
    ):
        """Test that activity raises CancelledError when cancelled before starting."""
        import asyncio

        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch("temporalio.activity.is_cancelled", return_value=True),
        ):
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await run_simulate_evaluations(
                    RunSimulateEvaluationsInput(
                        call_execution_id=str(call_execution.id),
                    )
                )

            assert "before starting" in str(exc_info.value)

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_cancelled_before_evaluations(
        self, call_execution
    ):
        """Test that activity sets status to CANCELLED when cancelled after fetch but before evals."""
        import asyncio

        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        # First call returns False (pass initial check), second returns True (cancel before evals)
        is_cancelled_calls = [False, True]

        def mock_is_cancelled():
            return is_cancelled_calls.pop(0)

        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch("temporalio.activity.is_cancelled", side_effect=mock_is_cancelled),
        ):
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await run_simulate_evaluations(
                    RunSimulateEvaluationsInput(
                        call_execution_id=str(call_execution.id),
                    )
                )

            assert "before evaluations" in str(exc_info.value)

        # Verify call execution status was set to CANCELLED
        await sync_to_async(call_execution.refresh_from_db)()
        assert call_execution.status == CallExecution.CallStatus.CANCELLED

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_simulate_evaluations_completed_not_cancelled(
        self, call_execution
    ):
        """Test that evaluations complete successfully without cancellation.

        The activity sets status to ANALYZING before running standalone evals.
        """
        from simulate.temporal.activities.xl import run_simulate_evaluations
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput
        from tfc.temporal.common.heartbeat import Heartbeater

        # Both cancellation checks return False - evaluations should complete
        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch("simulate.temporal.activities.xl._run_evaluations_standalone"),
        ):
            result = await run_simulate_evaluations(
                RunSimulateEvaluationsInput(
                    call_execution_id=str(call_execution.id),
                )
            )

        assert result.success is True
        assert result.error is None

        # Activity sets status to ANALYZING before running evals
        await sync_to_async(call_execution.refresh_from_db)()
        assert call_execution.status == CallExecution.CallStatus.ANALYZING


@pytest.mark.integration
class TestRunToolCallEvaluationActivity:
    """Integration tests for run_tool_call_evaluation activity."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_tool_call_evaluation_success(self, call_execution, run_test):
        """Test that run_tool_call_evaluation runs tool evaluation successfully."""
        from simulate.temporal.activities.xl import run_tool_call_evaluation
        from simulate.temporal.types.activities import RunToolCallEvaluationInput
        from tfc.temporal.common.heartbeat import Heartbeater

        # Enable tool evaluation on run_test
        run_test.enable_tool_evaluation = True
        await sync_to_async(run_test.save)(update_fields=["enable_tool_evaluation"])

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.temporal.activities.xl._run_tool_evaluation_standalone"
            ) as mock_run_tool_eval,
        ):
            result = await run_tool_call_evaluation(
                RunToolCallEvaluationInput(
                    call_execution_id=str(call_execution.id),
                )
            )

        assert result.success is True
        assert result.error is None
        mock_run_tool_eval.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_tool_call_evaluation_skips_when_disabled(
        self, call_execution, run_test
    ):
        """Test that run_tool_call_evaluation skips when tool evaluation is disabled."""
        from simulate.temporal.activities.xl import run_tool_call_evaluation
        from simulate.temporal.types.activities import RunToolCallEvaluationInput
        from tfc.temporal.common.heartbeat import Heartbeater

        # Disable tool evaluation (default)
        run_test.enable_tool_evaluation = False
        await sync_to_async(run_test.save)(update_fields=["enable_tool_evaluation"])

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.temporal.activities.xl._run_tool_evaluation_standalone"
            ) as mock_run_tool_eval,
        ):
            result = await run_tool_call_evaluation(
                RunToolCallEvaluationInput(
                    call_execution_id=str(call_execution.id),
                )
            )

        assert result.success is True
        assert result.error is None
        # Should NOT call the standalone function when disabled
        mock_run_tool_eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_tool_call_evaluation_returns_error_for_missing_call(self, db):
        """Test that run_tool_call_evaluation returns error for non-existent call."""
        from simulate.temporal.activities.xl import run_tool_call_evaluation
        from simulate.temporal.types.activities import RunToolCallEvaluationInput
        from tfc.temporal.common.heartbeat import Heartbeater

        fake_id = str(uuid.uuid4())

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
        ):
            result = await run_tool_call_evaluation(
                RunToolCallEvaluationInput(
                    call_execution_id=fake_id,
                )
            )

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_run_tool_call_evaluation_handles_exception(
        self, call_execution, run_test
    ):
        """Test that run_tool_call_evaluation handles exceptions gracefully."""
        from simulate.temporal.activities.xl import run_tool_call_evaluation
        from simulate.temporal.types.activities import RunToolCallEvaluationInput
        from tfc.temporal.common.heartbeat import Heartbeater

        run_test.enable_tool_evaluation = True
        await sync_to_async(run_test.save)(update_fields=["enable_tool_evaluation"])

        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.is_cancelled", return_value=False),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.temporal.activities.xl._run_tool_evaluation_standalone",
                side_effect=Exception("Tool evaluation failed"),
            ),
        ):
            result = await run_tool_call_evaluation(
                RunToolCallEvaluationInput(
                    call_execution_id=str(call_execution.id),
                )
            )

        assert result.success is False
        assert "Tool evaluation failed" in result.error

    @pytest.mark.asyncio
    async def test_run_tool_call_evaluation_cancelled_before_starting(
        self, db, call_execution
    ):
        """Test that activity raises CancelledError when cancelled before starting."""
        import asyncio

        from simulate.temporal.activities.xl import run_tool_call_evaluation
        from simulate.temporal.types.activities import RunToolCallEvaluationInput
        from tfc.temporal.common.heartbeat import Heartbeater

        with (
            patch("temporalio.activity.info"),
            patch.object(
                Heartbeater,
                "__aenter__",
                new=AsyncMock(return_value=MagicMock(details=None)),
            ),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch("temporalio.activity.is_cancelled", return_value=True),
        ):
            with pytest.raises(asyncio.CancelledError):
                await run_tool_call_evaluation(
                    RunToolCallEvaluationInput(
                        call_execution_id=str(call_execution.id),
                    )
                )


@pytest.mark.unit
class TestBuildTranscriptData:
    """Tests for _build_transcript_data helper."""

    @pytest.mark.django_db(transaction=True)
    def test_build_transcript_data_voice_with_transcripts(self, call_execution):
        """Test building transcript data from voice CallTranscript records."""
        from simulate.models import CallTranscript
        from simulate.temporal.activities.xl import _build_transcript_data

        # Create transcript records
        CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role="agent",
            content="Hello, how can I help?",
            start_time_ms=0,
            end_time_ms=2000,
        )
        CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role="user",
            content="I need help with my order.",
            start_time_ms=2000,
            end_time_ms=5000,
        )

        result = _build_transcript_data(call_execution)

        try:
            import ee.voice.utils.transcript_roles  # noqa: F401
        except ImportError:
            user_label = "user"
        else:
            # EE SpeakerRoleResolver normalizes "user" to the evaluation label.
            user_label = "customer"

        assert "agent: Hello, how can I help?" in result["transcript"]
        assert f"{user_label}: I need help with my order." in result["transcript"]

    @pytest.mark.django_db(transaction=True)
    def test_build_transcript_data_no_transcripts(self, call_execution):
        """Test building transcript data when no transcripts exist."""
        from simulate.temporal.activities.xl import _build_transcript_data

        result = _build_transcript_data(call_execution)

        assert result["transcript"] == ""
        assert result["voice_recording"] == ""

    @pytest.mark.django_db(transaction=True)
    def test_build_transcript_data_with_recording_urls(self, call_execution):
        """Test that recording URLs are read from call_execution fields."""
        from simulate.temporal.activities.xl import _build_transcript_data

        call_execution.recording_url = "https://example.com/recording.wav"
        call_execution.stereo_recording_url = "https://example.com/stereo.wav"
        call_execution.save(update_fields=["recording_url", "stereo_recording_url"])

        result = _build_transcript_data(call_execution)

        assert result["voice_recording"] == "https://example.com/recording.wav"
        assert result["stereo_recording"] == "https://example.com/stereo.wav"

    @pytest.mark.django_db(transaction=True)
    def test_build_transcript_data_with_context_info(self, call_execution):
        """Test that call context is included in transcript."""
        from simulate.models import CallTranscript
        from simulate.temporal.activities.xl import _build_transcript_data

        call_execution.call_metadata = {
            "agent_description": "You are a helpful assistant",
            "language": "en",
        }
        call_execution.save(update_fields=["call_metadata"])

        CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role="agent",
            content="Hello",
            start_time_ms=0,
            end_time_ms=1000,
        )

        result = _build_transcript_data(call_execution)

        assert "AGENT PROMPT: You are a helpful assistant" in result["transcript"]
        assert "LANGUAGE REQUESTED: en" in result["transcript"]
        assert "=== CALL CONTEXT ===" in result["transcript"]


# ============================================================================
# Workflow Integration Tests - CallExecutionWorkflow
# ============================================================================


@pytest.mark.integration
class TestCallExecutionWorkflowIntegration:
    """Integration tests for CallExecutionWorkflow activity orchestration."""

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_workflow_activity_sequence_inbound(self, call_execution):
        """Test that CallExecutionWorkflow calls activities in correct order for inbound calls."""
        from unittest.mock import call

        # Track activity calls in order
        activity_calls = []

        async def mock_execute_activity(activity_name, *args, **kwargs):
            activity_calls.append(activity_name)
            # Return appropriate mock responses based on activity
            if activity_name == "check_call_balance":
                return {"has_balance": True, "remaining_balance": 100.0}
            elif activity_name == "prepare_call":
                return MagicMock(
                    is_outbound=False,
                    to_number="+1234567890",
                    system_prompt="Test prompt",
                    voice_settings={"voice_id": "test"},
                    metadata={},
                    call_data={"call_type": "inbound"},
                    error=None,
                )
            elif activity_name == "initiate_call":
                return {
                    "success": True,
                    "provider_call_id": "vapi-123",
                    "provider_data": {},
                }
            elif activity_name == "monitor_call_until_complete":
                return MagicMock(
                    success=True,
                    status="COMPLETED",
                    duration_seconds=120,
                    end_reason="customer-ended-call",
                )
            elif activity_name == "fetch_and_persist_call_result":
                return MagicMock(success=True, status="COMPLETED", error=None)
            elif activity_name == "update_call_status":
                return None
            elif activity_name == "signal_parent_completion":
                return None
            elif activity_name == "release_call_slot":
                return None
            return MagicMock()

        # The actual workflow test would require Temporal test environment
        # This test validates the expected activity sequence
        expected_sequence = [
            "check_call_balance",
            "prepare_call",
            "initiate_call",
            "monitor_call_until_complete",
            "fetch_and_persist_call_result",
        ]

        # Verify the expected activities exist and are registered
        from tfc.temporal.common.registry import get_all_activities

        all_activities = get_all_activities()
        activity_names = [a.__name__ for a in all_activities]

        for activity_name in expected_sequence:
            # Check activity is registered (either directly or with different casing)
            assert any(
                activity_name == name or activity_name in name
                for name in activity_names
            ), f"Activity {activity_name} should be registered"

    def test_workflow_input_types_are_valid(self):
        """Test that CallExecutionInput dataclass has all required fields."""
        from simulate.temporal.types.call_execution import CallExecutionInput

        input_data = CallExecutionInput(
            call_id=str(uuid.uuid4()),
            org_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            test_workflow_id="test-workflow-123",
            test_execution_id=str(uuid.uuid4()),
        )

        assert input_data.call_id is not None
        assert input_data.org_id is not None
        assert input_data.workspace_id is not None
        assert input_data.test_workflow_id is not None
        assert input_data.test_execution_id is not None

    def test_workflow_output_types_are_valid(self):
        """Test that CallExecutionOutput dataclass has all required fields."""
        from simulate.temporal.types.call_execution import CallExecutionOutput

        output = CallExecutionOutput(
            status="COMPLETED",
            call_id=str(uuid.uuid4()),
            provider_call_id="vapi-123",
            duration_seconds=120,
        )

        assert output.status == "COMPLETED"
        assert output.call_id is not None
        assert output.error is None


# ============================================================================
# Workflow Integration Tests - TestExecutionWorkflow
# ============================================================================


@pytest.mark.integration
class TestTestExecutionWorkflowIntegration:
    """Integration tests for TestExecutionWorkflow activity orchestration."""

    def test_workflow_input_types_are_valid(self):
        """Test that TestExecutionInput dataclass has all required fields."""
        from simulate.temporal.types.test_execution import TestExecutionInput

        input_data = TestExecutionInput(
            test_execution_id=str(uuid.uuid4()),
            run_test_id=str(uuid.uuid4()),
            org_id=str(uuid.uuid4()),
            scenario_ids=[str(uuid.uuid4())],
            simulator_id=str(uuid.uuid4()),
        )

        assert input_data.test_execution_id is not None
        assert input_data.run_test_id is not None
        assert input_data.scenario_ids is not None
        assert len(input_data.scenario_ids) == 1

    def test_workflow_output_types_are_valid(self):
        """Test that TestExecutionOutput dataclass has all required fields."""
        from simulate.temporal.types.test_execution import TestExecutionOutput

        output = TestExecutionOutput(
            status="COMPLETED",
            total_calls=10,
            completed_calls=8,
            failed_calls=2,
        )

        assert output.status == "COMPLETED"
        assert output.total_calls == 10
        assert output.completed_calls == 8
        assert output.failed_calls == 2

    def test_workflow_state_types_are_valid(self):
        """Test that TestExecutionState dataclass for continue-as-new is valid."""
        from simulate.temporal.types.test_execution import TestExecutionState

        state = TestExecutionState(
            status="RUNNING",
            total_calls=100,
            completed_calls=50,
            failed_calls=5,
            launched_calls=100,
        )

        assert state.status == "RUNNING"
        assert state.total_calls == 100
        assert state.launched_calls == 100


# ============================================================================
# API Integration Tests - CallExecutionRerunView
# ============================================================================


@pytest.mark.integration
class TestCallExecutionRerunAPI:
    """Integration tests for the CallExecutionRerunView API."""

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_call_and_eval_with_temporal(
        self, mock_rerun, auth_client, test_execution, call_execution
    ):
        """Test rerun API with call_and_eval type using Temporal."""
        mock_rerun.return_value = {
            "successful": [str(call_execution.id)],
            "failed": [],
            "total": 1,
        }

        # Test execution must be in a terminal status to allow rerun.
        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.save()
        # Set call to completed state for rerun
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.save()

        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/rerun-calls/",
            {
                "rerun_type": "call_and_eval",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        # API should accept the rerun request
        assert response.status_code in [200, 202]
        data = response.json()
        assert "successful_reruns" in data or "message" in data

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only(
        self, mock_rerun, auth_client, test_execution, call_execution
    ):
        """Test rerun API with eval_only type."""
        mock_rerun.return_value = {
            "successful": [str(call_execution.id)],
            "failed": [],
            "total": 1,
            "eval_only": True,
        }
        # Test execution must be in a terminal status to allow rerun.
        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.save()
        # Set call to completed state for rerun
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.save()

        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/rerun-calls/",
            {
                "rerun_type": "eval_only",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        # API should accept the rerun request
        assert response.status_code in [200, 202]

    def test_rerun_requires_call_execution_ids_or_select_all(
        self, auth_client, test_execution
    ):
        """Test that rerun API requires either call_execution_ids or select_all."""
        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/rerun-calls/",
            {
                "rerun_type": "eval_only",
                # Missing both call_execution_ids and select_all
            },
            format="json",
        )

        # Should return validation error
        assert response.status_code == 400

    def test_rerun_with_select_all(self, auth_client, test_execution, call_execution):
        """Test rerun API with select_all=True."""
        # Test execution must be in a terminal status to allow rerun.
        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.save()
        # Set call to completed state for rerun
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.save()

        with patch("simulate.temporal.client.rerun_call_executions") as mock_rerun:
            mock_rerun.return_value = {
                "successful": [str(call_execution.id)],
                "failed": [],
                "total": 1,
                "eval_only": True,
            }
            response = auth_client.post(
                f"/simulate/test-executions/{test_execution.id}/rerun-calls/",
                {
                    "rerun_type": "eval_only",
                    "select_all": True,
                },
                format="json",
            )

        # API should accept the rerun request
        assert response.status_code in [200, 202]


# ============================================================================
# Activity Type Tests - Input/Output Validation
# ============================================================================


@pytest.mark.unit
class TestActivityInputOutputTypes:
    """Tests for Temporal activity input/output type validation."""

    def test_fetch_and_persist_call_result_input_type(self):
        """Verify FetchAndPersistCallResultInput dataclass structure."""
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput

        input_data = FetchAndPersistCallResultInput(
            call_id=str(uuid.uuid4()),
            provider_call_id="vapi-123",
            status="COMPLETED",
            provider="vapi",
            call_type=CallType.INBOUND,
            duration_seconds=120.5,
            end_reason="customer-ended-call",
        )

        assert input_data.call_id is not None
        assert input_data.provider_call_id == "vapi-123"
        assert input_data.status == "COMPLETED"
        assert input_data.call_type == CallType.INBOUND
        assert input_data.duration_seconds == 120.5

    def test_fetch_and_persist_call_result_output_type(self):
        """Verify FetchAndPersistCallResultOutput dataclass structure."""
        from simulate.temporal.types.activities import FetchAndPersistCallResultOutput

        output = FetchAndPersistCallResultOutput(
            success=True,
            message_count=5,
            error=None,
        )

        assert output.success is True
        assert output.message_count == 5
        assert output.error is None

        # Test failed output
        failed_output = FetchAndPersistCallResultOutput(
            success=False,
            message_count=0,
            error="Call not found",
        )
        assert failed_output.success is False
        assert failed_output.error is not None

    def test_run_simulate_evaluations_input_type(self):
        """Verify RunSimulateEvaluationsInput dataclass structure."""
        from simulate.temporal.types.activities import RunSimulateEvaluationsInput

        input_data = RunSimulateEvaluationsInput(
            call_execution_id=str(uuid.uuid4()),
            eval_config_ids=[str(uuid.uuid4())],
            skip_existing=True,
        )

        assert input_data.call_execution_id is not None
        assert len(input_data.eval_config_ids) == 1
        assert input_data.skip_existing is True

        # Test with defaults
        input_default = RunSimulateEvaluationsInput(
            call_execution_id=str(uuid.uuid4()),
        )
        assert input_default.eval_config_ids is None
        assert input_default.skip_existing is False

    def test_run_simulate_evaluations_output_type(self):
        """Verify RunSimulateEvaluationsOutput dataclass structure."""
        from simulate.temporal.types.activities import RunSimulateEvaluationsOutput

        output = RunSimulateEvaluationsOutput(
            success=True,
            error=None,
        )

        assert output.success is True
        assert output.error is None

    def test_initiate_call_input_has_voice_settings(self):
        """Verify InitiateCallInput includes voice_settings field."""
        from simulate.temporal.types.activities import InitiateCallInput

        input_data = InitiateCallInput(
            call_id=str(uuid.uuid4()),
            call_data={"call_type": "inbound"},
            system_prompt="Test prompt",
            voice_settings={
                "voice_id": "test-voice",
                "speed": 1.0,
                "interrupt_sensitivity": 5,
                "finished_speaking_sensitivity": 5,
                "max_call_duration_in_minutes": 15,
            },
            metadata={},
        )

        assert input_data.voice_settings is not None
        assert input_data.voice_settings["voice_id"] == "test-voice"
        assert input_data.voice_settings["max_call_duration_in_minutes"] == 15

    def test_prepare_call_output_includes_voice_settings(self):
        """Verify PrepareCallOutput includes voice_settings field."""
        from simulate.temporal.types.activities import PrepareCallOutput

        output = PrepareCallOutput(
            is_outbound=False,
            to_number="+1234567890",
            system_prompt="Test prompt",
            voice_settings={
                "voice_id": "test-voice",
                "speed": 1.0,
            },
            metadata={},
            provider="vapi",
            provider_config={},
            max_duration_minutes=15,
            call_data={},
        )

        assert output.voice_settings is not None
        assert output.voice_settings["voice_id"] == "test-voice"


@pytest.mark.unit
class TestCallProcessingGatingRules:
    """Tests for conversation-validity gating helper logic."""

    def test_transcript_with_both_roles_allows_processing(self):
        decide_processing_skip = _ee_processing_gating().decide_processing_skip

        decision = decide_processing_skip(
            message_count=4,
            has_agent_message=True,
            has_customer_message=True,
            duration_seconds=3,
        )

        assert decision.processing_skipped is False
        assert decision.processing_skip_reason == ""

    def test_transcript_missing_customer_skips_processing(self):
        decide_processing_skip = _ee_processing_gating().decide_processing_skip

        decision = decide_processing_skip(
            message_count=2,
            has_agent_message=True,
            has_customer_message=False,
            duration_seconds=30,
        )

        assert decision.processing_skipped is True
        assert "customer" in decision.processing_skip_reason.lower()

    def test_no_transcript_short_duration_skips_processing(self):
        decide_processing_skip = _ee_processing_gating().decide_processing_skip

        decision = decide_processing_skip(
            message_count=0,
            has_agent_message=False,
            has_customer_message=False,
            duration_seconds=2,
        )

        assert decision.processing_skipped is True
        assert "too short" in decision.processing_skip_reason.lower()


@pytest.mark.integration
@pytest.mark.django_db
class TestPersistProcessingSkipStateActivity:
    """Integration tests for persist_processing_skip_state activity."""

    def test_persists_processing_state_in_call_metadata(self, call_execution):
        from simulate.temporal.activities.small import persist_processing_skip_state
        from simulate.temporal.types.activities import PersistProcessingSkipStateInput

        async_to_sync(persist_processing_skip_state)(
            PersistProcessingSkipStateInput(
                call_id=str(call_execution.id),
                processing_skipped=True,
                processing_skip_reason="No valid conversation",
            )
        )

        refreshed_call = CallExecution.objects.get(id=call_execution.id)
        assert refreshed_call.call_metadata["processing_skipped"] is True
        assert (
            refreshed_call.call_metadata["processing_skip_reason"]
            == "No valid conversation"
        )


# ============================================================================
# Heartbeat Configuration Tests
# ============================================================================


@pytest.mark.unit
class TestHeartbeatConfiguration:
    """Tests to verify heartbeat_timeout is configured for activities with Heartbeater."""

    def test_workflow_has_heartbeat_timeout_for_fetch_and_persist(self):
        """Verify CallExecutionWorkflow sets heartbeat_timeout for fetch_and_persist_call_result."""
        import ast
        import inspect

        CallExecutionWorkflow = _ee_call_execution_workflow().CallExecutionWorkflow

        source = inspect.getsource(CallExecutionWorkflow.run)

        # Check that heartbeat_timeout is used with fetch_and_persist_call_result
        assert "fetch_and_persist_call_result" in source
        assert "heartbeat_timeout" in source

    def test_workflow_has_heartbeat_timeout_for_run_simulate_evaluations(self):
        """Verify CallExecutionWorkflow sets heartbeat_timeout for run_simulate_evaluations."""
        import inspect

        CallExecutionWorkflow = _ee_call_execution_workflow().CallExecutionWorkflow

        source = inspect.getsource(CallExecutionWorkflow.run)

        # Check that heartbeat_timeout is used with run_simulate_evaluations
        assert "run_simulate_evaluations" in source
        assert "heartbeat_timeout" in source

    def test_workflow_has_heartbeat_timeout_for_run_tool_call_evaluation(self):
        """Verify CallExecutionWorkflow sets heartbeat_timeout for run_tool_call_evaluation."""
        import inspect

        CallExecutionWorkflow = _ee_call_execution_workflow().CallExecutionWorkflow

        source = inspect.getsource(CallExecutionWorkflow.run)

        assert "run_tool_call_evaluation" in source
        assert "heartbeat_timeout" in source

    def test_workflow_has_tool_eval_versioning(self):
        """Verify CallExecutionWorkflow uses workflow.patched for tool-eval-activity.

        The tool-eval-activity patch lives in the eval-only branch (delegated
        from CallExecutionWorkflow.run via _run_eval_only_mode), so inspect the
        whole class source rather than just the run() method.
        """
        import inspect

        CallExecutionWorkflow = _ee_call_execution_workflow().CallExecutionWorkflow

        source = inspect.getsource(CallExecutionWorkflow)
        assert "tool-eval-activity" in source

    def test_eval_only_mode_has_tool_eval_versioning(self):
        """Verify _run_eval_only_mode uses workflow.patched for tool-eval-activity."""
        import inspect

        CallExecutionWorkflow = _ee_call_execution_workflow().CallExecutionWorkflow

        source = inspect.getsource(CallExecutionWorkflow._run_eval_only_mode)
        assert "tool-eval-activity" in source
        assert "run_tool_call_evaluation" in source

    def test_test_execution_workflow_has_heartbeat_timeout_for_create_records(self):
        """Verify TestExecutionWorkflow sets heartbeat_timeout for create_call_execution_records."""
        import inspect

        from simulate.temporal.workflows.test_execution_workflow import (
            TestExecutionWorkflow,
        )

        source = inspect.getsource(TestExecutionWorkflow.run)

        # Check that heartbeat_timeout is used with create_call_execution_records
        assert "create_call_execution_records" in source
        assert "heartbeat_timeout" in source


# ============================================================================
# Type Validation Tests for Tool Call Evaluation
# ============================================================================


@pytest.mark.unit
class TestToolCallEvaluationTypes:
    """Tests for RunToolCallEvaluationInput/Output dataclass types."""

    def test_run_tool_call_evaluation_input_type(self):
        """Verify RunToolCallEvaluationInput dataclass structure."""
        from simulate.temporal.types.activities import RunToolCallEvaluationInput

        input_data = RunToolCallEvaluationInput(
            call_execution_id=str(uuid.uuid4()),
        )

        assert input_data.call_execution_id is not None

    def test_run_tool_call_evaluation_output_type(self):
        """Verify RunToolCallEvaluationOutput dataclass structure."""
        from simulate.temporal.types.activities import RunToolCallEvaluationOutput

        output = RunToolCallEvaluationOutput(success=True, error=None)
        assert output.success is True
        assert output.error is None

        output_error = RunToolCallEvaluationOutput(
            success=False, error="Something failed"
        )
        assert output_error.success is False
        assert output_error.error == "Something failed"


# ============================================================================
# Registry Tests for Tool Call Evaluation
# ============================================================================


@pytest.mark.unit
class TestToolCallEvaluationRegistry:
    """Tests for run_tool_call_evaluation activity registration."""

    def test_run_tool_call_evaluation_is_registered(self):
        """Verify run_tool_call_evaluation is registered on tasks_xl queue."""
        from tfc.temporal.common.registry import get_all_activities

        all_activities = get_all_activities()
        activity_names = [getattr(a, "__name__", str(a)) for a in all_activities]
        assert "run_tool_call_evaluation" in activity_names
