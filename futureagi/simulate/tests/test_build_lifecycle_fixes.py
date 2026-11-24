import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import HostedHarnessJob
from simulate.services.harness_design_review import (
    DISCARD_REASON,
    READING,
    READING_FAILED,
    READING_TIMED_OUT,
    REVIEW_KEY,
    fail_stale_reading,
    open_review_after_reading,
    stored_review,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tasks.harness_agent_versions import (
    DESIGN_READING_DEADLINE_SECONDS,
    read_agent_for_design_review,
)
from simulate.tests.test_harness_design_review import (  # noqa: F401
    CONTRACT,
    URL,
)
from simulate.tests.test_harness_design_review_reading import (  # noqa: F401
    SIDE_ROW,
    STORED,
    _reading,
    _runtime,
    client,
    review_on,
)

READ = "simulate.services.agent_reading_sandbox.read_version_contract"
ACTIVITY_INFO = "tfc.logging.temporal.context.try_activity_info"


run_reading = read_agent_for_design_review._original_func


@pytest.fixture
def keep_connection():
    with patch("simulate.tasks.harness_agent_versions.close_old_connections"):
        yield


def _age_reading(job, seconds):
    review = stored_review(job)
    review["created_at"] = (timezone.now() - timedelta(seconds=seconds)).isoformat()
    job.payload["metadata"][REVIEW_KEY] = review
    job.save(update_fields=["payload", "updated_at"])


@pytest.mark.django_db
@pytest.mark.usefixtures("review_on")
class TestReadingThatDies:
    def test_the_last_attempt_keeps_the_readings_message_and_retry_rule(
        self,
        user,
        workspace,
        keep_connection,
        django_capture_on_commit_callbacks,
    ):
        runtime = _runtime()
        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)
        refusal = HostedHarnessError(
            "repository_unreachable", "The repository is gone.", retryable=False
        )

        with (
            patch(READ, side_effect=refusal),
            patch(ACTIVITY_INFO, return_value=None),
            patch(SIDE_ROW, return_value=runtime),
            patch(STORED, return_value=None),
            patch("simulate.tasks.harness_agent_versions.logger") as logger,
        ):
            outcome = run_reading(str(job.id))

        assert outcome == "failed"
        job.refresh_from_db()
        assert job.failure["code"] == "repository_unreachable"
        assert job.failure["message"] == "The repository is gone."
        assert job.failure["retryable"] is False
        assert logger.warning.call_args.kwargs["exc_info"] is True

    def test_a_reading_that_cannot_be_retried_is_closed_on_its_first_attempt(
        self,
        user,
        workspace,
        keep_connection,
        django_capture_on_commit_callbacks,
    ):
        runtime = _runtime()
        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)
        refusal = HostedHarnessError(
            "repository_unreachable", "The repository is gone.", retryable=False
        )

        with (
            patch(READ, side_effect=refusal),
            patch(
                ACTIVITY_INFO,
                return_value=SimpleNamespace(attempt=1, workflow_run_id="run-1"),
            ),
            patch(SIDE_ROW, return_value=runtime),
            patch(STORED, return_value=None),
        ):
            outcome = run_reading(str(job.id))

        assert outcome == "failed"
        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.FAILED

    def test_a_crash_with_retries_left_is_retried(
        self,
        user,
        workspace,
        keep_connection,
        django_capture_on_commit_callbacks,
    ):
        job = _reading(user, workspace, _runtime(), django_capture_on_commit_callbacks)

        with (
            patch(READ, side_effect=RuntimeError("sandbox died")),
            patch(
                ACTIVITY_INFO,
                return_value=SimpleNamespace(attempt=1, workflow_run_id="run-1"),
            ),
            pytest.raises(RuntimeError),
        ):
            run_reading(str(job.id))

        job.refresh_from_db()
        assert stored_review(job)["status"] == READING

    def test_a_reading_past_its_deadline_fails_on_the_next_read(
        self,
        client,  # noqa: F811
        user,
        workspace,
        django_capture_on_commit_callbacks,
    ):
        job = _reading(user, workspace, _runtime(), django_capture_on_commit_callbacks)
        _age_reading(job, DESIGN_READING_DEADLINE_SECONDS + 60)

        response = client.get(URL.format(job.id), HTTP_X_WORKSPACE_ID=str(workspace.id))

        assert response.status_code == 404
        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.FAILED
        assert job.failure["code"] == READING_TIMED_OUT
        assert job.failure["retryable"] is True
        assert stored_review(job)["status"] == READING_FAILED

    def test_a_reading_still_in_time_keeps_reading(
        self, user, workspace, django_capture_on_commit_callbacks
    ):
        job = _reading(user, workspace, _runtime(), django_capture_on_commit_callbacks)
        _age_reading(job, DESIGN_READING_DEADLINE_SECONDS - 60)

        assert not fail_stale_reading(job)
        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.RECEIVED

    def test_a_build_can_be_discarded_while_its_agent_is_read(
        self,
        client,  # noqa: F811
        user,
        workspace,
        django_capture_on_commit_callbacks,
    ):
        runtime = _runtime()
        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)

        response = client.post(
            URL.format(job.id) + "discard/",
            {},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

        assert response.status_code == 200, response.content
        job.refresh_from_db()
        assert (job.state, job.deleted, job.cancel_reason) == (
            HostedHarnessJob.State.CANCELED,
            True,
            DISCARD_REASON,
        )
        with (
            patch(SIDE_ROW, return_value=runtime),
            patch(
                STORED, return_value={**copy.deepcopy(CONTRACT), "reading_inputs": {}}
            ),
        ):
            assert open_review_after_reading(str(job.id)) == "closed"
