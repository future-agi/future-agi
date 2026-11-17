"""Running a simulation with Temporal test execution on.

A run test's saved dataset rows follow the same rule on the Temporal path as
on the legacy executor: only the saved rows that still exist run, the
execution records how many were skipped and the answer says so, and when none
of them is left the run is refused with the same sentence before any workflow
starts. The workflow's setup expands only those rows into calls; a scenario
none of whose rows is saved makes no call.

A prompt-based run test is a chat whatever the Temporal setting: its calls are
queued for the prompt chat dispatcher, it builds no voice services and starts
no voice workflow, so it runs with no voice key set.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    Scenarios,
    TestExecution,
)
from simulate.models.agent_version import AgentVersion
from simulate.services import test_executor as test_executor_module
from simulate.temporal.types.activities import SetupTestInput
from tfc.middleware.workspace_context import set_workspace_context, workspace_context
from tfc.settings import settings as app_settings

NOTHING_LEFT = (
    "None of the dataset rows saved for this test are available any more. "
    "Clear the row selection or pick rows again."
)
START_WORKFLOW = "simulate.temporal.client.start_test_execution_workflow"
VOICE_GATE = "simulate.views.run_test._voice_sim_gate_response"


@pytest.fixture
def temporal_on():
    """Temporal test execution on, with the workflow start answered here.

    The execute view reads both flags from the settings module itself, which
    override_settings does not reach, so the module is set as well.
    """
    with (
        override_settings(
            TEMPORAL_TEST_EXECUTION_ENABLED=True, HOSTED_RUNNER_ENABLED=False
        ),
        patch.object(
            app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", True, create=True
        ),
        patch.object(app_settings, "HOSTED_RUNNER_ENABLED", False, create=True),
        patch(START_WORKFLOW, return_value="test-execution-workflow") as start,
    ):
        yield start


@pytest.fixture
def no_voice_key(monkeypatch):
    monkeypatch.delenv("VAPI_API_KEY", raising=False)


@pytest.fixture
def voice_services():
    """The voice services as built, still the real ones."""
    real = test_executor_module.VoiceServiceManager
    with patch.object(test_executor_module, "VoiceServiceManager", wraps=real) as spy:
        yield spy


def _dataset_scenario(organization, workspace, name, row_count):
    """A completed dataset scenario and the rows of its dataset."""
    dataset = Dataset.objects.create(
        name=f"{name} rows", organization=organization, workspace=workspace
    )
    scenario = Scenarios.objects.create(
        name=name,
        source="Dataset rows",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    rows = [
        Row.objects.create(dataset=dataset, order=order) for order in range(row_count)
    ]
    return SimpleNamespace(scenario=scenario, rows=rows)


@pytest.fixture
def voice_run_test(organization, workspace):
    """A voice agent's run test with one dataset scenario of three rows."""
    agent = AgentDefinition.objects.create(
        agent_name="Temporal voice agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15550100",
        inbound=True,
        description="Voice agent run on Temporal",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    version = AgentVersion.objects.create(
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={"agent_type": "voice", "contact_number": "+15550100"},
    )
    run_test = RunTest.objects.create(
        name="Temporal voice run test",
        agent_definition=agent,
        agent_version=version,
        organization=organization,
        workspace=workspace,
    )
    first = _dataset_scenario(organization, workspace, "Temporal voice scenario", 3)
    run_test.scenarios.add(first.scenario)
    return SimpleNamespace(
        run_test=run_test,
        scenario=first.scenario,
        rows=first.rows,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_run_test(organization, workspace):
    """A prompt-based run test with one dataset scenario of three rows."""
    template = PromptTemplate.objects.create(
        name="Temporal prompt", organization=organization, workspace=workspace
    )
    version = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True
    )
    run_test = RunTest.objects.create(
        name="Temporal prompt run test",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=version,
        organization=organization,
        workspace=workspace,
    )
    first = _dataset_scenario(organization, workspace, "Temporal prompt scenario", 3)
    run_test.scenarios.add(first.scenario)
    return SimpleNamespace(template=template, run_test=run_test, rows=first.rows)


def _execute(auth_client, run_test, saved_rows=(), deleted_rows=()):
    """Save ``saved_rows``, delete ``deleted_rows``, then press Run."""
    run_test.dataset_row_ids = [str(row.id) for row in saved_rows]
    run_test.save(update_fields=["dataset_row_ids"])
    Row._base_manager.filter(id__in=[row.id for row in deleted_rows]).update(
        deleted=True
    )
    with patch(VOICE_GATE, return_value=None):
        return auth_client.post(
            f"/simulate/run-tests/{run_test.id}/execute/", {}, format="json"
        )


def _set_up(execution):
    """What the started workflow's setup step hands on to call creation.

    It runs as the worker runs it, with no workspace, organization or user
    bound, so the rows it picks come from its own scoping.
    """
    from simulate.temporal.activities import test_execution as activities

    with (
        workspace_context(None, organization=None, user=None),
        patch("temporalio.activity.info"),
        patch.object(activities, "close_old_connections", lambda: None),
    ):
        set_workspace_context(require_workspace=False)
        return async_to_sync(activities.setup_test_execution)(
            SetupTestInput(
                test_execution_id=str(execution.id),
                run_test_id=str(execution.run_test_id),
                scenario_ids=[
                    str(scenario_id) for scenario_id in execution.scenario_ids
                ],
            )
        )


def _rows_by_scenario(setup):
    assert setup.success, setup.error
    return {scenario["id"]: set(scenario["row_ids"]) for scenario in setup.scenarios}


def _ids(rows):
    return {str(row.id) for row in rows}


# --------------------------------------------------------------------------
# Saved dataset rows on the Temporal path
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_temporal_run_skips_the_saved_rows_that_are_gone_and_says_how_many(
    auth_client, temporal_on, voice_run_test
):
    rows = voice_run_test.rows

    response = _execute(
        auth_client, voice_run_test.run_test, saved_rows=rows, deleted_rows=[rows[1]]
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["message"] == "Test execution started successfully"
    assert body.get("skipped_dataset_row_count") == 1
    execution = TestExecution.objects.get(id=body["execution_id"])
    assert execution.execution_metadata["skipped_dataset_row_count"] == 1
    temporal_on.assert_called_once()
    assert temporal_on.call_args.kwargs["test_execution_id"] == str(execution.id)
    assert _rows_by_scenario(_set_up(execution)) == {
        str(voice_run_test.scenario.id): _ids([rows[0], rows[2]])
    }


@pytest.mark.django_db
def test_a_temporal_run_runs_only_the_saved_rows(
    auth_client, temporal_on, voice_run_test
):
    rows = voice_run_test.rows

    response = _execute(auth_client, voice_run_test.run_test, saved_rows=[rows[0]])

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body.get("skipped_dataset_row_count") == 0
    execution = TestExecution.objects.get(id=body["execution_id"])
    assert execution.execution_metadata["skipped_dataset_row_count"] == 0
    assert _rows_by_scenario(_set_up(execution)) == {
        str(voice_run_test.scenario.id): _ids([rows[0]])
    }


@pytest.mark.django_db
def test_a_temporal_run_with_no_rows_saved_runs_every_row(
    auth_client, temporal_on, voice_run_test
):
    response = _execute(auth_client, voice_run_test.run_test)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body.get("skipped_dataset_row_count") == 0
    execution = TestExecution.objects.get(id=body["execution_id"])
    assert "skipped_dataset_row_count" not in (execution.execution_metadata or {})
    assert _rows_by_scenario(_set_up(execution)) == {
        str(voice_run_test.scenario.id): _ids(voice_run_test.rows)
    }


@pytest.mark.django_db
def test_a_temporal_run_with_no_saved_row_left_is_refused_before_any_workflow_starts(
    auth_client, temporal_on, voice_run_test
):
    rows = voice_run_test.rows

    response = _execute(
        auth_client,
        voice_run_test.run_test,
        saved_rows=rows[:2],
        deleted_rows=rows[:2],
    )

    assert response.status_code == 400, response.json()
    assert response.json()["message"] == NOTHING_LEFT
    assert not TestExecution.objects.filter(run_test=voice_run_test.run_test).exists()
    temporal_on.assert_not_called()


@pytest.mark.django_db
def test_a_scenario_none_of_whose_rows_is_saved_makes_no_call_on_temporal(
    auth_client, temporal_on, voice_run_test
):
    other = _dataset_scenario(
        voice_run_test.organization,
        voice_run_test.workspace,
        "Temporal voice scenario with no saved row",
        2,
    )
    voice_run_test.run_test.scenarios.add(other.scenario)
    saved = voice_run_test.rows[:2]

    response = _execute(auth_client, voice_run_test.run_test, saved_rows=saved)

    assert response.status_code == 200, response.json()
    execution = TestExecution.objects.get(id=response.json()["execution_id"])
    assert _rows_by_scenario(_set_up(execution)) == {
        str(voice_run_test.scenario.id): _ids(saved)
    }


@pytest.mark.django_db
def test_setup_refuses_a_temporal_run_whose_saved_rows_went_after_it_started(
    auth_client, temporal_on, voice_run_test
):
    saved = voice_run_test.rows[:1]
    response = _execute(auth_client, voice_run_test.run_test, saved_rows=saved)
    assert response.status_code == 200, response.json()
    execution = TestExecution.objects.get(id=response.json()["execution_id"])
    Row._base_manager.filter(id=saved[0].id).update(deleted=True)

    setup = _set_up(execution)

    assert setup.success is False
    assert setup.error == NOTHING_LEFT
    assert setup.scenarios == []


# --------------------------------------------------------------------------
# A prompt-based run test with Temporal on
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "template_workspace", ["same-workspace", "saved-before-workspaces"]
)
def test_a_prompt_run_test_queues_its_chats_with_temporal_on(
    auth_client,
    temporal_on,
    no_voice_key,
    voice_services,
    prompt_run_test,
    template_workspace,
):
    if template_workspace == "saved-before-workspaces":
        # Use update so saving cannot fill the request workspace back in.
        PromptTemplate.no_workspace_objects.filter(
            pk=prompt_run_test.template.pk
        ).update(workspace=None)

    response = _execute(auth_client, prompt_run_test.run_test)

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["message"] == "Test execution started successfully"
    assert body["total_calls"] == 3
    calls = list(CallExecution.objects.filter(test_execution_id=body["execution_id"]))
    assert {str(call.row_id) for call in calls} == _ids(prompt_run_test.rows)
    assert {call.status for call in calls} == {CallExecution.CallStatus.REGISTERED}
    assert {call.simulation_call_type for call in calls} == {
        CallExecution.SimulationCallType.TEXT
    }
    assert {call.call_metadata["source_type"] for call in calls} == {"prompt"}
    temporal_on.assert_not_called()
    voice_services.assert_not_called()


@pytest.mark.django_db
def test_a_prompt_run_test_with_temporal_on_says_how_many_saved_rows_it_skipped(
    auth_client, temporal_on, no_voice_key, prompt_run_test
):
    rows = prompt_run_test.rows

    response = _execute(
        auth_client, prompt_run_test.run_test, saved_rows=rows, deleted_rows=[rows[1]]
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body.get("skipped_dataset_row_count") == 1
    calls = CallExecution.objects.filter(test_execution_id=body["execution_id"])
    assert {str(call.row_id) for call in calls} == _ids([rows[0], rows[2]])
    execution = TestExecution.objects.get(id=body["execution_id"])
    assert execution.execution_metadata["skipped_dataset_row_count"] == 1
    temporal_on.assert_not_called()


@pytest.mark.django_db
def test_a_prompt_run_test_with_temporal_on_and_no_saved_row_left_is_refused(
    auth_client, temporal_on, no_voice_key, prompt_run_test
):
    rows = prompt_run_test.rows

    response = _execute(
        auth_client, prompt_run_test.run_test, saved_rows=rows, deleted_rows=rows
    )

    assert response.status_code == 400, response.json()
    assert response.json()["message"] == NOTHING_LEFT
    assert not TestExecution.objects.filter(run_test=prompt_run_test.run_test).exists()
    temporal_on.assert_not_called()
