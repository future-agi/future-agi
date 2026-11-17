"""Defaults for newly created simulator agents; explicit choices still win."""

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    RunTest,
    Scenarios,
    TestExecution,
)
from simulate.serializers.requests.scenarios import ScenarioCreateRequestSerializer
from simulate.services.alk_simulate_ingestion import _simulator_llm_model
from simulate.services.test_executor import TestExecutor


def test_scenario_model_defaults_to_gpt_4o():
    field = ScenarioCreateRequestSerializer().fields["model"]

    assert field.run_validation() == "gpt-4o"


def test_scenario_model_preserves_explicit_choice():
    field = ScenarioCreateRequestSerializer().fields["model"]

    assert field.run_validation("custom-model") == "custom-model"


@pytest.mark.parametrize("configured_model", [None, "", "custom-model"])
def test_hosted_simulator_model_matches_runner_default(monkeypatch, configured_model):
    if configured_model is None:
        monkeypatch.delenv("SIMULATOR_LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("SIMULATOR_LLM_MODEL", configured_model)

    assert _simulator_llm_model() == (configured_model or "gpt-4.1")


@pytest.mark.integration
@pytest.mark.api
def test_chat_batch_fallback_simulator_model_defaults_to_gpt_4o(
    auth_client, organization, workspace
):
    agent_definition = AgentDefinition.objects.create(
        agent_name="Chat Support Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+1234567890",
        inbound=True,
        description="Test chat agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    agent_version = agent_definition.create_version(
        description="Active text chat version",
        commit_message="Initial chat version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    # Neither the scenario nor the run test has a simulator agent, so the batch
    # view creates the fallback agent for the scenario.
    scenario = Scenarios.objects.create(
        name="Chat Support Scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )
    run_test = RunTest.objects.create(
        name="Chat Test Run",
        agent_definition=agent_definition,
        agent_version=agent_version,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        scenario_ids=[str(scenario.id)],
        agent_definition=agent_definition,
        agent_version=agent_version,
    )

    response = auth_client.post(
        f"/simulate/test-executions/{test_execution.id}/chat/call-executions/batch/",
        {},
        format="json",
    )

    assert response.status_code == 200
    scenario.refresh_from_db()
    assert scenario.simulator_agent.model == "gpt-4o"


@pytest.mark.django_db
def test_executor_fallback_simulator_agent_defaults_to_gpt_4o(
    organization, workspace, user
):
    # A prompt simulation whose scenario has no simulator agent gets one
    # created by the executor when the run starts.
    template = PromptTemplate.objects.create(
        name="Default model prompt", organization=organization, workspace=workspace
    )
    version = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True
    )
    run_test = RunTest.objects.create(
        name="Default model simulation",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=version,
        organization=organization,
        workspace=workspace,
    )
    dataset = Dataset.objects.create(
        name="Default model dataset", organization=organization, workspace=workspace
    )
    # Completed, as both execute endpoints require before they run a test.
    scenario = Scenarios.objects.create(
        name="Default model scenario",
        source="Dataset rows",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    run_test.scenarios.add(scenario)
    Row.objects.create(dataset=dataset, order=0)

    result = TestExecutor(initialize_voice_service=False).execute_test(
        run_test_id=str(run_test.id),
        user_id=str(user.id),
        scenario_ids=[str(scenario.id)],
    )

    assert result["success"] is True, result
    scenario.refresh_from_db()
    assert scenario.simulator_agent is not None
    assert scenario.simulator_agent.model == "gpt-4o"
