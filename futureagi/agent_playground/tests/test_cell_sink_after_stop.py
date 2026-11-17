"""An agent row's step that was running when its experiment was stopped keeps
its output.

The cell sink writes a graph step's output into the experiment's cell. It
used to drop the output when the experiment had been stopped meanwhile, though
the step's call had been sent and paid for (on the customer's own key). Now
the output is saved over the Stop's mark, as for prompt rows; a step whose
experiment is running is unchanged, and the tenant check still comes first.
"""

import pytest

from agent_playground.services.engine.output_sink import OutputSinkContext
from agent_playground.services.engine.output_sinks.cell_sink import CellOutputSink
from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

MARK = "Experiment stopped by user"


@pytest.fixture
def destination(organization, workspace, user):
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name="agent rows", organization=organization, workspace=workspace
    )
    column = Column.no_workspace_objects.create(
        dataset=dataset, name="output", data_type="text", source="experiment"
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    # The Stop's cleanup marked this running cell stopped.
    cell = Cell.no_workspace_objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value=MARK,
        status=CellStatus.ERROR.value,
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="agent experiment",
        dataset=dataset,
        snapshot_dataset=dataset,
        user=user,
        status=StatusType.CANCELLED.value,
    )
    return experiment, dataset, column, row, cell


def store(node, destination, status="SUCCESS"):
    experiment, dataset, column, row, _ = destination
    config = {
        "dataset_id": str(dataset.pk),
        "column_id": str(column.pk),
        "row_id": str(row.pk),
        "experiment_id": str(experiment.pk),
    }
    node.config = {"_output_sinks": [{"name": "cell", "config": config}]}
    node.save(skip_validation=True)
    CellOutputSink().store(
        OutputSinkContext(
            node_id=str(node.pk),
            node_name=node.name,
            template_name="test",
            config=config,
            inputs={},
            outputs={"response": "late answer"},
            status=status,
            metadata={"error": "step failed"},
        )
    )
    cell = destination[4]
    cell.refresh_from_db()
    return cell


def test_a_step_running_at_the_stop_keeps_its_output(node, destination):
    cell = store(node, destination)
    assert (cell.value, cell.status) == ("late answer", CellStatus.PASS.value)


def test_a_failed_step_running_at_the_stop_keeps_its_error(node, destination):
    cell = store(node, destination, status="FAILED")
    assert (cell.value, cell.status) == ("step failed", CellStatus.ERROR.value)


def test_a_running_experiments_step_is_unchanged(node, destination):
    experiment = destination[0]
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    cell = store(node, destination)
    assert (cell.value, cell.status) == ("late answer", CellStatus.PASS.value)
