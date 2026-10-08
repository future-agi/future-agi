"""Dataset eval cells abandoned in ``running`` are closed like a crashed run.

Production (ClickHouse mirror, 2026-09-29): 11,452 ``running`` cells in 156
dataset eval columns and 2,108 in their reason columns, none written since the
mirror's first sync on 2026-07-18; nothing ever selects them again, so the
grid shows them loading forever. Live runs must be left alone: every flip to
``running`` is a bulk write that leaves ``updated_at`` behind, so the last
write is read from the mirror's ``_peerdb_synced_at``.

A rerun flips cells to ``running`` and often keeps their value (for example
develop_dataset.py's rerun of an eval column): such a cell still shows an
earlier result. Recovery closes only running cells without a value. Production
(mirror, 2026-09-29): 1,767 running cells in 62 live evals hold a value; 40 of
those evals have no other running cell and are left alone.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from conftest import _ch_test_native_client, _ch_test_owned_database
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.selectors import stale_eval_cells
from model_hub.selectors.stale_eval_cells import RunningColumn
from model_hub.services import stale_eval_recovery, stale_work
from model_hub.services.stale_eval_recovery import (
    DatasetEvalRecovery,
    recover_stale_dataset_evals,
)
from model_hub.tasks.stale_work import recover_stale_work_activity
from tfc.utils.error_codes import get_error_message
from tracer.services.clickhouse.schema import CDC_MODEL_HUB_CELL

RUNNING = CellStatus.RUNNING.value
INTERRUPTED = get_error_message("RUN_INTERRUPTED")


# --- The mirror read, on a real ClickHouse ---------------------------------


@pytest.fixture()
def mirror(monkeypatch):
    """A test-owned ``model_hub_cell`` the selector reads through its client."""
    ddl = CDC_MODEL_HUB_CELL.replace(
        "ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/model_hub_cell', "
        "'{replica}', _peerdb_version)",
        "ReplacingMergeTree(_peerdb_version)",
    )
    with _ch_test_owned_database("test_stale_eval_") as database:
        with _ch_test_native_client(database=database) as client:
            client.execute(ddl)

            class _Client:
                def execute_read(self, query, params=None, timeout_ms=None):
                    rows, columns = client.execute(
                        query, params or {}, with_column_types=True
                    )
                    return rows, columns, 0.0

            monkeypatch.setattr(stale_eval_cells, "get_clickhouse_client", _Client)
            yield client


def _write(client, *, cell, column, status, synced, version, deleted=0, written=None):
    # ``written`` is the Postgres write stamp; the mirror applies it at ``synced``.
    written = written or synced
    client.execute(
        "INSERT INTO model_hub_cell (id, dataset_id, column_id, row_id, status, "
        "created_at, updated_at, deleted, _peerdb_synced_at, _peerdb_version) VALUES",
        [
            (
                cell,
                uuid.uuid4(),
                column,
                uuid.uuid4(),
                status,
                written,
                written,
                deleted,
                synced,
                version,
            )
        ],
    )


def test_last_write_counts_every_version_bulk_resets_included(mirror):
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    old, recent = now - timedelta(hours=30), now - timedelta(hours=1)
    abandoned, rerun, finished, removed = (uuid.uuid4() for _ in range(4))
    _write(
        mirror,
        cell=uuid.uuid4(),
        column=abandoned,
        status=RUNNING,
        synced=old,
        version=1,
    )
    _write(
        mirror,
        cell=uuid.uuid4(),
        column=abandoned,
        status="pass",
        synced=old,
        version=1,
    )
    # A rerun re-flips an already-running cell: same values, new write.
    rerun_cell = uuid.uuid4()
    _write(mirror, cell=rerun_cell, column=rerun, status=RUNNING, synced=old, version=1)
    _write(
        mirror, cell=rerun_cell, column=rerun, status=RUNNING, synced=recent, version=2
    )
    finished_cell = uuid.uuid4()
    _write(
        mirror,
        cell=finished_cell,
        column=finished,
        status=RUNNING,
        synced=old,
        version=1,
    )
    _write(
        mirror,
        cell=finished_cell,
        column=finished,
        status="pass",
        synced=recent,
        version=2,
    )
    removed_cell = uuid.uuid4()
    _write(
        mirror, cell=removed_cell, column=removed, status=RUNNING, synced=old, version=1
    )
    _write(
        mirror,
        cell=removed_cell,
        column=removed,
        status=RUNNING,
        synced=old,
        version=2,
        deleted=1,
    )

    columns = stale_eval_cells.read_running_columns()

    assert columns == [
        RunningColumn(str(rerun), 1, recent.replace(tzinfo=UTC)),
        RunningColumn(str(abandoned), 1, old.replace(tzinfo=UTC)),
    ]
    assert stale_eval_cells.read_mirror_last_write() == recent.replace(tzinfo=UTC)


def test_a_mirror_replaying_a_backlog_is_only_as_current_as_its_newest_write(mirror):
    """While CDC replays a backlog every applied row is freshly synced, but the
    mirror still lacks everything Postgres wrote since the replayed rows."""
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    replayed = now - timedelta(hours=3)
    _write(
        mirror,
        cell=uuid.uuid4(),
        column=uuid.uuid4(),
        status=RUNNING,
        synced=now,
        written=replayed,
        version=1,
    )

    assert stale_eval_cells.read_mirror_last_write() == replayed.replace(tzinfo=UTC)


# --- Recovery, on PostgreSQL with the mirror's answer stubbed ----------------


@pytest.fixture()
def eval_run(user, workspace):
    """A Running dataset eval whose first row never finished."""
    template = EvalTemplate.no_workspace_objects.create(
        name=f"stale-{uuid.uuid4().hex[:6]}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
    )
    dataset = Dataset.objects.create(
        name="stale-eval",
        organization=user.organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        workspace=workspace,
    )
    metric = UserEvalMetric.objects.create(
        name="stale-eval",
        organization=user.organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {}},
        user=user,
        status=StatusType.RUNNING.value,
    )
    result = Column.objects.create(
        name="stale-eval",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        dataset=dataset,
    )
    reason = Column.objects.create(
        name="stale-eval-reason",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{result.id}-sourceid-{metric.id}",
        dataset=dataset,
    )
    for order, status in enumerate([RUNNING, CellStatus.PASS.value]):
        row = Row.objects.create(dataset=dataset, order=order)
        for column in (result, reason):
            Cell.objects.create(
                dataset=dataset, row=row, column=column, value="", status=status
            )
    # Went Running long ago (the flip stamps updated_at; .update() does not).
    UserEvalMetric.objects.filter(id=metric.id).update(
        updated_at=timezone.now() - timedelta(hours=30)
    )
    metric.refresh_from_db()
    return metric, [result, reason]


def _mirror_reports(monkeypatch, columns, *, last_write_age, mirror_idle=False):
    now = timezone.now()
    mirror_last_write = now - (timedelta(hours=2) if mirror_idle else timedelta())
    monkeypatch.setattr(stale_eval_recovery, "mirror_is_available", lambda: True)
    monkeypatch.setattr(
        stale_eval_recovery, "read_mirror_last_write", lambda: mirror_last_write
    )
    monkeypatch.setattr(
        stale_eval_recovery,
        "read_running_columns",
        lambda: [RunningColumn(str(c.id), 1, now - last_write_age) for c in columns],
    )


def _cells(columns):
    return list(
        Cell.objects.filter(column__in=columns)
        .order_by("row__order", "column__name")
        .values_list("status", "value", "value_infos")
    )


def test_abandoned_run_is_closed_like_the_runner_closes_a_crash(monkeypatch, eval_run):
    metric, columns = eval_run
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))

    result = recover_stale_dataset_evals(apply=True, limit=10)

    assert [(r.user_eval_metric_id, r.running_cells) for r in result.recovered] == [
        (str(metric.id), 2)
    ]
    metric.refresh_from_db()
    assert metric.status == StatusType.ERROR.value
    reason = json.dumps({"reason": INTERRUPTED})
    assert _cells(columns) == [
        ("error", INTERRUPTED, reason),
        ("error", INTERRUPTED, reason),
        ("pass", "", []),
        ("pass", "", []),
    ]


def test_finished_eval_keeps_its_status_and_loses_its_spinners(monkeypatch, eval_run):
    metric, columns = eval_run
    UserEvalMetric.objects.filter(id=metric.id).update(
        status=StatusType.COMPLETED.value
    )
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))

    recover_stale_dataset_evals(apply=True, limit=10)

    metric.refresh_from_db()
    assert metric.status == StatusType.COMPLETED.value
    assert [status for status, _, _ in _cells(columns)] == [
        "error",
        "error",
        "pass",
        "pass",
    ]


@pytest.mark.parametrize(
    "live",
    [
        "column_written_recently",
        "queued_run",
        "just_went_running",
        "mirror_idle",
        "dataset_deleted",
    ],
)
def test_live_or_unprovable_work_is_untouched(monkeypatch, eval_run, live):
    metric, columns = eval_run
    before = _cells(columns)
    if live == "dataset_deleted":
        Dataset.objects.filter(id=metric.dataset_id).update(deleted=True)
    if live == "queued_run":
        UserEvalMetric.objects.filter(id=metric.id).update(
            status=StatusType.NOT_STARTED.value
        )
    if live == "just_went_running":
        UserEvalMetric.objects.filter(id=metric.id).update(updated_at=timezone.now())
    _mirror_reports(
        monkeypatch,
        columns,
        last_write_age=timedelta(hours=1 if live == "column_written_recently" else 30),
        mirror_idle=live == "mirror_idle",
    )
    status_before = UserEvalMetric.objects.get(id=metric.id).status

    result = recover_stale_dataset_evals(apply=True, limit=10)

    assert result.recovered == []
    assert UserEvalMetric.objects.get(id=metric.id).status == status_before
    assert _cells(columns) == before


@pytest.mark.parametrize("status", [StatusType.RUNNING, StatusType.COMPLETED])
def test_a_rerun_started_meanwhile_keeps_its_cells(monkeypatch, eval_run, status):
    metric, columns = eval_run
    UserEvalMetric.objects.filter(id=metric.id).update(status=status.value)
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))
    before = _cells(columns)

    class _RerunStartsAfterTheRead:
        class objects:
            @staticmethod
            def filter(**kwargs):
                UserEvalMetric.objects.filter(id=metric.id).update(
                    status=StatusType.NOT_STARTED.value
                )
                return Cell.objects.filter(**kwargs)

    monkeypatch.setattr(stale_eval_recovery, "Cell", _RerunStartsAfterTheRead)

    assert recover_stale_dataset_evals(apply=True, limit=10).recovered == []
    assert _cells(columns) == before
    metric.refresh_from_db()
    assert metric.status == StatusType.NOT_STARTED.value


def test_experiment_columns_are_left_to_their_workflow(monkeypatch, eval_run):
    metric, columns = eval_run
    Column.objects.filter(id__in=[c.id for c in columns]).update(
        source=SourceChoices.EXPERIMENT_EVALUATION.value
    )
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))

    assert recover_stale_dataset_evals(apply=True, limit=10).recovered == []
    metric.refresh_from_db()
    assert metric.status == StatusType.RUNNING.value


def test_command_dry_run_changes_nothing_then_apply_closes(
    monkeypatch, eval_run, capsys
):
    metric, columns = eval_run
    before = _cells(columns)
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))

    call_command("recover_stale_work", "--source", "dataset_eval_cells")

    out = capsys.readouterr().out
    assert "dry run, nothing written" in out
    assert f"dataset_eval_cells {metric.organization_id}: 1, 2" in out
    assert _cells(columns) == before
    assert UserEvalMetric.objects.get(id=metric.id).status == StatusType.RUNNING.value

    call_command("recover_stale_work", "--source", "dataset_eval_cells", "--apply")

    assert "Stale work (applied): 1 recovered" in capsys.readouterr().out
    assert UserEvalMetric.objects.get(id=metric.id).status == StatusType.ERROR.value


def test_queueing_a_run_stamps_when_it_went_running(monkeypatch, eval_run):
    """``execute_evaluation`` flips queued evals with ``.update()``; without
    the stamp a queued run looks as old as the eval's last edit."""
    from model_hub.tasks import user_evaluation

    metric, _ = eval_run
    UserEvalMetric.objects.filter(id=metric.id).update(
        status=StatusType.NOT_STARTED.value
    )
    monkeypatch.setattr(user_evaluation, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        user_evaluation.process_evaluation_single_task,
        "apply_async",
        lambda args: None,
    )

    user_evaluation.execute_evaluation._original_func()

    metric.refresh_from_db()
    assert metric.status == StatusType.RUNNING.value
    assert timezone.now() - metric.updated_at < timedelta(minutes=1)


