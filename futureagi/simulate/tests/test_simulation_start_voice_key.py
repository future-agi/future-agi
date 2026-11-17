"""A run builds the voice services only when it calls a voice agent.

A prompt simulation is a chat: it starts on an install with no
``VAPI_API_KEY``, from its own execute endpoint and from the run test one, and
so does the chat it opens. A voice agent's run on such an install is refused
with one sentence that names the missing key. With the key set, a voice run
builds the voice services and starts as before.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import RunTest, Scenarios, TestExecution
from simulate.services import test_executor as test_executor_module
from simulate.services.chat_sim import initiate_chat
from simulate.services.test_executor import TestExecutor
from simulate.views.run_test import RunTestExecutionView
from tfc.settings import settings as app_settings

KEY_MISSING = (
    "Voice simulations need a Vapi API key. Add VAPI_API_KEY to this install, "
    "then run again."
)


@pytest.fixture
def no_voice_key(monkeypatch):
    monkeypatch.delenv("VAPI_API_KEY", raising=False)


@pytest.fixture
def voice_services():
    """The voice services as built, still the real ones."""
    real = test_executor_module.VoiceServiceManager
    with patch.object(test_executor_module, "VoiceServiceManager", wraps=real) as spy:
        yield spy


# --------------------------------------------------------------------------
# Prompt simulation execute endpoint
# --------------------------------------------------------------------------


@pytest.fixture
def prompt_simulation(organization, workspace):
    template = PromptTemplate.objects.create(
        name="No voice key prompt", organization=organization, workspace=workspace
    )
    version = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True
    )
    run_test = RunTest.objects.create(
        name="No voice key simulation",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=version,
        organization=organization,
        workspace=workspace,
    )
    dataset = Dataset.objects.create(
        name="No voice key rows", organization=organization, workspace=workspace
    )
    scenario = Scenarios.objects.create(
        name="No voice key scenario",
        source="Dataset rows",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    run_test.scenarios.add(scenario)
    rows = [Row.objects.create(dataset=dataset, order=order) for order in range(2)]
    return SimpleNamespace(template=template, run_test=run_test, rows=rows)


@pytest.mark.django_db
def test_a_prompt_simulation_starts_with_no_voice_key(
    auth_client, prompt_simulation, no_voice_key, voice_services
):
    with patch.object(
        TestExecutor, "_execute_call", return_value={"success": True}
    ) as call:
        response = auth_client.post(
            f"/simulate/prompt-templates/{prompt_simulation.template.id}"
            f"/simulations/{prompt_simulation.run_test.id}/execute/",
            {},
            format="json",
        )

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert result["message"] == "Simulation execution started successfully"
    assert result["total_calls"] == 2
    assert call.call_count == 2
    assert TestExecution.objects.filter(
        id=result["execution_id"], run_test=prompt_simulation.run_test
    ).exists()
    voice_services.assert_not_called()


# --------------------------------------------------------------------------
# Run test execute endpoint, legacy executor
# --------------------------------------------------------------------------


@pytest.fixture
def legacy_executor():
    """The run test endpoint on the legacy executor, with the run's start
    answered here instead of by the database."""
    started = {
        "success": True,
        "run_test_id": "",
        "execution_id": str(uuid4()),
        "status": "started",
        "total_scenarios": 1,
        "total_calls": 1,
    }
    with (
        patch.object(app_settings, "HOSTED_RUNNER_ENABLED", False, create=True),
        patch.object(
            app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", False, create=True
        ),
        patch("simulate.views.run_test.check_scenarios_incomplete", return_value=None),
        patch(
            "simulate.views.run_test._latest_repository_harness_execution",
            return_value=None,
        ),
        patch("simulate.views.run_test._voice_sim_gate_response", return_value=None),
        patch.object(TestExecutor, "execute_test", return_value=started) as execute,
    ):
        yield execute


def run_test_of(source_type, agent_type=None):
    run = MagicMock()
    run.id = uuid4()
    run.source_type = source_type
    run.agent_definition = MagicMock() if agent_type else None
    run.scenarios.filter.return_value.values_list.return_value = [uuid4()]
    return run


def start(run, agent_type=None):
    request = APIRequestFactory().post("/execute/", {}, format="json")
    user = SimpleNamespace(id=uuid4(), is_authenticated=True, organization=object())
    request.organization = user.organization
    request.workspace = SimpleNamespace(id=uuid4(), is_default=False)
    force_authenticate(request, user=user)
    with (
        patch("simulate.views.run_test.get_object_or_404", return_value=run),
        patch(
            "simulate.services.hosted_runner.agent_field_for_run",
            return_value=agent_type,
        ),
    ):
        return RunTestExecutionView.as_view()(request, run_test_id=run.id)


def test_a_voice_simulation_with_no_voice_key_is_refused_with_one_sentence(
    legacy_executor, no_voice_key, voice_services
):
    response = start(run_test_of("agent_definition", "voice"), "voice")

    assert response.status_code == 400
    assert response.data["message"] == KEY_MISSING
    assert "Something went wrong" not in str(response.data)
    voice_services.assert_called_once()
    legacy_executor.assert_not_called()


@pytest.mark.parametrize(
    ("source_type", "agent_type"),
    [("prompt", None), ("agent_definition", "text")],
    ids=["prompt-simulation", "chat-agent"],
)
def test_a_chat_run_starts_from_the_run_test_endpoint_with_no_voice_key(
    legacy_executor, no_voice_key, voice_services, source_type, agent_type
):
    response = start(run_test_of(source_type, agent_type), agent_type)

    assert response.status_code == 200, response.data
    assert response.data["message"] == "Test execution started successfully"
    legacy_executor.assert_called_once()
    voice_services.assert_not_called()


def test_a_voice_simulation_with_the_voice_key_builds_the_voice_services(
    legacy_executor, monkeypatch, voice_services
):
    monkeypatch.setenv("VAPI_API_KEY", "test-vapi-key")

    response = start(run_test_of("agent_definition", "voice"), "voice")

    assert response.status_code == 200, response.data
    assert response.data["message"] == "Test execution started successfully"
    voice_services.assert_called_once()
    legacy_executor.assert_called_once()


def test_a_prompt_simulation_with_the_voice_key_starts_as_before(
    legacy_executor, monkeypatch, voice_services
):
    monkeypatch.setenv("VAPI_API_KEY", "test-vapi-key")

    response = start(run_test_of("prompt"))

    assert response.status_code == 200, response.data
    legacy_executor.assert_called_once()
    voice_services.assert_not_called()


# --------------------------------------------------------------------------
# The chat a prompt simulation opens
# --------------------------------------------------------------------------


def test_a_chat_checks_its_balance_with_no_voice_key(no_voice_key, voice_services):
    call_execution = MagicMock()
    refusal = (False, 0, 0, "Usage limit exceeded")

    with (
        patch.object(TestExecutor, "_check_call_balance", return_value=refusal),
        pytest.raises(ValueError) as raised,
    ):
        initiate_chat(call_execution, organization=MagicMock(), workspace=None)

    assert "Usage limit exceeded" in str(raised.value)
    assert "VAPI_API_KEY" not in str(raised.value)
    assert call_execution.ended_reason.endswith("Usage limit exceeded")
    voice_services.assert_not_called()
