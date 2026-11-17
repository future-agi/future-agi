"""A run started on saved dataset rows says how many of them it skipped.

Saved rows that no longer exist are skipped and the rest run. Both execute
endpoints answer with ``skipped_dataset_row_count`` (0 when none was skipped),
and a run's KPIs carry the count its execution stored, so the run's page can
say it too. When none of the saved rows is left, both endpoints refuse the run
with the same 400 and the same sentence.
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
from simulate.services.test_executor import TestExecutor
from simulate.views.run_test import RunTestExecutionView
from tfc.settings import settings as app_settings

NOTHING_LEFT = (
    "None of the dataset rows saved for this test are available any more. "
    "Clear the row selection or pick rows again."
)


# --------------------------------------------------------------------------
# Prompt simulation execute endpoint
# --------------------------------------------------------------------------


@pytest.fixture
def prompt_simulation(organization, workspace):
    """A prompt simulation with one completed dataset scenario of three rows."""
    template = PromptTemplate.objects.create(
        name="Skipped rows prompt", organization=organization, workspace=workspace
    )
    version = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True
    )
    run_test = RunTest.objects.create(
        name="Skipped rows simulation",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=version,
        organization=organization,
        workspace=workspace,
    )
    dataset = Dataset.objects.create(
        name="Skipped rows dataset", organization=organization, workspace=workspace
    )
    scenario = Scenarios.objects.create(
        name="Skipped rows scenario",
        source="Dataset rows",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    run_test.scenarios.add(scenario)
    rows = [Row.objects.create(dataset=dataset, order=order) for order in range(3)]
    return SimpleNamespace(template=template, run_test=run_test, rows=rows)


def run_prompt_simulation(auth_client, simulation, saved_rows, deleted_rows=()):
    """Save ``saved_rows``, delete ``deleted_rows``, then press Run."""
    simulation.run_test.dataset_row_ids = [str(row.id) for row in saved_rows]
    simulation.run_test.save(update_fields=["dataset_row_ids"])
    Row._base_manager.filter(id__in=[row.id for row in deleted_rows]).update(
        deleted=True
    )
    with patch.object(TestExecutor, "_execute_call", return_value={"success": True}):
        return auth_client.post(
            f"/simulate/prompt-templates/{simulation.template.id}"
            f"/simulations/{simulation.run_test.id}/execute/",
            {},
            format="json",
        )


@pytest.mark.django_db
def test_a_prompt_simulation_says_how_many_saved_rows_it_skipped(
    auth_client, prompt_simulation
):
    rows = prompt_simulation.rows

    response = run_prompt_simulation(
        auth_client, prompt_simulation, saved_rows=rows, deleted_rows=[rows[1]]
    )

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert result["total_calls"] == 2
    assert result.get("skipped_dataset_row_count") == 1
    execution = TestExecution.objects.get(id=result["execution_id"])
    assert execution.execution_metadata["skipped_dataset_row_count"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("saved", ["every-saved-row-exists", "no-rows-saved"])
def test_a_prompt_simulation_that_skipped_no_row_says_zero(
    auth_client, prompt_simulation, saved
):
    saved_rows = prompt_simulation.rows[:2] if saved != "no-rows-saved" else []

    response = run_prompt_simulation(auth_client, prompt_simulation, saved_rows)

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert result["total_calls"] == (2 if saved_rows else 3)
    assert result.get("skipped_dataset_row_count") == 0


@pytest.mark.django_db
def test_a_prompt_simulation_with_no_saved_row_left_is_refused(
    auth_client, prompt_simulation
):
    rows = prompt_simulation.rows

    response = run_prompt_simulation(
        auth_client, prompt_simulation, saved_rows=rows[:2], deleted_rows=rows[:2]
    )

    assert response.status_code == 400
    assert response.json()["message"] == NOTHING_LEFT
    assert not TestExecution.objects.filter(
        run_test=prompt_simulation.run_test
    ).exists()


# --------------------------------------------------------------------------
# Run test execute endpoint, legacy executor
# --------------------------------------------------------------------------


@pytest.fixture
def legacy_executor():
    """The run test endpoint on the legacy executor, whose answer each test sets."""
    executor = MagicMock(name="TestExecutor()")
    run = MagicMock()
    run.id = uuid4()
    run.agent_definition = None
    run.scenarios.filter.return_value.values_list.return_value = [uuid4()]
    with (
        patch.object(app_settings, "HOSTED_RUNNER_ENABLED", False, create=True),
        patch.object(
            app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", False, create=True
        ),
        patch("simulate.views.run_test.get_object_or_404", return_value=run),
        patch("simulate.views.run_test.check_scenarios_incomplete", return_value=None),
        patch(
            "simulate.views.run_test._latest_repository_harness_execution",
            return_value=None,
        ),
        patch("simulate.views.run_test.TestExecutor", return_value=executor),
    ):
        yield SimpleNamespace(run=run, executor=executor)


def started(run, **fields):
    return {
        "success": True,
        "run_test_id": str(run.id),
        "execution_id": str(uuid4()),
        "status": "started",
        "total_scenarios": 1,
        "total_calls": 2,
        **fields,
    }


def run_agent_test(run):
    request = APIRequestFactory().post("/execute/", {}, format="json")
    user = SimpleNamespace(id=uuid4(), is_authenticated=True, organization=object())
    request.organization = user.organization
    request.workspace = SimpleNamespace(id=uuid4(), is_default=False)
    force_authenticate(request, user=user)
    return RunTestExecutionView.as_view()(request, run_test_id=run.id)


@pytest.mark.parametrize("skipped", [1, 3])
def test_a_run_test_says_how_many_saved_rows_it_skipped(legacy_executor, skipped):
    legacy_executor.executor.execute_test.return_value = started(
        legacy_executor.run, skipped_dataset_row_count=skipped
    )

    response = run_agent_test(legacy_executor.run)

    assert response.status_code == 200, response.data
    assert response.data["message"] == "Test execution started successfully"
    assert response.data.get("skipped_dataset_row_count") == skipped


@pytest.mark.parametrize(
    "fields",
    [{"skipped_dataset_row_count": 0}, {}],
    ids=["none-skipped", "count-not-reported"],
)
def test_a_run_test_that_skipped_no_row_says_zero(legacy_executor, fields):
    legacy_executor.executor.execute_test.return_value = started(
        legacy_executor.run, **fields
    )

    response = run_agent_test(legacy_executor.run)

    assert response.status_code == 200, response.data
    assert response.data.get("skipped_dataset_row_count") == 0


def test_a_run_test_with_no_saved_row_left_is_refused_with_the_sentence(
    legacy_executor,
):
    legacy_executor.executor.execute_test.return_value = {
        "success": False,
        "error": NOTHING_LEFT,
        "run_test_id": str(legacy_executor.run.id),
    }

    response = run_agent_test(legacy_executor.run)

    assert response.status_code == 400, response.data
    assert response.data["message"] == NOTHING_LEFT


# --------------------------------------------------------------------------
# A run's KPIs, read by the run's page
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("execution_metadata", "expected"),
    [({"skipped_dataset_row_count": 2}, 2), ({}, 0)],
    ids=["skipped-two", "nothing-stored"],
)
def test_a_runs_kpis_carry_how_many_saved_rows_it_skipped(
    auth_client, prompt_simulation, execution_metadata, expected
):
    execution = TestExecution.objects.create(
        run_test=prompt_simulation.run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        execution_metadata=execution_metadata,
    )

    response = auth_client.get(f"/simulate/test-executions/{execution.id}/kpis/")

    assert response.status_code == 200, response.json()
    assert response.json().get("skipped_dataset_row_count") == expected
