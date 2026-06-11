"""TH-5610 integration tests: simulation execution endpoints must 402
when a hard-cap plan has exhausted its free allowance."""

from unittest.mock import patch

import pytest

pytest.importorskip("ee.usage")  # gate is a no-op on OSS; nothing to test there

from ee.usage.schemas.events import CheckResult, UpgradeCTA
from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent

DENIED = CheckResult(
    allowed=False,
    error_code="FREE_TIER_LIMIT",
    reason="Free tier Voice Simulation Minutes limit reached",
    dimension="voice_sim_minutes",
    current_usage=61,
    limit=60,
    upgrade_cta=UpgradeCTA(
        text="Upgrade to Pay-as-you-go for unlimited usage", plan="payg"
    ),
)
ALLOWED = CheckResult(allowed=True)


@pytest.fixture
def agent_definition(db, organization, workspace):
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
def dataset_for_scenario(db, organization, user, workspace):
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


@pytest.mark.django_db
def test_run_test_execute_blocked_when_free_tier_exhausted(auth_client, run_test):
    with (
        patch("simulate.views.run_test._voice_sim_gate_response", return_value=None),
        patch(
            "ee.usage.services.metering.check_usage", return_value=DENIED
        ) as mock_check,
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/execute/",
            {"scenario_ids": []},
            format="json",
        )

    assert response.status_code == 402
    assert response.data["error_code"] == "FREE_TIER_LIMIT"
    mock_check.assert_called_once()
    assert mock_check.call_args.args[1] == "voice_call"


@pytest.mark.django_db
def test_run_test_execute_proceeds_when_allowed(auth_client, run_test):
    with (
        patch("simulate.views.run_test._voice_sim_gate_response", return_value=None),
        patch("ee.usage.services.metering.check_usage", return_value=ALLOWED),
        patch(
            "simulate.views.run_test.RunTestExecutionView._execute_with_temporal",
            return_value={
                "success": True,
                "execution_id": "exec-1",
                "run_test_id": "rt-1",
                "status": "pending",
                "total_scenarios": 1,
            },
        ),
        patch(
            "simulate.services.test_executor.TestExecutor.execute_test",
            return_value={
                "success": True,
                "execution_id": "exec-1",
                "run_test_id": "rt-1",
                "status": "pending",
                "total_scenarios": 1,
            },
        ),
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/execute/",
            {"scenario_ids": []},
            format="json",
        )

    assert response.status_code == 200


@pytest.mark.django_db
def test_chat_execute_blocked_with_text_dimension(auth_client, run_test):
    with patch(
        "ee.usage.services.metering.check_usage", return_value=DENIED
    ) as mock_check:
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/chat-execute/", {}, format="json"
        )

    assert response.status_code == 402
    assert response.data["error_code"] == "FREE_TIER_LIMIT"
    assert mock_check.call_args.args[1] == "text_call"


@pytest.mark.django_db
def test_prompt_simulation_execute_blocked_with_text_dimension(auth_client, run_test):
    import uuid

    with patch(
        "ee.usage.services.metering.check_usage", return_value=DENIED
    ) as mock_check:
        response = auth_client.post(
            f"/simulate/prompt-templates/{uuid.uuid4()}/simulations/{run_test.id}/execute/",
            {},
            format="json",
        )

    assert response.status_code == 402
    assert mock_check.call_args.args[1] == "text_call"


@pytest.mark.django_db
def test_rerun_test_executions_blocked_when_free_tier_exhausted(auth_client, run_test):
    with (
        patch("simulate.views.run_test._voice_sim_gate_response", return_value=None),
        patch(
            "ee.usage.services.metering.check_usage", return_value=DENIED
        ) as mock_check,
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/rerun-test-executions/",
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

    assert response.status_code == 402
    assert mock_check.call_args.args[1] == "voice_call"