def test_the_open_build_recovers_dataset_evals_alone(monkeypatch):
    """Usage rows exist only with the enterprise usage app, which registers
    their recovery when it loads; the open build registers nothing."""
    monkeypatch.setattr(stale_work, "_RECOVERERS", {})
    monkeypatch.setattr(
        stale_work,
        "recover_stale_dataset_evals",
        lambda **_: DatasetEvalRecovery(recovered=[]),
    )

    assert stale_work.recoverable_sources() == [stale_work.DATASET_EVAL_SOURCE]
    report = stale_work.recover_stale_work(apply=True, batch_size=10)
    assert report == stale_work.StaleWorkReport()
    with pytest.raises(ValueError, match="standalone_v2"):
        stale_work.recover_stale_work(
            apply=True, batch_size=10, sources=["standalone_v2"]
        )


def test_a_cell_still_showing_an_earlier_result_is_left_as_it_is(monkeypatch, eval_run):
    """The rerun kept the result cell's value; only the empty running cell
    (its reason) is closed, and the kept cell is reported."""
    metric, columns = eval_run
    result_column, _ = columns
    Cell.objects.filter(column=result_column, status=RUNNING).update(value="Passed")
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))

    recovery = recover_stale_dataset_evals(apply=True, limit=10)

    metric.refresh_from_db()
    assert metric.status == StatusType.ERROR.value
    assert _cells(columns) == [
        (RUNNING, "Passed", []),
        ("error", INTERRUPTED, json.dumps({"reason": INTERRUPTED})),
        ("pass", "", []),
        ("pass", "", []),
    ]
    assert [(r.user_eval_metric_id, r.running_cells) for r in recovery.recovered] == [
        (str(metric.id), 1)
    ]
    assert [(e.user_eval_metric_id, e.cells) for e in recovery.excluded] == [
        (str(metric.id), 1)
    ]


