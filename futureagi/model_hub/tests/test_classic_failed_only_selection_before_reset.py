"""Classic failed-only retries carry concrete row IDs past the status reset.

Use unbound classic ORM fixtures and the authenticated API. Only the final
Temporal dispatch is stubbed; the worker's real row selector reads the cells
after the view has reset them. No native-run helper is needed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.tests.test_experiment_dataset_completion_matcher import (
    _cell,
    _column,
    _evaluation,
    _login,
    _seed,
    _state,
    _table,
    _tenant_b,
)
from model_hub.tests.test_experiment_rerun_dependent_evaluations import (
    _after,
    _prompt,
    _rerun,
)
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal.experiments.activities import _resolve_row_ids

pytestmark = pytest.mark.django_db


@pytest.fixture
def workflow():
    with patch(
        "tfc.temporal.experiments.start_rerun_cells_v2_workflow",
        return_value="classic-failed-only-test",
    ) as start:
        yield start


def _classic_run(organization, workspace, *, failed=True):
    seeded = _seed(organization, workspace)
    seeded.prompt = _prompt(seeded.own)
    seeded.evaluation = _evaluation(seeded, seeded.own, "evaluation")
    Column.all_objects.filter(id=seeded.evaluation.id).update(
        status=StatusType.COMPLETED.value
    )
    for row in seeded.rows:
        _cell(seeded.evaluation, row, CellStatus.PASS.value)
    if failed:
        Cell.all_objects.filter(
            column_id__in=[seeded.own.output.id, seeded.evaluation.id],
            row_id=seeded.rows[0].id,
        ).update(status=CellStatus.ERROR.value, value="Failed output")
    for record in (seeded.experiment, seeded.own.table):
        record.status = StatusType.COMPLETED.value
        record.save(update_fields=["status"])
    return seeded


def _dispatched_rows(workflow, seeded, *, failed_only):
    workflow.assert_called_once()
    started = workflow.call_args.kwargs
    assert started["experiment_id"] == str(seeded.experiment.id)
    assert started["dataset_id"] == str(seeded.data.id)
    assert started["dataset_id"] != str(seeded.base.id)
    assert started["prompt_config_ids"] == [str(seeded.prompt.id)]
    assert started["agent_config_ids"] == []
    assert started["failed_only"] is failed_only
    # A queryset evaluated after ERROR -> RUNNING would lose the selection.
    assert isinstance(started["row_ids"], list)
    selected_rows = (started.get("row_ids_by_config") or {}).get(
        str(seeded.prompt.id), started["row_ids"]
    )
    selected = _resolve_row_ids(
        snapshot_dataset_id=started["dataset_id"],
        rerun_row_ids=selected_rows,
        failed_only=started["failed_only"],
        column_ids=[seeded.own.output.id],
    )
    return started, selected


def _assert_failed_cells_reset(seeded):
    expected = (
        StatusType.RUNNING.value,
        [CellStatus.RUNNING.value, CellStatus.PASS.value],
        ["", "Original"],
    )
    assert _after(seeded.own.output) == expected
    assert _after(seeded.evaluation) == expected


def _run_state(seeded):
    clear_workspace_context()
    seeded.experiment.refresh_from_db()
    seeded.own.table.refresh_from_db()
    return (
        seeded.experiment.status,
        seeded.own.table.status,
        _state([seeded.own.output, seeded.evaluation]),
    )


def test_classic_failed_only_materializes_failed_output_rows_before_reset(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace)
    # A matching output in the source dataset must not enter the snapshot retry.
    source_row = Row.objects.create(dataset=seeded.base, order=1)
    source_output = _column(
        seeded.base,
        SourceChoices.EXPERIMENT.value,
        seeded.own.table.id,
        status=StatusType.COMPLETED.value,
    )
    _cell(source_output, source_row, CellStatus.ERROR.value)
    source_before = _state([source_output])
    failed_before = _resolve_row_ids(
        str(seeded.data.id), [], True, [seeded.own.output.id]
    )
    assert failed_before == [seeded.rows[0].id]
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    _assert_failed_cells_reset(seeded)
    assert _state([source_output]) == source_before
    # ERROR is already gone. Dispatch must retain the IDs chosen before reset,
    # rather than asking the worker to rediscover failures in RUNNING cells.
    started, selected = _dispatched_rows(workflow, seeded, failed_only=True)
    assert selected == failed_before
    assert (started.get("row_ids_by_config") or {}).get(
        str(seeded.prompt.id), started["row_ids"]
    ) == [str(seeded.rows[0].id)]


def test_classic_ordinary_rerun_still_selects_and_resets_both_rows(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace)
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=False,
    )

    assert response.status_code == 200, response.content
    expected = (
        StatusType.RUNNING.value,
        [CellStatus.RUNNING.value] * 2,
        ["", ""],
    )
    assert _after(seeded.own.output) == expected
    assert _after(seeded.evaluation) == expected
    _, selected = _dispatched_rows(workflow, seeded, failed_only=False)
    assert selected == [row.id for row in seeded.rows]


def test_classic_failed_only_empty_set_does_not_dispatch_or_change_state(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace, failed=False)
    before = _run_state(seeded)
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    # Either a no-op or a refusal is valid; do not prescribe a response schema.
    assert response.status_code in (200, 400), response.content
    workflow.assert_not_called()
    assert _run_state(seeded) == before


def test_classic_explicit_cell_retry_uses_materialized_ids_after_reset(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace)
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        cells=[
            {
                "column_id": str(seeded.own.output.id),
                "row_id": str(seeded.rows[0].id),
            }
        ],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    _assert_failed_cells_reset(seeded)
    started, selected = _dispatched_rows(workflow, seeded, failed_only=True)
    assert selected == [seeded.rows[0].id]
    assert (started.get("row_ids_by_config") or {}).get(
        str(seeded.prompt.id), started["row_ids"]
    ) == [str(seeded.rows[0].id)]


def test_classic_failed_only_preserves_evaluation_error_retry(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace, failed=False)
    Cell.all_objects.filter(
        column_id=seeded.evaluation.id, row_id=seeded.rows[0].id
    ).update(status=CellStatus.ERROR.value)
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    started, selected = _dispatched_rows(workflow, seeded, failed_only=True)
    assert selected == [seeded.rows[0].id]
    assert (started.get("row_ids_by_config") or {}).get(
        str(seeded.prompt.id), started["row_ids"]
    ) == [str(seeded.rows[0].id)]
    assert _after(seeded.own.output)[1:] == (
        [CellStatus.PASS.value] * 2,
        ["Original", "Original"],
    )
    assert _after(seeded.evaluation)[1:] == (
        [CellStatus.RUNNING.value, CellStatus.PASS.value],
        ["", "Original"],
    )


def test_classic_failed_only_uses_canonical_failed_check_rows(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace, failed=False)
    Column.all_objects.filter(id=seeded.evaluation.id).update(
        data_type=DataTypeChoices.BOOLEAN.value
    )
    Cell.all_objects.filter(
        column_id=seeded.evaluation.id, row_id=seeded.rows[0].id
    ).update(value="false")
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    started, selected = _dispatched_rows(workflow, seeded, failed_only=True)
    assert selected == [seeded.rows[0].id]
    assert (started.get("row_ids_by_config") or {}).get(
        str(seeded.prompt.id), started["row_ids"]
    ) == [str(seeded.rows[0].id)]


def test_classic_failed_only_deleted_rows_do_not_start_a_retry(
    user, organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace)
    Row.objects.filter(id=seeded.rows[0].id).update(deleted=True)
    before = _run_state(seeded)
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    assert response.status_code in (200, 400), response.content
    workflow.assert_not_called()
    assert _run_state(seeded) == before


def _workflow_children(started, monkeypatch, *, input_value=None):
    from tfc.temporal.experiments import client as temporal_client
    from tfc.temporal.experiments import workflows

    if input_value is None:
        with patch.object(
            temporal_client,
            "start_workflow_sync",
            return_value=SimpleNamespace(id="classic-test"),
        ) as scheduled:
            temporal_client.start_rerun_cells_v2_workflow(**started)
        value = scheduled.call_args.kwargs["workflow_input"]
    else:
        value = input_value
    children = []
    commands = []

    async def activity(name, value, **kwargs):
        commands.append(name)
        if name == "mark_experiment_running_activity":
            return {"status": "READY"}
        if name == "analyze_dependencies_activity":
            return {"dependent_eval_ids": [], "independent_eval_ids": []}
        assert name == "check_experiment_status_activity"
        return {"status": "COMPLETED"}

    async def child(run, value, **kwargs):
        children.append(value)
        commands.append("child")

        async def completed():
            return {"completed_rows": len(value.rerun_row_ids), "failed_rows": 0}

        return asyncio.create_task(completed())

    monkeypatch.setattr(workflows.workflow, "execute_activity", activity)
    monkeypatch.setattr(workflows.workflow, "start_child_workflow", child)
    monkeypatch.setattr(workflows.workflow, "patched", lambda name: True)
    monkeypatch.setattr(
        workflows.workflow, "info", lambda: SimpleNamespace(workflow_id="classic-test")
    )
    result = asyncio.run(workflows.RerunCellsV2Workflow().run(value))
    assert result.status == "COMPLETED"
    return children, commands, value


@pytest.mark.parametrize("failure_kind", ["output_error", "failed_check"])
def test_classic_failed_only_disjoint_configs_keep_their_own_rows(
    user, organization, workspace, workflow, monkeypatch, failure_kind
):
    seeded = _classic_run(organization, workspace, failed=False)
    other = _table(seeded, "2")
    other_prompt = _prompt(other)
    other_eval = _evaluation(seeded, other, "evaluation")
    for row in seeded.rows:
        _cell(other_eval, row, CellStatus.PASS.value)
    for owned, evaluation, row in (
        (seeded.own, seeded.evaluation, seeded.rows[0]),
        (other, other_eval, seeded.rows[1]),
    ):
        if failure_kind == "output_error":
            Cell.all_objects.filter(column_id=owned.output.id, row_id=row.id).update(
                status=CellStatus.ERROR.value
            )
        else:
            Column.all_objects.filter(id=evaluation.id).update(
                data_type=DataTypeChoices.BOOLEAN.value
            )
            Cell.all_objects.filter(column_id=evaluation.id, row_id=row.id).update(
                value="false"
            )
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id), str(other.table.id)],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    workflow.assert_called_once()
    children, _, _ = _workflow_children(workflow.call_args.kwargs, monkeypatch)
    assert len(children) == 2
    columns = {
        str(seeded.prompt.id): seeded.own.output.id,
        str(other_prompt.id): other.output.id,
    }
    actual = {
        value.experiment_prompt_config_id: _resolve_row_ids(
            value.dataset_id,
            value.rerun_row_ids,
            value.failed_only,
            [columns[value.experiment_prompt_config_id]],
        )
        for value in children
    }
    assert actual == {
        str(seeded.prompt.id): [seeded.rows[0].id],
        str(other_prompt.id): [seeded.rows[1].id],
    }
    assert _after(seeded.own.output)[1][1] == CellStatus.PASS.value
    assert _after(other.output)[1][0] == CellStatus.PASS.value


def test_classic_failed_only_does_not_start_successful_config(
    user, organization, workspace, workflow, monkeypatch
):
    seeded = _classic_run(organization, workspace)
    successful = _table(seeded, "3")
    _prompt(successful)
    before = _state([successful.output])
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id), str(successful.table.id)],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    workflow.assert_called_once()
    children, _, _ = _workflow_children(workflow.call_args.kwargs, monkeypatch)
    assert [child.experiment_prompt_config_id for child in children] == [
        str(seeded.prompt.id)
    ]
    assert _state([successful.output]) == before


@pytest.mark.parametrize("kind", ["prompt", "agent"])
def test_classic_workflow_empty_config_selection_never_falls_back(kind, monkeypatch):
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput

    assert "row_ids_by_config" in RerunCellsV2WorkflowInput.__dataclass_fields__
    value = RerunCellsV2WorkflowInput(
        experiment_id="experiment",
        dataset_id="snapshot",
        prompt_config_ids=["a", "b", "c"] if kind == "prompt" else [],
        agent_config_ids=["a", "b", "c"] if kind == "agent" else [],
        row_ids=["must-not-fallback"],
        failed_only=True,
        row_ids_by_config={"a": ["row-a"], "b": []},
    )
    children, _, _ = _workflow_children({}, monkeypatch, input_value=value)
    assert len(children) == 1
    assert children[0].rerun_row_ids == ["row-a"]
    assert getattr(children[0], f"experiment_{kind}_config_id") == "a"


def test_classic_async_client_preserves_config_rows():
    from tfc.temporal.experiments import client as temporal_client
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput

    assert "row_ids_by_config" in RerunCellsV2WorkflowInput.__dataclass_fields__
    selected = {"prompt-a": ["row-a"]}
    with patch.object(
        temporal_client,
        "start_workflow_async",
        return_value=SimpleNamespace(id="classic-test"),
    ) as scheduled:
        asyncio.run(
            temporal_client.start_rerun_cells_v2_workflow_async(
                experiment_id="experiment",
                dataset_id="snapshot",
                prompt_config_ids=["prompt-a"],
                failed_only=True,
                row_ids_by_config=selected,
            )
        )
    assert scheduled.call_args.kwargs["workflow_input"].row_ids_by_config == selected


def test_classic_old_payload_dispatches_the_same_commands(monkeypatch):
    children, commands, value = _workflow_children(
        {
            "experiment_id": "experiment",
            "dataset_id": "snapshot",
            "prompt_config_ids": ["prompt-a", "prompt-b"],
            "row_ids": ["row-a", "row-b"],
            "failed_only": True,
        },
        monkeypatch,
    )
    assert getattr(value, "row_ids_by_config", None) is None
    assert commands == [
        "mark_experiment_running_activity",
        "child",
        "child",
        "check_experiment_status_activity",
    ]
    assert [child.experiment_prompt_config_id for child in children] == [
        "prompt-a",
        "prompt-b",
    ]
    assert [child.rerun_row_ids for child in children] == [["row-a", "row-b"]] * 2
    assert all(child.failed_only for child in children)


def test_classic_failed_only_other_tenant_cannot_select_or_reset_rows(
    organization, workspace, workflow
):
    seeded = _classic_run(organization, workspace)
    before = _run_state(seeded)
    other = _tenant_b()
    client = _login(other.user, other.organization, other.workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    assert response.status_code == 400, response.content
    workflow.assert_not_called()
    assert _run_state(seeded) == before
