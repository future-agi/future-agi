"""Running a run test with saved dataset rows runs the saved rows that still exist.

A run test created through the API or MCP can save ``dataset_row_ids``. A saved
row runs while it is a live row of a live dataset that an attached scenario
uses, with that scenario and dataset in the run test's organization and, by the
shared workspace rule, its workspace: a default workspace also covers rows
stored with no workspace, and a run test stored with no workspace covers its
whole organization. A saved id that is not a UUID, or names no such row, is
skipped and the execution records how many were skipped. The run is refused
only when none of the saved rows is left. A row of another organization never
runs. Running reads the saved rows, their dataset and scenario, and changes
none of them.

Rows are created under the bound workspace and then given the workspace (or
none) and organization they are stored with, the way older writers left them.
"""

from types import SimpleNamespace
from unittest.mock import call, patch
from uuid import uuid4

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import RunTest, Scenarios, TestExecution
from simulate.services.test_executor import TestExecutor
from tfc.utils.tenant_scope import workspace_scope_q

pytestmark = pytest.mark.django_db

NOTHING_LEFT = (
    "None of the dataset rows saved for this test are available any more. "
    "Clear the row selection or pick rows again."
)


@pytest.fixture
def stored(organization, workspace, user):
    """A prompt run test in the organization's default workspace."""
    template = PromptTemplate.objects.create(
        name="Saved rows prompt", organization=organization, workspace=workspace
    )
    version = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True
    )
    run_test = RunTest.objects.create(
        name="Saved rows run test",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=version,
        organization=organization,
        workspace=workspace,
    )
    return SimpleNamespace(
        run_test=run_test, organization=organization, workspace=workspace, user=user
    )


@pytest.fixture
def other_organization(user):
    """Another organization, with its own default workspace."""
    organization = Organization.objects.create(name="Other organization")
    workspace = Workspace.no_workspace_objects.create(
        name="Other organization default workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return SimpleNamespace(organization=organization, workspace=workspace)


def attach_scenario(stored, name, rows=2):
    """A dataset scenario attached to the run test, in its organization and
    workspace, and the dataset's rows in order."""
    dataset = Dataset.objects.create(
        name=name, organization=stored.organization, workspace=stored.workspace
    )
    scenario = Scenarios.objects.create(
        name=name,
        source="Dataset rows",
        dataset=dataset,
        organization=stored.organization,
        workspace=stored.workspace,
    )
    stored.run_test.scenarios.add(scenario)
    return scenario, [
        Row.objects.create(dataset=dataset, order=order) for order in range(rows)
    ]


def store(model, pk, **fields):
    """Write ``fields`` to the stored row directly.

    Saving through the model under a bound workspace fills an empty workspace
    in, so a row with no workspace, or of another organization, is written
    this way."""
    model._base_manager.filter(pk=pk).update(**fields)


def execute(stored, selection, scenarios):
    """Run the run test with ``selection`` saved; the dispatched calls as
    (scenario id, row id) pairs, in order."""
    stored.run_test.dataset_row_ids = selection
    stored.run_test.save(update_fields=["dataset_row_ids"])
    executor = TestExecutor(initialize_voice_service=False)
    with patch.object(
        executor, "_execute_call", return_value={"success": True}
    ) as execute_call:
        result = executor.execute_test(
            run_test_id=str(stored.run_test.id),
            user_id=str(stored.user.id),
            scenario_ids=[str(scenario.id) for scenario in scenarios],
        )
    dispatched = [
        (str(args.args[1].id), args.args[2]["row_id"])
        for args in execute_call.call_args_list
    ]
    return result, dispatched


def skipped_on_execution(result):
    execution = TestExecution.objects.get(id=result["execution_id"])
    return execution.execution_metadata["skipped_dataset_row_count"]


def stored_state(scenario):
    """The scenario, its dataset and the dataset's rows, as stored."""
    rows = Row._base_manager.filter(dataset_id=scenario.dataset_id).order_by("order")
    return (
        list(Scenarios._base_manager.filter(pk=scenario.pk).values()),
        list(Dataset._base_manager.filter(pk=scenario.dataset_id).values()),
        list(rows.values()),
    )


@pytest.mark.parametrize(
    "without_workspace",
    [("scenario",), ("dataset",), ("scenario", "dataset")],
    ids=["scenario", "dataset", "scenario-and-dataset"],
)
def test_rows_stored_with_no_workspace_run_from_the_default_workspace(
    stored, without_workspace
):
    scenario, rows = attach_scenario(stored, "No workspace")
    if "scenario" in without_workspace:
        store(Scenarios, scenario.id, workspace=None)
    if "dataset" in without_workspace:
        store(Dataset, scenario.dataset_id, workspace=None)

    result, dispatched = execute(stored, [str(rows[1].id)], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(scenario.id), str(rows[1].id))]
    assert skipped_on_execution(result) == 0


def test_run_test_stored_with_no_workspace_runs_rows_of_its_organization(stored):
    scenario, rows = attach_scenario(stored, "Default workspace rows")
    store(RunTest, stored.run_test.id, workspace=None)

    result, dispatched = execute(stored, [str(rows[0].id)], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(scenario.id), str(rows[0].id))]
    assert skipped_on_execution(result) == 0


