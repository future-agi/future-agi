"""Cutover guard for orphaned legacy eval-task activity invocations."""

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracer.models.eval_task import EvalTaskLogger, EvalTaskStatus, RowType
from tracer.utils import eval as eval_module
from tracer.utils.eval_tasks import process_eval_task, run_for_processed_spans


@pytest.fixture
def trace_eval_task(eval_task):
    eval_task.row_type = RowType.TRACES
    eval_task.status = EvalTaskStatus.PENDING
    eval_task.save(update_fields=["row_type", "status", "updated_at"])
    return eval_task


@pytest.mark.django_db
def test_legacy_trace_activity_bridges_before_status_query_or_dispatch(
    trace_eval_task, monkeypatch
):
    started = []

    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task: started.append(str(task.id)) or "workflow-id",
    )

    def legacy_path_reached(*_args, **_kwargs):
        raise AssertionError("retired trace dispatcher executed")

    monkeypatch.setattr(
        "tracer.utils.eval_tasks.get_mixpanel_properties", legacy_path_reached
    )

    process_eval_task._original_func(str(trace_eval_task.id))

    assert started == [str(trace_eval_task.id)]
    trace_eval_task.refresh_from_db()
    assert trace_eval_task.status == EvalTaskStatus.PENDING
    assert not EvalTaskLogger.objects.filter(eval_task=trace_eval_task).exists()


@pytest.mark.django_db
def test_legacy_span_activity_also_uses_the_only_supported_workflow_engine(
    eval_task, monkeypatch
):
    started = []

    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task: started.append(str(task.id)) or "workflow-id",
    )

    def legacy_path_reached(*_args, **_kwargs):
        raise AssertionError("retired span dispatcher executed")

    monkeypatch.setattr(
        "tracer.utils.eval_tasks.get_mixpanel_properties", legacy_path_reached
    )

    process_eval_task._original_func(str(eval_task.id))

    assert started == [str(eval_task.id)]
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.PENDING
    assert not EvalTaskLogger.objects.filter(eval_task=eval_task).exists()


@pytest.mark.django_db
def test_already_enqueued_legacy_batch_is_redirected_without_direct_dispatch(
    eval_task, monkeypatch
):
    started = []
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task: started.append(str(task.id)) or "workflow-id",
    )

    run_for_processed_spans._original_func(
        ["retired-span-id"], ["retired-eval-id"], str(eval_task.id)
    )

    assert started == [str(eval_task.id)]
    eval_task.refresh_from_db()
    assert eval_task.status == EvalTaskStatus.PENDING
    assert not EvalTaskLogger.objects.filter(eval_task=eval_task).exists()


@pytest.mark.django_db
def test_legacy_trace_activity_treats_existing_workflow_as_idempotent(
    trace_eval_task, monkeypatch
):
    trace_eval_task.status = EvalTaskStatus.RUNNING
    trace_eval_task.save(update_fields=["status", "updated_at"])

    def already_running(_task):
        raise WorkflowAlreadyStartedError(
            f"eval-task-{trace_eval_task.id}", "HistoricalEvalTaskWorkflow"
        )

    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        already_running,
    )

    process_eval_task._original_func(str(trace_eval_task.id))

    trace_eval_task.refresh_from_db()
    assert trace_eval_task.status == EvalTaskStatus.RUNNING
    assert not EvalTaskLogger.objects.filter(eval_task=trace_eval_task).exists()


@pytest.mark.django_db
def test_legacy_trace_activity_fails_closed_when_workflow_start_is_unavailable(
    trace_eval_task, monkeypatch
):
    def unavailable(_task):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync", unavailable
    )

    process_eval_task._original_func(str(trace_eval_task.id))

    trace_eval_task.refresh_from_db()
    assert trace_eval_task.status == EvalTaskStatus.PENDING
    assert not EvalTaskLogger.objects.filter(eval_task=trace_eval_task).exists()


@pytest.mark.django_db
def test_legacy_trace_activity_does_not_restart_terminal_task(
    trace_eval_task, monkeypatch
):
    trace_eval_task.status = EvalTaskStatus.COMPLETED
    trace_eval_task.save(update_fields=["status", "updated_at"])

    def unexpected_start(_task):
        raise AssertionError("terminal task restarted")

    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        unexpected_start,
    )

    process_eval_task._original_func(str(trace_eval_task.id))

    trace_eval_task.refresh_from_db()
    assert trace_eval_task.status == EvalTaskStatus.COMPLETED
    assert not EvalTaskLogger.objects.filter(eval_task=trace_eval_task).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("activity", "kwargs"),
    [
        (
            eval_module.evaluate_observation_span_observe,
            {
                "observation_span_id": "retired-span",
                "custom_eval_config_id": "retired-eval",
            },
        ),
        (
            eval_module.evaluate_trace_observe,
            {
                "trace_id": "retired-trace",
                "custom_eval_config_id": "retired-eval",
            },
        ),
        (
            eval_module.evaluate_trace_session_observe,
            {
                "session_id": "retired-session",
                "custom_eval_config_id": "retired-eval",
            },
        ),
    ],
)
def test_already_enqueued_per_row_activity_is_redirected_before_target_lookup(
    eval_task, monkeypatch, activity, kwargs
):
    started = []
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task: started.append(str(task.id)) or "workflow-id",
    )

    activity._original_func(**kwargs, eval_task_id=str(eval_task.id))

    assert started == [str(eval_task.id)]
