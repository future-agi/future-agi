"""The optimization runner takes each row out of NotStarted before it runs it."""

from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.db.models import QuerySet
from structlog.testing import capture_logs

from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.tasks.optimisation_runner import process_optimization

RUNNER = "model_hub.tasks.optimisation_runner"


@pytest.fixture
def optimization(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Optimization Runner Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    return OptimizationDataset.no_workspace_objects.create(
        name="Optimization Runner Row",
        optimize_type="PROMPT_TEMPLATE",
        dataset=dataset,
    )


def _stored_status(row):
    return OptimizationDataset.no_workspace_objects.values_list(
        "status", flat=True
    ).get(id=row.id)


@pytest.mark.django_db
def test_process_optimization_skips_a_row_another_pass_claimed(optimization):
    """Two passes can hold the same row: one that outlived its time limit is
    still draining its list while the next lists what it has not reached.
    Only the first to take the row out of NotStarted runs it."""
    assert optimization.status == StatusType.NOT_STARTED.value

    with (
        patch(f"{RUNNER}.close_old_connections"),
        patch(f"{RUNNER}.DevelopOptimizer") as optimizer,
    ):
        process_optimization(optimization)
        process_optimization(optimization)

    optimizer.assert_called_once_with(optim_obj_id=optimization.id)
    optimizer.return_value.run.assert_called_once_with()
    assert _stored_status(optimization) == StatusType.RUNNING.value


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [StatusType.RUNNING.value, StatusType.COMPLETED.value, StatusType.FAILED.value],
)
def test_process_optimization_runs_only_a_row_not_started(optimization, status):
    """A row that has left NotStarted since it was listed is not run again,
    whatever the stale copy in hand still says."""
    OptimizationDataset.no_workspace_objects.filter(id=optimization.id).update(
        status=status
    )
    assert optimization.status == StatusType.NOT_STARTED.value

    with (
        patch(f"{RUNNER}.close_old_connections"),
        patch(f"{RUNNER}.DevelopOptimizer") as optimizer,
    ):
        process_optimization(optimization)

    optimizer.assert_not_called()
    assert _stored_status(optimization) == status


@pytest.mark.django_db
def test_process_optimization_fails_a_claimed_row_that_cannot_start(optimization):
    """A row this pass claimed whose optimizer cannot even be built must not
    read as running for good: it is marked failed and the error still
    reaches the pool that logs it."""
    with (
        patch(f"{RUNNER}.close_old_connections"),
        patch(f"{RUNNER}.DevelopOptimizer", side_effect=RuntimeError("no column")),
        pytest.raises(RuntimeError, match="no column"),
    ):
        process_optimization(optimization)

    assert _stored_status(optimization) == StatusType.FAILED.value


@pytest.mark.django_db
def test_process_optimization_fails_a_claimed_row_whose_run_raises(optimization):
    """An error out of the run itself (its first save can refuse a config
    that was edited while the row waited) must not leave the claimed row
    running either."""
    with (
        patch(f"{RUNNER}.close_old_connections"),
        patch(f"{RUNNER}.DevelopOptimizer") as optimizer,
        pytest.raises(RuntimeError, match="invalid model config"),
    ):
        optimizer.return_value.run.side_effect = RuntimeError("invalid model config")
        process_optimization(optimization)

    assert _stored_status(optimization) == StatusType.FAILED.value


@pytest.mark.django_db
def test_process_optimization_keeps_what_a_run_wrote_before_it_raised(optimization):
    """The optimizer ends its own row: completed, or failed with its reason
    stored. An error after that must not overwrite it: only a row that is
    still running is marked failed."""

    def _ends_the_row_then_raises():
        OptimizationDataset.no_workspace_objects.filter(id=optimization.id).update(
            status=StatusType.COMPLETED.value
        )
        raise RuntimeError("cost log failed")

    with (
        patch(f"{RUNNER}.close_old_connections"),
        patch(f"{RUNNER}.DevelopOptimizer") as optimizer,
        pytest.raises(RuntimeError, match="cost log failed"),
    ):
        optimizer.return_value.run.side_effect = _ends_the_row_then_raises
        process_optimization(optimization)

    assert _stored_status(optimization) == StatusType.COMPLETED.value


def _recorded_runner_steps(*, failed_write_error=None):
    """Patches for one pass of the runner that record, in order, every
    connection recycle and every status the runner writes on a row.

    ``failed_write_error`` is raised by the write that marks the row failed,
    in place of that write.
    """
    steps = []
    real_update = QuerySet.update

    def _update(queryset, **fields):
        if queryset.model is OptimizationDataset and "status" in fields:
            steps.append(("write", fields["status"]))
            if (
                failed_write_error is not None
                and fields["status"] == StatusType.FAILED.value
            ):
                raise failed_write_error
        return real_update(queryset, **fields)

    return steps, (
        patch(
            f"{RUNNER}.close_old_connections",
            side_effect=lambda: steps.append("recycle"),
        ),
        patch.object(QuerySet, "update", autospec=True, side_effect=_update),
    )


@pytest.mark.django_db
def test_process_optimization_recycles_the_connection_before_failing_the_row(
    optimization,
):
    """The error that stops a claimed row can be its database connection
    going away. The write that marks the row failed uses the same thread's
    connection, so the dead one is dropped first: the row is then failed on a
    fresh connection instead of staying running for good."""
    lost_connection = OperationalError("server closed the connection unexpectedly")
    steps, (recycle, update) = _recorded_runner_steps()

    with (
        recycle,
        update,
        patch(f"{RUNNER}.DevelopOptimizer", side_effect=lost_connection),
        pytest.raises(OperationalError) as raised,
    ):
        process_optimization(optimization)

    assert raised.value is lost_connection
    assert steps == [
        "recycle",
        ("write", StatusType.RUNNING.value),
        "recycle",
        ("write", StatusType.FAILED.value),
        "recycle",
    ]
    assert _stored_status(optimization) == StatusType.FAILED.value


@pytest.mark.django_db
def test_process_optimization_raises_the_first_error_when_failing_the_row_fails(
    optimization,
):
    """When the database is still down, the write that marks the row failed
    raises as well. One line is logged for it, with the row's id and with
    exception information asked for (the line is written while that second
    error is being handled, so it is the one recorded), and the error that
    reaches the pool is the one that stopped the run. The row is left
    running: nothing could be written."""
    lost_connection = OperationalError("server closed the connection unexpectedly")
    still_down = OperationalError("could not connect to server")
    steps, (recycle, update) = _recorded_runner_steps(failed_write_error=still_down)

    with (
        recycle,
        update,
        patch(f"{RUNNER}.DevelopOptimizer", side_effect=lost_connection),
        capture_logs() as logs,
        pytest.raises(OperationalError) as raised,
    ):
        process_optimization(optimization)

    assert raised.value is lost_connection
    assert steps == [
        "recycle",
        ("write", StatusType.RUNNING.value),
        "recycle",
        ("write", StatusType.FAILED.value),
        "recycle",
    ]
    assert [
        {key: value for key, value in line.items() if key != "log_level"}
        for line in logs
    ] == [
        {
            "event": "optimization_mark_failed_error",
            "optimization_id": str(optimization.id),
            "exc_info": True,
        }
    ]
    assert _stored_status(optimization) == StatusType.RUNNING.value
