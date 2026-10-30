"""
Attempts that build a run's eval explanation summary.

An attempt whose worker was lost must not leave the summary pending or
running forever, polling the summary must not queue a second attempt beside
a live one, and an attempt that a later one replaced must not write over it.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework import status

from simulate.models.run_test import RunTest
from simulate.models.test_execution import EvalExplanationSummaryStatus, TestExecution
from simulate.tasks.eval_summary_tasks import run_eval_summary_task

SUMMARY_URL = "/simulate/test-executions/{}/eval-explanation-summary/"
REFRESH_URL = "/simulate/test-executions/{}/eval-explanation-summary/refresh/"
APPLY_ASYNC = "simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async"
BUILD_SUMMARY = "simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval"

# Well past the summary activity's 30-minute time limit and any margin on it.
LONG_AGO = timedelta(hours=2)


@pytest.fixture
def run_test(db, organization, workspace):
    return RunTest.objects.create(
        name="Summary Run", organization=organization, workspace=workspace
    )


@pytest.fixture
def test_execution(db, run_test):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_calls=5,
    )


def _set_summary(test_execution, *, status, last_updated, summary=None):
    TestExecution.objects.filter(id=test_execution.id).update(
        eval_explanation_summary=summary,
        eval_explanation_summary_status=status,
        eval_explanation_summary_last_updated=last_updated,
    )
    test_execution.refresh_from_db()


def _run_dispatched(mock_apply_async, index=-1):
    """Run the summary job exactly as the given dispatch queued it."""
    queued = mock_apply_async.call_args_list[index].kwargs
    run_eval_summary_task._original_func(
        *queued["args"], **(queued.get("kwargs") or {})
    )


@pytest.mark.integration
@pytest.mark.api
class TestSummaryPoll:
    def test_poll_requeues_refresh_left_running_past_time_limit(
        self, auth_client, test_execution
    ):
        # A refresh whose worker died: the old summary is still shown, and the
        # status says running forever, so the page polls forever.
        old_summary = {"eval": [{"kind": "failure", "theme": "old"}]}
        died_at = timezone.now() - LONG_AGO
        _set_summary(
            test_execution,
            status=EvalExplanationSummaryStatus.RUNNING,
            last_updated=died_at,
            summary=old_summary,
        )

        with patch(APPLY_ASYNC) as mock_apply_async:
            response = auth_client.get(SUMMARY_URL.format(test_execution.id))

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["response"] == old_summary
        assert result["status"] == EvalExplanationSummaryStatus.PENDING
        mock_apply_async.assert_called_once()
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )
        assert test_execution.eval_explanation_summary_last_updated > died_at

    def test_poll_requeues_first_summary_left_running_past_time_limit(
        self, auth_client, test_execution
    ):
        _set_summary(
            test_execution,
            status=EvalExplanationSummaryStatus.RUNNING,
            last_updated=timezone.now() - LONG_AGO,
        )

        with (
            patch(APPLY_ASYNC) as mock_apply_async,
            patch(BUILD_SUMMARY, return_value={"eval": []}),
        ):
            response = auth_client.get(SUMMARY_URL.format(test_execution.id))
            # The queued job is the run's current attempt, so it runs.
            _run_dispatched(mock_apply_async)

        assert response.json()["result"]["status"] == (
            EvalExplanationSummaryStatus.PENDING
        )
        # The poll's job only builds a summary that is still pending.
        mock_apply_async.assert_called_once_with(
            args=(str(test_execution.id),), kwargs={"pending_only": True}
        )
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )
        assert test_execution.eval_explanation_summary == {"eval": []}

    def test_poll_leaves_fresh_running_summary_alone(self, auth_client, test_execution):
        started_at = timezone.now() - timedelta(minutes=1)
        _set_summary(
            test_execution,
            status=EvalExplanationSummaryStatus.RUNNING,
            last_updated=started_at,
        )

        with patch(APPLY_ASYNC) as mock_apply_async:
            response = auth_client.get(SUMMARY_URL.format(test_execution.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["status"] == (
            EvalExplanationSummaryStatus.RUNNING
        )
        mock_apply_async.assert_not_called()
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.RUNNING
        )
        assert test_execution.eval_explanation_summary_last_updated == started_at

    def test_poll_leaves_freshly_queued_summary_alone(
        self, auth_client, test_execution
    ):
        _set_summary(
            test_execution,
            status=EvalExplanationSummaryStatus.PENDING,
            last_updated=timezone.now() - timedelta(seconds=10),
        )

        with patch(APPLY_ASYNC) as mock_apply_async:
            auth_client.get(SUMMARY_URL.format(test_execution.id))

        mock_apply_async.assert_not_called()

    def test_repeated_polls_queue_one_attempt(self, auth_client, test_execution):
        url = SUMMARY_URL.format(test_execution.id)

        with patch(APPLY_ASYNC) as mock_apply_async:
            first = auth_client.get(url)
            second = auth_client.get(url)

        mock_apply_async.assert_called_once()
        for response in (first, second):
            result = response.json()["result"]
            assert result["response"] is None
            assert result["status"] == EvalExplanationSummaryStatus.PENDING
            # No summary has been written yet, so none has a time to show.
            assert result["last_updated"] is None

    def test_poll_requeues_after_failed_dispatch(self, auth_client, test_execution):
        url = SUMMARY_URL.format(test_execution.id)

        with patch(
            APPLY_ASYNC, side_effect=[TimeoutError("dispatch timed out"), MagicMock()]
        ) as mock_apply_async:
            first = auth_client.get(url)
            test_execution.refresh_from_db()
            assert (
                test_execution.eval_explanation_summary_status
                == EvalExplanationSummaryStatus.PENDING
            )
            assert test_execution.eval_explanation_summary_last_updated is None
            second = auth_client.get(url)

        assert first.status_code == status.HTTP_200_OK
        assert first.json()["result"]["status"] == EvalExplanationSummaryStatus.PENDING
        assert second.status_code == status.HTTP_200_OK
        assert mock_apply_async.call_count == 2


@pytest.mark.integration
@pytest.mark.api
class TestSummaryAttempt:
    def test_summary_happy_path(self, auth_client, test_execution):
        summary = {"eval": [{"kind": "failure", "theme": "timeouts"}]}
        url = SUMMARY_URL.format(test_execution.id)

        with (
            patch(APPLY_ASYNC) as mock_apply_async,
            patch(BUILD_SUMMARY, return_value=summary) as mock_build,
        ):
            auth_client.get(url)
            _run_dispatched(mock_apply_async)
            response = auth_client.get(url)

        mock_build.assert_called_once()
        mock_apply_async.assert_called_once()
        result = response.json()["result"]
        assert result["response"] == summary
        assert result["status"] == EvalExplanationSummaryStatus.COMPLETED
        assert result["last_updated"] is not None
        test_execution.refresh_from_db()
        assert test_execution.eval_explanation_summary == summary
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )

    def test_failing_summary_is_marked_failed(self, auth_client, test_execution):
        with (
            patch(APPLY_ASYNC) as mock_apply_async,
            patch(BUILD_SUMMARY, side_effect=ValueError("clustering failed")),
        ):
            auth_client.get(SUMMARY_URL.format(test_execution.id))
            _run_dispatched(mock_apply_async)

        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.FAILED
        )
        assert test_execution.eval_explanation_summary is None
        assert test_execution.eval_explanation_summary_last_updated is not None

    def test_attempt_refreshed_while_running_does_not_write(
        self, auth_client, test_execution
    ):
        refreshed = []

        def build_while_refreshed(*args, **kwargs):
            auth_client.post(REFRESH_URL.format(test_execution.id), {}, format="json")
            refreshed.append(
                TestExecution.objects.get(
                    id=test_execution.id
                ).eval_explanation_summary_last_updated
            )
            return {"eval": [{"theme": "stale"}]}

        with (
            patch(APPLY_ASYNC) as mock_apply_async,
            patch(BUILD_SUMMARY, side_effect=build_while_refreshed),
        ):
            run_eval_summary_task._original_func(str(test_execution.id))

        mock_apply_async.assert_called_once()
        test_execution.refresh_from_db()
        assert test_execution.eval_explanation_summary is None
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )
        assert test_execution.eval_explanation_summary_last_updated == refreshed[0]

    def test_attempt_replaced_while_running_does_not_overwrite_newer_result(
        self, test_execution
    ):
        builds = []

        def build(*args, **kwargs):
            builds.append(len(builds))
            if len(builds) == 1:
                # A newer job starts and finishes while the first still builds.
                run_eval_summary_task._original_func(str(test_execution.id))
                return {"eval": [{"theme": "older"}]}
            return {"eval": [{"theme": "newer"}]}

        with patch(BUILD_SUMMARY, side_effect=build):
            run_eval_summary_task._original_func(str(test_execution.id))

        assert len(builds) == 2
        test_execution.refresh_from_db()
        assert test_execution.eval_explanation_summary == {"eval": [{"theme": "newer"}]}
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )

    def test_replaced_attempt_failing_does_not_mark_newer_attempt_failed(
        self, auth_client, test_execution
    ):
        def fail_after_refresh(*args, **kwargs):
            auth_client.post(REFRESH_URL.format(test_execution.id), {}, format="json")
            raise ValueError("clustering failed")

        with (
            patch(APPLY_ASYNC),
            patch(BUILD_SUMMARY, side_effect=fail_after_refresh),
        ):
            run_eval_summary_task._original_func(str(test_execution.id))

        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )

    def test_two_queued_jobs_build_the_summary_once(self, auth_client, test_execution):
        with (
            patch(APPLY_ASYNC) as mock_apply_async,
            patch(BUILD_SUMMARY, return_value={"eval": []}) as mock_build,
        ):
            auth_client.get(SUMMARY_URL.format(test_execution.id))
            auth_client.post(REFRESH_URL.format(test_execution.id), {}, format="json")

            # Whichever job starts first builds the pending summary...
            _run_dispatched(mock_apply_async, index=0)
            # ...and the other finds it built and does nothing.
            _run_dispatched(mock_apply_async, index=1)

        assert mock_apply_async.call_count == 2
        mock_build.assert_called_once()
        test_execution.refresh_from_db()
        assert test_execution.eval_explanation_summary == {"eval": []}
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )

    def test_job_slow_to_start_still_builds_after_requeue(
        self, auth_client, test_execution
    ):
        url = SUMMARY_URL.format(test_execution.id)

        with (
            patch(APPLY_ASYNC) as mock_apply_async,
            patch(BUILD_SUMMARY, return_value={"eval": []}) as mock_build,
        ):
            auth_client.get(url)
            # The queue is backed up: the job has waited past the stale time,
            # so the next poll queues another one.
            TestExecution.objects.filter(id=test_execution.id).update(
                eval_explanation_summary_last_updated=timezone.now() - LONG_AGO
            )
            auth_client.get(url)

            # The first job finally starts; it must not be locked out by the
            # second, or a queue that stays slow would never build the summary.
            _run_dispatched(mock_apply_async, index=0)
            test_execution.refresh_from_db()
            assert (
                test_execution.eval_explanation_summary_status
                == EvalExplanationSummaryStatus.COMPLETED
            )

            _run_dispatched(mock_apply_async, index=1)

        assert mock_apply_async.call_count == 2
        mock_build.assert_called_once()

    def test_replaced_attempt_still_returns_what_it_built(
        self, auth_client, test_execution
    ):
        # The optimiser builds the summary inline and uses what it built.
        from simulate.tasks.eval_summary_tasks import build_eval_summary

        def build_while_refreshed(*args, **kwargs):
            auth_client.post(REFRESH_URL.format(test_execution.id), {}, format="json")
            return {"eval": [{"theme": "inline"}]}

        with (
            patch(APPLY_ASYNC),
            patch(BUILD_SUMMARY, side_effect=build_while_refreshed),
        ):
            built = build_eval_summary(str(test_execution.id))

        assert built == {"eval": [{"theme": "inline"}]}
        test_execution.refresh_from_db()
        assert test_execution.eval_explanation_summary is None
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )
