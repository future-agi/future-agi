"""Webhook event housekeeping: cancel on delete, expiry, the pending cap,
auto-disable after failures, and the purge command."""

import threading
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from accounts.models import Organization
from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services import webhook_hygiene
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.webhook_delivery import deliver_webhook, deliver_webhook_events

SESSION = "agentcc.services.webhook_delivery.build_ssrf_safe_session"
URL_CHECK = "agentcc.services.webhook_delivery.ensure_public_http_url"
EVENTS = AgentccWebhookEvent.all_objects


def _session(status):
    http = MagicMock()

    def post(*_args, **_kwargs):
        # A streamed response: delivery reads its body through ``raw``.
        resp = MagicMock()
        resp.status_code = status
        resp.text = "server error" if status >= 400 else "ok"
        resp.encoding = "utf-8"
        resp.raw.read.return_value = resp.text.encode()
        return resp

    http.post.side_effect = post
    return http


def _webhook(org, name="hook", **kwargs):
    kwargs.setdefault("url", "https://hooks.example.com/x")
    kwargs.setdefault("events", ["request.completed"])
    return AgentccWebhook.objects.create(organization=org, name=name, **kwargs)


def _event(webhook, status="pending", age=None, **kwargs):
    event = AgentccWebhookEvent.objects.create(
        organization=webhook.organization,
        webhook=webhook,
        event_type="request.completed",
        payload={"event": "request.completed"},
        status=status,
        **kwargs,
    )
    if age is not None:
        EVENTS.filter(pk=event.pk).update(created_at=timezone.now() - age)
    return event


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Other Organization")


def _statuses(webhook):
    return sorted(EVENTS.filter(webhook=webhook).values_list("status", flat=True))


@pytest.mark.integration
@pytest.mark.api
class TestDeletingAWebhook:
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_leaves_no_deliverable_events(
        self, mock_session, _mock_url_check, auth_client, user
    ):
        http = _session(200)
        mock_session.return_value = http
        webhook = _webhook(user.organization)
        for status in ("pending", "pending", "failed", "dead_letter"):
            _event(webhook, status=status)
        delivered = _event(webhook, status="delivered")

        response = auth_client.delete(f"/agentcc/webhooks/{webhook.id}/")

        assert response.status_code == 200
        # The pending events are cancelled at once; the failed and
        # dead-lettered history waits for the maintenance run, so a delete
        # never walks a long history inside the request.
        assert _statuses(webhook) == [
            "cancelled",
            "cancelled",
            "dead_letter",
            "delivered",
            "failed",
        ]
        assert not EVENTS.filter(webhook=webhook, status="pending").exists()
        assert EVENTS.get(pk=delivered.pk).status == "delivered"
        cancelled = EVENTS.filter(webhook=webhook, status="cancelled").first()
        assert cancelled.last_error == "Webhook deleted"
        assert cancelled.next_retry_at is None

        assert deliver_webhook_events() == {"delivered": 0, "failed": 0, "retired": 0}
        assert http.post.call_count == 0

        response = auth_client.post(f"/agentcc/webhook-events/{cancelled.id}/retry/")
        assert response.status_code == 400

        assert webhook_hygiene.cancel_orphaned_events() == 2
        assert _statuses(webhook) == ["cancelled"] * 4 + ["delivered"]

    def test_an_event_ingest_creates_after_the_delete_is_cancelled_not_dead_lettered(
        self, organization
    ):
        webhook = _webhook(organization)
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(deleted=True)
        event = _event(webhook)

        deliver_webhook(webhook.pk)

        event.refresh_from_db()
        assert event.status == "cancelled"
        assert event.next_retry_at is None

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_an_inactive_webhooks_events_stay_pending(
        self, mock_session, _mock_url_check, organization
    ):
        http = _session(200)
        mock_session.return_value = http
        webhook = _webhook(organization, is_active=False)
        event = _event(webhook)

        deliver_webhook(webhook.pk)

        event.refresh_from_db()
        assert event.status == "pending"
        assert event.attempts == 0
        assert http.post.call_count == 0

    def test_events_of_webhooks_deleted_before_this_release_are_cancelled(
        self, organization
    ):
        orphan = _webhook(organization, name="old-deleted")
        _event(orphan)
        _event(orphan, status="dead_letter")
        _event(orphan, status="delivered")
        AgentccWebhook.all_objects.filter(pk=orphan.pk).update(deleted=True)
        live = _webhook(organization, name="live")
        _event(live)

        assert webhook_hygiene.cancel_orphaned_events() == 2

        assert _statuses(orphan) == ["cancelled", "cancelled", "delivered"]
        assert _statuses(live) == ["pending"]


