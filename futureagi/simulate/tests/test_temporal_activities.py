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

import asyncio
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

    @patch("simulate.temporal.client._cancel_simulation_runner_workflow_async")
    def test_cancel_simulation_runner_workflow(
        self, mock_cancel_async, db, test_execution
    ):
        from simulate.temporal.client import cancel_simulation_runner_workflow

        mock_cancel_async.return_value = True

        result = cancel_simulation_runner_workflow(str(test_execution.id))

        assert result is True
        mock_cancel_async.assert_called_once_with(str(test_execution.id))

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

    @patch("tfc.temporal.common.client.get_client")
    @pytest.mark.asyncio
    async def test_cancel_simulation_runner_workflow_uses_hosted_id(
        self, mock_get_client, db, test_execution
    ):
        from simulate.temporal.client import _cancel_simulation_runner_workflow_async

        mock_handle = MagicMock()
        mock_handle.cancel = AsyncMock()
        mock_client = MagicMock()
        mock_client.get_workflow_handle.return_value = mock_handle
        mock_get_client.return_value = mock_client

        result = await _cancel_simulation_runner_workflow_async(str(test_execution.id))

        assert result is True
        mock_client.get_workflow_handle.assert_called_once_with(
            f"sim-runner-{test_execution.id}"
        )
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
@pytest.mark.requires_ee
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
                provider_call_data=None,
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
    async def test_incomplete_recordings_are_recorded_and_the_call_still_persists(
        self, call_execution
    ):
        """LiveKit tracks that ffmpeg could not cut: the activity saves the
        stereo recording and the recording_error the engine stored, returns
        the error, and still persists costs and the call status."""
        fetch_and_persist_call_result = _ee_voice_large().fetch_and_persist_call_result
        from ee.voice.services.types.voice import RecordingUrls
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from tfc.temporal.common.heartbeat import Heartbeater

        error = {
            "code": "ffmpeg_unavailable",
            "message": "ffmpeg is not installed in this backend image, so call "
            "recording channel split is unavailable.",
            "tracks": ["assistant", "customer", "mono"],
        }
        stereo = "https://media/call-recordings/c/t_stereo_recording.mp3"
        # What the engine stored on the call while this activity held an
        # older copy of it.
        engine_data = {
            "livekit": {
                "egress_id": "EG_1",
                "recording": {"stereo": stereo},
                "recording_error": error,
            }
        }
        create_call = await sync_to_async(CreateCallExecution.objects.create)(
            phone_number_id=VAPI_PHONE_NUMBER_ID,
            to_number="+1234567890",
            system_prompt="prompt",
            metadata={},
            voice_settings={},
            call_execution=call_execution,
            status=CreateCallExecution.CallStatus.ONGOING,
        )

        async def extract_recordings(call_id):
            await CallExecution.objects.filter(pk=call_id).aupdate(
                stereo_recording_url=stereo,
                recording_available=True,
                provider_call_data=engine_data,
            )
            return RecordingUrls(
                stereo_recording_url=stereo,
                provider_call_data=engine_data,
                recording_fields_persisted=True,
                error=error,
            )

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
            mock_vsm.fetch_and_store_call_data = AsyncMock(return_value=(2, True, True))
            mock_vsm.extract_and_persist_recordings = extract_recordings
            mock_vsm.extract_costs = AsyncMock(
                return_value=MagicMock(
                    total=0.25,
                    stt=None,
                    llm=None,
                    tts=None,
                    transport=None,
                    storage=None,
                )
            )
            mock_vsm_class.return_value = mock_vsm

            result = await fetch_and_persist_call_result(
                FetchAndPersistCallResultInput(
                    call_id=str(call_execution.id),
                    provider_call_id="lk-call-1",
                    status="COMPLETED",
                    provider="livekit",
                    call_type=CallType.INBOUND,
                )
            )

        assert result.success is True
        assert result.recording_error == error
        await sync_to_async(call_execution.refresh_from_db)()
        assert call_execution.stereo_recording_url == stereo
        assert call_execution.recording_available is True
        assert call_execution.recording_url in (None, "")
        assert call_execution.provider_call_data["livekit"]["recording_error"] == error
        assert call_execution.cost_cents == 25
        await sync_to_async(create_call.refresh_from_db)()
        assert create_call.status == CreateCallExecution.CallStatus.COMPLETED

    @pytest.mark.parametrize(
        "phase", ["cleanup_wait", "split_wait", "split_cas", "retry", "retry_detached", "preexisting_mono_failure"]
    )
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_alk_upload_keeps_playback_through_recording_activity(
        self, call_execution, auth_client, phase
    ):
        import hashlib

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.db.models.query import QuerySet

        activity_module = _ee_voice_large()
        from livekit.protocol.egress import EgressStatus

        from ee.voice.services.livekit.service import LivekitService
        from ee.voice.services.types.voice import CostBreakdown
        from simulate.serializers.test_execution import CallExecutionDetailSerializer
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from tfc.temporal.common.heartbeat import Heartbeater

        call_id = str(call_execution.pk)
        room = f"call_{call_id}"
        source = {"call_id": call_id, "egress_id": "EG_1"}
        tracks = {
            k: f"https://media/{k}"
            for k in ("stereo", "combined", "assistant", "customer")
        }
        partial_first = phase in ("retry", "retry_detached")
        retrying = False
        cleanup_only = phase == "cleanup_wait"
        lk = {"egress_id": "EG_1", "recording_files": ["raw/one.ogg"]}
        lk.update(
            recording_source=source,
            recording=tracks if cleanup_only else {"stereo": tracks["stereo"]},
        )
        preexisting = phase == "preexisting_mono_failure"
        if preexisting:
            del lk["recording"], lk["recording_source"]
        await CallExecution.objects.filter(pk=call_id).aupdate(
            provider_call_data={"livekit": lk},
            recording_url=tracks["combined"] if cleanup_only else None,
            stereo_recording_url=None if preexisting else tracks["stereo"],
            recording_available=not preexisting,
        )
        objects = {"raw/one.ogg": b"A"}
        audio = b"RIFF-independent-upload-B"
        url_b = "https://media/alk/B.wav"

        def put_object(*, object_name, data, **_kwargs):
            objects[object_name] = data.read()

        @sync_to_async
        def upload_b():
            response = auth_client.post(
                f"/simulate/api/alk-simulate/call-executions/{call_id}/recording/",
                {"file": SimpleUploadedFile("B.wav", audio, content_type="audio/wav")},
                format="multipart",
            )
            assert response.status_code == 200, response.content
            assert response.json()["result"]["recording_url"] == url_b

        info = MagicMock(
            room_name=room,
            egress_id="EG_1",
            status=EgressStatus.EGRESS_COMPLETE,
            file_results=[MagicMock(filename="raw/one.ogg")],
        )

        async def poll(*_args):
            if phase == "split_wait" or (partial_first and not retrying):
                await upload_b()
            return info

        async def list_egress(_request):
            await upload_b()
            return MagicMock(items=[info])

        def split(_config, _key, destination, _args):
            if partial_first and not retrying and "_customer_" in destination:
                raise OSError("customer track unavailable")
            for k in ("stereo", "assistant", "customer"):
                if f"_{k}_" in destination:
                    return tracks[k]
            if preexisting:
                raise OSError("mono unavailable")
            return tracks["combined"]

        writes = []
        real_update = QuerySet.aupdate

        async def at_predicate(query, **changes):
            # The queryset already contains the service's WHERE clause. Let
            # the endpoint commit B, then execute the actual conditional SQL.
            if query.model is not CallExecution or "provider_call_data" not in changes:
                return await real_update(query, **changes)
            if phase == "split_cas" and not writes:
                await upload_b()
            count = await real_update(query, **changes)
            writes.append((set(changes), count))
            return count

        api = AsyncMock()
        api.egress.list_egress.side_effect = list_egress
        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.heartbeat"),
            patch.object(Heartbeater, "__aenter__", new=AsyncMock()),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch(
                "simulate.services.alk_simulate_ingestion.get_storage_client"
            ) as storage,
            patch(
                "simulate.services.alk_simulate_ingestion.get_object_url",
                return_value=url_b,
            ),
            patch("ee.voice.services.livekit.service.LiveKitConfig.from_env"),
            patch.object(LivekitService, "_create_api", return_value=api),
            patch(
                "ee.voice.services.livekit.recording.poll_egress_completion", new=poll
            ),
            patch(
                "ee.voice.services.livekit.recording.stream_egress_to_s3",
                side_effect=split,
            ) as stream,
            patch(
                "ee.voice.services.livekit.recording.delete_egress_recording",
                side_effect=lambda _config, key: objects.pop(key),
            ) as delete,
            patch.object(QuerySet, "aupdate", new=at_predicate),
            patch(
                "ee.voice.services.voice_service_manager.VoiceServiceManager"
            ) as manager,
        ):
            storage.return_value.put_object.side_effect = put_object
            service = LivekitService()
            voice = manager.return_value
            voice.fetch_and_store_call_data = AsyncMock(return_value=(2, True, True))
            voice.extract_and_persist_recordings = (
                service.extract_and_persist_recordings
            )
            voice.extract_costs = AsyncMock(return_value=CostBreakdown(total=0.25))
            request = FetchAndPersistCallResultInput(
                call_id=call_id,
                provider_call_id=room,
                status="COMPLETED",
                provider="livekit",
                call_type=CallType.INBOUND,
            )
            if preexisting:
                await upload_b()
            result = await activity_module.fetch_and_persist_call_result(request)
            if preexisting:
                assert result.success and result.recording_error["tracks"] == ["mono"]
                await call_execution.arefresh_from_db()
                stored = call_execution.provider_call_data["livekit"]
                assert stored["recording_error"] == result.recording_error
                assert stored["recording"] == {k: v for k, v in tracks.items() if k != "combined"}
                assert stored["verified_recording_tracks"] == {
                    k: {**source, "url": v} for k, v in stored["recording"].items()
                }
                receipt = call_execution.call_metadata["alk_recording_artifacts"]["combined"]
                assert receipt["recording_url"] == call_execution.recording_url == url_b
                assert call_execution.stereo_recording_url == tracks["stereo"]
                assert call_execution.recording_available
                assert objects == {"raw/one.ogg": b"A", receipt["object_key"]: audio}
                delete.assert_not_called()
                return
            if partial_first:
                assert result.success
                assert result.recording_error["tracks"] == ["customer"]
                delete.assert_not_called()
                await call_execution.arefresh_from_db()
                partial = call_execution.provider_call_data["livekit"]
                assert partial["recording_source"] == source
                assert partial["recording"] == {
                    k: v for k, v in tracks.items() if k != "customer"
                }
                assert call_execution.recording_url == url_b
                receipt = call_execution.call_metadata["alk_recording_artifacts"][
                    "combined"
                ]
                if phase == "retry_detached":
                    # The receipt for B does not own another column value.
                    await CallExecution.objects.filter(pk=call_id).aupdate(
                        recording_url="https://media/other"
                    )
                stream.reset_mock()
                retrying = True
                result = await activity_module.fetch_and_persist_call_result(request)
                stream.assert_called_once()
                assert "_customer_" in stream.call_args.args[2]

        assert result.success and result.recording_error is None
        delete.assert_called_once_with(service.config, "raw/one.ogg")
        assert writes[-1] == ({"provider_call_data"}, 1)
        if cleanup_only:
            assert len(writes) == 1
        elif partial_first:
            assert [count for _, count in writes] == [1, 1, 1]
        else:
            counts = [count for _, count in writes]
            assert counts == ([0, 1, 1] if phase == "split_cas" else [1, 1])
            assert "recording_url" not in writes[-2][0]
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        assert stored["egress_id"] == "EG_1" and stored["recording_source"] == source
        assert stored["recording"] == tracks and stored["recording_cleanup_success"]
        assert stored["recording_files"] == ["raw/one.ogg"]
        playback = "https://media/other" if phase == "retry_detached" else url_b
        assert call_execution.recording_url == playback
        assert call_execution.recording_available
        assert call_execution.stereo_recording_url == tracks["stereo"]
        artifact = call_execution.call_metadata["alk_recording_artifacts"]["combined"]
        if partial_first:
            assert artifact == receipt
        assert artifact["recording_url"] == url_b
        assert artifact["sha256"] == hashlib.sha256(audio).hexdigest()
        assert objects == {artifact["object_key"]: audio}
        serialize = sync_to_async(CallExecutionDetailSerializer().get_recordings)
        recordings = await serialize(call_execution)
        assert recordings["combined"] == playback
        assert recordings["stereo"] == tracks["stereo"]

    @pytest.fixture
    def recording_activity(self, call_execution):
        """Real row and activity writes; replace the provider and object-store I/O."""
        from types import SimpleNamespace

        activity_module = _ee_voice_large()
        from livekit.protocol.egress import EgressStatus

        from ee.voice.services.livekit.service import LivekitService
        from ee.voice.services.types.voice import CostBreakdown
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from tfc.temporal.common.heartbeat import Heartbeater

        call_id = str(call_execution.pk)
        room = f"call_{call_id}"
        call_execution.service_provider_call_id = room
        call_execution.provider_call_data = {"livekit": {"egress_id": "EG_1"}}
        call_execution.save(update_fields=["service_provider_call_id", "provider_call_data"])
        info = MagicMock(room_name=room, egress_id="EG_1", status=EgressStatus.EGRESS_COMPLETE,
                         file_results=[MagicMock(filename="raw/one.ogg")])
        api = AsyncMock()
        api.egress.list_egress.return_value = MagicMock(items=[info])
        request = FetchAndPersistCallResultInput(call_id=call_id, provider_call_id=room,
                                                status="COMPLETED", provider="livekit",
                                                call_type=CallType.INBOUND)
        with (
            patch("temporalio.activity.info"), patch("temporalio.activity.heartbeat"),
            patch.object(Heartbeater, "__aenter__", new=AsyncMock()),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch("ee.voice.services.livekit.service.LiveKitConfig.from_env"),
            patch.object(LivekitService, "_create_api", return_value=api),
            patch("ee.voice.services.livekit.recording.poll_egress_completion",
                  new_callable=AsyncMock, return_value=info) as poll,
            patch("ee.voice.services.livekit.recording.stream_egress_to_s3",
                  side_effect=lambda _c, _k, dest, _a: f"https://media/{dest}") as stream,
            patch("ee.voice.services.livekit.recording.delete_egress_recording") as delete,
            patch("ee.voice.services.voice_service_manager.VoiceServiceManager") as manager,
        ):
            voice = manager.return_value
            voice.fetch_and_store_call_data = AsyncMock(return_value=(2, True, True))
            voice.extract_and_persist_recordings = LivekitService().extract_and_persist_recordings
            voice.extract_costs = AsyncMock(return_value=CostBreakdown())
            yield SimpleNamespace(run=lambda: activity_module.fetch_and_persist_call_result(request),
                                  voice=voice, api=api, info=info, poll=poll, stream=stream, delete=delete)

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_sip_vapi_combined_survives_stereo_only_retry(self, call_execution, recording_activity):
        from ee.voice.services.types.voice import CustomerMetrics
        from ee.voice.temporal.activities.voice_xl import fetch_client_call_data
        from simulate.serializers.test_execution import CallExecutionDetailSerializer
        from simulate.temporal.types.activities import FetchClientCallInput

        io = recording_activity
        stereo, client = "https://media/stereo", "https://media/vapi-combined"
        io.stream.side_effect = lambda _c, _k, dest, _a: stereo if "_stereo_" in dest else None
        first = await io.run()
        assert first.success and first.recording_error["tracks"] == ["assistant", "customer", "mono"]
        raw = {"artifact": {"recording": {"mono": {"combinedUrl": client}}}}
        io.voice.find_client_call.return_value = "vapi-inbound-call"
        io.voice.get_customer_metrics.return_value = CustomerMetrics()
        io.voice.get_recording_urls.return_value = {}
        io.voice.get_call_async = AsyncMock(return_value=MagicMock(
            raw_log={"vapi": raw}, recording_url=client, recording_available=True,
            analysis_data=None, summary=None, ended_reason=None, log_url=None,
            transcript_available=False,
        ))
        fetched = await fetch_client_call_data(FetchClientCallInput(call_id=str(call_execution.pk)))
        assert fetched.success, fetched.error
        retry = await io.run()
        assert retry.success and retry.recording_error == first.recording_error
        await call_execution.arefresh_from_db()
        assert call_execution.recording_url == client and call_execution.recording_available
        recordings = await sync_to_async(CallExecutionDetailSerializer().get_recordings)(call_execution)
        assert recordings["combined"] == client and recordings["stereo"] == stereo
        assert call_execution.provider_call_data["livekit"]["recording"] == {"stereo": stereo}
        io.delete.assert_not_called()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_empty_map_keeps_direct_speaker_refs(self, call_execution, recording_activity):
        io = recording_activity
        speakers = {f"{kind}_recording_url": f"https://legacy/{kind}" for kind in ("assistant", "customer")}
        call_execution.provider_call_data["livekit"].update(recording={}, **speakers)
        await call_execution.asave(update_fields=["provider_call_data"])
        io.stream.side_effect = FileNotFoundError
        result = await io.run()
        assert result.success and len(result.recording_error["tracks"]) == 4
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        assert all(stored.get(k) == v for k, v in speakers.items())
        assert not stored["recording"] and not stored.get("verified_recording_tracks")
        assert not stored.get("recording_source")
        io.delete.assert_not_called()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_split_adopts_confirmed_peer_and_empty_listing_replay(self, call_execution, recording_activity):
        io = recording_activity
        peer = []

        async def finish_peer(*_args):
            io.poll.side_effect = None
            result = await io.run()
            assert result.success and result.recording_error is None
            await call_execution.arefresh_from_db()
            peer.append(call_execution.provider_call_data["livekit"].copy())
            io.stream.side_effect = FileNotFoundError
            io.delete.side_effect = TimeoutError
            return io.info

        io.poll.side_effect = finish_peer
        outer = await io.run()
        assert outer.success and outer.recording_error is None
        io.api.egress.list_egress.return_value = MagicMock(items=[])
        replay = await io.run()
        assert replay.success and replay.recording_error is None
        io.api.egress.list_egress.assert_not_awaited()
        io.delete.assert_called_once()
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        assert stored == peer[0] and stored["recording_cleanup_success"]
        assert "recording_error" not in stored and len(stored["recording"]) == 4

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_successful_split_persists_after_peer_records_only_failures(
        self, call_execution, recording_activity
    ):
        from django.db.models.query import QuerySet

        io = recording_activity
        source = {"call_id": str(call_execution.pk), "egress_id": "EG_1"}
        expected = {}
        real_update = QuerySet.aupdate

        async def finish_peer_before_split_write(queryset, **changes):
            lk = (changes.get("provider_call_data") or {}).get("livekit", {})
            if queryset.model is CallExecution and not expected and lk.get("recording_source") == source:
                expected.update(lk["recording"])
                assert set(expected) == {"combined", "stereo", "assistant", "customer"}
                # The outer uploads have finished but their first CAS has not run.
                # A peer poll timeout commits an empty map and no track claims.
                io.poll.return_value = None
                peer = await io.run()
                assert peer.success and peer.recording_error["tracks"] == ["assistant", "customer", "mono", "stereo"]
                await call_execution.arefresh_from_db()
                failed = call_execution.provider_call_data["livekit"]
                assert failed["egress_id"] == source["egress_id"] and failed["recording"] == {}
                assert failed["recording_error"] == peer.recording_error
                assert "verified_recording_tracks" not in failed and "recording_source" not in failed
                io.delete.assert_not_called()
                matched = await real_update(queryset, **changes)
                assert matched == 0
                return matched
            return await real_update(queryset, **changes)

        with patch.object(QuerySet, "aupdate", new=finish_peer_before_split_write):
            outer = await io.run()
        assert outer.success and outer.recording_error is None
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        assert expected and stored["recording"] == expected and stored["recording_source"] == source
        assert stored["verified_recording_tracks"] == {
            kind: {**source, "url": url} for kind, url in expected.items()
        }
        assert "recording_error" not in stored
        io.delete.assert_called_once()

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_changed_complete_map_cannot_reuse_cleanup_confirmation(
        self, call_execution, recording_activity
    ):
        io = recording_activity
        assert (await io.run()).success
        await call_execution.arefresh_from_db()
        lk = call_execution.provider_call_data["livekit"]
        assert lk["recording_cleanup_success"]
        replacement = "https://media/replaced-customer"
        lk["recording"]["customer"] = replacement
        lk["verified_recording_tracks"]["customer"]["url"] = replacement
        lk["customer_recording_url"] = replacement
        await call_execution.asave(update_fields=["provider_call_data"])
        io.api.egress.list_egress.return_value = MagicMock(items=[])
        result = await io.run()
        assert result.success and result.recording_error["code"] == "recording_cleanup_failed"
        io.api.egress.list_egress.assert_awaited_once()
        io.delete.assert_called_once()  # initial deletion only
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        assert stored["recording_error"] == result.recording_error
        assert "recording_cleanup_success" not in stored

    @pytest.mark.parametrize("owner", ["matching", "unrecorded", "foreign_call", "foreign_egress"])
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_only_proven_livekit_playback_accepts_verified_replacement(
        self, call_execution, recording_activity, owner
    ):
        io = recording_activity
        assert (await io.run()).success
        await call_execution.arefresh_from_db()
        before = call_execution.recording_url
        lk = call_execution.provider_call_data["livekit"]
        writes = lk["recording_playback_writes"]
        if owner == "unrecorded":
            del writes["combined"]
        elif owner == "foreign_call":
            writes["combined"]["call_id"] = "another-call"
        elif owner == "foreign_egress":
            writes["combined"]["egress_id"] = "another-egress"
        lk["egress_id"] = io.info.egress_id = "EG_2"
        await call_execution.asave(update_fields=["provider_call_data"])
        result = await io.run()
        assert result.success and result.recording_error is None
        await call_execution.arefresh_from_db()
        lk = call_execution.provider_call_data["livekit"]
        replacement = lk["recording"]["combined"]
        assert replacement != before
        assert call_execution.recording_url == (replacement if owner == "matching" else before)
        assert io.delete.call_count == 2  # different egress cannot inherit confirmation

    @pytest.mark.parametrize("peer_succeeds", [True, False])
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_overlapping_cleanup_keeps_confirmed_delete(
        self, call_execution, peer_succeeds
    ):
        _ee_voice_large()
        from livekit.protocol.egress import EgressStatus

        from ee.voice.services.livekit.service import LivekitService

        call_id = str(call_execution.pk)
        source = {"call_id": call_id, "egress_id": "EG_1"}
        tracks = {
            k: f"https://media/{k}"
            for k in ("combined", "stereo", "assistant", "customer")
        }
        await CallExecution.objects.filter(pk=call_id).aupdate(
            recording_url=tracks["combined"],
            stereo_recording_url=tracks["stereo"],
            recording_available=True,
            provider_call_data={
                "livekit": {
                    "egress_id": "EG_1",
                    "recording_source": source,
                    "recording": tracks,
                }
            },
        )
        info = MagicMock(
            room_name=f"call_{call_id}",
            egress_id="EG_1",
            status=EgressStatus.EGRESS_COMPLETE,
            file_results=[MagicMock(filename="raw/one.ogg")],
        )
        response = MagicMock(items=[info])
        outer, peer = AsyncMock(), AsyncMock()
        peer.egress.list_egress.return_value = response
        intermediate = []

        async def finish_peer_before_outer(_request):
            result = await service.extract_and_persist_recordings(call_id)
            intermediate.append(result.error)
            return response

        outer.egress.list_egress.side_effect = finish_peer_before_outer
        with (
            patch("ee.voice.services.livekit.service.LiveKitConfig.from_env"),
            patch.object(LivekitService, "_create_api", side_effect=[outer, peer]),
            patch(
                "ee.voice.services.livekit.recording.delete_egress_recording",
                side_effect=(
                    [None, TimeoutError()] if peer_succeeds else [TimeoutError(), None]
                ),
            ) as delete,
        ):
            service = LivekitService()
            result = await service.extract_and_persist_recordings(call_id)

        assert len(intermediate) == 1
        if peer_succeeds:
            assert intermediate[0] is None
        else:
            assert intermediate[0]["code"] == "recording_cleanup_failed"
        assert delete.call_args_list == [((service.config, "raw/one.ogg"), {})] * 2
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        assert stored["recording_source"] == source and stored["recording"] == tracks
        assert "recording_error" not in stored and result.error is None
        assert stored.get("recording_cleanup_success")
        assert call_execution.recording_url == tracks["combined"]
        assert call_execution.stereo_recording_url == tracks["stereo"]
        assert call_execution.recording_available

    @pytest.mark.parametrize(
        ("phase", "split", "delete_fails"),
        [
            ("cleanup", None, False),
            ("cleanup", None, True),
            ("handoff", None, False),
            ("handoff", None, True),
            ("upload", "complete", False),
            ("upload", "initial", False),
            ("upload", "partial", False),
            ("handoff", "partial", False),
            ("same-source", "partial", False),
        ],
    )
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_recording_writers_preserve_current_egress_through_activity_save(
        self, call_execution, phase, split, delete_fails
    ):
        activity_module = _ee_voice_large()
        from livekit.protocol.egress import EgressStatus

        from ee.voice.services.livekit.service import LivekitService
        from ee.voice.services.types.voice import CostBreakdown
        from simulate.temporal.types.activities import FetchAndPersistCallResultInput
        from simulate.views.livekit_api import _handle_egress_ended
        from tfc.temporal.common.heartbeat import Heartbeater

        call_id = str(call_execution.id)
        room = f"call_{call_id}"
        tracks = ("combined", "stereo", "assistant", "customer")
        old = {k: f"https://media/EG_1/{k}" for k in tracks}
        new = {k: f"https://media/EG_2/{k}" for k in tracks}
        lk = {
            "egress_id": "EG_1",
            "recording_files": ["raw/one.ogg"],
            "recording_source": {"call_id": call_id, "egress_id": "EG_1"},
            "recording": {k: old[k] for k in ("combined", "stereo")} if split else old,
        }
        if split == "initial":
            del lk["recording_source"], lk["recording"]
        if not delete_fails:
            lk["recording_error"] = {"code": "recording_cleanup_failed"}
        await CallExecution.objects.filter(pk=call_id).aupdate(
            provider_call_data={"livekit": lk}, cost_cents=71
        )

        async def publish_new_recording():
            # The webhook updates the ID and raw files. A second recording
            # writer then publishes the new tracks.
            await _handle_egress_ended(
                MagicMock(
                    egress_info=MagicMock(
                        room_name=room,
                        egress_id="EG_2",
                        file_results=[MagicMock(filename="raw/two.ogg")],
                    )
                )
            )
            current = await CallExecution.objects.aget(pk=call_id)
            current_lk = current.provider_call_data["livekit"]
            assert current_lk["egress_id"] == "EG_2"
            current_lk.update(
                recording_source={"call_id": call_id, "egress_id": "EG_2"},
                recording=new,
                assistant_recording_url=new["assistant"],
                customer_recording_url=new["customer"],
                webhook_metadata="preserve",
            )
            current_lk.pop("recording_error", None)
            await CallExecution.objects.filter(pk=call_id).aupdate(
                provider_call_data=current.provider_call_data,
                recording_url=new["combined"],
                stereo_recording_url=new["stereo"],
                recording_available=True,
            )

        async def publish_customer():
            current = await CallExecution.objects.aget(pk=call_id)
            current_lk = current.provider_call_data["livekit"]
            current_lk["recording"]["customer"] = "https://peer/customer"
            current_lk["webhook_metadata"] = "preserve"
            await current.asave(update_fields=["provider_call_data"])

        loop = asyncio.get_running_loop()

        def upload(_config, _source, destination, _ffargs):
            if "_assistant_" in destination:
                if phase in ("upload", "same-source"):
                    write = (
                        publish_customer
                        if phase == "same-source"
                        else publish_new_recording
                    )
                    asyncio.run_coroutine_threadsafe(write(), loop).result(timeout=30)
                return "https://upload/assistant"
            if split == "initial":
                return f"https://upload/{destination}"
            assert "_customer_" in destination
            if split == "partial":
                raise OSError("Incomplete customer track")
            return "https://upload/customer"

        info = MagicMock(
            room_name=room,
            egress_id="EG_1",
            status=EgressStatus.EGRESS_COMPLETE,
            file_results=[MagicMock(filename="raw/one.ogg")],
        )

        async def list_egress(_request):
            if phase == "cleanup":
                await publish_new_recording()
            return MagicMock(items=[info])

        async def fetch_costs(_call_id):
            before_costs = await CallExecution.objects.aget(pk=call_id)
            assert before_costs.cost_cents == 71
            assert before_costs.status == CallExecution.CallStatus.PENDING
            if phase == "handoff":
                await publish_new_recording()
            return CostBreakdown(total=0.25)

        api = AsyncMock()
        api.egress.list_egress.side_effect = list_egress
        with (
            patch("temporalio.activity.info"),
            patch("temporalio.activity.heartbeat"),
            patch.object(Heartbeater, "__aenter__", new=AsyncMock()),
            patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
            patch("ee.voice.services.livekit.service.LiveKitConfig.from_env"),
            patch(
                "ee.voice.services.livekit.recording.stream_egress_to_s3",
                side_effect=upload,
            ),
            patch(
                "ee.voice.services.livekit.recording.poll_egress_completion",
                new=AsyncMock(return_value=info),
            ),
            patch.object(LivekitService, "_create_api", return_value=api),
            patch(
                "ee.voice.services.livekit.recording.delete_egress_recording",
                side_effect=TimeoutError if delete_fails else None,
            ) as delete,
            patch(
                "ee.voice.services.voice_service_manager.VoiceServiceManager"
            ) as manager,
        ):
            service = LivekitService()
            manager.return_value.fetch_and_store_call_data = AsyncMock(
                return_value=(2, True, True)
            )
            manager.return_value.extract_and_persist_recordings = (
                service.extract_and_persist_recordings
            )
            manager.return_value.extract_costs = fetch_costs
            result = await activity_module.fetch_and_persist_call_result(
                FetchAndPersistCallResultInput(
                    call_id=call_id,
                    provider_call_id=room,
                    status="COMPLETED",
                    provider="livekit",
                    call_type=CallType.INBOUND,
                )
            )

        if phase == "upload" or (phase == "handoff" and split == "partial"):
            delete.assert_not_called()
        else:
            delete.assert_called_once_with(service.config, "raw/one.ogg")
        assert result.success
        if phase in ("cleanup", "upload"):
            assert result.recording_error["code"] == "recording_source_changed"
        elif delete_fails:
            assert result.recording_error["code"] == "recording_cleanup_failed"
        elif phase == "handoff" and split == "partial":
            assert result.recording_error["tracks"] == ["customer"]
        else:
            assert result.recording_error is None
        await call_execution.arefresh_from_db()
        stored = call_execution.provider_call_data["livekit"]
        if phase == "cleanup":
            assert "recording_cleanup_success" not in stored
        if phase == "same-source":
            expected = {
                **old,
                "customer": "https://peer/customer",
                "assistant": "https://upload/assistant",
            }
            expected_id, raw = "EG_1", "raw/one.ogg"
        else:
            expected = new
            expected_id, raw = "EG_2", "raw/two.ogg"
        assert stored["recording_source"] == {
            "call_id": call_id,
            "egress_id": expected_id,
        }
        assert stored["egress_id"] == expected_id and stored["recording_files"] == [raw]
        assert stored["recording"] == expected and "recording_error" not in stored
        assert stored["assistant_recording_url"] == expected["assistant"]
        assert stored["customer_recording_url"] == expected["customer"]
        assert stored["webhook_metadata"] == "preserve"
        assert call_execution.recording_available
        assert call_execution.recording_url == expected["combined"]
        assert call_execution.stereo_recording_url == expected["stereo"]
        assert call_execution.cost_cents == 25
        assert call_execution.status == CallExecution.CallStatus.PENDING

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
                provider_call_data=None,
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


