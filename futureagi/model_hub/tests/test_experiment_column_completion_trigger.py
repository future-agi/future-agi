"""Completing an experiment evaluation or reason column re-checks its table.

``check_and_update_column_status`` settles a column once none of its cells are
running. For an experiment evaluation or evaluation reason column it then runs
``check_and_update_experiment_dataset_status`` for the experiment table
(``ExperimentDatasetTable``) the column belongs to. The table is found from the
column's source id:

* ``<table id>-<output column id>-sourceid-<metric id>``: evaluation columns
  written by ``EvaluationRunner._get_column_config`` and
  ``CompositeEvaluationRunner.build_column_config``;
* ``<table id>-sourceid-<metric id>``: evaluation columns of earlier tables;
* ``<evaluation or output column id>-sourceid-<metric id>``: reason columns
  written by ``EvaluationRunner._create_reason_column``, which also links them
  to the table.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models import Organization
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.tests.test_experiment_dataset_completion_matcher import (
    FORMATS,
    _cell,
    _column,
    _create_run,
    _evaluation,
    _seed,
    _snapshot_state,
    _state,
    _status,
    _table,
    _table_view,
    _tenant_b,
    _write_cells,
)
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def runner_log():
    with patch("model_hub.views.experiment_runner.logger") as log:
        yield log


@pytest.fixture
def no_model_call():
    """No step of these runs sends a model request."""
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    with patch.object(
        RunPrompt, "litellm_response", side_effect=AssertionError("model call")
    ) as model_call:
        yield model_call


def _settle(column):
    from model_hub.views.experiment_runner import check_and_update_column_status

    clear_workspace_context()
    return check_and_update_column_status(column.id)


def _finish(column, rows):
    for row in rows:
        _cell(column, row, CellStatus.PASS.value)


def _reason(seeded, owned, parent):
    """A reason column written by the evaluation runner's own writer.

    ``parent`` is the evaluation column, or ``None`` when the runner finds no
    evaluation column and keys the reason column off the output column.
    """
    from model_hub.views.eval_runner import EvaluationRunner

    runner = SimpleNamespace(
        user_eval_metric=seeded.metric,
        user_eval_metric_id=seeded.metric.id,
        experiment_dataset=owned.table,
        column=owned.output,
        replace_column_id=owned.output.id,
        is_only_eval=False,
        optimize=None,
    )

    def create_or_update(dataset, config, new_column=False):
        return EvaluationRunner._create_or_update_column(
            runner, dataset, config, new_column
        )

    runner._create_or_update_column = create_or_update
    column = EvaluationRunner._create_reason_column(
        runner, seeded.data, "Completion reason", parent_column=parent
    )
    key = parent.id if parent is not None else owned.output.id
    assert column.source == SourceChoices.EVALUATION_REASON.value
    assert column.source_id == f"{key}-sourceid-{seeded.metric.id}"
    assert column.status == StatusType.RUNNING.value
    assert owned.table.columns.filter(id=column.id).exists()
    return column


@pytest.mark.parametrize("fmt", FORMATS)
def test_finished_evaluation_column_completes_its_table(
    organization, workspace, fmt, runner_log
):
    seeded = _seed(organization, workspace)
    column = _evaluation(seeded, seeded.own, fmt)
    _finish(column, seeded.rows)

    assert _settle(column) is True

    column.refresh_from_db()
    assert column.status == StatusType.COMPLETED.value
    assert _status(seeded.own.table) == StatusType.COMPLETED.value
    runner_log.exception.assert_not_called()


@pytest.mark.parametrize("fmt", FORMATS)
def test_running_evaluation_column_keeps_its_table_open(
    organization, workspace, fmt, runner_log
):
    seeded = _seed(organization, workspace)
    column = _evaluation(seeded, seeded.own, fmt)
    _cell(column, seeded.rows[0], CellStatus.PASS.value)
    _cell(column, seeded.rows[1], CellStatus.RUNNING.value)
    before = _state([column])

    assert _settle(column) is False

    assert _state([column]) == before
    assert _status(seeded.own.table) == StatusType.RUNNING.value
    runner_log.exception.assert_not_called()


@pytest.mark.parametrize("parent", ["evaluation", "output"])
def test_finished_reason_column_completes_its_table(
    organization, workspace, parent, runner_log
):
    seeded = _seed(organization, workspace)
    if parent == "evaluation":
        evaluation = _evaluation(seeded, seeded.own, "evaluation")
        reason = _reason(seeded, seeded.own, evaluation)
    else:
        # No evaluation column exists yet when the reason column is written.
        reason = _reason(seeded, seeded.own, None)
        evaluation = _evaluation(seeded, seeded.own, "evaluation")
    # Every evaluation cell is done; the column itself is settled by the table check.
    _finish(evaluation, seeded.rows)
    _finish(reason, seeded.rows)

    assert _settle(reason) is True

    reason.refresh_from_db()
    evaluation.refresh_from_db()
    assert reason.status == StatusType.COMPLETED.value
    assert _status(seeded.own.table) == StatusType.COMPLETED.value
    assert evaluation.status == StatusType.COMPLETED.value
    runner_log.exception.assert_not_called()


def test_finished_reason_column_keeps_a_running_table_open(
    organization, workspace, runner_log
):
    seeded = _seed(organization, workspace)
    evaluation = _evaluation(seeded, seeded.own, "evaluation")
    _cell(evaluation, seeded.rows[0], CellStatus.PASS.value)
    _cell(evaluation, seeded.rows[1], CellStatus.RUNNING.value)
    reason = _reason(seeded, seeded.own, evaluation)
    _finish(reason, seeded.rows)
    before = _state([evaluation])

    assert _settle(reason) is True

    assert _state([evaluation]) == before
    assert _status(seeded.own.table) == StatusType.RUNNING.value
    runner_log.exception.assert_not_called()


@pytest.mark.parametrize("fmt", ["evaluation", "earlier"])
def test_only_the_columns_own_table_is_checked(
    organization, workspace, fmt, runner_log
):
    seeded = _seed(organization, workspace)
    other = _table(seeded, "f")
    assert str(other.table.id)[0] != str(seeded.own.table.id)[0]
    # The other table has nothing left to run but is not checked.
    done = _evaluation(seeded, other, "evaluation")
    _finish(done, seeded.rows)
    other_before = _state([other.output, done])
    column = _evaluation(seeded, seeded.own, fmt)
    _finish(column, seeded.rows)

    assert _settle(column) is True

    assert _status(seeded.own.table) == StatusType.COMPLETED.value
    assert _status(other.table) == StatusType.RUNNING.value
    assert _state([other.output, done]) == other_before
    runner_log.exception.assert_not_called()


@pytest.mark.parametrize("fmt", ["evaluation", "earlier"])
@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_matching_column_in_another_dataset_leaves_the_table_alone(
    organization, workspace, owner, fmt, runner_log
):
    seeded = _seed(organization, workspace)
    own = _evaluation(seeded, seeded.own, fmt)
    _cell(own, seeded.rows[0], CellStatus.RUNNING.value)
    other_org, other_workspace = organization, workspace
    if owner == "other-tenant":
        other_org = Organization.objects.create(name="Trigger tenant B")
        other_workspace = None
    other = Dataset.no_workspace_objects.create(
        name="Unrelated", organization=other_org, workspace=other_workspace
    )
    other_row = Row.objects.create(dataset=other, order=1)
    stray = _column(other, SourceChoices.EXPERIMENT_EVALUATION.value, own.source_id)
    _cell(stray, other_row, CellStatus.PASS.value)
    own_before = _state([seeded.own.output, own])

    assert _settle(stray) is True

    stray.refresh_from_db()
    assert stray.status == StatusType.COMPLETED.value
    assert _state([seeded.own.output, own]) == own_before
    assert _status(seeded.own.table) == StatusType.RUNNING.value


# Two tenants, real create API, the workflow's own column status step.


@pytest.mark.django_db(transaction=True)
def test_created_run_completes_when_its_evaluation_column_finishes(
    user, organization, workspace, no_model_call, runner_log
):
    from tfc.temporal.experiments.activities import _check_column_status_sync

    tenant_b = _tenant_b()
    run_a = _create_run(user, organization, workspace)
    run_b = _create_run(tenant_b.user, tenant_b.organization, tenant_b.workspace)
    clear_workspace_context()
    metric_id = run_a.evaluation.source_id.split("-sourceid-")[1]
    # The create API also writes the reason column, keyed off the evaluation column.
    assert run_a.table.columns.filter(
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{run_a.evaluation.id}-sourceid-{metric_id}",
    ).exists()
    # Tenant B's snapshot carries a column with A's evaluation id.
    stray = Column.no_workspace_objects.create(
        name="Copied evaluation",
        data_type="text",
        dataset_id=run_b.snapshot_id,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=run_a.evaluation.source_id,
        status=StatusType.RUNNING.value,
    )
    _write_cells(run_b, stray, CellStatus.RUNNING.value, "")
    b_before = _snapshot_state(run_b)

    # The output finishes while the evaluation is in flight: the table stays open.
    _write_cells(run_a, run_a.output, CellStatus.PASS.value, "Sunny")
    _write_cells(run_a, run_a.evaluation, CellStatus.RUNNING.value, "")
    clear_workspace_context()
    assert (
        _check_column_status_sync(str(run_a.output.id))["status"]
        == StatusType.COMPLETED.value
    )
    assert _status(run_a.table) == StatusType.RUNNING.value

    # The evaluation finishes; its own column check completes the table.
    _write_cells(run_a, run_a.evaluation, CellStatus.PASS.value, "Passed")
    clear_workspace_context()
    runner_log.reset_mock()
    check = _check_column_status_sync(str(run_a.evaluation.id))

    assert check["is_complete"] is True
    assert check["status"] == StatusType.COMPLETED.value
    assert _status(run_a.table) == StatusType.COMPLETED.value
    runner_log.exception.assert_not_called()
    assert _table_view(run_a.client, run_a) == (
        200,
        (CellStatus.PASS.value, [CellStatus.PASS.value]),
    )
    assert _table_view(run_b.client, run_a) == (404, None)
    assert _snapshot_state(run_b) == b_before
    no_model_call.assert_not_called()
