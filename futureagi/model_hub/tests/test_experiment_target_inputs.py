"""RL1891 r2: preserve canonical nullable cell links and exact graph inputs.

Source-prepared regressions, NOT_RUN locally. Real models, validation, managers,
GraphExecution and budget binding; only activity logging is instrumented.
"""

from unittest.mock import patch

import pytest

from agent_playground.models import GraphExecution
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.tests import test_experiment_agent_target_admission as old
from model_hub.tests import test_experiment_target_kind as kind

console_flag = kind.console_flag
tenants = old.tenants
pytestmark = pytest.mark.django_db(transaction=True)


def add_input(agent, *, dataset=None, row=None, name="query", value="native input"):
    snapshot = agent.experiment.snapshot_dataset
    column = Column.objects.create(
        dataset=dataset or snapshot,
        name=name,
        data_type=old.DataTypeChoices.TEXT.value,
        source=old.SourceChoices.OTHERS.value,
    )
    return Cell.objects.create(
        dataset=snapshot,
        row=row or agent.row,
        column=column,
        value=value,
        status=old.CellStatus.PASS.value,
    )


@pytest.mark.parametrize("duplicate", ["null", "matching", "foreign"])
@pytest.mark.parametrize("legacy_payload", [False, True], ids=["current", "old"])
def test_prepare_preserves_exact_native_inputs(
    tenants, console_flag, duplicate, legacy_payload
):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = old.stored_agent(tenants["a"])
    cell = add_input(agent)
    # A second valid input makes exclusion of a foreign duplicate observable
    # without confusing an intentionally empty input with a failed admission.
    add_input(agent, name="context", value="kept context")
    duplicate_id = {
        "null": None,
        "matching": agent.experiment.snapshot_dataset_id,
        "foreign": tenants["b"]["exp"]["dataset"].pk,
    }[duplicate]
    Cell._base_manager.filter(pk=cell.pk).update(dataset_id=duplicate_id)
    agent.experiment.run_policy = {"limits": {"timeout_s": 60}}
    agent.experiment.save(update_fields=["run_policy"])
    before = GraphExecution._base_manager.count()
    args = kind.worker_args(agent)
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        result = _prepare_agent_row_sync(*(args[:4] if legacy_payload else args))
    expected = {"context": "kept context"}
    if duplicate != "foreign":
        expected["query"] = "native input"
    assert result["status"] == "READY", result
    assert result["input_payload"] == expected
    execution = GraphExecution.no_workspace_objects.get(pk=result["graph_execution_id"])
    assert execution.input_payload == expected
    assert execution.graph_version_id == agent.version.pk
    assert GraphExecution._base_manager.count() == before + 1
    budget = ExperimentRowBudget.objects.get(graph_execution_id=execution.pk)
    assert budget.experiment_id == agent.experiment.pk
    assert budget.row_id == agent.row.pk
    assert budget.variant == f"agent:{agent.eac.pk}:direct"
    cell.refresh_from_db()
    assert cell.dataset_id == duplicate_id  # Admission never repairs legacy data.


@pytest.mark.parametrize(
    "invalid",
    ["foreign_column", "deleted_column", "foreign_row", "deleted_row", "deleted_cell"],
)
@pytest.mark.parametrize("legacy_payload", [False, True], ids=["current", "old"])
def test_nullable_duplicate_never_overrides_authoritative_live_links(
    tenants, console_flag, invalid, legacy_payload
):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = old.stored_agent(tenants["a"])
    cell = add_input(agent)
    Cell._base_manager.filter(pk=cell.pk).update(dataset=None)
    if invalid == "foreign_column":
        Column._base_manager.filter(pk=cell.column_id).update(
            dataset=tenants["b"]["exp"]["dataset"]
        )
    elif invalid == "deleted_column":
        Column._base_manager.filter(pk=cell.column_id).update(deleted=True)
    elif invalid == "foreign_row":
        Cell._base_manager.filter(pk=cell.pk).update(row=tenants["b"]["exp"]["row"])
    elif invalid == "deleted_row":
        Row._base_manager.filter(pk=agent.row.pk).update(deleted=True)
    else:
        Cell._base_manager.filter(pk=cell.pk).update(deleted=True)
    before = kind.execution_state(agent)
    args = kind.worker_args(agent)
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        result = _prepare_agent_row_sync(*(args[:4] if legacy_payload else args))
    assert result["input_payload"] == {}
    if invalid == "deleted_row":
        assert result["status"] == "FAILED", result
        assert old.CODE in result["error"]
        assert kind.execution_state(agent) == before
    else:
        assert result["status"] == "READY", result
        execution = GraphExecution.no_workspace_objects.get(
            pk=result["graph_execution_id"]
        )
        assert execution.input_payload == {}
        assert execution.graph_version_id == agent.version.pk