@pytest.mark.integration
class TestExpiry:
    @override_settings(AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS=1)
    def test_old_pending_events_expire_and_are_counted(self, organization):
        webhook = _webhook(organization)
        old = _event(webhook, age=timedelta(hours=2))
        fresh = _event(webhook, age=timedelta(minutes=10))

        assert webhook_hygiene.expire_pending_events() == 1

        old.refresh_from_db()
        assert old.status == "expired"
        assert EVENTS.get(pk=fresh.pk).status == "pending"
        webhook.refresh_from_db()
        assert webhook.dropped_events_total == 1
        assert webhook.last_dropped_at is not None

    @override_settings(AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS=0)
    def test_zero_never_expires(self, organization):
        webhook = _webhook(organization)
        _event(webhook, age=timedelta(days=30))

        assert webhook_hygiene.expire_pending_events() == 0
        assert _statuses(webhook) == ["pending"]


@pytest.mark.integration
class TestPendingCap:
    @override_settings(AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK=5)
    def test_ingest_keeps_the_newest_and_drops_the_oldest(self, organization):
        AgentccAPIKey.objects.create(
            gateway_key_id="cap-key", name="cap-key", organization=organization
        )
        webhook = _webhook(organization)

        for i in range(8):
            ingest_request_logs(
                [
                    {
                        "request_id": f"cap-{i}",
                        "model": "gpt-4o",
                        "status_code": 200,
                        "auth_key_id": "cap-key",
                    }
                ]
            )

        kept = EVENTS.filter(webhook=webhook, status="pending")
        dropped = EVENTS.filter(webhook=webhook, status="dropped")
        assert sorted(kept.values_list("payload__request_id", flat=True)) == [
            f"cap-{i}" for i in range(3, 8)
        ]
        assert sorted(dropped.values_list("payload__request_id", flat=True)) == [
            "cap-0",
            "cap-1",
            "cap-2",
        ]
        assert dropped.first().last_error == "Pending cap of 5 reached"
        webhook.refresh_from_db()
        assert webhook.dropped_events_total == 3

    @override_settings(AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK=0)
    def test_zero_is_no_cap(self, organization):
        webhook = _webhook(organization)
        for _ in range(4):
            _event(webhook)

        assert webhook_hygiene.enforce_pending_cap([webhook.pk]) == {}
        assert _statuses(webhook) == ["pending"] * 4


