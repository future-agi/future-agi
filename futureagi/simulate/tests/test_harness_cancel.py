from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from django.utils import timezone

from simulate.models import (
    CallExecution,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessReceipt,
)
from simulate.services import harness_cancel
from simulate.services.harness_cancel import (
    Confirmation,
    ProviderCalls,
    ProviderCallsActive,
    cancel_status,
    confirm_cancel,
    provider_calls,
    provider_connector,
    record_confirmation,
    record_overdue_cleanup_failure,
    run_call_ids,
    stop_summaries,
    stop_summary,
    treated_as_sandbox,
)
from simulate.services.harness_provider import serialize_job
from simulate.services.phone_telephony import HostedPhoneRoomCleanupError
from simulate.tasks.hosted_harness_cancel import confirm_hosted_harness_provider_cancel
from simulate.tests.test_cancelled_call_journey import (  # noqa: F401 - fixture
    running_run as running_run,
)
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import _certify, _environment, _stopped
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]

_VAPI = {"connector": "vapi", "config": {"assistant_id": "asst-1"}}
_RETELL = {"connector": "retell", "config": {"agent_id": "agent-1"}}
_LIVEKIT = {"connector": "livekit", "config": {"agent_name": "front-desk"}}
_PHONE = {"connector": "phone", "config": {"phone_number": "+15550100"}}
_VAPI_PHONE = {
    "connector": "vapi",
    "mode": "connect_only",
    "config": {"assistant_id": "asst-1", "phone_number": "+15550100"},
}
_LIVEKIT_SECRETS = {
    "LIVEKIT_URL": "wss://customer.livekit.example",
    "LIVEKIT_API_KEY": "lk-key",
    "LIVEKIT_API_SECRET": "lk-secret",
}


def _canceled(user, workspace, storage, key, *, agent=None, source=None):
    environment = _environment(user, workspace, storage, key)
    _certify(environment, storage)
    job = _stopped(environment, key="stop")
    payload = dict(job.payload)
    if agent is not None:
        payload["agent"] = {**(payload.get("agent") or {}), **agent}
    if source is not None:
        payload["source"] = {"kind": source}
    else:
        payload.pop("source", None)
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        payload=payload, state=HostedHarnessJob.State.CANCELED
    )
    job.refresh_from_db()
    return job


def _response(body):
    return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)


def _job(agent, job_id="12345678-aaaa-bbbb-cccc-1234567890ab"):
    return SimpleNamespace(id=job_id, payload={"agent": agent})


class TestConnector:
    @pytest.mark.parametrize(
        ("agent", "expected"),
        [
            (_VAPI, "vapi"),
            (_RETELL, "retell"),
            (_LIVEKIT, "livekit"),
            (_PHONE, None),
            (_VAPI_PHONE, None),
            ({"connector": "http"}, None),
            ({}, None),
        ],
    )
    def test_only_a_provider_web_call_or_a_livekit_agent_is_checked_with_its_provider(
        self, agent, expected
    ):
        assert provider_connector({"agent": agent}) == expected

    @pytest.mark.parametrize(
        ("connector", "expected"),
        [("livekit", True), (" LiveKit ", True), ("vapi", False), (None, False)],
    )
    def test_only_a_livekit_agent_is_treated_as_a_sandbox_agent(
        self, connector, expected
    ):
        assert treated_as_sandbox(connector) is expected


class TestRunCallIds:
    def _receive(self, run, attempt, execution, call_id):
        CallExecution.no_workspace_objects.filter(
            id=execution.call_execution_id
        ).update(customer_call_id=call_id)
        HostedHarnessReceipt.no_workspace_objects.create(
            job=run,
            attempt=attempt,
            scenario=execution.source_scenario,
            execution=execution,
            attempt_number=attempt.attempt_number,
            digest="sha256:" + "0" * 64,
            status="completed",
            body={},
        )

    def test_every_call_received_with_its_provider_id_gives_the_ids(self, running_run):
        run, attempt = running_run
        executions = HostedHarnessExecution.no_workspace_objects.filter(job=run)
        for number, execution in enumerate(executions):
            self._receive(run, attempt, execution, f"call-{number}")

        assert sorted(run_call_ids(run)) == ["call-0", "call-1"]

    def test_a_call_still_without_a_receipt_falls_back_to_the_time_window(
        self, running_run
    ):
        run, attempt = running_run
        first, _ = HostedHarnessExecution.no_workspace_objects.filter(job=run)
        self._receive(run, attempt, first, "call-0")

        assert run_call_ids(run) is None

    def test_a_received_call_without_a_provider_id_falls_back(self, running_run):
        run, attempt = running_run
        for execution in HostedHarnessExecution.no_workspace_objects.filter(job=run):
            self._receive(run, attempt, execution, None)

        assert run_call_ids(run) is None

    def test_a_stale_id_from_an_earlier_attempt_without_a_receipt_falls_back(
        self, running_run
    ):
        run, _ = running_run
        CallExecution.no_workspace_objects.filter(
            hosted_harness_execution__job=run
        ).update(customer_call_id="old-call")

        assert run_call_ids(run) is None


class TestConfirmCancel:
    @pytest.mark.parametrize("source", ["github", "archive"])
    def test_a_sandboxed_agent_is_confirmed_by_its_sandbox_being_gone(
        self, user, workspace, storage, source
    ):
        job = _canceled(user, workspace, storage, f"cx-{source}", source=source)

        confirm_cancel(job)

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.SANDBOX_ABSENT
        assert job.cancel_confirmed_at is not None
        assert cancel_status(job) == "confirmed"

    def test_a_phone_agent_is_confirmed_by_its_rooms_being_gone(
        self, user, workspace, storage
    ):
        job = _canceled(user, workspace, storage, "cx-phone", agent=_PHONE)

        confirm_cancel(job)

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.PROVIDER_CALL_ENDED

    @pytest.mark.parametrize("agent", [_VAPI, _RETELL, _LIVEKIT])
    def test_a_provider_agent_schedules_a_check_after_the_stop_commits(
        self, user, workspace, storage, django_capture_on_commit_callbacks, agent
    ):
        job = _canceled(
            user, workspace, storage, f"cx-{agent['connector']}", agent=agent
        )

        with (
            patch(
                "simulate.tasks.hosted_harness_cancel.schedule_provider_call_check"
            ) as schedule,
            django_capture_on_commit_callbacks(execute=True),
        ):
            confirm_cancel(job)

        schedule.assert_called_once_with(str(job.id))
        job.refresh_from_db()
        assert job.cancel_confirmed_at is None
        assert cancel_status(job) == "requested"

    def test_an_agent_at_its_own_url_is_never_confirmed(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        job = _canceled(
            user, workspace, storage, "cx-remote", agent={"connector": "http"}
        )

        with (
            patch(
                "simulate.tasks.hosted_harness_cancel.schedule_provider_call_check"
            ) as schedule,
            django_capture_on_commit_callbacks(execute=True),
        ):
            confirm_cancel(job)

        schedule.assert_not_called()
        job.refresh_from_db()
        assert cancel_status(job) == "requested"

    @pytest.mark.parametrize(
        "state", [HostedHarnessJob.State.COMPLETED, HostedHarnessJob.State.FAILED]
    )
    def test_a_stop_on_a_run_that_then_ended_on_its_own_is_confirmed(
        self, user, workspace, storage, state
    ):
        job = _canceled(user, workspace, storage, f"cx-ended-{state}", source="github")
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(state=state)
        job.refresh_from_db()

        confirm_cancel(job)

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.SANDBOX_ABSENT

    def test_a_new_stop_starts_unconfirmed(self, user, workspace, storage):
        from simulate.services.hosted_harness import request_cancellation

        job = _canceled(user, workspace, storage, "cx-new-stop", source="github")
        record_confirmation(job.id, Confirmation.SANDBOX_ABSENT)
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state=HostedHarnessJob.State.RUNNING
        )
        job.refresh_from_db()

        request_cancellation(job, "user_canceled")

        job.refresh_from_db()
        assert (job.cancel_confirmed_at, job.cancel_confirmed_by) == (None, "")
        assert cancel_status(job) == "requested"

    def test_a_job_not_yet_canceled_is_left_alone(self, user, workspace, storage):
        job = _canceled(user, workspace, storage, "cx-running", source="github")
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            state=HostedHarnessJob.State.RUNNING
        )
        job.refresh_from_db()

        confirm_cancel(job)

        job.refresh_from_db()
        assert job.cancel_confirmed_at is None


