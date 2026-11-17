"""Whether a Hub checker is running: the Hub records when each checker last called,
and a received or checking submission says the checks are not running, and marks its
checks as stopped, while none is alive. A request that lists many submissions asks
whether a checker is alive at most once."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from omega_hub.api.serializers.submissions import STATUS_TEXT
from omega_hub.models import CheckJob, Submission
from omega_hub.services import checks, metrics
from omega_hub.tests.helpers import (
    CHECKER_TOKEN,
    build_report,
    checker_client,
    entry_files,
    metrics_client,
    post_result,
    run_checks,
    submit,
    token_client,
)

pytestmark = pytest.mark.django_db

CLAIM = "/omega-hub/internal/checker/claim"
METRICS = "/omega-hub/internal/ops/metrics"
GAUGE = "omega_hub_checker_seconds_since_seen"
RECEIVED_NO_CHECKER = "Received. The checks are not running right now. This upload is checked when they start."
CHECKING_NO_CHECKER = "The checks stopped because no Hub checker is running. They start again when one is running."


def upload(world, name="first-agent"):
    files = entry_files(name, manifest={"version": "1.0.0"})
    response = submit(
        token_client(world.token_a), "future-agi", files, name=name, version="1.0.0"
    )
    assert response.status_code == 201, response.content
    return response.json()["id"], files


def running_job(world, *, lease_ends) -> CheckJob:
    """A check job a checker holds until `lease_ends`, with no checker call recorded."""
    submission = Submission.objects.create(
        publisher=world.official,
        submitted_by=world.maint_a.user,
        channel=Submission.Channel.WEB,
        raw_sha256="c" * 64,
        raw_bytes=1,
        status=Submission.Status.CHECKING,
    )
    return CheckJob.objects.create(
        submission=submission,
        status=CheckJob.Status.RUNNING,
        attempt=1,
        leased_by="busy-checker",
        claimed_at=lease_ends - checks.LEASE,
        lease_expires_at=lease_ends,
    )


def seen(checker_id: str, at) -> None:
    from omega_hub.models import CheckerPresence

    CheckerPresence.objects.create(checker_id=checker_id, last_seen_at=at)


def last_called(seconds_ago: int) -> None:
    """Every checker the Hub knows of last called `seconds_ago` seconds ago."""
    from omega_hub.models import CheckerPresence

    at = timezone.now() - timedelta(seconds=seconds_ago)
    CheckerPresence.objects.update(last_seen_at=at)


def presence(checker_id: str = "test-checker"):
    """When the Hub last recorded a call from `checker_id`."""
    from omega_hub.models import CheckerPresence

    return CheckerPresence.objects.get(checker_id=checker_id).last_seen_at


def seen_ids() -> set[str]:
    from omega_hub.models import CheckerPresence

    return set(CheckerPresence.objects.values_list("checker_id", flat=True))


def job_call(checker, job, files, call):
    if call == "heartbeat":
        return checker.post(job["heartbeatUrl"])
    if call == "context":
        context = {"name": "first-agent", "version": "1.0.0"}
        return checker.post(job["contextUrl"], context, format="json")
    report, bundle = build_report(job, files)
    return post_result(checker, job, report, bundle)


def status_texts(world, submission_id) -> set[tuple]:
    """The sentence, and whether the answer says the checks stopped, on its own page,
    its My submissions row and the Review page."""
    own = world.a.get(f"/omega-hub/v1/submissions/{submission_id}")
    mine = world.a.get("/omega-hub/v1/submissions")
    review = world.b.get(f"/omega-hub/v1/review/submissions/{submission_id}")
    for response in (own, mine, review):
        assert response.status_code == 200, response.content
    row = next(item for item in mine.json() if item["id"] == str(submission_id))
    answers = (own.json(), row, review.json())
    return {(answer["statusText"], answer.get("checksStopped")) for answer in answers}


def submissions(world, count: int, status: str) -> set[str]:
    """`count` submissions of `status`, made directly so the upload limit does not
    apply."""
    made = set()
    for _ in range(count):
        row = Submission.objects.create(
            publisher=world.official,
            submitted_by=world.maint_a.user,
            channel=Submission.Channel.WEB,
            raw_sha256="d" * 64,
            raw_bytes=1,
            status=status,
        )
        made.add(str(row.pk))
    return made


def count_alive_calls(monkeypatch) -> list[dict]:
    """Records each time anything asks whether a checker is alive."""
    from omega_hub.services import checker_presence

    calls = []
    asked = checker_presence.alive

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return asked(*args, **kwargs)

    monkeypatch.setattr(checker_presence, "alive", counted)
    return calls


def my_submissions(world, **params) -> list[dict]:
    response = world.a.get("/omega-hub/v1/submissions", params)
    assert response.status_code == 200, response.content
    return response.json()


def readings(rows) -> set[tuple]:
    """Each listed row's status, its sentence and whether it says its checks stopped."""
    return {(row["status"], row["statusText"], row["checksStopped"]) for row in rows}


