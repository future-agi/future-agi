"""
API tests for Test Execution Rerun endpoints.

Tests cover:
- CallExecutionRerunView: POST /simulate/test-executions/<uuid>/rerun-calls/
- TestExecutionRerunView: POST /simulate/run-tests/<uuid>/rerun-test-executions/
"""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError
from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    HostedHarnessJob,
    Scenarios,
    SimulateEvalConfig,
)
from simulate.models.run_test import CreateCallExecution, RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import (
    CallExecution,
    CallExecutionSnapshot,
    EvalExplanationSummaryStatus,
    TestExecution,
)
from simulate.services.harness_evals import (
    outputs_for_eval_rerun,
    platform_graded_evals,
)
from simulate.services.harness_run_evals import queue_eval_for_finished_calls
from simulate.utils.processing_outcomes import build_skipped_eval_output_payload

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
def test_execution(db, run_test, simulator_agent, agent_definition):
    """Create a test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    """Create a completed call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-123",
        eval_outputs={"eval1": {"score": 0.9}},
        call_metadata={
            "base_prompt": "You are a test agent",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )


@pytest.fixture
def call_execution_2(db, test_execution, scenario):
    """Create a second completed call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-456",
        eval_outputs={"eval1": {"score": 0.7}},
        call_metadata={
            "base_prompt": "You are a test agent",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )


@pytest.fixture
def test_execution_2(db, run_test, simulator_agent, agent_definition):
    """Create a second test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution_te2(db, test_execution_2, scenario):
    """Create a call execution for test_execution_2."""
    return CallExecution.objects.create(
        test_execution=test_execution_2,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-789",
        eval_outputs={"eval1": {"score": 0.8}},
        call_metadata={
            "base_prompt": "You are a test agent",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )


# ============================================================================
# CallExecutionRerunView Tests (existing endpoint)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionRerunView:
    """Tests for POST /simulate/test-executions/<uuid>/rerun-calls/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/rerun-calls/"

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_select_all(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
        call_execution_2,
    ):
        """Test eval_only rerun with select_all=True."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "eval_only"
        assert data["success_count"] == 2
        assert data["failure_count"] == 0
        assert len(data["successful_reruns"]) == 2

        # Verify Temporal workflow was started
        assert mock_rerun.call_count == 1

        # Verify snapshots were created
        assert CallExecutionSnapshot.objects.count() == 2

        # Verify test execution status updated
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_specific_ids(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
        call_execution_2,
    ):
        """Test eval_only rerun with specific call_execution_ids."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success_count"] == 1
        assert str(call_execution.id) in data["successful_reruns"]

    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_rerun_eval_only_tolerates_dispatch_failure(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
    ):
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success_count"] == 0
        assert data["failure_count"] == 1
        assert data["failed_reruns"][0]["call_execution_id"] == str(call_execution.id)
        assert "temporal dispatch timed out" in data["failed_reruns"][0]["error"]
        assert data["dispatch_error"] == "temporal dispatch timed out"
        assert "async dispatch failed" in data["message"]
        mock_rerun.assert_called_once()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["rerun_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.eval_outputs == {"eval1": {"score": 0.9}}
        assert call_execution.call_metadata["eval_completed"] is True

    @pytest.mark.requires_ee
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_call_and_eval_select_all(
        self, mock_rerun, auth_client, test_execution, call_execution
    ):
        """Test call_and_eval rerun with select_all=True."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "call_and_eval"
        assert data["success_count"] == 1

        # Verify test execution status updated - RUNNING because Temporal workflow started
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.RUNNING

    @pytest.mark.requires_ee
    @override_settings(HARNESS_PROVIDER="sandbox")
    @patch(
        "simulate.services.harness_credentials.credentials_for_rerun",
        return_value=({"LIVEKIT_URL": "wss://example.invalid"}, {}),
    )
    @patch("simulate.services.harness_sandbox.HarnessSandboxClient.rerun")
    def test_repository_harness_rerun_restarts_saved_environment_without_resetting_calls(
        self,
        rerun_saved_job,
        _saved_credentials,
        auth_client,
        test_execution,
        call_execution,
    ):
        job_id = uuid.uuid4()
        # Repository registrations may use a generic TEXT shell even when the
        # authoritative ALK contract is voice. That shell must not block the
        # saved environment rerun.
        agent = test_execution.run_test.agent_definition
        agent.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        agent.save(update_fields=["agent_type"])
        HostedHarnessJob.no_workspace_objects.create(
            id=job_id,
            organization=test_execution.run_test.organization,
            workspace=test_execution.run_test.workspace,
            run_id=uuid.uuid4(),
            idempotency_key=f"rerun-{job_id}",
            request_digest=f"sha256:{'0' * 64}",
            schema_version="1.4",
            payload={},
            state=HostedHarnessJob.State.COMPLETED,
            seed=1,
            scenario_count=1,
            artifact_level="standard",
            max_artifact_bytes=1,
            deadline_at=timezone.now() + timedelta(hours=1),
            run_test=test_execution.run_test,
            test_execution=test_execution,
        )
        rerun_saved_job.return_value = {
            "status": {"stage": "queued", "detail": "waiting to restart"}
        }
        original_metadata = dict(call_execution.call_metadata)

        response = auth_client.post(
            self.URL_TEMPLATE.format(test_execution.id),
            {
                "rerun_type": "call_and_eval",
                "select_all": True,
                "environment_values": {"LIVEKIT_URL": "wss://example.invalid"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["harness_job_id"] == str(job_id)
        rerun_saved_job.assert_called_once_with(
            str(job_id),
            {
                "environment_values": {"LIVEKIT_URL": "wss://example.invalid"},
                "secret_refs": {},
                "only": [],
            },
        )
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.COMPLETED
        assert call_execution.call_metadata == original_metadata


# ============================================================================
# Eval-only rerun on a call the harness scored
# ============================================================================

_HARNESS_VERDICT_ROW = {
    "name": "Task completion",
    "output": "Passed",
    "output_type": "Pass/Fail",
    "reason": "the caller's task was done",
    "status": "completed",
    "source": "harness",
}

_LEFT_TO_THE_HARNESS_ROW = build_skipped_eval_output_payload(
    eval_name="Task completion",
    reason=(
        "The harness scores this evaluation during the call. "
        "Only rerunning the call refreshes it."
    ),
)


def _eval_config_on(run_test, organization, name, *, mapping, deleted=False):
    """An eval of the run test. With an empty mapping it is a column the
    harness fills; with a mapping of its own the platform grades it."""
    template = EvalTemplate.objects.create(
        name=f"{name} template",
        config={
            "required_keys": list(mapping) or ["conversation"],
            "output": "Pass/Fail",
        },
        owner="system",
        organization=organization,
    )
    return SimulateEvalConfig.objects.create(
        name=name,
        eval_template=template,
        run_test=run_test,
        config={},
        mapping=mapping,
        deleted=deleted,
    )


def _stored_columns(call_execution, *, without=()):
    """The call's row as the database holds it, minus the named columns."""
    row = CallExecution.objects.filter(id=call_execution.id).values().get()
    return {column: value for column, value in row.items() if column not in without}


@pytest.mark.integration
@pytest.mark.api
class TestEvalOnlyRerunKeepsHarnessChecks:
    """An eval-only re-run keeps every row the harness wrote on a call and
    marks pending only the evals the platform grades on that call."""

    CALL_URL = "/simulate/test-executions/{}/rerun-calls/"
    BULK_URL = "/simulate/run-tests/{}/rerun-test-executions/"

    @staticmethod
    def _scored_by(harness_scored_by, run_test, *call_executions):
        """Make the calls ones the harness scored: through the job that owns
        their run test, or through the receipt an SDK run leaves on each
        call."""
        if harness_scored_by == "job":
            TestTestExecutionRuntimeContracts._owned_by_a_harness_environment(run_test)
            return
        assert harness_scored_by == "sdk", harness_scored_by
        for call_execution in call_executions:
            call_execution.call_metadata["harness_evaluations"] = [
                {"name": "ends_call_on_goodbye", "kind": "checkpoint", "passed": True}
            ]
            call_execution.save(update_fields=["call_metadata"])

    @staticmethod
    def _seed_harness_rows(
        call_execution, organization, *, filled_row=None, configs=None
    ):
        """Write on a call one row of each kind a harness-scored call holds.

        ``code_check`` has no eval behind it. ``filled`` sits under an eval
        with an empty mapping, the column the harness fills. ``own`` sits
        under an eval with a mapping of its own, the one the platform grades.
        Returns the three keys and the two evals; pass those evals back as
        ``configs`` to seed another call of the same run test.
        """
        run_test = call_execution.test_execution.run_test
        filled, own = configs or (
            _eval_config_on(run_test, organization, "Task completion", mapping={}),
            _eval_config_on(
                run_test, organization, "Mapped", mapping={"output": "transcript"}
            ),
        )
        code_check = str(uuid.uuid4())
        call_execution.eval_outputs = {
            **(call_execution.eval_outputs or {}),
            code_check: {
                "name": "ends_call_on_goodbye",
                "output": "Passed",
                "output_type": "Pass/Fail",
                "reason": "said goodbye",
                "status": "completed",
                "source": "harness",
                "kind": "checkpoint",
            },
            str(filled.id): copy.deepcopy(filled_row or _HARNESS_VERDICT_ROW),
            str(own.id): {
                "output": "Passed",
                "reason": "ok",
                "output_type": "Pass/Fail",
                "name": own.name,
            },
        }
        call_execution.save(update_fields=["eval_outputs"])
        # Compare later against what the database holds, not what was assigned.
        call_execution.refresh_from_db()
        return SimpleNamespace(
            code_check=code_check,
            filled=str(filled.id),
            own=str(own.id),
            configs=(filled, own),
        )

    @pytest.mark.parametrize(
        "filled_row",
        [_HARNESS_VERDICT_ROW, _LEFT_TO_THE_HARNESS_ROW],
        ids=["harness-verdict", "left-to-the-harness"],
    )
    @pytest.mark.parametrize("harness_scored_by", ["job", "sdk"])
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_eval_only_keeps_every_harness_column_and_pends_the_platform_graded_evals(
        self,
        mock_rerun,
        harness_scored_by,
        filled_row,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
        call_execution_2,
    ):
        """On a call the harness scored, every row the harness wrote stays as
        it was and only the eval with a mapping of its own reads pending.
        Nothing else on the call changes, and no other call is touched."""
        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        self._scored_by(harness_scored_by, run_test, call_execution)
        rows = self._seed_harness_rows(
            call_execution, organization, filled_row=filled_row
        )
        call_execution.recording_url = "https://example.com/recording.wav"
        call_execution.overall_score = 0.8
        call_execution.save(update_fields=["recording_url", "overall_score"])
        call_execution.refresh_from_db()
        outputs_before = copy.deepcopy(call_execution.eval_outputs)
        metadata_before = copy.deepcopy(call_execution.call_metadata)
        rewritten = ("eval_outputs", "call_metadata", "updated_at")
        columns_before = copy.deepcopy(
            _stored_columns(call_execution, without=rewritten)
        )
        other_call_before = copy.deepcopy(_stored_columns(call_execution_2))

        response = auth_client.post(
            self.CALL_URL.format(test_execution.id),
            {
                "rerun_type": "eval_only",
                "select_all": False,
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert str(call_execution.id) in response.json()["successful_reruns"]

        call_execution.refresh_from_db()
        outputs = call_execution.eval_outputs
        assert outputs[rows.code_check] == outputs_before[rows.code_check]
        assert outputs[rows.filled] == outputs_before[rows.filled]
        assert outputs["eval1"] == outputs_before["eval1"]
        assert outputs[rows.own] == {"status": "pending"}
        assert outputs == {**outputs_before, rows.own: {"status": "pending"}}

        metadata = call_execution.call_metadata
        assert metadata["eval_started"] is False
        assert metadata["eval_completed"] is False
        flags = ("eval_started", "eval_completed")
        assert {key: metadata[key] for key in metadata if key not in flags} == {
            key: metadata_before[key] for key in metadata_before if key not in flags
        }

        assert call_execution.status == columns_before["status"]
        assert call_execution.recording_url == columns_before["recording_url"]
        assert call_execution.overall_score == columns_before["overall_score"]
        assert _stored_columns(call_execution, without=rewritten) == columns_before

        snapshots = list(
            CallExecutionSnapshot.objects.filter(call_execution=call_execution)
        )
        assert len(snapshots) == 1
        assert snapshots[0].rerun_type == "eval_only"
        assert snapshots[0].eval_outputs == outputs_before

        mock_rerun.assert_called_once()
        assert mock_rerun.call_args.kwargs["eval_only"] is True
        assert mock_rerun.call_args.kwargs["call_execution_ids"] == [
            str(call_execution.id)
        ]

        assert _stored_columns(call_execution_2) == other_call_before
        assert not CallExecutionSnapshot.objects.filter(
            call_execution=call_execution_2
        ).exists()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_eval_only_on_a_call_the_harness_did_not_score_pends_every_live_eval_and_keeps_stale_rows(
        self,
        mock_rerun,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
    ):
        """On a call the harness did not score, every live eval is graded
        again whatever its mapping, and a row no live eval owns stays."""
        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        no_mapping = _eval_config_on(run_test, organization, "No mapping", mapping={})
        own = _eval_config_on(
            run_test, organization, "Mapped", mapping={"output": "transcript"}
        )
        removed = _eval_config_on(
            run_test,
            organization,
            "Removed",
            mapping={"output": "transcript"},
            deleted=True,
        )
        call_execution.eval_outputs = {
            **call_execution.eval_outputs,
            **{
                str(config.id): {
                    "output": "Passed",
                    "reason": "ok",
                    "output_type": "Pass/Fail",
                    "name": config.name,
                }
                for config in (no_mapping, own, removed)
            },
        }
        call_execution.save(update_fields=["eval_outputs"])
        call_execution.refresh_from_db()
        outputs_before = copy.deepcopy(call_execution.eval_outputs)

        response = auth_client.post(
            self.CALL_URL.format(test_execution.id),
            {
                "rerun_type": "eval_only",
                "select_all": False,
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert str(call_execution.id) in response.json()["successful_reruns"]

        call_execution.refresh_from_db()
        outputs = call_execution.eval_outputs
        assert outputs[str(no_mapping.id)] == {"status": "pending"}
        assert outputs[str(own.id)] == {"status": "pending"}
        assert outputs[str(removed.id)] == outputs_before[str(removed.id)]
        assert outputs["eval1"] == outputs_before["eval1"]
        assert outputs == {
            **outputs_before,
            str(no_mapping.id): {"status": "pending"},
            str(own.id): {"status": "pending"},
        }

    @pytest.mark.parametrize("harness_scored_by", ["job", "sdk"])
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_bulk_eval_only_keeps_every_harness_column_on_every_call(
        self,
        mock_rerun,
        harness_scored_by,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """The run-level re-run follows the same rule on each call of each
        execution, and each snapshot holds the call's results as they were."""
        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        self._scored_by(harness_scored_by, run_test, call_execution, call_execution_te2)
        first = self._seed_harness_rows(call_execution, organization)
        second = self._seed_harness_rows(
            call_execution_te2, organization, configs=first.configs
        )
        seeded = [(call_execution, first), (call_execution_te2, second)]
        before = {call.id: copy.deepcopy(call.eval_outputs) for call, _ in seeded}

        response = auth_client.post(
            self.BULK_URL.format(run_test.id),
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["overall_success_count"] == 2
        assert response.json()["overall_failure_count"] == 0
        for call, rows in seeded:
            outputs_before = before[call.id]
            call.refresh_from_db()
            outputs = call.eval_outputs
            assert outputs[rows.code_check] == outputs_before[rows.code_check]
            assert outputs[rows.filled] == outputs_before[rows.filled]
            assert outputs[rows.own] == {"status": "pending"}
            assert outputs == {**outputs_before, rows.own: {"status": "pending"}}
            assert call.call_metadata["eval_started"] is False
            assert call.call_metadata["eval_completed"] is False

            snapshots = list(CallExecutionSnapshot.objects.filter(call_execution=call))
            assert len(snapshots) == 1
            assert snapshots[0].rerun_type == "eval_only"
            assert snapshots[0].eval_outputs == outputs_before

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_bulk_eval_only_on_calls_the_harness_did_not_score_pends_every_live_eval(
        self,
        mock_rerun,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """On calls no harness scored, the run-level re-run marks every eval
        of the run test pending, the one with an empty mapping included, and
        keeps the row no eval stands behind."""
        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        first = self._seed_harness_rows(call_execution, organization)
        second = self._seed_harness_rows(
            call_execution_te2, organization, configs=first.configs
        )
        seeded = [(call_execution, first), (call_execution_te2, second)]
        before = {call.id: copy.deepcopy(call.eval_outputs) for call, _ in seeded}

        response = auth_client.post(
            self.BULK_URL.format(run_test.id),
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["overall_success_count"] == 2
        for call, rows in seeded:
            call.refresh_from_db()
            assert call.eval_outputs == {
                **before[call.id],
                rows.own: {"status": "pending"},
                rows.filled: {"status": "pending"},
            }

    @pytest.mark.parametrize("route", ["call", "bulk"])
    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_a_queue_failure_puts_every_row_back(
        self,
        mock_rerun,
        route,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
    ):
        """When the grading job cannot be queued, the eval the request marked
        pending holds its previous verdict again and every row the harness
        wrote is as it was."""
        self._scored_by("sdk", run_test, call_execution)
        self._seed_harness_rows(call_execution, organization)
        outputs_before = copy.deepcopy(call_execution.eval_outputs)

        if route == "call":
            response = auth_client.post(
                self.CALL_URL.format(test_execution.id),
                {
                    "rerun_type": "eval_only",
                    "select_all": False,
                    "call_execution_ids": [str(call_execution.id)],
                },
                format="json",
            )
        else:
            response = auth_client.post(
                self.BULK_URL.format(run_test.id),
                {"rerun_type": "eval_only", "select_all": True},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        mock_rerun.assert_called_once()
        call_execution.refresh_from_db()
        test_execution.refresh_from_db()
        assert call_execution.eval_outputs == outputs_before
        assert call_execution.call_metadata["eval_completed"] is True
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_the_graded_set_is_read_once_per_request(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        call_execution_2,
        test_execution_2,
        call_execution_te2,
    ):
        """Which evals the platform grades is read once for the run test,
        however many calls or executions one request covers."""
        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        with patch(
            "simulate.views.run_test.platform_graded_evals",
            wraps=platform_graded_evals,
        ) as read_graded_evals:
            response = auth_client.post(
                self.CALL_URL.format(test_execution.id),
                {
                    "rerun_type": "eval_only",
                    "select_all": False,
                    "call_execution_ids": [
                        str(call_execution.id),
                        str(call_execution_2.id),
                    ],
                },
                format="json",
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.json()["success_count"] == 2
            assert read_graded_evals.call_count == 1

            read_graded_evals.reset_mock()
            response = auth_client.post(
                self.BULK_URL.format(run_test.id),
                {"rerun_type": "eval_only", "select_all": True},
                format="json",
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.json()["overall_success_count"] == 3
            assert read_graded_evals.call_count == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("owned_by_job", "call_metadata", "graded"),
    [
        (False, {}, {"own", "filled"}),
        (False, None, {"own", "filled"}),
        (False, "not a dict", {"own", "filled"}),
        (False, {"harness_evaluations": []}, {"own"}),
        (False, {"harness_evaluations": [{"name": "x"}]}, {"own"}),
        (True, {}, {"own"}),
        (True, None, {"own"}),
    ],
    ids=[
        "no-receipt",
        "no-metadata",
        "metadata-not-a-dict",
        "empty-receipt",
        "receipt",
        "job-owns-run-test",
        "job-owns-run-test-no-metadata",
    ],
)
def test_platform_graded_evals_follows_the_call(
    owned_by_job, call_metadata, graded, run_test, organization
):
    """A call the harness scored, by a receipt on the call or by a job owning
    its run test, is graded only on evals with a mapping of their own. Any
    other call is graded on every live eval. A removed eval is never graded."""
    configs = {
        "own": _eval_config_on(
            run_test, organization, "Mapped", mapping={"output": "transcript"}
        ),
        "filled": _eval_config_on(
            run_test, organization, "Task completion", mapping={}
        ),
        "gone": _eval_config_on(
            run_test,
            organization,
            "Removed",
            mapping={"output": "transcript"},
            deleted=True,
        ),
    }
    if owned_by_job:
        TestTestExecutionRuntimeContracts._owned_by_a_harness_environment(run_test)

    graded_ids = platform_graded_evals(run_test.id).for_call(call_metadata)

    assert sorted(graded_ids) == sorted(str(configs[name].id) for name in graded)
    assert str(configs["gone"].id) not in graded_ids
    assert platform_graded_evals(None).for_call({}) == ()
    assert platform_graded_evals(uuid.uuid4()).for_call({}) == ()


@pytest.mark.unit
def test_outputs_for_eval_rerun_is_pure_and_touches_only_the_regraded_ids():
    """The rows returned are the stored rows with pending under each eval
    about to be graded; the stored value itself is never changed, and a
    stored value that is not a dict reads as no rows at all."""
    kept_row = {"name": "ends_call_on_goodbye", "output": "Passed", "source": "harness"}
    regraded_id = uuid.uuid4()
    new_id = uuid.uuid4()
    stored = {
        "kept": kept_row,
        str(regraded_id): {"output": "Failed", "reason": "old verdict"},
    }
    stored_before = copy.deepcopy(stored)

    outputs = outputs_for_eval_rerun(stored, [regraded_id, new_id])

    assert stored == stored_before
    assert outputs is not stored
    assert outputs == {
        "kept": stored_before["kept"],
        str(regraded_id): {"status": "pending"},
        str(new_id): {"status": "pending"},
    }
    assert outputs["kept"] is kept_row
    assert all(isinstance(key, str) for key in outputs)
    assert outputs[str(regraded_id)] is not outputs[str(new_id)]

    assert outputs_for_eval_rerun(None, [regraded_id]) == {
        str(regraded_id): {"status": "pending"}
    }
    for not_a_dict in (["x"], "x"):
        assert outputs_for_eval_rerun(not_a_dict, []) == {}
        assert outputs_for_eval_rerun(not_a_dict, [new_id]) == {
            str(new_id): {"status": "pending"}
        }


@pytest.mark.integration
@pytest.mark.api
class TestRerunParallelismAdmission:
    """A saved W>1 job must re-cross the register_attempt admission gate on rerun.

    register_attempt is the single chokepoint every re-admission path shares
    (C4 §4 pin ii / §5, decision D23): a saved W=4 job reruns at W=1 whenever the
    flag/digest no longer qualify, and the stored requested value is preserved so
    a later rerun re-evaluates honestly against the then-current flag/digest.
    """

    @staticmethod
    def _saved_w4_job(organization, workspace):
        return HostedHarnessJob.no_workspace_objects.create(
            id=uuid.uuid4(),
            organization=organization,
            workspace=workspace,
            run_id=uuid.uuid4(),
            idempotency_key=f"parallel-{uuid.uuid4()}",
            request_digest=f"sha256:{'0' * 64}",
            schema_version="futureagi.harness-job.v1",
            payload={
                "source": {"kind": "remote", "endpoint": "https://agent.example.com"},
                "scenario_count": 4,
                "runtime": {
                    "parallelism": 4,
                    "cpu_units": 8,
                    "max_duration_seconds": 600,
                },
                "metadata": {},
            },
            state=HostedHarnessJob.State.COMPLETED,
            seed=1,
            scenario_count=4,
            artifact_level="standard",
            max_artifact_bytes=1,
            deadline_at=timezone.now() + timedelta(hours=1),
        )

    @override_settings(
        HARNESS_PARALLELISM_ENABLED=False,
        HARNESS_PARALLEL_SNAPSHOT_DIGESTS=[],
        ALK_DAYTONA_DOCKERFILE="",
    )
    def test_saved_w4_job_reruns_at_w1_when_flag_off(self, db, organization, workspace):
        from simulate.services.hosted_harness import register_attempt

        job = self._saved_w4_job(organization, workspace)
        # A rerun re-registers an attempt through the SAME chokepoint.
        register_attempt(job.id, endpoint_base_url="https://platform.example")
        job.refresh_from_db()
        assert job.payload["metadata"]["parallelism_clamped"] == {
            "requested": 4,
            "admitted": 1,
        }
        # Requested value preserved -> a later rerun can re-qualify.
        assert job.payload["runtime"]["parallelism"] == 4

    @override_settings(
        HARNESS_PARALLELISM_ENABLED=True,
        HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:certified"],
        ALK_DAYTONA_DOCKERFILE="",
    )
    def test_saved_w4_job_reruns_at_w4_when_flag_and_digest_qualify(
        self, db, organization, workspace
    ):
        from simulate.services.hosted_harness import register_attempt

        job = self._saved_w4_job(organization, workspace)
        register_attempt(
            job.id,
            endpoint_base_url="https://platform.example",
            snapshot_digest="sha256:certified",
        )
        job.refresh_from_db()
        assert "parallelism_clamped" not in job.payload["metadata"]
        assert job.payload["runtime"]["parallelism"] == 4


@pytest.mark.integration
@pytest.mark.api
class TestRepositoryRunAgainView:
    """The simulation header and grid must share the repository lifecycle."""

    URL_TEMPLATE = "/simulate/test-executions/{}/rerun-calls/"

    @override_settings(HARNESS_PROVIDER="sandbox")
    @patch("simulate.views.run_test._voice_sim_gate_response", return_value=None)
    @patch(
        "simulate.services.harness_credentials.credentials_for_rerun",
        return_value=({}, {}),
    )
    @patch("simulate.services.harness_sandbox.HarnessSandboxClient.rerun")
    def test_run_again_restarts_saved_harness_instead_of_creating_pending_execution(
        self,
        rerun_saved_job,
        _saved_credentials,
        _voice_gate,
        auth_client,
        run_test,
        test_execution,
        scenario,
    ):
        job_id = uuid.uuid4()
        run_test.agent_definition.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        run_test.agent_definition.save(update_fields=["agent_type"])
        test_execution.execution_metadata = {"harness_job_id": str(job_id)}
        test_execution.scenario_ids = [str(scenario.id)]
        test_execution.save(update_fields=["execution_metadata", "scenario_ids"])
        rerun_saved_job.return_value = {"status": {"stage": "queued"}}
        execution_count = TestExecution.objects.count()

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/execute/",
            {"scenario_ids": [str(scenario.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "queued"
        assert TestExecution.objects.count() == execution_count
        rerun_saved_job.assert_called_once_with(
            str(job_id),
            {"environment_values": {}, "secret_refs": {}, "only": []},
        )

    @pytest.mark.requires_ee
    @patch("simulate.views.run_test._hosted_execution_eligible", return_value=False)
    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_rerun_call_and_eval_marks_failed_when_dispatch_fails(
        self,
        mock_rerun,
        _native_execution,
        auth_client,
        test_execution,
        call_execution,
    ):
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "call_and_eval",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success_count"] == 0
        assert data["failure_count"] == 1
        assert data["failed_reruns"][0]["call_execution_id"] == str(call_execution.id)
        assert "temporal dispatch timed out" in data["failed_reruns"][0]["error"]
        assert data["dispatch_error"] == "temporal dispatch timed out"
        assert "async dispatch failed" in data["message"]
        mock_rerun.assert_called_once()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.FAILED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["rerun_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.status == CallExecution.CallStatus.FAILED
        assert "Rerun dispatch failed" in call_execution.ended_reason
        assert (
            CreateCallExecution.objects.filter(call_execution=call_execution).count()
            == 0
        )

    def test_rerun_missing_params(self, auth_client, test_execution):
        """Test rerun with neither select_all nor call_execution_ids."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rerun_invalid_rerun_type(self, auth_client, test_execution):
        """Test rerun with invalid rerun_type."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "invalid", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_rejects_unknown_fields(
        self, mock_rerun, auth_client, test_execution
    ):
        """Unknown request fields should fail before any rerun work starts."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "select_all": True,
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]
        mock_rerun.assert_not_called()

    def test_rerun_nonexistent_test_execution(self, auth_client):
        """Test rerun with non-existent test_execution_id returns error."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_rerun_unauthenticated(self, api_client, test_execution):
        """Test rerun without authentication."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = api_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        # DRF returns 403 Forbidden for unauthenticated requests by default
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_no_call_executions(self, mock_rerun, auth_client, test_execution):
        """Test rerun when test execution has no call executions."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# TestExecutionRerunView Tests (new endpoint)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionRerunView:
    """Tests for POST /simulate/run-tests/<uuid>/rerun-test-executions/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/rerun-test-executions/"

    @override_settings(HARNESS_PROVIDER="sandbox")
    @patch("simulate.views.run_test._voice_sim_gate_response", return_value=None)
    @patch(
        "simulate.services.harness_credentials.credentials_for_rerun",
        return_value=({}, {}),
    )
    @patch("simulate.services.harness_sandbox.HarnessSandboxClient.rerun")
    def test_grid_rerun_restarts_saved_harness_without_resetting_calls(
        self,
        rerun_saved_job,
        _saved_credentials,
        _voice_gate,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        job_id = uuid.uuid4()
        run_test.agent_definition.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        run_test.agent_definition.save(update_fields=["agent_type"])
        test_execution.execution_metadata = {"harness_job_id": str(job_id)}
        test_execution.save(update_fields=["execution_metadata"])
        rerun_saved_job.return_value = {"status": {"stage": "queued"}}
        original_metadata = dict(call_execution.call_metadata)

        response = auth_client.post(
            self.URL_TEMPLATE.format(run_test.id),
            {
                "rerun_type": "call_and_eval",
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["overall_success_count"] == 1
        rerun_saved_job.assert_called_once_with(
            str(job_id),
            {"environment_values": {}, "secret_refs": {}, "only": []},
        )
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.COMPLETED
        assert call_execution.call_metadata == original_metadata

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_select_all(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        call_execution_2,
        test_execution_2,
        call_execution_te2,
    ):
        """Test eval_only rerun with select_all=True across multiple test executions."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "eval_only"
        assert data["total_test_executions"] == 2
        assert data["overall_success_count"] == 3  # 2 from te1 + 1 from te2
        assert data["overall_failure_count"] == 0
        assert len(data["results"]) == 2

        # Verify rerun_call_executions was called for each test execution
        assert mock_rerun.call_count == 2

        # Verify snapshots were created
        assert CallExecutionSnapshot.objects.count() == 3

        # Verify both test executions updated
        test_execution.refresh_from_db()
        test_execution_2.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        assert test_execution_2.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_specific_ids(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test eval_only rerun with specific test_execution_ids."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total_test_executions"] == 1
        assert data["overall_success_count"] == 1

        # Only test_execution should be updated, not test_execution_2
        test_execution.refresh_from_db()
        test_execution_2.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        assert test_execution_2.status == TestExecution.ExecutionStatus.COMPLETED

    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_rerun_test_execution_tolerates_dispatch_failure(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["overall_success_count"] == 0
        assert data["overall_failure_count"] == 1
        assert data["results"][0]["success_count"] == 0
        assert data["results"][0]["failure_count"] == 1
        assert data["results"][0]["dispatch_error"] == "temporal dispatch timed out"
        assert data["results"][0]["failed_reruns"][0]["call_execution_id"] == str(
            call_execution.id
        )
        assert (
            "temporal dispatch timed out"
            in data["results"][0]["failed_reruns"][0]["error"]
        )
        mock_rerun.assert_called_once()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["rerun_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.eval_outputs == {"eval1": {"score": 0.9}}
        assert call_execution.call_metadata["eval_completed"] is True

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_select_all_with_exclusion(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test select_all=True with test_execution_ids acting as exclusion list."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "select_all": True,
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Only test_execution_2 should be processed (test_execution excluded)
        assert data["total_test_executions"] == 1
        assert data["results"][0]["test_execution_id"] == str(test_execution_2.id)

    @pytest.mark.requires_ee
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_call_and_eval_select_all(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test call_and_eval rerun with select_all=True."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "call_and_eval"
        assert data["total_test_executions"] == 2
        assert data["overall_success_count"] == 2

        # Verify both test executions set to RUNNING (Temporal workflow started)
        test_execution.refresh_from_db()
        test_execution_2.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.RUNNING
        assert test_execution_2.status == TestExecution.ExecutionStatus.RUNNING

    def test_rerun_missing_params(self, auth_client, run_test):
        """Test rerun with neither select_all nor test_execution_ids."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rerun_invalid_rerun_type(self, auth_client, run_test):
        """Test rerun with invalid rerun_type."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "invalid", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_rejects_unknown_fields(self, mock_rerun, auth_client, run_test):
        """Unknown request fields should fail before any rerun work starts."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "select_all": True,
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]
        mock_rerun.assert_not_called()

    def test_rerun_nonexistent_run_test(self, auth_client):
        """Test rerun with non-existent run_test_id returns error."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_rerun_unauthenticated(self, api_client, run_test):
        """Test rerun without authentication."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = api_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        # DRF returns 403 Forbidden for unauthenticated requests by default
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_skips_test_execution_with_no_calls(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test that test executions with no call executions are skipped."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        # test_execution has no call executions, test_execution_2 has one
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total_test_executions"] == 2

        # Find the skipped result
        skipped = [r for r in data["results"] if r.get("skipped")]
        assert len(skipped) == 1
        assert skipped[0]["test_execution_id"] == str(test_execution.id)

    def test_rerun_no_test_executions_found(self, auth_client, run_test):
        """Test rerun when no test executions match."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [str(uuid.uuid4())],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# RunTestEvalExplanationSummaryView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalExplanationSummary:
    """Tests for GET /simulate/test-executions/<uuid>/eval-explanation-summary/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/eval-explanation-summary/"

    def test_get_eval_explanation_summary_returns_summary(
        self, auth_client, test_execution
    ):
        """Populated summary is returned with status and last_updated."""
        summary_payload = {
            "total_evals": 3,
            "buckets": [
                {"label": "pass", "count": 2},
                {"label": "fail", "count": 1},
            ],
        }
        last_updated = timezone.now()
        test_execution.eval_explanation_summary = summary_payload
        test_execution.eval_explanation_summary_last_updated = last_updated
        test_execution.eval_explanation_summary_status = (
            EvalExplanationSummaryStatus.COMPLETED
        )
        test_execution.save(
            update_fields=[
                "eval_explanation_summary",
                "eval_explanation_summary_last_updated",
                "eval_explanation_summary_status",
            ]
        )

        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["response"] == summary_payload
        assert result["status"] == EvalExplanationSummaryStatus.COMPLETED
        assert result["last_updated"] is not None

        # Status should not be flipped back to PENDING when summary already exists
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )

    def test_get_eval_explanation_summary_unauthenticated_returns_401(
        self, api_client, test_execution
    ):
        """Unauthenticated request is rejected before hitting the view logic."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = api_client.get(url)

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        body = response.json()
        assert "detail" in body or body.get("status") is False

    def test_get_eval_explanation_summary_not_found_returns_404(self, auth_client):
        """Unknown test-execution id returns 404 with the standard error body."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.json()
        assert body.get("status") is False

    def test_get_eval_explanation_summary_other_workspace_returns_404(
        self,
        auth_client,
        organization,
        user,
        agent_definition,
        simulator_agent,
    ):
        """A test-execution scoped to another workspace of the same org is not visible."""
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other EvalSummary Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_run_test = RunTest.no_workspace_objects.create(
            name="Hidden Run Test",
            description="Hidden run test in another workspace.",
            agent_definition=agent_definition,
            simulator_agent=simulator_agent,
            organization=organization,
            workspace=other_workspace,
        )
        hidden_summary = {"total_evals": 42, "buckets": []}
        hidden_last_updated = timezone.now()
        hidden_test_execution = TestExecution.no_workspace_objects.create(
            run_test=hidden_run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            total_calls=1,
            simulator_agent=simulator_agent,
            agent_definition=agent_definition,
            eval_explanation_summary=hidden_summary,
            eval_explanation_summary_last_updated=hidden_last_updated,
            eval_explanation_summary_status=EvalExplanationSummaryStatus.COMPLETED,
        )

        url = self.URL_TEMPLATE.format(hidden_test_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.json()
        assert body.get("status") is False

        # Target row untouched
        hidden_test_execution.refresh_from_db()
        assert hidden_test_execution.eval_explanation_summary == hidden_summary
        assert (
            hidden_test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionRuntimeContracts:
    """Request validation tests for related test-execution actions."""

    def test_column_order_update_accepts_canonical_body(
        self, auth_client, test_execution
    ):
        url = f"/simulate/test-executions/{test_execution.id}/column-order/"
        column_order = [
            {"id": "status", "column_name": "Status", "visible": True},
            {"id": "latency", "column_name": "Latency", "visible": False},
        ]

        response = auth_client.put(
            url,
            {"column_order": column_order},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["column_order"] == column_order
        test_execution.refresh_from_db()
        assert test_execution.execution_metadata["column_order"] == column_order

    def test_column_order_update_rejects_unknown_fields(
        self, auth_client, test_execution
    ):
        url = f"/simulate/test-executions/{test_execution.id}/column-order/"
        response = auth_client.put(
            url,
            {
                "column_order": [
                    {"id": "status", "column_name": "Status", "visible": True}
                ],
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    @patch("simulate.views.run_test.TestExecutor")
    def test_cancel_accepts_empty_body(
        self, mock_test_executor, auth_client, test_execution
    ):
        mock_test_executor.return_value.cancel_test.return_value = {
            "success": True,
            "message": "Cancellation initiated",
            "test_execution_id": str(test_execution.id),
        }
        url = f"/simulate/test-executions/{test_execution.id}/cancel/"

        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.CANCELLING

    def test_cancel_rejects_unknown_fields(self, auth_client, test_execution):
        url = f"/simulate/test-executions/{test_execution.id}/cancel/"
        response = auth_client.post(
            url,
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_cancel_other_workspace_returns_404(
        self,
        auth_client,
        organization,
        user,
        agent_definition,
        simulator_agent,
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Cancel Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_run_test = RunTest.no_workspace_objects.create(
            name="Hidden Cancel Run Test",
            description="Run test in another workspace",
            agent_definition=agent_definition,
            simulator_agent=simulator_agent,
            organization=organization,
            workspace=other_workspace,
        )
        hidden_te = TestExecution.no_workspace_objects.create(
            run_test=hidden_run_test,
            status=TestExecution.ExecutionStatus.RUNNING,
            total_scenarios=1,
            total_calls=1,
            simulator_agent=simulator_agent,
            agent_definition=agent_definition,
        )
        url = f"/simulate/test-executions/{hidden_te.id}/cancel/"
        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_te.refresh_from_db()
        assert hidden_te.status == TestExecution.ExecutionStatus.RUNNING

    @staticmethod
    def _hosted_job_for(test_execution, state):
        """The hosted harness job that ran this execution's calls."""
        run_test = test_execution.run_test
        return HostedHarnessJob.no_workspace_objects.create(
            organization=run_test.organization,
            workspace=run_test.workspace,
            run_id=uuid.uuid4(),
            idempotency_key=f"cancel-{uuid.uuid4()}",
            request_digest=f"sha256:{'0' * 64}",
            schema_version="1.4",
            payload={},
            state=state,
            seed=1,
            scenario_count=1,
            artifact_level="standard",
            max_artifact_bytes=1,
            deadline_at=timezone.now() + timedelta(hours=1),
            run_test=run_test,
            test_execution=test_execution,
        )

    @pytest.mark.parametrize(
        "job_state",
        [
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        ],
    )
    @patch("simulate.views.run_test.TestExecutor")
    def test_cancel_refuses_to_stop_grading_on_a_finished_harness_run(
        self, mock_test_executor, job_state, auth_client, test_execution
    ):
        job = self._hosted_job_for(test_execution, job_state)
        test_execution.status = TestExecution.ExecutionStatus.EVALUATING
        test_execution.save(update_fields=["status"])

        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/cancel/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["message"] == (
            "Grading can't be stopped. It finishes on its own."
        )
        mock_test_executor.assert_not_called()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        job.refresh_from_db()
        assert job.state == job_state
        assert job.cancel_requested_at is None

    @patch("simulate.views.run_test.TestExecutor")
    def test_cancel_refuses_grading_on_a_harness_run_whose_execution_has_no_job(
        self, mock_test_executor, auth_client, test_execution
    ):
        """A run of a harness environment is graded again as a harness run
        whether or not its own execution has a job, so it is refused a stop
        by the same test."""
        self._owned_by_a_harness_environment(test_execution.run_test)
        test_execution.status = TestExecution.ExecutionStatus.EVALUATING
        test_execution.save(update_fields=["status"])

        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/cancel/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        mock_test_executor.assert_not_called()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.views.run_test.TestExecutor")
    def test_cancel_stops_a_running_hosted_harness_run(
        self, mock_test_executor, auth_client, test_execution
    ):
        mock_test_executor.return_value.cancel_test.return_value = {
            "success": True,
            "message": "Cancellation initiated",
            "test_execution_id": str(test_execution.id),
        }
        self._hosted_job_for(test_execution, HostedHarnessJob.State.RUNNING)
        test_execution.status = TestExecution.ExecutionStatus.RUNNING
        test_execution.save(update_fields=["status"])

        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/cancel/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_test_executor.return_value.cancel_test.assert_called_once()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.CANCELLING

    @patch("simulate.views.run_test.TestExecutor")
    def test_cancel_stops_grading_on_a_run_with_no_hosted_job(
        self, mock_test_executor, auth_client, test_execution
    ):
        mock_test_executor.return_value.cancel_test.return_value = {
            "success": True,
            "message": "Cancellation initiated",
            "test_execution_id": str(test_execution.id),
        }
        test_execution.status = TestExecution.ExecutionStatus.EVALUATING
        test_execution.save(update_fields=["status"])

        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/cancel/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_test_executor.return_value.cancel_test.assert_called_once()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.CANCELLING

    def test_eval_summary_refresh_rejects_unknown_fields(
        self, auth_client, test_execution
    ):
        url = (
            f"/simulate/test-executions/{test_execution.id}/"
            "eval-explanation-summary/refresh/"
        )
        response = auth_client.post(
            url,
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    @patch(
        "simulate.views.run_test.run_eval_summary_task.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_eval_summary_refresh_tolerates_dispatch_failure(
        self, mock_apply_async, auth_client, test_execution
    ):
        url = (
            f"/simulate/test-executions/{test_execution.id}/"
            "eval-explanation-summary/refresh/"
        )

        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert "marked pending" in response.data["result"]["message"]
        mock_apply_async.assert_called_once_with(args=(str(test_execution.id),))
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )

    def test_optimiser_refresh_rejects_unknown_fields(
        self, auth_client, test_execution
    ):
        url = (
            f"/simulate/test-executions/{test_execution.id}/optimiser-analysis/refresh/"
        )
        response = auth_client.post(
            url,
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_run_new_evals_rejects_unknown_fields(self, auth_client, run_test):
        url = f"/simulate/run-tests/{run_test.id}/run-new-evals/"
        response = auth_client.post(
            url,
            {
                "select_all": True,
                "eval_config_ids": [],
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    @pytest.mark.parametrize(
        "eval_completed_before",
        [True, False, None],
        ids=["finished", "not-finished", "never-set"],
    )
    @patch(
        "simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_run_new_evals_tolerates_dispatch_failure(
        self,
        mock_apply_async,
        eval_completed_before,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        organization,
    ):
        eval_template = EvalTemplate.objects.create(
            name="dispatch failure eval",
            config={"output": "Pass/Fail"},
            organization=organization,
        )
        eval_config = SimulateEvalConfig.objects.create(
            name="dispatch failure config",
            eval_template=eval_template,
            run_test=run_test,
            config={},
            mapping={},
        )
        # A suite eval the harness already scored on this call: a failed
        # dispatch must not leave its score replaced by a pending placeholder.
        suite_template = EvalTemplate.objects.create(
            name="dispatch failure suite eval",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="system",
        )
        suite_config = SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=suite_template,
            run_test=run_test,
            config={},
            mapping={},
        )
        call_execution.eval_outputs[str(suite_config.id)] = {
            "name": "Task completion",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "reason": "The agent finished the task.",
            "status": "completed",
            "source": "harness",
        }
        call_execution.call_metadata = {"eval_started": True}
        if eval_completed_before is not None:
            call_execution.call_metadata["eval_completed"] = eval_completed_before
        call_execution.save(update_fields=["eval_outputs", "call_metadata"])
        outputs_before = dict(call_execution.eval_outputs)
        url = f"/simulate/run-tests/{run_test.id}/run-new-evals/"

        response = auth_client.post(
            url,
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(eval_config.id), str(suite_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "may not have started" in response.data["message"]
        assert response.data["dispatched"] is False
        assert response.data["call_execution_count"] == 1
        mock_apply_async.assert_called_once()

        call_execution.refresh_from_db()
        test_execution.refresh_from_db()
        # Every call is back to what it held: the harness score is untouched
        # and the eval that was never graded has no placeholder left behind.
        assert call_execution.eval_outputs == outputs_before
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["eval_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.call_metadata["eval_started"] is False
        assert (
            call_execution.call_metadata["eval_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.call_metadata.get("eval_completed") == (
            eval_completed_before
        )
        assert ("eval_completed" in call_execution.call_metadata) is (
            eval_completed_before is not None
        )

        # Adding an eval to the run afterwards sees each call exactly as it
        # was before the failed dispatch.
        added_template = EvalTemplate.objects.create(
            name="added after a failed dispatch",
            config={"required_keys": ["output"], "output": "Pass/Fail"},
            organization=organization,
        )
        added = SimulateEvalConfig.objects.create(
            name="Added after a failed dispatch",
            eval_template=added_template,
            run_test=run_test,
            config={},
            mapping={"output": "transcript"},
        )
        counts = queue_eval_for_finished_calls(test_execution, added)
        assert counts["queued"] == (1 if eval_completed_before else 0)
        assert counts["skipped_pending"] == (0 if eval_completed_before else 1)

    @staticmethod
    def _owned_by_a_harness_environment(run_test):
        """Link the run test to a harness job, as registering an environment's
        scenarios does; only then is it a harness run."""
        HostedHarnessJob.no_workspace_objects.create(
            organization=run_test.organization,
            workspace=run_test.workspace,
            run_id=uuid.uuid4(),
            idempotency_key=f"regrade-{uuid.uuid4()}",
            request_digest=f"sha256:{'0' * 64}",
            schema_version="1.4",
            payload={},
            state=HostedHarnessJob.State.COMPLETED,
            seed=1,
            scenario_count=1,
            artifact_level="standard",
            max_artifact_bytes=1,
            deadline_at=timezone.now() + timedelta(hours=1),
            run_test=run_test,
        )

    @pytest.mark.parametrize(
        ("owner", "required_keys"),
        [
            ("user", ["conversation"]),
            ("system", ["conversation", "expected_response"]),
            ("system", []),
        ],
        ids=["per-scenario-claim", "asks-for-more", "asks-for-nothing"],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_refuses_a_harness_only_eval_before_writing_anything(
        self,
        mock_apply_async,
        owner,
        required_keys,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        organization,
    ):
        self._owned_by_a_harness_environment(run_test)
        harness_template = EvalTemplate.objects.create(
            name="harness_only_eval",
            config={"required_keys": required_keys, "output": "Pass/Fail"},
            owner=owner,
            organization=organization,
        )
        harness = SimulateEvalConfig.objects.create(
            name="Harness claim",
            eval_template=harness_template,
            run_test=run_test,
            config={},
            mapping={},
        )
        mapped_template = EvalTemplate.objects.create(
            name="mapped_regrade_eval",
            config={"required_keys": ["output"], "output": "Pass/Fail"},
            organization=organization,
        )
        mapped = SimulateEvalConfig.objects.create(
            name="Mapped",
            eval_template=mapped_template,
            run_test=run_test,
            config={},
            mapping={"output": "transcript"},
        )
        outputs_before = dict(call_execution.eval_outputs)
        metadata_before = dict(test_execution.execution_metadata or {})

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(mapped.id), str(harness.id)],
                "enable_tool_evaluation": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["message"] == (
            "Harness claim is scored by the harness during the call. "
            "Only rerunning the call refreshes it."
        )
        mock_apply_async.assert_not_called()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        run_test.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert (test_execution.execution_metadata or {}) == metadata_before
        assert call_execution.eval_outputs == outputs_before
        assert run_test.enable_tool_evaluation is False

    @pytest.mark.parametrize(
        ("owner", "required_keys"),
        [
            ("user", ["conversation"]),
            ("system", ["conversation", "agent_prompt"]),
            ("system", []),
        ],
        ids=["claim-shaped", "suite-shaped", "asks-for-nothing"],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_grades_every_eval_on_a_run_no_harness_owns_as_before(
        self,
        mock_apply_async,
        owner,
        required_keys,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        organization,
    ):
        template = EvalTemplate.objects.create(
            name="native_empty_mapping_eval",
            config={"required_keys": required_keys, "output": "Pass/Fail"},
            owner=owner,
            organization=organization,
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Native empty mapping",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(cfg.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["dispatched"] is True
        mock_apply_async.assert_called_once_with(
            args=([str(call_execution.id)], [str(cfg.id)])
        )

        cfg.refresh_from_db()
        call_execution.refresh_from_db()
        test_execution.refresh_from_db()
        assert cfg.mapping == {}
        assert call_execution.eval_outputs[str(cfg.id)] == {"status": "pending"}
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @pytest.mark.parametrize(
        "required_keys",
        [["conversation"], ["conversation", "agent_prompt"]],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_sends_a_suite_eval_by_id_alone(
        self,
        mock_apply_async,
        required_keys,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        self._owned_by_a_harness_environment(run_test)
        template = EvalTemplate.objects.create(
            name="suite_regrade_eval",
            config={"required_keys": required_keys, "output": "Pass/Fail"},
            owner="system",
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(cfg.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["dispatched"] is True
        # The grader works out the suite eval's inputs itself, so nothing but
        # ids crosses to a worker that may be older than this server.
        mock_apply_async.assert_called_once_with(
            args=([str(call_execution.id)], [str(cfg.id)])
        )

        cfg.refresh_from_db()
        call_execution.refresh_from_db()
        test_execution.refresh_from_db()
        assert cfg.mapping == {}
        assert call_execution.eval_outputs[str(cfg.id)] == {"status": "pending"}
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_on_a_harness_run_saves_enable_tool_evaluation(
        self,
        mock_apply_async,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        self._owned_by_a_harness_environment(run_test)
        template = EvalTemplate.objects.create(
            name="suite_tool_eval_toggle",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="system",
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )
        assert run_test.enable_tool_evaluation is False

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(cfg.id)],
                "enable_tool_evaluation": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_apply_async.assert_called_once()
        run_test.refresh_from_db()
        assert run_test.enable_tool_evaluation is True

    @pytest.mark.parametrize(
        "job_state",
        [
            HostedHarnessJob.State.RUNNING,
            HostedHarnessJob.State.FINALIZING,
            HostedHarnessJob.State.CLEANING_UP,
            HostedHarnessJob.State.RETRY_WAIT,
        ],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_refuses_a_harness_run_whose_job_is_still_finishing(
        self,
        mock_apply_async,
        job_state,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        """The calls can all be ingested, making the run COMPLETED, while the
        job is still tearing its sandbox down and has yet to write its end
        state onto the run."""
        self._hosted_job_for(test_execution, job_state)
        template = EvalTemplate.objects.create(
            name="suite_eval_job_still_live",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="system",
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )
        call_execution.eval_outputs = {
            **call_execution.eval_outputs,
            str(cfg.id): {"source": "harness", "output": "Passed"},
        }
        call_execution.save(update_fields=["eval_outputs"])
        outputs_before = dict(call_execution.eval_outputs)
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert run_test.enable_tool_evaluation is False

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(cfg.id)],
                "enable_tool_evaluation": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["message"] == (
            "This run is still finishing. Try again in a moment."
        )
        mock_apply_async.assert_not_called()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        run_test.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert call_execution.eval_outputs == outputs_before
        assert run_test.enable_tool_evaluation is False

    @pytest.mark.parametrize(
        "execution_status",
        [
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.RUNNING,
        ],
        ids=["over-the-harness-only-refusal", "over-the-not-completed-refusal"],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_says_still_finishing_before_any_other_refusal(
        self,
        mock_apply_async,
        execution_status,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        organization,
    ):
        self._hosted_job_for(test_execution, HostedHarnessJob.State.CLEANING_UP)
        test_execution.status = execution_status
        test_execution.save(update_fields=["status"])
        template = EvalTemplate.objects.create(
            name="harness_claim_while_job_live",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="user",
            organization=organization,
        )
        harness_only = SimulateEvalConfig.objects.create(
            name="Harness claim",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(harness_only.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["message"] == (
            "This run is still finishing. Try again in a moment."
        )
        mock_apply_async.assert_not_called()
        test_execution.refresh_from_db()
        assert test_execution.status == execution_status

    @pytest.mark.parametrize(
        "job_state",
        [
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        ],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_regrades_a_harness_run_whose_job_has_ended(
        self,
        mock_apply_async,
        job_state,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        self._hosted_job_for(test_execution, job_state)
        template = EvalTemplate.objects.create(
            name="suite_eval_job_ended",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="system",
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(cfg.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_apply_async.assert_called_once_with(
            args=([str(call_execution.id)], [str(cfg.id)])
        )
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_sees_the_status_teardown_wrote_with_the_job_end(
        self,
        mock_apply_async,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        """Teardown commits the job's end state and the run's status together,
        so a job that reads as ended must be followed by a status read that
        sees the run's new status."""
        self._hosted_job_for(test_execution, HostedHarnessJob.State.FAILED)
        template = EvalTemplate.objects.create(
            name="suite_eval_teardown_race",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="system",
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )
        jobs = HostedHarnessJob.no_workspace_objects
        real_filter = jobs.filter

        def teardown_commits_during_the_job_read():
            TestExecution.objects.filter(id=test_execution.id).update(
                status=TestExecution.ExecutionStatus.FAILED
            )
            return False

        def job_filter(*args, **kwargs):
            if "test_execution__in" not in kwargs:
                return real_filter(*args, **kwargs)
            job_read = MagicMock()
            job_read.exclude.return_value.exists.side_effect = (
                teardown_commits_during_the_job_read
            )
            return job_read

        with patch.object(jobs, "filter", side_effect=job_filter):
            response = auth_client.post(
                f"/simulate/run-tests/{run_test.id}/run-new-evals/",
                {
                    "test_execution_ids": [str(test_execution.id)],
                    "eval_config_ids": [str(cfg.id)],
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_apply_async.assert_not_called()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.FAILED

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_keeps_the_old_dispatch_shape_for_mapped_evals(
        self,
        mock_apply_async,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        organization,
    ):
        self._owned_by_a_harness_environment(run_test)
        template = EvalTemplate.objects.create(
            name="mapped_shape_eval",
            config={"required_keys": ["output"], "output": "Pass/Fail"},
            organization=organization,
        )
        cfg = SimulateEvalConfig.objects.create(
            name="Mapped shape",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={"output": "transcript"},
        )

        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(cfg.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_apply_async.assert_called_once_with(
            args=([str(call_execution.id)], [str(cfg.id)])
        )

    @staticmethod
    def _mapped_config(run_test, organization, name="Mapped regrade"):
        template = EvalTemplate.objects.create(
            name=f"{name} template",
            config={"required_keys": ["output"], "output": "Pass/Fail"},
            organization=organization,
        )
        return SimulateEvalConfig.objects.create(
            name=name,
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={"output": "transcript"},
        )

    @staticmethod
    def _post_run_new_evals(client, run_test, executions, configs, **extra):
        return client.post(
            f"/simulate/run-tests/{run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(execution.id) for execution in executions],
                "eval_config_ids": [str(config.id) for config in configs],
                **extra,
            },
            format="json",
        )

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_refuses_a_run_with_no_calls_before_writing_anything(
        self,
        mock_apply_async,
        auth_client,
        run_test,
        test_execution,
        organization,
    ):
        cfg = self._mapped_config(run_test, organization)
        metadata_before = dict(test_execution.execution_metadata or {})

        response = self._post_run_new_evals(
            auth_client,
            run_test,
            [test_execution],
            [cfg],
            enable_tool_evaluation=True,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["message"] == (
            "No call executions found in the selected test executions."
        )
        mock_apply_async.assert_not_called()
        test_execution.refresh_from_db()
        run_test.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert (test_execution.execution_metadata or {}) == metadata_before
        assert run_test.enable_tool_evaluation is False

    @pytest.mark.parametrize(
        ("call_statuses", "graded"),
        [
            ([CallExecution.CallStatus.FAILED], False),
            (
                [CallExecution.CallStatus.FAILED, CallExecution.CallStatus.CANCELLED],
                False,
            ),
            (
                [CallExecution.CallStatus.FAILED, CallExecution.CallStatus.COMPLETED],
                True,
            ),
        ],
        ids=["all-failed", "failed-and-cancelled", "one-completed"],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_refuses_a_run_whose_calls_never_completed(
        self,
        mock_apply_async,
        call_statuses,
        graded,
        auth_client,
        run_test,
        test_execution,
        scenario,
        organization,
    ):
        """The graders skip every call that did not complete, and a run leaves
        EVALUATING only once a completed call has been graded; one completed
        call is enough."""
        calls = [
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number="+1234567890",
                status=call_status,
                eval_outputs={"eval1": {"score": 0.9}},
            )
            for call_status in call_statuses
        ]
        cfg = self._mapped_config(run_test, organization)

        response = self._post_run_new_evals(
            auth_client, run_test, [test_execution], [cfg]
        )

        test_execution.refresh_from_db()
        if graded:
            assert response.status_code == status.HTTP_200_OK, response.data
            mock_apply_async.assert_called_once()
            assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
            return
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["message"] == (
            "Nothing to grade again: no call in this run completed."
        )
        mock_apply_async.assert_not_called()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        for call in calls:
            call.refresh_from_db()
            assert call.eval_outputs == {"eval1": {"score": 0.9}}

    @pytest.mark.parametrize(
        ("failed_runs", "message_start"),
        [
            (1, "1 of the 2 selected runs has no completed call: "),
            (3, "3 of the 4 selected runs have no completed call: "),
            (4, "4 of the 5 selected runs have no completed call: "),
        ],
        ids=["one", "all-shown", "more-than-shown"],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_refuses_a_selection_with_one_run_that_has_no_completed_call(
        self,
        mock_apply_async,
        failed_runs,
        message_start,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        scenario,
        organization,
    ):
        """Grading the other runs would still leave these in EVALUATING, and
        the refusal names which of the selected runs they are."""
        failed = []
        for _ in range(failed_runs):
            failed_run = TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.COMPLETED,
                total_scenarios=1,
                total_calls=1,
            )
            CallExecution.objects.create(
                test_execution=failed_run,
                scenario=scenario,
                phone_number="+1234567890",
                status=CallExecution.CallStatus.FAILED,
            )
            failed.append(failed_run)
        cfg = self._mapped_config(run_test, organization)

        response = self._post_run_new_evals(
            auth_client, run_test, [test_execution, *failed], [cfg]
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        message = response.data["message"]
        assert message.startswith(message_start) and message.endswith(".")
        listed = message[len(message_start) : -1].split(", ")
        if failed_runs > 3:
            assert listed.pop() == "..."
        assert len(listed) == min(failed_runs, 3)
        assert set(listed) <= {str(run.id) for run in failed}
        mock_apply_async.assert_not_called()
        for execution in (test_execution, *failed):
            execution.refresh_from_db()
            assert execution.status == TestExecution.ExecutionStatus.COMPLETED

    @pytest.mark.parametrize(
        "raced", ["the-only-run", "one-of-two-runs"], ids=lambda raced: raced
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_refuses_a_second_request_that_raced_the_first(
        self,
        mock_apply_async,
        raced,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        scenario,
        organization,
    ):
        """Both requests read COMPLETED; the first moves the run to EVALUATING
        before the second writes. The second claims nothing, writes nothing,
        and dispatches nothing."""
        executions = [test_execution]
        if raced == "one-of-two-runs":
            other = TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.COMPLETED,
                total_scenarios=1,
                total_calls=1,
            )
            CallExecution.objects.create(
                test_execution=other,
                scenario=scenario,
                phone_number="+1234567890",
                status=CallExecution.CallStatus.COMPLETED,
            )
            executions.append(other)
        cfg = self._mapped_config(run_test, organization)
        outputs_before = dict(call_execution.eval_outputs)
        metadata_before = dict(test_execution.execution_metadata or {})
        # The last read before the claim is the completed-call check; the
        # first request's claim lands right after it.
        manager = CallExecution.objects
        real_filter = manager.filter

        def first_request_claims_in_between(*args, **kwargs):
            if kwargs.get("status") == CallExecution.CallStatus.COMPLETED:
                TestExecution.objects.filter(id=test_execution.id).update(
                    status=TestExecution.ExecutionStatus.EVALUATING
                )
            return real_filter(*args, **kwargs)

        with patch.object(
            manager, "filter", side_effect=first_request_claims_in_between
        ):
            response = self._post_run_new_evals(
                auth_client,
                run_test,
                executions,
                [cfg],
                enable_tool_evaluation=True,
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["message"] == "Grading is already running on this run."
        mock_apply_async.assert_not_called()
        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        run_test.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        assert (test_execution.execution_metadata or {}) == metadata_before
        assert call_execution.eval_outputs == outputs_before
        assert run_test.enable_tool_evaluation is False
        for execution in executions[1:]:
            execution.refresh_from_db()
            assert execution.status == TestExecution.ExecutionStatus.COMPLETED

    def _suite_eval_on_a_finished_harness_run(self, run_test, test_execution):
        self._hosted_job_for(test_execution, HostedHarnessJob.State.COMPLETED)
        template = EvalTemplate.objects.create(
            name=f"suite_eval_{uuid.uuid4().hex[:6]}",
            config={"required_keys": ["conversation"], "output": "Pass/Fail"},
            owner="system",
        )
        return SimulateEvalConfig.objects.create(
            name="Task completion",
            eval_template=template,
            run_test=run_test,
            config={},
            mapping={},
        )

    @pytest.mark.parametrize(
        "failing_write",
        [CallExecution, TestExecution],
        ids=["placeholders", "column-order"],
    )
    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_leaves_the_run_as_it_was_when_a_write_after_the_claim_fails(
        self,
        mock_apply_async,
        failing_write,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        cfg = self._suite_eval_on_a_finished_harness_run(run_test, test_execution)
        outputs_before = dict(call_execution.eval_outputs)
        call_metadata_before = dict(call_execution.call_metadata or {})
        metadata_before = dict(test_execution.execution_metadata or {})

        with patch.object(
            failing_write.objects,
            "bulk_update",
            side_effect=OperationalError("statement timeout"),
        ):
            response = self._post_run_new_evals(
                auth_client,
                run_test,
                [test_execution],
                [cfg],
                enable_tool_evaluation=True,
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        mock_apply_async.assert_not_called()
        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        run_test.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert (test_execution.execution_metadata or {}) == metadata_before
        assert call_execution.eval_outputs == outputs_before
        assert (call_execution.call_metadata or {}) == call_metadata_before
        assert run_test.enable_tool_evaluation is False

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_on_every_run_leaves_out_a_run_started_meanwhile(
        self,
        mock_apply_async,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        scenario,
    ):
        cfg = self._suite_eval_on_a_finished_harness_run(run_test, test_execution)
        started = []
        jobs = HostedHarnessJob.no_workspace_objects
        real_filter = jobs.filter

        def a_run_starts_during_the_job_read():
            run = TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.PENDING,
                total_scenarios=1,
                total_calls=1,
            )
            CallExecution.objects.create(
                test_execution=run,
                scenario=scenario,
                phone_number="+1234567890",
                status=CallExecution.CallStatus.COMPLETED,
            )
            started.append(run)
            return False

        def job_filter(*args, **kwargs):
            if "test_execution__in" not in kwargs:
                return real_filter(*args, **kwargs)
            job_read = MagicMock()
            job_read.exclude.return_value.exists.side_effect = (
                a_run_starts_during_the_job_read
            )
            return job_read

        with patch.object(jobs, "filter", side_effect=job_filter):
            response = auth_client.post(
                f"/simulate/run-tests/{run_test.id}/run-new-evals/",
                {"select_all": True, "eval_config_ids": [str(cfg.id)]},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        mock_apply_async.assert_called_once_with(
            args=([str(call_execution.id)], [str(cfg.id)])
        )
        (run,) = started
        run.refresh_from_db()
        assert run.status == TestExecution.ExecutionStatus.PENDING
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING


# ============================================================================
# TestExecutionRerunSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestTestExecutionRerunSerializer:
    """Tests for TestExecutionRerunSerializer validation."""

    def test_valid_with_select_all(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(
            data={"rerun_type": "eval_only", "select_all": True}
        )
        assert serializer.is_valid()

    def test_valid_with_test_execution_ids(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(
            data={
                "rerun_type": "call_and_eval",
                "test_execution_ids": [str(uuid.uuid4())],
            }
        )
        assert serializer.is_valid()

    def test_invalid_missing_both(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(data={"rerun_type": "eval_only"})
        assert not serializer.is_valid()

    def test_invalid_rerun_type(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(
            data={"rerun_type": "bad_type", "select_all": True}
        )
        assert not serializer.is_valid()


# ============================================================================
# TestExecutionBulkDeleteView Tests (new endpoint)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionBulkDeleteView:
    """Tests for POST /simulate/run-tests/<uuid>/delete-test-executions/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/delete-test-executions/"

    def test_delete_select_all(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test deleting all test executions with select_all=True."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == 2
        assert len(data["deleted_ids"]) == 2

        # Verify they're actually deleted
        assert TestExecution.objects.filter(run_test=run_test).count() == 0
        # Verify cascade deleted call executions
        assert (
            CallExecution.objects.filter(test_execution__run_test=run_test).count() == 0
        )

    def test_delete_specific_ids(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test deleting specific test executions by IDs."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"test_execution_ids": [str(test_execution.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == 1
        assert str(test_execution.id) in data["deleted_ids"]

        # test_execution_2 should still exist
        assert TestExecution.objects.filter(id=test_execution_2.id).exists()
        assert not TestExecution.objects.filter(id=test_execution.id).exists()

    def test_delete_select_all_with_exclusion(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test select_all=True with test_execution_ids as exclusion list."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "select_all": True,
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == 1
        assert str(test_execution_2.id) in data["deleted_ids"]

        # test_execution should still exist (excluded)
        assert TestExecution.objects.filter(id=test_execution.id).exists()

    def test_delete_blocks_running_executions(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        """Test that running test executions cannot be deleted."""
        test_execution.status = TestExecution.ExecutionStatus.RUNNING
        test_execution.save()

        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Verify nothing was deleted
        assert TestExecution.objects.filter(id=test_execution.id).exists()

    def test_delete_missing_params(self, auth_client, run_test):
        """Test delete with neither select_all nor test_execution_ids."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_rejects_unknown_fields(self, auth_client, run_test):
        """Unknown request fields should fail before matching executions."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"select_all": True, "legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_delete_nonexistent_run_test(self, auth_client):
        """Test delete with non-existent run_test_id."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_unauthenticated(self, api_client, run_test):
        """Test delete without authentication."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = api_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_delete_no_matching_test_executions(self, auth_client, run_test):
        """Test delete when no test executions match."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"test_execution_ids": [str(uuid.uuid4())]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionDeleteView:
    """Tests for DELETE /simulate/test-executions/<uuid>/delete/.

    Happy-path child-cascade coverage lives in
    ``test_call_execution_action_scope.TestCallExecutionActionScope
    ::test_test_execution_delete_soft_deletes_child_call_execution``, and
    cross-tenant coverage lives beside it. This class fills the remaining
    not-found + unauthenticated gaps.
    """

    def test_test_execution_delete_not_found_returns_404(self, auth_client):
        response = auth_client.delete(
            f"/simulate/test-executions/{uuid.uuid4()}/delete/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(response.content).lower()

    def test_test_execution_delete_unauthenticated_returns_401(
        self, api_client, test_execution
    ):
        response = api_client.delete(
            f"/simulate/test-executions/{test_execution.id}/delete/"
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        test_execution.refresh_from_db()
        assert test_execution.deleted is False


# ============================================================================
# RunTestEvalExplanationSummaryRefreshView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestEvalExplanationSummaryRefreshView:
    """Tests for POST /simulate/test-executions/<uuid>/eval-explanation-summary/refresh/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/eval-explanation-summary/refresh/"

    @patch(
        "simulate.views.run_test.run_eval_summary_task.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_eval_explanation_refresh_marks_failed_when_dispatch_fails(
        self, mock_apply_async, auth_client, test_execution
    ):
        # Seed a terminal state so we can prove the view moved it off COMPLETED
        # and did not leave it stuck mid-refresh when dispatch blew up.
        test_execution.eval_explanation_summary_status = (
            EvalExplanationSummaryStatus.COMPLETED
        )
        test_execution.save(update_fields=["eval_explanation_summary_status"])

        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert "marked pending" in response.data["result"]["message"]
        mock_apply_async.assert_called_once_with(args=(str(test_execution.id),))

        test_execution.refresh_from_db()
        # Should be PENDING or FAILED; must not be stuck in a live state like
        # RUNNING or lie about COMPLETED.
        assert test_execution.eval_explanation_summary_status in {
            EvalExplanationSummaryStatus.PENDING,
            EvalExplanationSummaryStatus.FAILED,
        }
        assert test_execution.eval_explanation_summary_status not in {
            EvalExplanationSummaryStatus.RUNNING,
            EvalExplanationSummaryStatus.COMPLETED,
        }


@pytest.mark.integration
@pytest.mark.api
class TestOptimiserAnalysisRefreshView:
    URL_TEMPLATE = "/simulate/test-executions/{}/optimiser-analysis/refresh/"

    @patch("simulate.utils.agent_optimiser.prepare_simulation_analysis_input")
    @patch("simulate.tasks.agent_optimiser_tasks.execute_optimiser_run")
    def test_optimiser_analysis_refresh_marks_failed_when_dispatch_fails(
        self,
        mock_task,
        mock_prepare,
        auth_client,
        test_execution,
        call_execution,
    ):
        from simulate.models import AgentOptimiserRun

        mock_prepare.return_value = {"test_execution_id": str(test_execution.id)}
        mock_task.delay.side_effect = TimeoutError("temporal dispatch timed out")

        response = auth_client.post(
            self.URL_TEMPLATE.format(test_execution.id), {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert (
            response.data["result"]["status"]
            == AgentOptimiserRun.OptimiserStatus.FAILED
        )
        mock_task.delay.assert_called_once()

        run = AgentOptimiserRun.objects.order_by("-created_at").first()
        assert run is not None
        assert run.status == AgentOptimiserRun.OptimiserStatus.FAILED
        assert (run.metadata or {}).get("error", {}).get("dispatch_error") == (
            "temporal dispatch timed out"
        )


# ============================================================================
# Eval-only rerun: the request, then the grader
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestEvalOnlyRerunRequestAndGraderAgree:
    """The request and the grader settle on the same evals for a call: every
    row the request marks pending the grader fills, and every row the request
    keeps the grader leaves alone."""

    @staticmethod
    def _fresh_verdict(eval_config):
        return {
            "output": "Failed",
            "reason": "regraded",
            "output_type": "Pass/Fail",
            "name": eval_config.name,
        }

    @staticmethod
    def _regrades(eval_config, call_execution, transcript_data):
        """Stand in for the judge: store a fresh verdict for this eval."""
        call_execution.eval_outputs[str(eval_config.id)] = (
            TestEvalOnlyRerunRequestAndGraderAgree._fresh_verdict(eval_config)
        )
        call_execution.save(update_fields=["eval_outputs"])

    @pytest.mark.parametrize("harness_scored_by", ["job", "sdk", "plain"])
    @patch("simulate.temporal.activities.xl._run_single_evaluation")
    @patch("simulate.temporal.activities.xl._build_transcript_data")
    @patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_no_row_is_left_pending_and_every_harness_column_survives(
        self,
        mock_rerun,
        mock_transcript,
        mock_single,
        harness_scored_by,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
    ):
        """After the request and then the grader, no row still reads pending,
        every row the harness wrote is as it was, and each eval the platform
        grades on the call holds its new verdict."""
        from simulate.temporal.activities.xl import _run_evaluations_standalone

        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        mock_transcript.return_value = {"transcript": "agent: Hello.\nuser: Hi."}
        mock_single.side_effect = self._regrades
        request_side = TestEvalOnlyRerunKeepsHarnessChecks
        if harness_scored_by != "plain":
            request_side._scored_by(harness_scored_by, run_test, call_execution)
        rows = request_side._seed_harness_rows(call_execution, organization)
        filled, own = rows.configs
        outputs_before = copy.deepcopy(call_execution.eval_outputs)

        response = auth_client.post(
            request_side.CALL_URL.format(test_execution.id),
            {
                "rerun_type": "eval_only",
                "select_all": False,
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert str(call_execution.id) in response.json()["successful_reruns"]

        _run_evaluations_standalone(call_execution, skip_existing=False)

        call_execution.refresh_from_db()
        outputs = call_execution.eval_outputs
        regraded = [own, filled] if harness_scored_by == "plain" else [own]
        assert [
            key for key, row in outputs.items() if row.get("status") == "pending"
        ] == []
        assert outputs[rows.code_check] == outputs_before[rows.code_check]
        if harness_scored_by == "plain":
            assert outputs[rows.filled] == self._fresh_verdict(filled)
        else:
            assert outputs[rows.filled] == outputs_before[rows.filled]
        assert outputs[rows.own] == self._fresh_verdict(own)
        assert outputs == {
            **outputs_before,
            **{str(config.id): self._fresh_verdict(config) for config in regraded},
        }
        assert call_execution.call_metadata["eval_completed"] is True

    @pytest.mark.parametrize("harness_scored_by", ["job", "sdk", "plain"])
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_a_failed_grading_job_marks_skipped_only_what_the_request_marked_pending(
        self,
        mock_rerun,
        harness_scored_by,
        auth_client,
        organization,
        run_test,
        test_execution,
        call_execution,
    ):
        """When the grading job fails as a whole, the rows the request marked
        pending read skipped and every row the harness wrote is as it was."""
        from asgiref.sync import async_to_sync

        from simulate.temporal.activities.small import persist_processing_skip_state
        from simulate.temporal.types.activities import PersistProcessingSkipStateInput

        mock_rerun.return_value = {"workflow_id": "wf", "merged": False}
        request_side = TestEvalOnlyRerunKeepsHarnessChecks
        if harness_scored_by != "plain":
            request_side._scored_by(harness_scored_by, run_test, call_execution)
        rows = request_side._seed_harness_rows(call_execution, organization)
        filled, own = rows.configs
        outputs_before = copy.deepcopy(call_execution.eval_outputs)
        reason = "Call failed during evaluation processing, so processing was skipped."

        response = auth_client.post(
            request_side.CALL_URL.format(test_execution.id),
            {
                "rerun_type": "eval_only",
                "select_all": False,
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert str(call_execution.id) in response.json()["successful_reruns"]

        async_to_sync(persist_processing_skip_state)(
            PersistProcessingSkipStateInput(
                call_id=str(call_execution.id),
                processing_skipped=True,
                processing_skip_reason=reason,
                mark_eval_outputs_skipped=True,
            )
        )

        call_execution.refresh_from_db()
        marked = [own, filled] if harness_scored_by == "plain" else [own]
        assert call_execution.eval_outputs == {
            **outputs_before,
            **{
                str(config.id): build_skipped_eval_output_payload(
                    eval_name=config.name, reason=reason
                )
                for config in marked
            },
        }
        assert call_execution.call_metadata["eval_completed"] is True