class TestVapiCalls:
    @pytest.fixture
    def job(self, user, workspace, storage):
        return _canceled(user, workspace, storage, "cx-vapi-calls", agent=_VAPI)

    def _read(self, job, calls, secrets=None):
        with (
            patch.object(
                harness_cancel,
                "_job_secrets",
                return_value={"VAPI_API_KEY": "vapi-key"}
                if secrets is None
                else secrets,
            ),
            patch.object(
                harness_cancel.requests, "get", return_value=_response(calls)
            ) as get,
        ):
            return provider_calls(job), get

    def test_a_web_call_still_live_reads_active(self, job):
        outcome, get = self._read(
            job,
            [
                {"type": "vapi.websocketCall", "status": "ended"},
                {"type": "vapi.websocketCall", "status": "in-progress"},
            ],
        )

        assert outcome is ProviderCalls.ACTIVE
        params = get.call_args.kwargs["params"]
        assert params["assistantId"] == "asst-1"
        assert params["createdAtGe"]

    def test_every_web_call_ended_reads_ended(self, job):
        outcome, _ = self._read(
            job,
            [
                {"type": "vapi.websocketCall", "status": "ended"},
                {"type": "inboundPhoneCall", "status": "in-progress"},
            ],
        )

        assert outcome is ProviderCalls.ENDED

    @pytest.mark.parametrize(
        ("calls", "outcome"),
        [
            ([], ProviderCalls.NO_CALLS),
            ([{"type": "inboundPhoneCall", "status": "ended"}], ProviderCalls.NO_CALLS),
            (
                [{"type": "vapi.websocketCall", "status": "ended"}] * 100,
                ProviderCalls.TOO_MANY_CALLS,
            ),
            ({"error": "unexpected"}, ProviderCalls.UNKNOWN),
        ],
    )
    def test_no_web_call_or_a_full_page_proves_nothing_and_says_why(
        self, job, calls, outcome
    ):
        read, _ = self._read(job, calls)

        assert read is outcome

    def test_no_key_proves_nothing_calls_nobody_and_says_so(self, job):
        outcome, get = self._read(job, [], secrets={})

        assert outcome is ProviderCalls.NO_KEY
        get.assert_not_called()

    def test_no_assistant_proves_nothing_and_says_so(self, user, workspace, storage):
        job = _canceled(
            user, workspace, storage, "cx-vapi-none", agent={"connector": "vapi"}
        )

        outcome, get = self._read(job, [])

        assert outcome is ProviderCalls.NO_TARGET
        get.assert_not_called()

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_proves_nothing_and_says_so(self, job, status):
        rejected = requests.HTTPError(response=SimpleNamespace(status_code=status))
        with (
            patch.object(
                harness_cancel, "_job_secrets", return_value={"VAPI_API_KEY": "k"}
            ),
            patch.object(harness_cancel.requests, "get", side_effect=rejected),
        ):
            assert provider_calls(job) is ProviderCalls.KEY_REJECTED

    def test_vapi_unreachable_reads_active_so_the_check_retries(self, job):
        with (
            patch.object(
                harness_cancel, "_job_secrets", return_value={"VAPI_API_KEY": "k"}
            ),
            patch.object(
                harness_cancel.requests,
                "get",
                side_effect=requests.ConnectionError("down"),
            ),
        ):
            assert provider_calls(job) is ProviderCalls.ACTIVE

    @pytest.mark.parametrize(
        ("statuses", "outcome"),
        [
            (["ended", "ended"], ProviderCalls.ENDED),
            (["ended", "in-progress"], ProviderCalls.ACTIVE),
        ],
    )
    def test_known_call_ids_are_read_one_by_one_not_by_time_window(
        self, job, statuses, outcome
    ):
        calls = iter(_response({"status": status}) for status in statuses)
        with (
            patch.object(harness_cancel, "run_call_ids", return_value=["c1", "c2"]),
            patch.object(
                harness_cancel, "_job_secrets", return_value={"VAPI_API_KEY": "k"}
            ),
            patch.object(
                harness_cancel.requests, "get", side_effect=lambda *a, **k: next(calls)
            ) as get,
        ):
            assert provider_calls(job) is outcome

        urls = [call.args[0] for call in get.call_args_list]
        assert urls == ["https://api.vapi.ai/call/c1", "https://api.vapi.ai/call/c2"]
        assert all("params" not in call.kwargs for call in get.call_args_list)