def test_an_idle_claim_records_the_checker_as_seen(world):
    before = timezone.now()
    assert checker_client().post(CLAIM).status_code == 204
    assert seen_ids() == {"test-checker"}
    assert presence() >= before
    # One row per checker id.
    other = APIClient()
    other.credentials(
        HTTP_X_OMEGA_HUB_CHECKER_TOKEN=CHECKER_TOKEN,
        HTTP_X_OMEGA_HUB_CHECKER_ID="second-checker",
    )
    assert other.post(CLAIM).status_code == 204
    assert seen_ids() == {"test-checker", "second-checker"}


@pytest.mark.parametrize("call", ["heartbeat", "context", "result"])
def test_a_job_call_records_the_checker_as_seen(world, call):
    sub_id, files = upload(world)
    checker = checker_client()
    job = checker.post(CLAIM).json()
    assert job["submission"] == sub_id
    last_called(10 * 60)
    before = timezone.now()
    response = job_call(checker, job, files, call)
    assert response.status_code == 200, response.content
    assert presence() >= before


def test_a_second_call_within_thirty_seconds_does_not_write_again(world):
    checker = checker_client()
    assert checker.post(CLAIM).status_code == 204
    last_called(20)
    recorded = presence()
    assert checker.post(CLAIM).status_code == 204
    assert presence() == recorded
    last_called(31)
    before = timezone.now()
    assert checker.post(CLAIM).status_code == 204
    assert presence() >= before


def test_a_checker_never_seen_is_not_alive(world):
    from omega_hub.services import checker_presence

    assert checker_presence.alive() is False


def test_a_checker_seen_sixty_seconds_ago_is_alive(world):
    from omega_hub.services import checker_presence

    now = timezone.now()
    seen("idle-checker", now - timedelta(seconds=60))
    assert checker_presence.alive(now=now) is True


def test_a_checker_seen_two_minutes_ago_without_a_running_lease_is_not_alive(world):
    from omega_hub.services import checker_presence

    now = timezone.now()
    seen("idle-checker", now - timedelta(seconds=120))
    assert checker_presence.alive(now=now) is False


def test_a_running_job_under_a_live_lease_is_alive_without_a_recent_call(world):
    from omega_hub.services import checker_presence

    now = timezone.now()
    # A busy checker calls only every 5 minutes, inside its 15-minute lease.
    seen("busy-checker", now - timedelta(minutes=4))
    running_job(world, lease_ends=now + timedelta(minutes=11))
    assert checker_presence.alive(now=now) is True


def test_a_running_job_whose_lease_has_ended_is_not_alive(world):
    from omega_hub.services import checker_presence

    now = timezone.now()
    seen("busy-checker", now - timedelta(minutes=16))
    running_job(world, lease_ends=now - timedelta(seconds=1))
    assert checker_presence.alive(now=now) is False


def test_a_received_upload_reads_whether_a_checker_is_alive(world):
    first, files = upload(world)
    second, _ = upload(world, name="notes-plugin")
    assert status_texts(world, second) == {(RECEIVED_NO_CHECKER, True)}
    # A checker claims, checks and reports the first upload; then no job is running.
    run_checks(files)
    assert Submission.objects.get(pk=first).status == Submission.Status.IN_REVIEW
    assert not CheckJob.objects.filter(status=CheckJob.Status.RUNNING).exists()
    assert status_texts(world, second) == {(STATUS_TEXT["received"], False)}
    # Two minutes without a call: no checker is alive again.
    last_called(120)
    assert status_texts(world, second) == {(RECEIVED_NO_CHECKER, True)}
    # Only a received or checking submission says its checks stopped.
    done = world.a.get(f"/omega-hub/v1/submissions/{first}").json()
    assert done["statusText"] == STATUS_TEXT["in_review"]
    assert done["checksStopped"] is False


