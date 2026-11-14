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
    resp = MagicMock()
    resp.status_code = status
    resp.text = "server error" if status >= 400 else "ok"
    http.post.return_value = resp
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
        assert _statuses(webhook) == ["cancelled"] * 4 + ["delivered"]
        assert not EVENTS.filter(
            webhook=webhook, status__in=["pending", "failed", "dead_letter"]
        ).exists()
        assert EVENTS.get(pk=delivered.pk).status == "delivered"
        cancelled = EVENTS.filter(webhook=webhook, status="cancelled").first()
        assert cancelled.last_error == "Webhook deleted"
        assert cancelled.next_retry_at is None

        assert deliver_webhook_events() == {"delivered": 0, "failed": 0, "retired": 0}
        assert http.post.call_count == 0

        response = auth_client.post(f"/agentcc/webhook-events/{cancelled.id}/retry/")
        assert response.status_code == 400

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

    def test_an_inactive_webhooks_events_are_dead_lettered(self, organization):
        webhook = _webhook(organization, is_active=False)
        event = _event(webhook)

        deliver_webhook(webhook.pk)

        event.refresh_from_db()
        assert event.status == "dead_letter"
        assert event.next_retry_at is None

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
    @override_settings(AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3)
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_webhook_answering_500_is_disabled_after_n_failures(
        self, mock_session, _mock_url_check, user
    ):
        mock_session.return_value = _session(500)
        webhook = _webhook(user.organization)
        for _ in range(5):
            _event(webhook)

        for _ in range(3):
            deliver_webhook(webhook.pk)
            # The failed event backs off; the next run tries the next one.

        webhook.refresh_from_db()
        assert webhook.is_active is False
        assert "HTTP 500" in webhook.disabled_reason
        assert "3 consecutive" in webhook.disabled_reason
        assert webhook.disabled_at is not None
        assert webhook.consecutive_failures == 3
        assert not EVENTS.filter(webhook=webhook, status="pending").exists()
        assert EVENTS.filter(webhook=webhook, status="dead_letter").count() == 5

    @override_settings(AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3)
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_success_resets_the_streak(self, mock_session, _mock_url_check, user):
        webhook = _webhook(user.organization)
        for _ in range(3):
            _event(webhook)
        mock_session.return_value = _session(500)
        deliver_webhook(webhook.pk)
        deliver_webhook(webhook.pk)
        webhook.refresh_from_db()
        assert webhook.consecutive_failures == 2

        mock_session.return_value = _session(200)
        deliver_webhook(webhook.pk)

        webhook.refresh_from_db()
        assert webhook.consecutive_failures == 0
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
@override_settings(AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3)
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