class TestRetellCalls:
    @pytest.fixture
    def job(self, user, workspace, storage):
        return _canceled(user, workspace, storage, "cx-retell-calls", agent=_RETELL)

    def _read(self, job, body):
        with (
            patch.object(
                harness_cancel, "_job_secrets", return_value={"RETELL_API_KEY": "k"}
            ),
            patch.object(
                harness_cancel.requests, "post", return_value=_response(body)
            ) as post,
        ):
            return provider_calls(job), post

    @pytest.mark.parametrize("status", ["registered", "ongoing"])
    def test_a_call_not_ended_reads_active(self, job, status):
        outcome, post = self._read(
            job, {"items": [{"call_status": "ended"}, {"call_status": status}]}
        )

        assert outcome is ProviderCalls.ACTIVE
        criteria = post.call_args.kwargs["json"]["filter_criteria"]
        assert criteria["agent"] == [{"agent_id": "agent-1"}]

    def test_every_call_ended_reads_ended(self, job):
        outcome, _ = self._read(
            job,
            {"items": [{"call_status": "ended"}, {"call_status": "not_connected"}]},
        )

        assert outcome is ProviderCalls.ENDED

    @pytest.mark.parametrize(
        ("body", "outcome"),
        [
            ({"items": []}, ProviderCalls.NO_CALLS),
            (
                {"items": [{"call_status": "ended"}], "has_more": True},
                ProviderCalls.TOO_MANY_CALLS,
            ),
            ([], ProviderCalls.UNKNOWN),
        ],
    )
    def test_no_call_or_more_than_a_page_proves_nothing_and_says_why(
        self, job, body, outcome
    ):
        read, _ = self._read(job, body)

        assert read is outcome

    @pytest.mark.parametrize(
        ("status", "outcome"),
        [("ended", ProviderCalls.ENDED), ("ongoing", ProviderCalls.ACTIVE)],
    )
    def test_known_call_ids_are_read_one_by_one_not_by_time_window(
        self, job, status, outcome
    ):
        with (
            patch.object(harness_cancel, "run_call_ids", return_value=["call-9"]),
            patch.object(
                harness_cancel, "_job_secrets", return_value={"RETELL_API_KEY": "k"}
            ),
            patch.object(
                harness_cancel.requests,
                "get",
                return_value=_response({"call_status": status}),
            ) as get,
            patch.object(harness_cancel.requests, "post") as post,
        ):
            assert provider_calls(job) is outcome

        assert get.call_args.args[0] == "https://api.retellai.com/v2/get-call/call-9"
        post.assert_not_called()