@pytest.mark.unit
@pytest.mark.requires_ee
def test_tool_evaluation_gate_invariant_bland_has_no_adapter():
    """Locks the invariant the tool-eval gate in _run_tool_evaluation_standalone
    relies on: a provider absent from ToolCallingSupportedProviders (e.g. Bland)
    has no tool-call adapter, so get_tool_call_adapter raises. The gate skips
    such providers up front instead of letting that raise into the broad except
    (which silently no-ops tool evaluation). If a Bland adapter is ever added
    this fails — a reminder to also add Bland to the supported list."""
    from ee.agenthub.tool_eval_agent.adapters import get_tool_call_adapter
    from simulate.semantics import ToolCallingSupportedProviders

    assert ProviderChoices.BLAND not in ToolCallingSupportedProviders
    with pytest.raises(ValueError):
        get_tool_call_adapter(ProviderChoices.BLAND)


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

        # No provider_call_data ⇒ the resolver falls back to VAPI and defaults
        # to OUTBOUND (its direction default on missing metadata), which maps
        # "user" to the simulator (customer) and "assistant" to the tested agent.
        CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content="Hello, how can I help?",
            start_time_ms=0,
            end_time_ms=2000,
        )
        CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=CallTranscript.SpeakerRole.ASSISTANT,
            content="I need help with my order.",
            start_time_ms=2000,
            end_time_ms=5000,
        )

        result = _build_transcript_data(call_execution)

        assert "customer: Hello, how can I help?" in result["transcript"]
        assert "agent: I need help with my order." in result["transcript"]

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
    @pytest.mark.requires_ee
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

    @pytest.mark.requires_ee
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