def test_an_eval_whose_running_cells_all_hold_results_is_left_alone(
    monkeypatch, eval_run
):
    metric, columns = eval_run
    Cell.objects.filter(column__in=columns, status=RUNNING).update(value="Passed")
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))
    before = _cells(columns)

    recovery = recover_stale_dataset_evals(apply=True, limit=10)

    metric.refresh_from_db()
    assert metric.status == StatusType.RUNNING.value
    assert _cells(columns) == before
    assert recovery.recovered == []
    assert [(e.user_eval_metric_id, e.cells) for e in recovery.excluded] == [
        (str(metric.id), 2)
    ]


@pytest.mark.parametrize("apply", [False, True], ids=["report_only", "apply"])
def test_the_schedule_changes_nothing_until_recovery_is_switched_on(
    monkeypatch, settings, eval_run, apply
):
    """``STALE_WORK_RECOVERY_APPLY`` is off by default: the hourly tick only
    reports what it would close. A deploy re-registers every schedule and
    clears a pause, so the setting is the switch that holds."""
    settings.STALE_WORK_RECOVERY_APPLY = apply
    monkeypatch.setattr(stale_work, "_RECOVERERS", {})
    metric, columns = eval_run
    _mirror_reports(monkeypatch, columns, last_write_age=timedelta(hours=30))
    before = _cells(columns)

    result = recover_stale_work_activity._original_func()

    metric.refresh_from_db()
    if apply:
        assert metric.status == StatusType.ERROR.value
        assert [status for status, _, _ in _cells(columns)][:2] == ["error", "error"]
    else:
        assert metric.status == StatusType.RUNNING.value
        assert _cells(columns) == before
    assert result == {
        "mode": "apply" if apply else "report_only",
        "recovered" if apply else "would_recover": {
            "dataset_eval_cells": {str(metric.organization_id): 1}
        },
        "excluded": {},
        "skipped": {},
    }