class TestLiveKitRooms:
    def _read(self, secrets, cleanup=None):
        with (
            patch.object(harness_cancel, "_job_secrets", return_value=secrets),
            patch(
                "simulate.services.phone_telephony.cleanup_livekit_rooms",
                side_effect=cleanup,
            ) as cleaned,
        ):
            return provider_calls(_job(_LIVEKIT)), cleaned

    def test_our_rooms_on_the_customers_project_deleted_and_gone_reads_ended(self):
        outcome, cleaned = self._read(_LIVEKIT_SECRETS)

        assert outcome is ProviderCalls.ENDED
        cleaned.assert_called_once_with(
            room_prefixes=("harness-12345678-",),
            url="wss://customer.livekit.example",
            api_key="lk-key",
            api_secret="lk-secret",
        )

    @pytest.mark.parametrize(
        "error",
        [
            HostedPhoneRoomCleanupError("rooms still active"),
            requests.ConnectionError("down"),
        ],
    )
    def test_a_room_still_there_or_livekit_unreachable_reads_active(self, error):
        outcome, _ = self._read(_LIVEKIT_SECRETS, cleanup=error)

        assert outcome is ProviderCalls.ACTIVE

    def test_a_rejected_livekit_key_proves_nothing_and_says_so(self):
        rejected = requests.HTTPError(response=SimpleNamespace(status_code=401))

        outcome, _ = self._read(_LIVEKIT_SECRETS, cleanup=rejected)

        assert outcome is ProviderCalls.KEY_REJECTED

    @pytest.mark.parametrize(
        "missing", ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    )
    def test_a_missing_livekit_credential_proves_nothing(self, missing):
        secrets = {k: v for k, v in _LIVEKIT_SECRETS.items() if k != missing}

        outcome, cleaned = self._read(secrets)

        assert outcome is ProviderCalls.NO_KEY
        cleaned.assert_not_called()

    def test_the_url_configured_on_the_agent_is_used_without_a_url_secret(self):
        secrets = {k: v for k, v in _LIVEKIT_SECRETS.items() if k != "LIVEKIT_URL"}
        agent = {**_LIVEKIT, "config": {"livekit_url": "wss://configured.example"}}

        with (
            patch.object(harness_cancel, "_job_secrets", return_value=secrets),
            patch("simulate.services.phone_telephony.cleanup_livekit_rooms") as cleaned,
        ):
            assert provider_calls(_job(agent)) is ProviderCalls.ENDED

        assert cleaned.call_args.kwargs["url"] == "wss://configured.example"


