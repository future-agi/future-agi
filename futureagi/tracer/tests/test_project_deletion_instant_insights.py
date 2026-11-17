"""Deleting a project stops its Instant eval and Insights work and removes the
trace text they copied (excerpts, unit readings, report results)."""

import uuid
from unittest import mock

import pytest
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow, InstantEvalRun
from tracer.services.project_deletion import soft_delete_projects

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def _instant_run(project, *, status=EvalTaskStatus.RUNNING, cancelled=False):
    task = EvalTask.objects.create(
        project=project,
        name="Instant eval · traces",
        filters={},
        sampling_rate=100,
        run_type=RunType.HISTORICAL,
        row_type="traces",
        status=status,
    )
    run = InstantEvalRun.objects.create(
        eval_task=task,
        project=project,
        organization=project.organization,
        unit="traces",
        row_type="traces",
        population=1,
        rows_requested=1,
        questions=1,
        credits_estimate=0,
        cancelled_at=timezone.now() if cancelled else None,
    )
    row = InstantEvalRow.objects.create(
        task_id=task.id,
        row_id=str(uuid.uuid4()),
        unit="traces",
        excerpt="Customer: my card 4242 was charged twice",
    )
    InstantEvalAnswer.objects.create(
        task_id=task.id, row=row, config_id=uuid.uuid4(), label="Yes"
    )
    return task, run


def _insight_run(project, *, status="running", deleted=False):
    config = InsightConfig.objects.create(
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        name="Where is the agent failing?",
        unit="sessions",
        lens="failures",
        question="Where is the agent failing?",
    )
    run = InsightRun.objects.create(
        config=config,
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        unit="sessions",
        status=status,
        result={"findings": [{"text": "Refunds fail", "quote": "charged twice"}]},
    )
    InsightUnit.objects.create(
        run=run,
        unit_id="s1",
        excerpt="charged twice",
        content="Customer: my card 4242 was charged twice",
        summary="A double charge was not refunded",
    )
    if deleted:
        InsightRun._base_manager.filter(id=run.id).update(deleted=True)
    return config, run


def _delete(project):
    with mock.patch(
        "tracer.services.project_deletion.publish_project_invalidation"
    ), mock.patch(
        "tfc.temporal.eval_tasks.client.signal_pause_eval_task_workflow"
    ) as pause:
        soft_delete_projects(project.__class__.objects.filter(id=project.id), "observe")
    return pause


def test_instant_eval_copies_are_removed_and_the_run_cancelled(observe_project):
    task, run = _instant_run(observe_project)

    _delete(observe_project)

    assert not InstantEvalRow.objects.filter(task_id=task.id).exists()
    assert not InstantEvalAnswer.objects.filter(task_id=task.id).exists()
    run.refresh_from_db()
    assert run.cancelled_at is not None
    assert EvalTask.all_objects.get(id=task.id).deleted is True


def test_an_active_instant_run_is_paused_after_commit(
    observe_project, django_capture_on_commit_callbacks
):
    active, _ = _instant_run(observe_project)
    finished, _ = _instant_run(observe_project, status=EvalTaskStatus.COMPLETED)
    cancelled, _ = _instant_run(observe_project, cancelled=True)

    with mock.patch(
        "tracer.services.project_deletion.publish_project_invalidation"
    ), mock.patch(
        "tfc.temporal.eval_tasks.client.signal_pause_eval_task_workflow"
    ) as pause:
        # The signal is sent once the delete commits, inside the patch.
        with django_capture_on_commit_callbacks(execute=True):
            soft_delete_projects(
                observe_project.__class__.objects.filter(id=observe_project.id),
                "observe",
            )

    assert [call.args[0] for call in pause.call_args_list] == [str(active.id)]
    assert str(finished.id) not in str(pause.call_args_list)
    assert str(cancelled.id) not in str(pause.call_args_list)


def test_insights_reports_lose_their_copied_text(observe_project):
    config, run = _insight_run(observe_project, status="completed")
    _, earlier = _insight_run(observe_project, status="completed", deleted=True)

    _delete(observe_project)

    assert not InsightUnit.objects.filter(run__in=[run, earlier]).exists()
    for report in InsightRun._base_manager.filter(id__in=[run.id, earlier.id]):
        assert report.deleted is True
        assert report.result == {}
        assert report.status == "completed"
    assert InsightConfig._base_manager.get(id=config.id).deleted is True
    assert not InsightRun.objects.filter(project=observe_project).exists()


def test_a_running_report_is_failed_so_no_stage_continues(observe_project):
    _, running = _insight_run(observe_project, status="running")
    _, pending = _insight_run(observe_project, status="pending")

    _delete(observe_project)

    for report in InsightRun._base_manager.filter(id__in=[running.id, pending.id]):
        assert report.status == "failed"
        assert report.error == "The project was deleted."
        assert report.finished_at is not None


def test_other_projects_keep_their_runs(observe_project, organization, workspace):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    other = Project.objects.create(
        name="Other",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    task, run = _instant_run(other)
    _, report = _insight_run(other, status="completed")

    _delete(observe_project)

    assert InstantEvalRow.objects.filter(task_id=task.id).exists()
    run.refresh_from_db()
    assert run.cancelled_at is None
    report.refresh_from_db()
    assert report.deleted is False
    assert InsightUnit.objects.filter(run=report).count() == 1