@pytest.mark.integration
@pytest.mark.api
class TestAutoDisable:
    @override_settings(
        AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3,
        AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=0,
    )
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_webhook_answering_500_is_disabled_after_n_failures(
        self, mock_session, _mock_url_check, user
    ):
        mock_session.return_value = _session(500)
        webhook = _webhook(user.organization)
        for _ in range(5):
            _event(webhook)

        # One run: three failed attempts in a row stop it.
        deliver_webhook(webhook.pk)

        webhook.refresh_from_db()
        assert webhook.is_active is False
        assert "HTTP 500" in webhook.disabled_reason
        assert "3 consecutive" in webhook.disabled_reason
        assert webhook.disabled_at is not None
        assert webhook.consecutive_failures == 3

    @override_settings(
        AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3,
        AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=0,
    )
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_disabling_keeps_its_pending_events_for_when_it_is_enabled(
        self, mock_session, _mock_url_check, auth_client, user
    ):
        mock_session.return_value = _session(500)
        webhook = _webhook(user.organization)
        events = [_event(webhook) for _ in range(5)]

        deliver_webhook(webhook.pk)

        webhook.refresh_from_db()
        assert webhook.is_active is False
        assert _statuses(webhook) == ["pending"] * 5
        assert not EVENTS.filter(webhook=webhook, status="dead_letter").exists()

        # Fixed and turned back on: the next run delivers every one of them.
        http = _session(200)
        mock_session.return_value = http
        response = auth_client.patch(
            f"/agentcc/webhooks/{webhook.id}/", {"is_active": True}, format="json"
        )
        assert response.status_code == 200, response.json()
        EVENTS.filter(webhook=webhook).update(next_retry_at=None)  # backoff over
        deliver_webhook(webhook.pk)

        assert _statuses(webhook) == ["delivered"] * 5
        assert http.post.call_count == len(events)

    @override_settings(
        AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3,
        AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=60,
    )
    def test_failures_must_also_span_the_minimum_time(self, organization):
        webhook = _webhook(organization)

        for _ in range(10):
            assert not webhook_hygiene.record_delivery_result(
                webhook.pk, False, "HTTP 503"
            )
        webhook.refresh_from_db()
        assert webhook.is_active is True
        assert webhook.consecutive_failures == 10
        started = webhook.failing_since
        assert started is not None

        # The streak keeps its start; once it is an hour old the next
        # failure disables the webhook.
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
            failing_since=timezone.now() - timedelta(minutes=61)
        )
        assert webhook_hygiene.record_delivery_result(webhook.pk, False, "HTTP 503")
        webhook.refresh_from_db()
        assert webhook.is_active is False
        assert "over 60+ minutes" in webhook.disabled_reason

    @override_settings(
        AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3,
        AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=60,
    )
    def test_a_long_streak_of_few_failures_does_not_disable(self, organization):
        webhook = _webhook(organization)
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
            consecutive_failures=1,
            failing_since=timezone.now() - timedelta(hours=3),
        )

        assert not webhook_hygiene.record_delivery_result(webhook.pk, False, "x")

        webhook.refresh_from_db()
        assert webhook.is_active is True
        assert webhook.consecutive_failures == 2

    @override_settings(AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3)
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_success_resets_the_streak(self, mock_session, _mock_url_check, user):
        webhook = _webhook(user.organization)
        for _ in range(2):
            _event(webhook)
        mock_session.return_value = _session(500)
        deliver_webhook(webhook.pk)
        webhook.refresh_from_db()
        assert webhook.consecutive_failures == 2
        assert webhook.failing_since is not None

        _event(webhook)
        mock_session.return_value = _session(200)
        deliver_webhook(webhook.pk)

        webhook.refresh_from_db()
        assert webhook.consecutive_failures == 0
        assert webhook.failing_since is None
        assert webhook.is_active is True

    @override_settings(AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3)
    @patch("agentcc.views.webhook_outbound.ensure_public_http_url")
    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    def test_a_failed_test_send_does_not_count(
        self, mock_session, _mock_url_check, auth_client, user
    ):
        mock_session.return_value = _session(500)
        webhook = _webhook(user.organization, consecutive_failures=2)

        response = auth_client.post(f"/agentcc/webhooks/{webhook.id}/test/")

        assert response.status_code == 200
        webhook.refresh_from_db()
        assert webhook.consecutive_failures == 2
        assert webhook.is_active is True

    def test_re_enabling_clears_the_reason_and_the_streak(self, auth_client, user):
        webhook = _webhook(
            user.organization,
            is_active=False,
            consecutive_failures=100,
            disabled_reason="Disabled after 100 consecutive failed deliveries",
            disabled_at=timezone.now(),
            failing_since=timezone.now() - timedelta(hours=2),
            next_delivery_at=timezone.now() + timedelta(minutes=5),
        )

        response = auth_client.patch(
            f"/agentcc/webhooks/{webhook.id}/", {"is_active": True}, format="json"
        )

        assert response.status_code == 200, response.json()
        body = response.json()["result"]
        assert body["is_active"] is True
        assert body["disabled_reason"] == ""
        assert body["consecutive_failures"] == 0
        webhook.refresh_from_db()
        assert (webhook.consecutive_failures, webhook.disabled_reason) == (0, "")
        assert webhook.disabled_at is None
        assert webhook.failing_since is None
        assert webhook.next_delivery_at is None

    def test_an_edit_does_not_write_back_counters_a_run_changed(
        self, auth_client, user
    ):
        from agentcc.views.webhook_outbound import AgentccWebhookViewSet

        webhook = _webhook(user.organization)
        stale = AgentccWebhook.all_objects.get(pk=webhook.pk)
        # A delivery run fails and drops events while the edit is in flight.
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
            consecutive_failures=7,
            dropped_events_total=9,
            failing_since=timezone.now(),
        )

        with patch.object(AgentccWebhookViewSet, "get_object", return_value=stale):
            response = auth_client.patch(
                f"/agentcc/webhooks/{webhook.id}/",
                {"description": "edited"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        webhook.refresh_from_db()
        assert webhook.description == "edited"
        assert webhook.consecutive_failures == 7
        assert webhook.dropped_events_total == 9
        assert webhook.failing_since is not None

    def test_the_health_fields_are_read_only(self, auth_client, user):
        webhook = _webhook(user.organization)

        auth_client.patch(
            f"/agentcc/webhooks/{webhook.id}/",
            {
                "consecutive_failures": 7,
                "disabled_reason": "x",
                "dropped_events_total": 9,
            },
            format="json",
        )

        webhook.refresh_from_db()
        assert webhook.consecutive_failures == 0
        assert webhook.disabled_reason == ""
        assert webhook.dropped_events_total == 0

    def test_retry_refuses_events_of_an_inactive_or_deleted_webhook(
        self, auth_client, user
    ):
        inactive = _webhook(user.organization, name="off", is_active=False)
        off_event = _event(inactive, status="dead_letter")
        response = auth_client.post(f"/agentcc/webhook-events/{off_event.id}/retry/")
        assert response.status_code == 400
        assert "Enable the webhook" in response.content.decode()

        deleted = _webhook(user.organization, name="gone")
        gone_event = _event(deleted, status="failed")
        AgentccWebhook.all_objects.filter(pk=deleted.pk).update(deleted=True)
        response = auth_client.post(f"/agentcc/webhook-events/{gone_event.id}/retry/")
        assert response.status_code == 400
        assert "Webhook was deleted" in response.content.decode()

        for status in ("expired", "dropped"):
            event = _event(
                _webhook(user.organization, name=f"w-{status}"), status=status
            )
            response = auth_client.post(f"/agentcc/webhook-events/{event.id}/retry/")
            assert response.status_code == 400, status


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@override_settings(
    AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3, AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=0
)
def test_two_workers_failing_at_once_disable_the_webhook_once(organization):
    webhook = _webhook(organization, consecutive_failures=1)
    barrier = threading.Barrier(2)
    results = []

    def fail():
        try:
            barrier.wait()
            results.append(
                webhook_hygiene.record_delivery_result(webhook.pk, False, "HTTP 500")
            )
        finally:
            connection.close()

    threads = [threading.Thread(target=fail) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    webhook.refresh_from_db()
    assert sorted(results) == [False, True]
    assert webhook.consecutive_failures == 3
    assert webhook.is_active is False


@pytest.mark.integration
class TestPurgeCommand:
    @pytest.fixture
    def seeded(self, organization):
        gone = _webhook(organization, name="gone")
        rows = []
        statuses = ["pending", "delivered", "failed", "dead_letter", "cancelled"]
        for i in range(1234):
            rows.append(
                AgentccWebhookEvent(
                    organization=organization,
                    webhook=gone,
                    event_type="request.completed",
                    payload={},
                    status=statuses[i % len(statuses)],
                )
            )
        AgentccWebhookEvent.all_objects.bulk_create(rows)
        AgentccWebhook.all_objects.filter(pk=gone.pk).update(deleted=True)

        live = _webhook(organization, name="live")
        old_delivered = _event(live, status="delivered", age=timedelta(days=40))
        recent_delivered = _event(live, status="delivered", age=timedelta(days=5))
        old_pending = _event(live, status="pending", age=timedelta(days=40))
        fresh_pending = _event(live, status="pending")
        return {
            "gone": gone,
            "live": live,
            "old_delivered": old_delivered,
            "recent_delivered": recent_delivered,
            "pending": [old_pending, fresh_pending],
        }

    def _run(self, *args):
        out = StringIO()
        call_command("purge_agentcc_webhook_events", *args, stdout=out)
        return out.getvalue()

    def test_deleted_webhooks(self, seeded):
        output = self._run(
            "--deleted-webhooks", "--batch-size", "500", "--sleep-seconds", "0"
        )

        assert "Deleted 1234 webhook events" in output
        assert "1234 of deleted webhooks" in output
        assert not EVENTS.filter(webhook=seeded["gone"]).exists()
        assert EVENTS.filter(webhook=seeded["live"]).count() == 4

    def test_delivered_older_than(self, seeded):
        output = self._run("--delivered-older-than-days", "30")

        assert "Deleted 1 webhook events: 1 delivered older than 30 days." in output
        assert not EVENTS.filter(pk=seeded["old_delivered"].pk).exists()
        assert EVENTS.filter(pk=seeded["recent_delivered"].pk).exists()
        assert EVENTS.filter(webhook=seeded["gone"]).count() == 1234

    def test_dry_run_deletes_nothing(self, seeded):
        output = self._run(
            "--deleted-webhooks", "--delivered-older-than-days", "30", "--dry-run"
        )

        assert output.startswith("Would delete 1235 webhook events")
        assert EVENTS.filter(webhook=seeded["gone"]).count() == 1234
        assert EVENTS.filter(pk=seeded["old_delivered"].pk).exists()

    def test_live_pending_events_always_survive(self, seeded):
        self._run(
            "--deleted-webhooks",
            "--delivered-older-than-days",
            "0",
            "--terminal-older-than-days",
            "0",
        )

        assert sorted(
            EVENTS.filter(webhook=seeded["live"]).values_list("pk", flat=True)
        ) == sorted(e.pk for e in seeded["pending"])

    def test_terminal_older_than(self, organization):
        live = _webhook(organization, name="terminal")
        for status in ("dead_letter", "failed", "cancelled", "expired", "dropped"):
            _event(live, status=status, age=timedelta(days=40))
        _event(live, status="dead_letter", age=timedelta(days=1))

        output = self._run("--terminal-older-than-days", "30")

        assert "5 terminal older than 30 days" in output
        assert _statuses(live) == ["dead_letter"]

    def test_organization_scopes_the_purge(self, seeded, other_org):
        other = _webhook(other_org, name="other-gone")
        _event(other, status="delivered")
        AgentccWebhook.all_objects.filter(pk=other.pk).update(deleted=True)

        self._run("--deleted-webhooks", "--organization", str(other_org.id))

        assert not EVENTS.filter(webhook=other).exists()
        assert EVENTS.filter(webhook=seeded["gone"]).count() == 1234

    def test_without_a_selector_it_refuses(self):
        with pytest.raises(CommandError):
            call_command("purge_agentcc_webhook_events")


@pytest.mark.integration
@override_settings(
    AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS=1, AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK=2
)
def test_the_maintenance_activity_cancels_expires_and_caps(organization):
    orphan = _webhook(organization, name="orphan")
    _event(orphan)
    AgentccWebhook.all_objects.filter(pk=orphan.pk).update(deleted=True)
    stale = _webhook(organization, name="stale")
    _event(stale, age=timedelta(hours=3))
    busy = _webhook(organization, name="busy")
    for minutes in (4, 3, 2, 1):
        _event(busy, age=timedelta(minutes=minutes))

    result = webhook_hygiene.agentcc_webhook_event_maintenance_activity._original_func()

    assert result == {"cancelled": 1, "expired": 1, "dropped": 2}
    assert _statuses(orphan) == ["cancelled"]
    assert _statuses(stale) == ["expired"]
    assert _statuses(busy) == ["dropped", "dropped", "pending", "pending"]


def test_the_maintenance_schedule_is_registered():
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
    from tfc.temporal.schedules import ALL_SCHEDULES

    (config,) = [
        s for s in ALL_SCHEDULES if s.schedule_id == "agentcc-webhook-event-maintenance"
    ]
    activity = webhook_hygiene.agentcc_webhook_event_maintenance_activity
    assert config.activity_name == activity._activity_name
    assert config.interval_seconds == 600
    assert config.queue == "default"
    assert activity._metadata["time_limit"] == 600
    assert "agentcc.services.webhook_hygiene" in TEMPORAL_ACTIVITY_MODULES


def test_the_purge_command_may_run_as_an_operator_job():
    from model_hub.apps import OPERATOR_STARTUP_MUTATION_COMMANDS

    assert "purge_agentcc_webhook_events" in OPERATOR_STARTUP_MUTATION_COMMANDS


@pytest.mark.integration
def test_purge_rechecks_each_batch_before_deleting(organization):
    """A Retry can make a selected event pending again before its batch is
    deleted; the purge must leave it."""
    webhook = _webhook(organization)
    old = _event(webhook, status="dead_letter", age=timedelta(days=40))
    real = webhook_hygiene._purge_batch_ids

    def select_then_retry(queryset, batch_size):
        ids = real(queryset, batch_size)
        EVENTS.filter(pk=old.pk).update(status="pending")  # the user's Retry
        return ids

    with patch.object(webhook_hygiene, "_purge_batch_ids", select_then_retry):
        counts = webhook_hygiene.purge_webhook_events(
            terminal_older_than_days=30, sleep_seconds=0
        )

    assert counts == {"terminal": 0}
    assert EVENTS.get(pk=old.pk).status == "pending"


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@override_settings(AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK=5)
def test_two_callers_capping_one_webhook_drop_only_the_excess(organization):
    webhook = _webhook(organization)
    for i in range(10):
        _event(webhook, age=timedelta(minutes=60 - i))
    barrier = threading.Barrier(2)
    results = []

    def cap():
        try:
            barrier.wait()
            results.append(webhook_hygiene.enforce_pending_cap([webhook.pk]))
        finally:
            connection.close()

    threads = [threading.Thread(target=cap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert EVENTS.filter(webhook=webhook, status="pending").count() == 5
    assert EVENTS.filter(webhook=webhook, status="dropped").count() == 5
    webhook.refresh_from_db()
    assert webhook.dropped_events_total == 5
    assert sum(r.get(webhook.pk, 0) for r in results) == 5


@pytest.mark.integration
class TestEventsBeingSent:
    """The cap and the age expiry leave an event a run is sending to the run."""

    @override_settings(AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK=1)
    def test_the_cap_drops_the_oldest_event_not_in_flight(self, organization):
        from agentcc.services.webhook_delivery import _claim

        webhook = _webhook(organization)
        sending = _event(webhook, age=timedelta(minutes=10))
        newer = _event(webhook, age=timedelta(minutes=5))
        assert _claim(sending.pk, timezone.now())

        dropped = webhook_hygiene.enforce_pending_cap([webhook.pk])

        assert dropped == {webhook.pk: 1}
        assert EVENTS.get(pk=sending.pk).status == "pending"
        assert EVENTS.get(pk=newer.pk).status == "dropped"

    @override_settings(AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS=1)
    def test_expiry_waits_for_the_attempt_to_be_recorded(self, organization):
        from agentcc.services.webhook_delivery import _claim, _record_attempt

        webhook = _webhook(organization)
        event = _event(webhook)
        # Claimed while still within its age, then the age passes mid-send.
        assert _claim(event.pk, timezone.now())
        EVENTS.filter(pk=event.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )

        assert webhook_hygiene.expire_pending_events() == 0
        assert EVENTS.get(pk=event.pk).status == "pending"

        _record_attempt(EVENTS.get(pk=event.pk), 500, "HTTP 500")
        assert webhook_hygiene.expire_pending_events() == 1
        assert EVENTS.get(pk=event.pk).status == "expired"

    def test_a_backing_off_event_is_not_in_flight(self, organization):
        from agentcc.services.webhook_delivery import _claim, _record_attempt

        webhook = _webhook(organization)
        event = _event(webhook)
        assert _claim(event.pk, timezone.now())
        _record_attempt(EVENTS.get(pk=event.pk), 503, "HTTP 503")

        in_flight = EVENTS.filter(webhook_hygiene.in_flight(timezone.now()))
        assert not in_flight.filter(pk=event.pk).exists()
        assert EVENTS.get(pk=event.pk).next_retry_at > timezone.now()


@pytest.mark.integration
@pytest.mark.api
class TestWebhookHeaders:
    @pytest.mark.parametrize("headers", ["abc", [1], {"X-Bad": {"a": 1}}])
    @patch("agentcc.serializers.webhook.ensure_public_http_url")
    def test_headers_must_be_an_object_of_text_values(
        self, _mock_url_check, auth_client, user, headers
    ):
        response = auth_client.post(
            "/agentcc/webhooks/",
            {
                "name": "with-headers",
                "url": "https://hooks.example.com/x",
                "events": ["request.completed"],
                "headers": headers,
            },
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["details"].keys() == {"headers"}

    @patch("agentcc.serializers.webhook.ensure_public_http_url")
    def test_a_number_value_is_stored_as_text(self, _mock_url_check, auth_client, user):
        response = auth_client.post(
            "/agentcc/webhooks/",
            {
                "name": "numeric-header",
                "url": "https://hooks.example.com/x",
                "events": ["request.completed"],
                "headers": {"X-Tenant": 42},
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert AgentccWebhook.objects.get(name="numeric-header").headers == {
            "X-Tenant": "42"
        }

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_stored_headers_that_are_not_an_object_do_not_break_delivery(
        self, mock_session, _mock_url_check, organization
    ):
        http = _session(200)
        mock_session.return_value = http
        webhook = _webhook(organization)
        # Saved before the serializer checked them.
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(headers=["x"])
        event = _event(webhook)

        result = deliver_webhook(webhook.pk)

        assert result["delivered"] == 1
        assert EVENTS.get(pk=event.pk).status == "delivered"
