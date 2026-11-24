import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import HostedHarnessJob
from simulate.services.agent_contracts import contract_bytes
from simulate.services.harness_build_retry import retry_failed_build, retryable
from simulate.services.harness_design_review import (
    APPROVED,
    DISCARD_REASON,
    READING,
    READING_FAILED,
    READING_STAGE,
    READING_TIMED_OUT,
    REVIEW_KEY,
    add_edit,
    approve,
    fail_design_reading,
    fail_stale_reading,
    open_review_after_reading,
    stored_review,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import _seed_contract_for
from simulate.tasks.harness_agent_versions import (
    DESIGN_READING_DEADLINE_SECONDS,
    read_agent_for_design_review,
)
from simulate.tests.test_harness_design_review import (  # noqa: F401
    APPROVED_KEY,
    CONTRACT,
    URL,
    stored_contracts,
    waiting,
)
from simulate.tests.test_harness_design_review_reading import (  # noqa: F401
    SCHEDULE,
    SIDE_ROW,
    STORED,
    _reading,
    _runtime,
    client,
    review_on,
)

START = "simulate.temporal.client.start_hosted_harness_gateway_workflow"
AUTHORING = "simulate.services.harness_usage.require_harness_authoring"
READ = "simulate.services.agent_reading_sandbox.read_version_contract"
ACTIVITY_INFO = "tfc.logging.temporal.context.try_activity_info"
READ_KEY = "agent-contracts/org/agent/version/read.json"
EDITED_KEY = "environment-contracts/org/env/approved.json"
BASE_URL = "https://platform.example.com"

run_reading = read_agent_for_design_review._original_func


@pytest.fixture
def keep_connection():
    with patch("simulate.tasks.harness_agent_versions.close_old_connections"):
        yield


def _failed_reading(user, workspace, capture, *, can_retry):
    job = _reading(user, workspace, _runtime(), capture)
    assert fail_design_reading(
        str(job.id),
        code="agent_unreadable",
        message="Reading the agent failed.",
        retryable=can_retry,
    )
    job.refresh_from_db()
    return job


def _age_reading(job, seconds):
    review = stored_review(job)
    review["created_at"] = (timezone.now() - timedelta(seconds=seconds)).isoformat()
    job.payload["metadata"][REVIEW_KEY] = review
    job.save(update_fields=["payload", "updated_at"])


def _approved_job(**review):
    return SimpleNamespace(
        id=uuid.uuid4(),
        payload={
            "metadata": {
                REVIEW_KEY: {
                    "status": APPROVED,
                    "approved_contract_key": "",
                    "source_contract_key": READ_KEY,
                    **review,
                }
            }
        },
        agent_version_id=uuid.uuid4(),
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("review_on")
class TestRetryAfterAFailedReading:
    def test_a_reading_that_failed_for_good_is_not_tried_again(
        self, user, workspace, django_capture_on_commit_callbacks
    ):
        job = _failed_reading(
            user, workspace, django_capture_on_commit_callbacks, can_retry=False
        )

        with (
            patch(AUTHORING),
            patch(START) as start,
            patch(SCHEDULE) as schedule,
            pytest.raises(HostedHarnessError) as refused,
        ):
            retry_failed_build(job, user=user, base_url=BASE_URL)

        assert (refused.value.code, refused.value.status_code) == (
            "build_retry_not_retryable",
            409,
        )
        start.assert_not_called()
        schedule.assert_not_called()
        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.FAILED
        assert not retryable(job)

    def test_a_reading_that_cannot_start_again_leaves_the_build_failed(
        self, user, workspace, django_capture_on_commit_callbacks
    ):
        job = _failed_reading(
            user, workspace, django_capture_on_commit_callbacks, can_retry=True
        )

        with (
            patch(AUTHORING),
            patch(START) as start,
            patch(SCHEDULE, side_effect=RuntimeError("temporal down")),
            pytest.raises(HostedHarnessError) as refused,
        ):
            retry_failed_build(job, user=user, base_url=BASE_URL)

        assert refused.value.code == "scheduler_unavailable"
        start.assert_not_called()
        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.FAILED
        assert job.failure["code"] == "agent_unreadable"
        assert stored_review(job)["status"] == READING_FAILED

    def test_a_failed_reading_is_read_again_and_never_built_unapproved(
        self, user, workspace, django_capture_on_commit_callbacks
    ):
        job = _failed_reading(
            user, workspace, django_capture_on_commit_callbacks, can_retry=True
        )
        assert retryable(job)

        with patch(AUTHORING), patch(START) as start, patch(SCHEDULE) as schedule:
            retried, point = retry_failed_build(job, user=user, base_url=BASE_URL)

        assert point == "start"
        start.assert_not_called()
        schedule.assert_called_once_with(str(job.id))
        job.refresh_from_db()
        assert (job.state, job.current_stage) == (
            HostedHarnessJob.State.RECEIVED,
            READING_STAGE,
        )
        assert (job.failure, job.terminal_at) == (None, None)
        assert stored_review(job)["status"] == READING
        assert retried.state == HostedHarnessJob.State.RECEIVED

    def test_a_reading_stuck_past_its_deadline_can_be_tried_again(
        self, user, workspace, django_capture_on_commit_callbacks
    ):
        job = _reading(user, workspace, _runtime(), django_capture_on_commit_callbacks)
        _age_reading(job, DESIGN_READING_DEADLINE_SECONDS + 60)

        with patch(AUTHORING), patch(START) as start, patch(SCHEDULE) as schedule:
            retry_failed_build(job, user=user, base_url=BASE_URL)

        start.assert_not_called()
        schedule.assert_called_once_with(str(job.id))
        job.refresh_from_db()
        assert stored_review(job)["status"] == READING


@pytest.mark.django_db
class TestApproveRollback:
    def test_a_build_that_started_before_its_start_failed_keeps_its_design(
        self,
        user,
        waiting,  # noqa: F811
        stored_contracts,  # noqa: F811
    ):
        add_edit(
            waiting, op="remove", kind="tool", item_id="notify", text=None, user=user
        )

        def started_then_lost(*_args, **_kwargs):
            HostedHarnessJob.no_workspace_objects.filter(id=waiting.id).update(
                state=HostedHarnessJob.State.RUNNING
            )
            raise RuntimeError("response lost")

        with (
            patch(START, side_effect=started_then_lost),
            patch("simulate.services.agent_contracts.remove_contract") as removed,
            pytest.raises(HostedHarnessError),
        ):
            approve(waiting, user=user, base_url=BASE_URL)

        removed.assert_not_called()
        waiting.refresh_from_db()
        assert waiting.state == HostedHarnessJob.State.RUNNING
        review = stored_review(waiting)
        assert review["status"] == APPROVED
        assert review["approved_contract_key"] == APPROVED_KEY


class TestApprovedSeed:
    @pytest.fixture(autouse=True)
    def seeding_on(self, settings):
        settings.HARNESS_SEED_CONTRACT_ENABLED = True

    def test_an_edited_design_that_cannot_be_read_fails_the_build(self):
        job = _approved_job(approved_contract_key=EDITED_KEY)

        with (
            patch("simulate.services.agent_contracts.read_contract", return_value=None),
            patch(STORED, return_value=copy.deepcopy(CONTRACT)),
            pytest.raises(HostedHarnessError) as failed,
        ):
            _seed_contract_for(job)

        assert (failed.value.code, failed.value.retryable) == (
            "approved_design_unreadable",
            True,
        )

    def test_an_unchanged_design_whose_reading_is_gone_fails_the_build(self):
        job = _approved_job()

        with (
            patch("simulate.services.agent_contracts.read_contract", return_value=None),
            patch(STORED, return_value=copy.deepcopy(CONTRACT)),
            pytest.raises(HostedHarnessError) as failed,
        ):
            _seed_contract_for(job)

        assert failed.value.code == "approved_design_unreadable"

    def test_an_unchanged_design_seeds_from_the_contract_it_was_read_from(self):
        reviewed = {**copy.deepcopy(CONTRACT), "reading_inputs": {}}
        replaced = {"agent": {"name": "a later build"}}
        job = _approved_job()

        with (
            patch(
                "simulate.services.agent_contracts.read_contract",
                side_effect=lambda key: reviewed if key == READ_KEY else None,
            ),
            patch(STORED, return_value=replaced),
        ):
            body = _seed_contract_for(job)

        assert body == contract_bytes(reviewed)

    def test_an_edited_design_seeds_from_its_stored_edit(self):
        edited = {"agent": {"name": "edited"}}
        job = _approved_job(approved_contract_key=EDITED_KEY)

        with patch(
            "simulate.services.agent_contracts.read_contract",
            side_effect=lambda key: edited if key == EDITED_KEY else None,
        ):
            assert _seed_contract_for(job) == contract_bytes(edited)


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
