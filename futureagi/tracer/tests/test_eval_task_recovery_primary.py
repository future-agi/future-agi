"""The read-after-write dispatch guard must not use a routed read connection."""

import pytest
from django.db import connections, router, transaction
from django.test.utils import CaptureQueriesContext

from tfc.temporal.eval_tasks import client
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.services.eval_tasks.recovery import recover_task


@pytest.mark.django_db(transaction=True, databases=["default", "default_direct"])
@pytest.mark.parametrize("stop", [None, EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED])
def test_dispatch_guard_reads_primary_and_respects_committed_stop(
    project, monkeypatch, stop
):
    task = EvalTask.no_workspace_objects.create(
        project=project,
        name="synthetic primary guard",
        filters={},
        sampling_rate=100,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
    )

    class IndependentReader:
        def db_for_read(self, model, **hints):
            return "default_direct" if model is EvalTask else None

        def db_for_write(self, model, **hints):
            return None

    monkeypatch.setattr(router, "routers", [IndependentReader(), *router.routers])
    starts = []
    monkeypatch.setattr(
        client, "describe_eval_task_workflow_sync", lambda _: client.WF_CLOSED
    )
    monkeypatch.setattr(
        client,
        "start_eval_task_workflow_sync",
        lambda task, **kw: starts.append(task.pk),
    )

    def after_update(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            stop
            and sql.startswith('UPDATE "tracer_eval_task"')
            and '"start_time"' in sql
        ):
            transaction.on_commit(
                lambda: (
                    EvalTask.no_workspace_objects.using("default")
                    .filter(pk=task.pk)
                    .update(status=stop)
                )
            )
        return result

    with (
        CaptureQueriesContext(connections["default"]) as primary,
        CaptureQueriesContext(connections["default_direct"]) as reader,
    ):
        with connections["default"].execute_wrapper(after_update):
            outcome = recover_task(task, stale_running_seconds=7200)

    def guards(queries):
        return [
            q["sql"]
            for q in queries
            if "SELECT 1 AS" in q["sql"] and "tracer_eval_task" in q["sql"]
        ]

    assert guards(primary), (
        "Final lifecycle guard must read the primary's committed state"
    )
    assert guards(reader) == [], (
        "A lagged reader can suppress valid recovery or miss a pause"
    )
    if stop:
        assert starts == [] and outcome["skipped"]
    else:
        assert starts == [task.pk] and outcome["restarted"]
