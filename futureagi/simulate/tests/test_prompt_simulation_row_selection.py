"""Prompt simulations must dispatch only the saved dataset-row selection.

Saved rows that are no longer available are skipped and the rest run; the run
is refused only when none of the saved rows is left.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import RunTest, Scenarios, TestExecution
from simulate.services.test_executor import TestExecutor

pytestmark = pytest.mark.django_db

NOTHING_LEFT = (
    "None of the dataset rows saved for this test are available any more. "
    "Clear the row selection or pick rows again."
)


@pytest.fixture
def simulation(organization, workspace, user):
    template = PromptTemplate.objects.create(
        name="Row selection prompt", organization=organization, workspace=workspace
    )
    version = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True
    )
    run_test = RunTest.objects.create(
        name="Targeted simulation",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=version,
        organization=organization,
        workspace=workspace,
    )

    def add_scenario(name):
        dataset = Dataset.objects.create(
            name=name, organization=organization, workspace=workspace
        )
        scenario = Scenarios.objects.create(
            name=name,
            source="Dataset rows",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
        )
        run_test.scenarios.add(scenario)
        rows = [Row.objects.create(dataset=dataset, order=order) for order in range(2)]
        return scenario, rows

    scenario, rows = add_scenario("Selected scenario")
    return SimpleNamespace(
        run_test=run_test,
        scenario=scenario,
        rows=rows,
        user=user,
        organization=organization,
        workspace=workspace,
        add_scenario=add_scenario,
    )


def execute(simulation, selected_rows, scenarios=None):
    simulation.run_test.dataset_row_ids = selected_rows
    simulation.run_test.save(update_fields=["dataset_row_ids"])
    executor = TestExecutor(initialize_voice_service=False)
    with patch.object(
        executor, "_execute_call", return_value={"success": True}
    ) as call:
        result = executor.execute_test(
            run_test_id=str(simulation.run_test.id),
            user_id=str(simulation.user.id),
            scenario_ids=[str(s.id) for s in scenarios or [simulation.scenario]],
        )
    return result, call


@pytest.mark.parametrize(
    ("selection", "expected"),
    [([0], [0]), ([], [0, 1]), ([1, 0, 1], [0, 1])],
    ids=["selected-row-only", "empty-means-all", "dataset-order-no-duplicates"],
)
def test_execute_dispatches_only_saved_rows(simulation, selection, expected):
    result, call = execute(
        simulation, [str(simulation.rows[index].id) for index in selection]
    )

    assert result["success"] is True
    assert result["total_calls"] == len(expected)
    execution = TestExecution.objects.get(id=result["execution_id"])
    assert execution.total_calls == len(expected)
    assert [args.args[2]["row_id"] for args in call.call_args_list] == [
        str(simulation.rows[index].id) for index in expected
    ]


def test_execute_accepts_equivalent_uuid_spelling(simulation):
    result, call = execute(simulation, [simulation.rows[0].id.hex.upper()])

    assert result["success"] is True
    assert result["total_calls"] == 1
    assert call.call_args.args[2]["row_id"] == str(simulation.rows[0].id)
    assert call.call_count == 1


@pytest.mark.parametrize(
    "subset", [False, True], ids=["all-scenarios", "scenario-subset"]
)
def test_execute_intersects_rows_per_scenario(simulation, subset):
    other_scenario, other_rows = simulation.add_scenario("Other scenario")
    selected_rows = [str(simulation.rows[1].id), str(other_rows[0].id)]
    scenarios = (
        [simulation.scenario] if subset else [simulation.scenario, other_scenario]
    )

    result, call = execute(simulation, selected_rows, scenarios)

    expected = [(str(simulation.scenario.id), str(simulation.rows[1].id))]
    if not subset:
        expected.append((str(other_scenario.id), str(other_rows[0].id)))
    assert result["success"] is True
    assert result["total_calls"] == len(expected)
    assert TestExecution.objects.get(id=result["execution_id"]).total_calls == len(
        expected
    )
    assert [
        (str(args.args[1].id), args.args[2]["row_id"])
        for args in call.call_args_list
    ] == expected


def test_scenario_without_selected_rows_does_not_fall_back_to_all(simulation):
    other_scenario, _ = simulation.add_scenario("Unselected scenario")

    result, call = execute(
        simulation,
        [str(simulation.rows[0].id)],
        [simulation.scenario, other_scenario],
    )

    assert result["success"] is True
    assert result["total_calls"] == 1
    assert call.call_count == 1
    assert call.call_args.args[1].id == simulation.scenario.id
    assert call.call_args.args[2]["row_id"] == str(simulation.rows[0].id)


UNAVAILABLE_KINDS = [
    "missing",
    "malformed",
    "deleted-row",
    "deleted-dataset",
    "unattached",
    "foreign",
    "other-workspace",
]


def unavailable_row_id(simulation, kind):
    """A saved id the run test can no longer run, of the given kind."""
    if kind == "missing":
        return str(uuid4())
    if kind == "malformed":
        return "not-a-row-id"
    if kind == "deleted-row":
        Row.all_objects.filter(id=simulation.rows[1].id).update(deleted=True)
        return str(simulation.rows[1].id)
    if kind == "unattached":
        dataset = Dataset.objects.create(
            name="Unattached dataset",
            organization=simulation.organization,
            workspace=simulation.workspace,
        )
        return str(Row.objects.create(dataset=dataset, order=0).id)
    scenario, rows = simulation.add_scenario("Unavailable dataset")
    if kind == "deleted-dataset":
        Dataset.all_objects.filter(id=scenario.dataset_id).update(deleted=True)
    elif kind == "foreign":
        foreign_org = Organization.objects.create(name="Other organization")
        Dataset.all_objects.filter(id=scenario.dataset_id).update(
            organization=foreign_org, workspace=None
        )
    else:
        # A workspace of the same organization that is not its default.
        workspace = Workspace.objects.create(
            name="Other workspace",
            organization=simulation.organization,
            created_by=simulation.user,
            is_default=False,
        )
        Dataset.all_objects.filter(id=scenario.dataset_id).update(workspace=workspace)
    return str(rows[0].id)


@pytest.mark.parametrize("unavailable_kind", UNAVAILABLE_KINDS)
def test_unavailable_saved_row_is_skipped_and_the_rest_run(
    simulation, unavailable_kind
):
    selected = simulation.rows[0]
    unavailable_id = unavailable_row_id(simulation, unavailable_kind)

    result, call = execute(simulation, [str(selected.id), unavailable_id])

    assert result["success"] is True
    assert result["total_calls"] == 1
    assert call.call_count == 1
    assert call.call_args.args[2]["row_id"] == str(selected.id)
    execution = TestExecution.objects.get(id=result["execution_id"])
    assert execution.execution_metadata["skipped_dataset_row_count"] == 1


@pytest.mark.parametrize("unavailable_kind", UNAVAILABLE_KINDS)
def test_saved_selection_with_no_row_left_creates_no_execution(
    simulation, unavailable_kind
):
    unavailable_id = unavailable_row_id(simulation, unavailable_kind)

    result, call = execute(simulation, [unavailable_id])

    assert result["success"] is False
    assert result["error"] == NOTHING_LEFT
    assert not TestExecution.objects.filter(run_test=simulation.run_test).exists()
    call.assert_not_called()