def test_a_checking_upload_says_the_checks_stopped_while_no_checker_is_alive(world):
    job = running_job(world, lease_ends=timezone.now() - timedelta(minutes=1))
    submission = job.submission
    # Its checker stopped: the lease has ended and no checker has called.
    assert status_texts(world, submission.pk) == {(CHECKING_NO_CHECKER, True)}
    # The reaper requeues the job; the submission stays checking and nothing runs it.
    assert checks.reap_expired_leases() == 1
    job.refresh_from_db()
    submission.refresh_from_db()
    assert job.status == CheckJob.Status.QUEUED
    assert submission.status == Submission.Status.CHECKING
    assert status_texts(world, submission.pk) == {(CHECKING_NO_CHECKER, True)}
    # A checker comes back and claims it.
    claimed = checker_client().post(CLAIM)
    assert claimed.status_code == 200
    assert claimed.json()["job"] == str(job.pk)
    assert status_texts(world, submission.pk) == {(STATUS_TEXT["checking"], False)}


def test_a_checking_upload_under_a_live_lease_keeps_its_sentence(world):
    job = running_job(world, lease_ends=timezone.now() + timedelta(minutes=10))
    expected = {(STATUS_TEXT["checking"], False)}
    assert status_texts(world, job.submission_id) == expected


def test_an_upload_is_accepted_while_no_checker_is_alive(world):
    sub_id, _ = upload(world)
    assert Submission.objects.get(pk=sub_id).status == Submission.Status.RECEIVED
    assert CheckJob.objects.get(submission_id=sub_id).status == CheckJob.Status.QUEUED
    assert status_texts(world, sub_id) == {(RECEIVED_NO_CHECKER, True)}
    # A checker that stopped an hour ago changes nothing: the next upload is accepted.
    seen("stopped-checker", timezone.now() - timedelta(hours=1))
    upload(world, name="notes-plugin")


def test_the_metric_reports_seconds_since_any_checker_was_seen(world):
    now = timezone.now()
    values = metrics.snapshot(now=now)
    assert GAUGE in values
    # Never seen reads 0, as the seconds since the last claim do before any claim.
    assert values[GAUGE] == 0
    seen("first-checker", now - timedelta(minutes=5))
    seen("second-checker", now - timedelta(minutes=2))
    assert metrics.snapshot(now=now)[GAUGE] == 120
    assert checker_client().post(CLAIM).status_code == 204
    scraped = metrics_client().get(METRICS).content.decode().splitlines()
    gauge = [line for line in scraped if line.startswith(GAUGE + " ")]
    assert len(gauge) == 1
    assert float(gauge[0].split()[1]) < 30


def test_my_submissions_asks_once_whether_a_checker_is_alive(world, monkeypatch):
    calls = count_alive_calls(monkeypatch)
    waiting = submissions(world, 30, Submission.Status.RECEIVED)
    waiting |= submissions(world, 3, Submission.Status.CHECKING)
    rows = my_submissions(world)
    # One question for the whole list, not one per received or checking row.
    assert len(calls) == 1
    assert {row["id"] for row in rows} == waiting
    assert readings(rows) == {
        ("received", RECEIVED_NO_CHECKER, True),
        ("checking", CHECKING_NO_CHECKER, True),
    }
    # A checker called a moment ago: the next request asks once more, and every row
    # reads its usual sentence.
    seen("idle-checker", timezone.now())
    rows = my_submissions(world)
    assert len(calls) == 2
    assert readings(rows) == {
        ("received", STATUS_TEXT["received"], False),
        ("checking", STATUS_TEXT["checking"], False),
    }


def test_a_list_without_a_received_or_checking_row_does_not_ask(world, monkeypatch):
    calls = count_alive_calls(monkeypatch)
    done = submissions(world, 5, Submission.Status.IN_REVIEW)
    rows = my_submissions(world)
    assert {row["id"] for row in rows} == done
    assert readings(rows) == {("in_review", STATUS_TEXT["in_review"], False)}
    # Received rows that the status filter leaves out are not asked about either.
    submissions(world, 3, Submission.Status.RECEIVED)
    rows = my_submissions(world, status="in_review")
    assert {row["id"] for row in rows} == done
    assert calls == []
