"""A run_eval_func_task keeps its usage event id across the retries of its
activity, and a new task is billed again.

The task evaluates once for the log it was started for. Its evaluation is
keyed to that log, the task's workflow run, and the template with its model,
so a retry after the usage was emitted is billed once, for a Jev evaluation and
for any other evaluator alike: run_eval_func keys both kinds of usage event to
the step it is given. A new task is a new workflow run and is billed again. A
task with no log is not keyed: its usage gets fresh ids.
"""

from types import SimpleNamespace
from unittest.mock import patch

from evaluations.usage_step import step_event_id
from model_hub.views.utils import evals


def _run_task(
    workflow_run, log_id="log-1", template_id="template-1", model="turing_large"
):
    """One attempt of run_eval_func_task in ``workflow_run``. Returns the
    usage event id its evaluation is keyed to, or None."""
    with (
        patch.object(evals, "EvalTemplate") as templates,
        patch.object(evals, "Organization") as organizations,
        patch.object(evals, "run_eval_func") as run,
        patch("temporalio.activity.in_activity", return_value=True),
        patch(
            "temporalio.activity.info",
            return_value=SimpleNamespace(workflow_run_id=workflow_run),
        ),
    ):
        templates.no_workspace_objects.get.return_value = SimpleNamespace(
            id=template_id
        )
        organizations.objects.get.return_value = SimpleNamespace(id="org-1")
        evals.run_eval_func_task(
            {"input": "made-up input"},
            template_id,
            "org-1",
            model=model,
            log_id=log_id,
        )
    run.assert_called_once()
    return step_event_id(run.call_args.kwargs.get("usage_step"))


def test_a_retry_of_the_task_keeps_its_usage_event_id():
    first = _run_task("workflow-run-1")
    retry = _run_task("workflow-run-1")
    assert first is not None
    assert retry == first


def test_a_new_task_gets_a_new_usage_event_id():
    first = _run_task("workflow-run-1")
    rerun = _run_task("workflow-run-2")
    assert first is not None
    assert rerun is not None
    assert rerun != first


def test_a_task_without_a_log_is_not_keyed():
    assert _run_task("workflow-run-1", log_id=None) is None


def test_each_log_template_and_model_gets_its_own_usage_event_id():
    ids = [
        _run_task("workflow-run-1", log_id="log-1"),
        _run_task("workflow-run-1", log_id="log-2"),
        _run_task("workflow-run-1", template_id="template-2"),
        _run_task("workflow-run-1", model="other-model"),
    ]
    assert None not in ids
    assert len(set(ids)) == 4