class TestProviderCheckTask:
    @pytest.fixture
    def job(self, user, workspace, storage):
        return _canceled(user, workspace, storage, "cx-task", agent=_LIVEKIT)

    def _run(self, job, outcome):
        with patch(
            "simulate.services.harness_cancel.provider_calls", return_value=outcome
        ):
            return confirm_hosted_harness_provider_cancel._original_func(str(job.id))

    def test_a_call_still_live_retries_within_five_minutes(self, job):
        with pytest.raises(ProviderCallsActive):
            self._run(job, ProviderCalls.ACTIVE)

        job.refresh_from_db()
        assert job.cancel_confirmed_at is None
        assert cancel_status(job) == "requested"

    def test_a_call_still_open_after_five_minutes_gives_up_as_not_confirmed(self, job):
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            cancel_requested_at=timezone.now() - timedelta(minutes=5, seconds=1)
        )

        assert self._run(job, ProviderCalls.ACTIVE)["confirmed"] is False

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.PROVIDER_CALL_OPEN
        assert job.cancel_confirmed_at is not None
        assert cancel_status(job) == "unconfirmed"

    def test_an_ended_call_confirms_the_stop(self, job):
        assert self._run(job, ProviderCalls.ENDED)["confirmed"] is True

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.PROVIDER_CALL_ENDED

    @pytest.mark.parametrize(
        ("outcome", "recorded", "reason"),
        [
            (ProviderCalls.UNKNOWN, Confirmation.PROVIDER_UNCHECKED, None),
            (ProviderCalls.NO_KEY, Confirmation.UNCHECKED_NO_KEY, "no_key"),
            (
                ProviderCalls.KEY_REJECTED,
                Confirmation.UNCHECKED_KEY_REJECTED,
                "key_rejected",
            ),
            (ProviderCalls.NO_TARGET, Confirmation.UNCHECKED_NO_TARGET, "no_target"),
            (ProviderCalls.NO_CALLS, Confirmation.UNCHECKED_NO_CALLS, "no_calls"),
            (
                ProviderCalls.TOO_MANY_CALLS,
                Confirmation.UNCHECKED_TOO_MANY_CALLS,
                "too_many_calls",
            ),
        ],
    )
    def test_calls_that_cannot_be_checked_give_up_as_not_confirmed_and_say_why(
        self, job, outcome, recorded, reason
    ):
        assert self._run(job, outcome)["confirmed"] is False

        job.refresh_from_db()
        assert job.cancel_confirmed_by == recorded
        assert cancel_status(job) == "unconfirmed"
        summary = stop_summary(job)
        assert (summary["confirmed_by"], summary["reason"]) == (
            "provider_unchecked",
            reason,
        )

    def test_a_stop_cleared_by_a_resume_is_not_checked_or_confirmed(self, job):
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            cancel_requested_at=None, state=HostedHarnessJob.State.RUNNING
        )

        with patch("simulate.services.harness_cancel.provider_calls") as read:
            result = confirm_hosted_harness_provider_cancel._original_func(str(job.id))

        assert result["confirmed"] is False
        read.assert_not_called()
        job.refresh_from_db()
        assert (job.cancel_confirmed_at, job.cancel_confirmed_by) == (None, "")

    def test_an_already_confirmed_stop_asks_the_provider_nothing(self, job):
        record_confirmation(job.id, Confirmation.SANDBOX_ABSENT)

        with patch("simulate.services.harness_cancel.provider_calls") as read:
            result = confirm_hosted_harness_provider_cancel._original_func(str(job.id))

        assert result["confirmed"] is True
        read.assert_not_called()


class TestCleanupFailure:
    @pytest.fixture
    def job(self, user, workspace, storage):
        return _canceled(user, workspace, storage, "cx-overdue", source="github")

    def _requested(self, job, minutes_ago):
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            cancel_requested_at=timezone.now() - timedelta(minutes=minutes_ago)
        )

    def _attempt_id(self, job):
        return job.attempts.get(attempt_number=job.current_attempt_number).id

    def test_cleanup_unverified_under_ten_minutes_records_nothing(self, job):
        self._requested(job, 9)

        assert record_overdue_cleanup_failure(self._attempt_id(job)) is False

        job.refresh_from_db()
        assert cancel_status(job) == "requested"

    def test_cleanup_unverified_over_ten_minutes_records_a_failure_once(self, job):
        self._requested(job, 11)

        assert record_overdue_cleanup_failure(self._attempt_id(job)) is True
        assert record_overdue_cleanup_failure(self._attempt_id(job)) is False

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.CLEANUP_FAILED
        assert cancel_status(job) == "cleanup_failed"

    def test_a_later_confirmation_replaces_the_failure(self, job):
        self._requested(job, 11)
        record_overdue_cleanup_failure(self._attempt_id(job))

        assert record_confirmation(job.id, Confirmation.SANDBOX_ABSENT) is True

        job.refresh_from_db()
        assert cancel_status(job) == "confirmed"

    def test_a_failure_replaces_a_provider_that_gave_up_but_not_the_reverse(self, job):
        record_confirmation(job.id, Confirmation.PROVIDER_CALL_OPEN)
        self._requested(job, 11)

        assert record_overdue_cleanup_failure(self._attempt_id(job)) is True
        assert record_confirmation(job.id, Confirmation.PROVIDER_UNCHECKED) is False

        job.refresh_from_db()
        assert cancel_status(job) == "cleanup_failed"

    def test_a_later_confirmation_replaces_a_provider_that_gave_up(self, job):
        record_confirmation(job.id, Confirmation.PROVIDER_CALL_OPEN)

        assert record_confirmation(job.id, Confirmation.PROVIDER_CALL_ENDED) is True

        job.refresh_from_db()
        assert cancel_status(job) == "confirmed"

    def test_a_failure_never_replaces_a_confirmation(self, job):
        record_confirmation(job.id, Confirmation.SANDBOX_ABSENT)
        self._requested(job, 11)

        assert record_overdue_cleanup_failure(self._attempt_id(job)) is False

        job.refresh_from_db()
        assert job.cancel_confirmed_by == Confirmation.SANDBOX_ABSENT