def test_selection_scope_follows_the_shared_workspace_rule(stored):
    """Scenarios and datasets are scoped by the shared workspace rule.

    That rule also covers the organization's other default workspaces. An
    organization holds a single default workspace in the database, so such a
    row cannot be stored here; the rule's own tests cover that case.
    """
    scenario, rows = attach_scenario(stored, "Shared rule")

    with patch(
        "simulate.services.test_executor.workspace_scope_q", wraps=workspace_scope_q
    ) as scope:
        result, dispatched = execute(stored, [str(rows[0].id)], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(scenario.id), str(rows[0].id))]
    assert scope.call_args_list == [
        call(stored.workspace),
        call(stored.workspace, "dataset__workspace"),
    ]


def test_a_row_deleted_since_is_skipped_and_the_others_run(stored):
    scenario, rows = attach_scenario(stored, "Deleted row", rows=3)
    store(Row, rows[1].id, deleted=True)

    result, dispatched = execute(stored, [str(row.id) for row in rows], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [
        (str(scenario.id), str(rows[0].id)),
        (str(scenario.id), str(rows[2].id)),
    ]
    assert result["total_calls"] == 2
    assert result["skipped_dataset_row_count"] == 1
    assert skipped_on_execution(result) == 1


def test_an_id_that_is_not_a_uuid_is_skipped_and_the_rest_run(stored):
    scenario, rows = attach_scenario(stored, "Not a UUID")

    result, dispatched = execute(stored, ["row-7", str(rows[1].id)], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(scenario.id), str(rows[1].id))]
    assert skipped_on_execution(result) == 1


@pytest.mark.parametrize("gone", ["deleted", "not-a-uuid", "names-nothing"])
def test_a_selection_with_no_row_left_is_refused_with_a_plain_sentence(stored, gone):
    scenario, rows = attach_scenario(stored, "Nothing left")
    selection = [str(row.id) for row in rows]
    if gone == "deleted":
        Row._base_manager.filter(id__in=[row.id for row in rows]).update(deleted=True)
    elif gone == "not-a-uuid":
        selection = ["row-1", "row-2"]
    else:
        selection = [str(uuid4())]

    result, dispatched = execute(stored, selection, [scenario])

    assert result["success"] is False
    assert result["error"] == NOTHING_LEFT
    assert dispatched == []
    assert not TestExecution.objects.filter(run_test=stored.run_test).exists()


@pytest.mark.parametrize("other_workspace", ["none", "its-default"])
@pytest.mark.parametrize("moved", ["dataset", "scenario-and-dataset"])
def test_rows_of_another_organization_never_run(
    stored, other_organization, moved, other_workspace
):
    own_scenario, own_rows = attach_scenario(stored, "Own rows")
    other_scenario, other_rows = attach_scenario(stored, "Other organization rows")
    fields = {
        "organization": other_organization.organization,
        "workspace": (
            None if other_workspace == "none" else other_organization.workspace
        ),
    }
    store(Dataset, other_scenario.dataset_id, **fields)
    if moved == "scenario-and-dataset":
        store(Scenarios, other_scenario.id, **fields)

    result, dispatched = execute(
        stored,
        [str(own_rows[0].id), str(other_rows[0].id)],
        [own_scenario, other_scenario],
    )

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(own_scenario.id), str(own_rows[0].id))]
    assert skipped_on_execution(result) == 1


@pytest.mark.parametrize("other_workspace", ["none", "its-default"])
def test_a_selection_of_only_another_organizations_rows_is_refused(
    stored, other_organization, other_workspace
):
    scenario, rows = attach_scenario(stored, "Other organization rows")
    store(
        Dataset,
        scenario.dataset_id,
        organization=other_organization.organization,
        workspace=None if other_workspace == "none" else other_organization.workspace,
    )

    result, dispatched = execute(stored, [str(row.id) for row in rows], [scenario])

    assert result["success"] is False
    assert result["error"] == NOTHING_LEFT
    assert dispatched == []
    assert not TestExecution.objects.filter(run_test=stored.run_test).exists()


def test_an_empty_selection_runs_every_row(stored):
    scenario, rows = attach_scenario(stored, "Every row")

    result, dispatched = execute(stored, [], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(scenario.id), str(row.id)) for row in rows]
    execution = TestExecution.objects.get(id=result["execution_id"])
    assert "skipped_dataset_row_count" not in execution.execution_metadata


@pytest.mark.parametrize("without_workspace", ["scenario-and-dataset", "run-test"])
def test_running_rows_stored_with_no_workspace_changes_none_of_them(
    stored, without_workspace
):
    scenario, rows = attach_scenario(stored, "Read only")
    if without_workspace == "run-test":
        store(RunTest, stored.run_test.id, workspace=None)
    else:
        store(Scenarios, scenario.id, workspace=None)
        store(Dataset, scenario.dataset_id, workspace=None)
    before = stored_state(scenario)

    result, dispatched = execute(stored, [str(rows[0].id)], [scenario])

    assert result["success"] is True, result.get("error")
    assert dispatched == [(str(scenario.id), str(rows[0].id))]
    assert stored_state(scenario) == before
    executions = TestExecution.objects.filter(run_test=stored.run_test)
    assert executions.count() == 1
    assert str(executions.get().id) == result["execution_id"]
