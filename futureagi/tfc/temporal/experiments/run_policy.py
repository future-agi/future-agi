"""Policy projection shared by experiment prompt and graph error handling."""

import json
from contextlib import contextmanager


def failed_row_values(policy, message):
    """Zero participates in aggregates; exclusion never masquerades as a score."""
    if policy not in ("zero", "exclude"):
        return None
    return {
        "value": "0" if policy == "zero" else "",
        "status": "pass" if policy == "zero" else "error",
        "value_infos": json.dumps(
            {
                "reason": message,
                "upstream_error": True,
                "error_policy": policy,
                "excluded": policy == "exclude",
            }
        ),
        "deleted": False,
    }


# The legacy prompt workflow has no failed-row eval step and scores every row
# its activity returns, so its rows keep the original COMPLETED contract.
LEGACY_ROW_WORKFLOWS = frozenset({"ProcessPromptWorkflow"})


def reports_failed_rows():
    """Whether the calling workflow settles failed rows itself."""
    from temporalio import activity

    try:
        workflow_type = activity.info().workflow_type
    except RuntimeError:
        return True
    return workflow_type not in LEGACY_ROW_WORKFLOWS


def persisted_prompt_status(row_id, column_id, dataset_id, experiment_id):
    """The row implementation can handle errors without raising; inspect its write.

    The written cell must be in the source dataset's organization and
    workspace scope. A source with no workspace is in its organization's
    default workspace, where older snapshots of it were saved.
    """
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentsTable
    from model_hub.utils.snapshot_scope import same_workspace_scope

    experiment = ExperimentsTable.objects.get(id=experiment_id, deleted=False)
    # Snapshot runs keep rows in the snapshot; legacy runs in the dataset itself.
    admitted = experiment.snapshot_dataset_id or experiment.dataset_id
    if str(admitted) != str(dataset_id):
        raise ValueError("Row dataset does not match the experiment dataset.")
    source = experiment.dataset
    written = (
        Cell.objects.filter(
            row_id=row_id,
            row__dataset_id=dataset_id,
            column_id=column_id,
            column__dataset_id=dataset_id,
            dataset_id=dataset_id,
            dataset__organization_id=source.organization_id,
            deleted=False,
        )
        .values_list("status", "dataset__workspace_id")
        .first()
    )
    if written is None:
        return "FAILED"
    status, workspace_id = written
    if not same_workspace_scope(
        workspace_id, source.workspace_id, source.organization_id
    ):
        return "FAILED"
    return "COMPLETED" if status == CellStatus.PASS.value else "FAILED"


def failed_row_policy(cell):
    """The error policy a failed-row outcome cell was written with, or None."""
    info = cell.value_infos
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except ValueError:
            return None
    if not isinstance(info, dict) or info.get("upstream_error") is not True:
        return None
    policy = info.get("error_policy")
    return policy if policy in ("zero", "exclude") else None


def is_policy_zero(cell):
    """True for a failed-row cell the zero policy wrote as a score of 0."""
    return failed_row_policy(cell) == "zero"


def holds_failed_row_outcome(dataset, column, row, upstream_column):
    """True while the row's result cell holds a settled failed-row outcome.

    The outcome stands only while the row's upstream output is still failed:
    a rerun that resets the result cell to RUNNING, or a re-run whose output
    succeeds, hands the cell back to the evaluator.
    """
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell

    if upstream_column is None:
        return False
    cell = (
        Cell.objects.filter(dataset=dataset, column=column, row=row, deleted=False)
        .exclude(status=CellStatus.RUNNING.value)
        .only("value_infos")
        .first()
    )
    if cell is None or failed_row_policy(cell) is None:
        return False
    upstream = (
        Cell.objects.filter(
            dataset=dataset, column=upstream_column, row=row, deleted=False
        )
        .values_list("status", flat=True)
        .first()
    )
    return upstream == CellStatus.ERROR.value


def evaluation_stopped(metric_id):
    """True when the evaluation was stopped or deleted (a plain read)."""
    from model_hub.models.choices import StatusType
    from model_hub.models.evals_metric import UserEvalMetric

    state = (
        UserEvalMetric.all_objects.filter(id=metric_id)
        .values_list("status", "deleted")
        .first()
    )
    return (
        state is None
        or bool(state[1])
        or state[0] in (StatusType.CANCELLED.value, StatusType.ERROR.value)
    )


@contextmanager
def experiment_write_lock(experiment_id):
    """Hold the experiment row while result cells are checked and written.

    The failed-row writer and the whole-experiment stop take the same row, so
    a check made under it cannot go stale before its write. Without an
    experiment this is a no-op.
    """
    if not experiment_id:
        yield
        return
    from django.db import transaction

    from model_hub.models.experiments import ExperimentsTable

    with transaction.atomic():
        list(
            ExperimentsTable.objects.select_for_update()
            .filter(id=experiment_id)
            .values_list("id", flat=True)
        )
        yield


def rows_holding_failed_row_outcomes(dataset, column, upstream_column, row_ids=None):
    """Ids of rows (within `row_ids` when given) whose result cell in `column`
    holds a settled failed-row outcome while their upstream output is still
    failed."""
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell

    if column is None or upstream_column is None:
        return set()
    failed = Cell.objects.filter(
        dataset=dataset,
        column=upstream_column,
        status=CellStatus.ERROR.value,
        deleted=False,
    )
    if row_ids:
        failed = failed.filter(row_id__in=row_ids)
    failed = failed.values_list("row_id", flat=True)
    cells = (
        Cell.objects.filter(
            dataset=dataset, column=column, row_id__in=failed, deleted=False
        )
        .exclude(status=CellStatus.RUNNING.value)
        .only("row_id", "value_infos")
    )
    return {cell.row_id for cell in cells if failed_row_policy(cell) is not None}


def write_failed_row_cells(dataset, column, rows, values):
    """Write the failed-row outcome for these rows in one result column.

    An upstream failure decides the row's result, so every existing cell for
    the row is replaced (a placeholder, another evaluator pass's generic error
    or a stale score) and a missing cell is created. Callers decide whether the
    write may happen at all (stops, user-stopped evaluations).
    """
    from model_hub.models.develop_dataset import Cell

    rows = list(rows)
    if not rows:
        return 0
    existing = Cell.objects.filter(
        dataset=dataset,
        column=column,
        row_id__in=[row.id for row in rows],
        deleted=False,
    )
    present = set(existing.values_list("row_id", flat=True))
    updated = existing.update(**values)
    missing = [
        Cell(dataset=dataset, column=column, row=row, **values)
        for row in rows
        if row.id not in present
    ]
    if missing:
        Cell.objects.bulk_create(missing)
    return updated + len(missing)
