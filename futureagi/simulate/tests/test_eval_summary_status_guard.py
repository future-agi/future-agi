"""Behavioral regression test for #313 / PR #343.

The bug: run_eval_summary_task set eval_explanation_summary_status=RUNNING, and if the
summary computation died in a way `except Exception` cannot see (worker cancellation,
SystemExit -- any BaseException), the status was stranded RUNNING forever. The fix adds a
`finally` guard that re-fetches and flips a still-RUNNING status to FAILED no matter how
the body exited.

Exercises the real run_eval_summary_task against the real ORM; only the summary
computation (_get_cluster_dict_by_eval) is patched to fail, since a real summary needs a
live LLM. The BaseException case fails on the pre-fix code (no finally guard -> status
stays RUNNING) and passes on the fix.
"""
import pytest

from simulate.models import RunTest, TestExecution
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.tasks import eval_summary_tasks


@pytest.fixture
def test_execution(db, organization, workspace):
    run_test = RunTest.objects.create(
        name="eval-summary-guard-313",
        organization=organization,
        workspace=workspace,
    )
    return TestExecution.objects.create(run_test=run_test)


@pytest.fixture(autouse=True)
def _keep_test_db_connection(monkeypatch):
    # The @temporal_activity drop-in wrapper calls close_old_connections() around the
    # task body, which severs pytest-django's test connection. Patching it out here is
    # the repo's own convention for calling decorated tasks in tests (see
    # test_chat_simulation.py's @patch("tfc.temporal.drop_in.decorator.close_old_connections")).
    monkeypatch.setattr(
        "tfc.temporal.drop_in.decorator.close_old_connections", lambda: None
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_failing_summary_leaves_status_failed_not_running(test_execution, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("summary computation exploded")

    monkeypatch.setattr(eval_summary_tasks, "_get_cluster_dict_by_eval", boom)
    eval_summary_tasks.run_eval_summary_task(str(test_execution.id))

    test_execution.refresh_from_db()
    assert (
        test_execution.eval_explanation_summary_status
        == EvalExplanationSummaryStatus.FAILED
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_base_exception_cannot_strand_status_running(test_execution, monkeypatch):
    # The #313 regression: a cancellation-style BaseException bypasses `except Exception`;
    # only the `finally` guard can repair RUNNING -> FAILED. Pre-fix this strands RUNNING.
    def boom(*args, **kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(eval_summary_tasks, "_get_cluster_dict_by_eval", boom)
    with pytest.raises(SystemExit):
        eval_summary_tasks.run_eval_summary_task(str(test_execution.id))

    test_execution.refresh_from_db()
    assert (
        test_execution.eval_explanation_summary_status
        == EvalExplanationSummaryStatus.FAILED
    ), "status stranded RUNNING: only the finally guard repairs a BaseException exit"
