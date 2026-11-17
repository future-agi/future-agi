"""HUB-OPS fix round 3: what a deploy shape must wire, and the clocks behind the pages.

The verifier found that every shipped shape left OMEGA_HUB_CHECKER_TOKEN and OMEGA_HUB_MAINTAINER_ORG_ID
unset, so an upload waited in "received" forever and the stall page ended as an unrouted log line;
that the stall age came from created_at, which survives a requeue, so the reap that requeued a job
paged in the same run; and that the SLA clock was a fixed 09:00-17:00 UTC weekday window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.core.checks import registry
from django.utils import timezone

from omega_hub import ops_checks
from omega_hub.models import CheckJob, EmailOutbox, Report, Submission
from omega_hub.services import metrics, notify, ops, support_hours
from omega_hub.tests.helpers import entry_files, metrics_client, submit, token_client

pytestmark = pytest.mark.django_db


def _queued_job(world, *, status=CheckJob.Status.QUEUED, **fields) -> CheckJob:
    submission = Submission.objects.create(
        publisher=world.official, submitted_by=world.maint_a.user, channel=Submission.Channel.WEB,
        raw_sha256="f" * 64, raw_bytes=1, status=Submission.Status.CHECKING,
    )
    return CheckJob.objects.create(submission=submission, status=status, **fields)


def _ids(found) -> list[str]:
    return [alert["id"] for alert in found]


def _check_ids() -> set[str]:
    found = [*ops_checks.operations_unconfigured(), *ops_checks.operations_pages_unrouted(databases=["default"])]
    return {message.id for message in found}


def test_the_deploy_wiring_checks_are_registered():
    registered = registry.registry.get_checks(include_deployment_checks=False)
    assert ops_checks.operations_unconfigured in registered
    assert ops_checks.operations_pages_unrouted in registered


# The stall clock -------------------------------------------------------------------------------------


def test_a_job_the_reaper_just_requeued_does_not_page_in_the_same_run(world, monkeypatch):
    # Live at 08:10 on the rig: reaped=1 and omega_hub_checker_stalled in one run, from created_at
    # 18 minutes earlier, although the job had been RUNNING until the reap.
    now = timezone.now()
    job = _queued_job(world, status=CheckJob.Status.RUNNING, attempt=1, leased_by="dead",
                      claimed_at=now - timedelta(minutes=16), lease_expires_at=now - timedelta(minutes=1))
    CheckJob.objects.filter(pk=job.pk).update(created_at=now - timedelta(minutes=18),
                                              queued_at=now - timedelta(minutes=18))
    paged = []
    monkeypatch.setattr(ops.notify, "ops_alert", lambda alert: paged.append(alert["id"]) or True)
    result = ops.check_and_page(now=now)
    assert result["reaped"] == 1
    assert "omega_hub_checker_stalled" not in _ids(result["alerts"])
    job.refresh_from_db()
    assert job.status == CheckJob.Status.QUEUED and job.queued_at >= now
    # Still unclaimed ten minutes after the requeue: now it pages.
    later = ops.check_and_page(now=job.queued_at + timedelta(minutes=11))
    assert "omega_hub_checker_stalled" in _ids(later["alerts"])
    assert paged == ["omega_hub_checker_stalled"]


def test_queue_age_is_measured_from_the_last_time_the_job_entered_the_queue(world):
    now = timezone.now()
    job = _queued_job(world)
    CheckJob.objects.filter(pk=job.pk).update(created_at=now - timedelta(hours=3), queued_at=now - timedelta(seconds=30))
    age = metrics.snapshot(now=now)["omega_hub_checker_oldest_queued_age_seconds"]
    assert 29 <= age <= 31


# The SLA clock ---------------------------------------------------------------------------------------


def test_the_default_calendar_is_weekdays_nine_to_five_utc():
    monday = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    assert support_hours.business_hours(monday, monday + timedelta(hours=10)) == 8.0
    friday_evening = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
    monday_ten = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    assert support_hours.business_hours(friday_evening, monday_ten) == 2.0  # Fri 16-17 and Mon 9-10


def test_a_holiday_counts_no_hours(settings):
    settings.OMEGA_HUB_SUPPORT_HOLIDAYS = "2026-12-25"
    start = datetime(2026, 12, 24, 16, 0, tzinfo=UTC)  # Thursday; Friday the 25th is a holiday
    assert support_hours.business_hours(start, datetime(2026, 12, 28, 10, 0, tzinfo=UTC)) == 2.0


def test_the_support_day_is_in_the_configured_zone(settings):
    settings.OMEGA_HUB_SUPPORT_TIMEZONE = "Asia/Kolkata"  # 09:00-17:00 IST is 03:30-11:30 UTC
    start = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    assert support_hours.business_hours(start, datetime(2026, 10, 20, 4, 30, tzinfo=UTC)) == 1.0
    assert support_hours.business_hours(start, datetime(2026, 10, 20, 23, 0, tzinfo=UTC)) == 8.0


def test_a_bad_calendar_is_reported_and_the_clock_keeps_running(settings):
    settings.OMEGA_HUB_SUPPORT_HOURS = "17:00-09:00"
    assert support_hours.problems() == ["OMEGA_HUB_SUPPORT_HOURS '17:00-09:00' closes before it opens"]
    assert "omega_hub.W008" in _check_ids()
    monday = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    assert support_hours.business_hours(monday, monday + timedelta(hours=8)) == 8.0


def test_the_security_page_follows_the_configured_calendar(world, settings, monkeypatch):
    settings.OMEGA_HUB_SUPPORT_TIMEZONE = "Asia/Kolkata"
    entry = _published_entry(world)
    report = Report.objects.create(entry=entry, reporter=world.carol.user, reason=Report.Reason.SECURITY, details="x")
    opened = datetime(2026, 10, 19, 3, 30, tzinfo=UTC)  # Monday 09:00 IST
    Report.objects.filter(pk=report.pk).update(created_at=opened)
    monkeypatch.setattr(ops.notify, "ops_alert", lambda alert: True)
    at_seven = [a for a in ops.alerts(now=opened + timedelta(hours=7)) if a["id"] == "omega_hub_security_report_sla"]
    at_eight = [a for a in ops.alerts(now=opened + timedelta(hours=8)) if a["id"] == "omega_hub_security_report_sla"]
    assert at_seven == []
    assert at_eight and at_eight[0]["supportCalendar"] == "09:00-17:00 Asia/Kolkata, Monday to Friday"


def _published_entry(world):
    from omega_hub.tests.flows import publish

    return publish(world)


# Who a page reaches ----------------------------------------------------------------------------------


def test_an_ops_page_reaches_the_on_call_route_when_no_maintainer_is_granted(world, settings):
    from django.contrib.auth.models import Permission

    Permission.objects.filter(codename="hub_maintainer").delete()
    settings.OMEGA_HUB_OPS_ALERT_EMAILS = "hub-oncall@example.test, ops@example.test"
    now = timezone.now()
    job = _queued_job(world)
    CheckJob.objects.filter(pk=job.pk).update(queued_at=now - timedelta(minutes=11))
    result = ops.check_and_page(now=now)
    assert result["paged"] == 1
    row = EmailOutbox.objects.get(template="ops_alert")
    assert row.recipients == ["hub-oncall@example.test", "ops@example.test"]
    assert metrics.snapshot(now=now)["omega_hub_ops_alert_recipients"] == 2


def test_the_on_call_route_is_paged_alongside_the_maintainers(world, settings):
    settings.OMEGA_HUB_OPS_ALERT_EMAILS = "hub-oncall@example.test"
    assert notify.ops_recipients() == sorted({world.maint_a.email, world.maint_b.email, "hub-oncall@example.test"})


# A deploy without a checker --------------------------------------------------------------------------


def test_an_upload_is_refused_when_no_checker_can_ever_claim_it(world, settings):
    settings.OMEGA_HUB_CHECKER_TOKEN = None
    files = entry_files("first-agent")
    response = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.0")
    # A configuration error says what is missing: intake's 503, never the
    # 404 of a hidden part; the operator is also told by the page, the metric and the system check below.
    assert response.status_code == 503, response.content
    error = response.json()["error"]
    assert error["code"] == "OMEGA_HUB_CHECKER_NOT_CONFIGURED"
    assert "Nothing was stored" in error["message"] and "OMEGA_HUB_CHECKER_TOKEN" in error["fix"]
    assert Submission.objects.count() == 0 and CheckJob.objects.count() == 0


def test_a_missing_checker_token_pages_and_shows_on_the_metrics(world, settings):
    settings.ENV_TYPE = "staging"
    settings.OMEGA_HUB_CHECKER_TOKEN = None
    assert "omega_hub_checker_not_configured" in _ids(ops.alerts())
    body = metrics_client().get("/omega-hub/internal/ops/metrics").content.decode()
    assert "omega_hub_checker_configured 0" in body and "omega_hub_ops_alert_recipients 2" in body


def test_the_local_environment_does_not_page_for_a_missing_checker(world, settings):
    settings.ENV_TYPE = "local"
    settings.OMEGA_HUB_CHECKER_TOKEN = None
    assert "omega_hub_checker_not_configured" not in _ids(ops.alerts())


def test_system_checks_name_every_missing_deploy_setting(world, settings):
    settings.ENV_TYPE = "staging"
    settings.OMEGA_HUB_CHECKER_TOKEN = None
    settings.OMEGA_HUB_MAINTAINER_ORG_ID = None
    settings.OMEGA_HUB_METRICS_TOKEN = None
    found = _check_ids()
    # No maintainer org means no maintainer, and no on-call route is set: pages reach nobody.
    assert {"omega_hub.W004", "omega_hub.W005", "omega_hub.W006", "omega_hub.W007"} <= found


def test_a_fully_wired_deploy_raises_none_of_them(world, settings):
    settings.ENV_TYPE = "staging"
    found = _check_ids()
    assert not {"omega_hub.W004", "omega_hub.W005", "omega_hub.W006", "omega_hub.W007", "omega_hub.W008"} & found


def test_the_local_environment_skips_the_deploy_settings_checks(world, settings):
    settings.ENV_TYPE = "local"
    settings.OMEGA_HUB_CHECKER_TOKEN = None
    settings.OMEGA_HUB_MAINTAINER_ORG_ID = None
    found = _check_ids()
    assert not {"omega_hub.W004", "omega_hub.W005", "omega_hub.W006", "omega_hub.W007"} & found
