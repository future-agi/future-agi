"""SQL workflow routing and admission-failure regressions without a server."""

import importlib
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from django.test import override_settings
from temporalio.exceptions import ActivityError

from tfc.temporal.drop_in.runner import _start_activity_async
from tfc.temporal.drop_in.workflow import TaskRunnerInput
from tfc.temporal.sql_workbench import SQLExecutionWorkflow
from tracer.sql_workbench.tasks import execute_sql


@pytest.mark.parametrize("queue", ["tasks_xl", "exact_aggregation"])
async def test_real_dispatch_selects_sql_workflow_on_configured_control_queue(queue):
    client = AsyncMock()
    with patch("tfc.temporal.common.client.get_client", return_value=client):
        await _start_activity_async(
            execute_sql._activity_name, ["job"], {}, queue, "job"
        )
    args, kwargs = client.start_workflow.call_args
    assert args[0] == SQLExecutionWorkflow.run
    assert kwargs["task_queue"] == queue
    assert args[1].max_retries == 0


async def test_admission_failure_has_available_control_queue_finalizer():
    failure = ActivityError(
        "schedule timeout",
        scheduled_event_id=1,
        started_event_id=0,
        identity="",
        activity_type=execute_sql._activity_name,
        activity_id="1",
        retry_state=None,
    )
    execute = AsyncMock(side_effect=[failure, 1])
    with (
        patch("tfc.temporal.sql_workbench.workflow.execute_activity", execute),
        pytest.raises(ActivityError),
    ):
        await SQLExecutionWorkflow().run(
            TaskRunnerInput(execute_sql._activity_name, ["job"], {}, queue="tasks_xl")
        )
    scan, finalize = execute.call_args_list
    assert scan.kwargs["task_queue"] == "sql_workbench"
    assert scan.kwargs["schedule_to_start_timeout"] == timedelta(seconds=45)
    assert scan.kwargs["retry_policy"].maximum_attempts == 1
    assert finalize.kwargs["task_queue"] == "tasks_s"
    assert finalize.args == (
        "tracer.sql_workbench.fail_execution",
        {"args": ["job"], "kwargs": {}},
    )


@pytest.mark.parametrize("queue", ["tasks_xl", "exact_aggregation"])
def test_retention_schedule_uses_configured_queue(queue):
    from tfc.temporal.schedules import sql_workbench

    try:
        with override_settings(EXACT_AGGREGATION_TASK_QUEUE=queue):
            schedules = importlib.reload(sql_workbench).SQL_WORKBENCH_SCHEDULES
            assert schedules[0].queue == queue
            assert schedules[1].queue == "tasks_s"
    finally:
        importlib.reload(sql_workbench)


def test_sql_scan_registration_is_not_on_exact_or_fallback_activity_slots():
    from temporalio import activity

    from tfc.temporal.common.registry import get_activities_for_queue

    def names(queue):
        return {
            activity._Definition.must_from_callable(fn).name
            for fn in get_activities_for_queue(queue)
        }

    assert execute_sql._activity_name in names("sql_workbench")
    assert execute_sql._activity_name not in names("exact_aggregation")
    assert execute_sql._activity_name not in names("tasks_xl")


def test_generic_worker_cannot_steal_sql_scan_slots():
    from tfc.management.commands.start_temporal_worker import _generic_all_queues

    assert _generic_all_queues(["sql_workbench", "exact_aggregation", "tasks_s"]) == [
        "tasks_s"
    ]


async def test_retention_uses_control_activity_capacity():
    from tfc.temporal.sql_workbench import SQLRetentionWorkflow

    execute = AsyncMock()
    with patch("tfc.temporal.sql_workbench.workflow.execute_activity", execute):
        await SQLRetentionWorkflow().run()
    assert execute.call_args.kwargs["task_queue"] == "tasks_s"
    assert execute.call_args.args[0] == "tracer.sql_workbench.purge_expired_results"


@pytest.mark.parametrize("value", ["-1", "text", "3601"])
def test_reuse_environment_setting_fails_closed(monkeypatch, value):
    from django.core.exceptions import ImproperlyConfigured

    from tfc.settings.sql_workbench import _integer

    monkeypatch.setenv("SQL_WORKBENCH_RESULT_CACHE_SECONDS", value)
    with pytest.raises(ImproperlyConfigured):
        _integer("SQL_WORKBENCH_RESULT_CACHE_SECONDS", 60, 0, 3600)


def test_sql_workflows_are_registered_for_both_control_queues():
    from tfc.temporal.common.registry import get_workflows_for_queue
    from tfc.temporal.sql_workbench import SQLRetentionWorkflow

    # The deployed embedded and standalone workers use UnsandboxedWorkflowRunner.
    for queue in ("tasks_xl", "exact_aggregation"):
        registered = get_workflows_for_queue(queue)
        assert SQLExecutionWorkflow in registered
        assert SQLRetentionWorkflow in registered