class TestReadApi:
    def test_the_job_status_reports_the_stop_and_its_confirmation(
        self, user, workspace, storage
    ):
        job = _canceled(user, workspace, storage, "cx-read", source="github")
        confirm_cancel(job)
        job.refresh_from_db()

        status = serialize_job(job)["status"]

        assert status["cancel_requested_at"] == job.cancel_requested_at.isoformat()
        assert status["cancel_confirmed_at"] == job.cancel_confirmed_at.isoformat()
        assert status["cancel_confirmed_by"] == "sandbox_absent"
        assert status["cancel_status"] == "confirmed"

    def test_a_job_never_stopped_reports_no_cancel_status(
        self, user, workspace, storage
    ):
        job = _canceled(user, workspace, storage, "cx-read-none", source="github")
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            cancel_requested_at=None
        )
        job.refresh_from_db()

        status = serialize_job(job)["status"]

        assert status["cancel_status"] is None
        assert status["cancel_confirmed_by"] is None

    def test_runs_read_their_stop_by_run(self, user, workspace, storage):
        job = _canceled(user, workspace, storage, "cx-read-runs", source="github")
        run = str(job.test_execution_id)

        assert stop_summaries([job.test_execution_id])[run]["status"] == "requested"
        confirm_cancel(job)
        job.refresh_from_db()

        assert stop_summaries([job.test_execution_id]) == {
            run: {
                "status": "confirmed",
                "kind": "sandbox",
                "provider": None,
                "requested_at": job.cancel_requested_at.isoformat(),
                "confirmed_at": job.cancel_confirmed_at.isoformat(),
                "confirmed_by": "sandbox_absent",
                "reason": None,
                "check_window_minutes": 5,
                "cleanup_grace_minutes": 10,
            }
        }
        assert stop_summaries([]) == {}

    @pytest.mark.parametrize(
        ("agent", "source", "kind", "provider"),
        [
            (_VAPI, None, "provider", "Vapi"),
            (_RETELL, None, "provider", "Retell"),
            (_LIVEKIT, None, "sandbox", "LiveKit"),
            (_VAPI_PHONE, None, "provider", "Vapi"),
            ({"connector": "http"}, "remote", "endpoint", None),
            (None, "github", "sandbox", None),
        ],
    )
    def test_a_stop_says_how_it_can_be_confirmed_and_by_whom(
        self, user, workspace, storage, agent, source, kind, provider
    ):
        job = _canceled(
            user, workspace, storage, f"cx-kind-{kind}", agent=agent, source=source
        )

        summary = stop_summary(job)

        assert (summary["kind"], summary["provider"], summary["status"]) == (
            kind,
            provider,
            "requested",
        )

    def test_a_run_never_stopped_has_no_stop(self, user, workspace, storage):
        job = _canceled(user, workspace, storage, "cx-kind-none", source="github")
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            cancel_requested_at=None
        )
        job.refresh_from_db()

        assert stop_summary(job) is None
        assert stop_summaries([job.test_execution_id]) == {}
