"""Gateway webhook delivery runs on a schedule, per webhook, never inside log ingest."""

import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.db import OperationalError
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services import webhook_delivery
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.webhook_delivery import (
    deliver_webhook,
    deliver_webhook_events,
    dispatch_webhook_deliveries_activity,
)

SESSION = "agentcc.services.webhook_delivery.build_ssrf_safe_session"
URL_CHECK = "agentcc.services.webhook_delivery.ensure_public_http_url"


def _response(status):
    """A streamed response: delivery reads its body through ``raw``."""
    import io

    resp = MagicMock()
    resp.status_code = status
    resp.text = "server error" if status >= 400 else "ok"
    resp.encoding = "utf-8"
    resp.raw = io.BytesIO(resp.text.encode())
    return resp


def _session(status_by_url, sleep_by_url=None):
    """A fake SSRF-safe session answering by URL, recording each POST."""
    http = MagicMock()
    sleep_by_url = sleep_by_url or {}

    def post(url, data=None, headers=None, timeout=None, stream=False):
        if url in sleep_by_url:
            time.sleep(sleep_by_url[url])
        return _response(status_by_url[url])

    http.post.side_effect = post
    return http


def _webhook(org, name, url, events=("request.completed",), **kwargs):
    return AgentccWebhook.objects.create(
        organization=org, name=name, url=url, events=list(events), **kwargs
    )


def _events(webhook, count, start_minutes_ago=60, **kwargs):
    """Pending events, the first the oldest, one second apart."""
    now = timezone.now()
    events = []
    for i in range(count):
        event = AgentccWebhookEvent.objects.create(
            organization=webhook.organization,
            webhook=webhook,
            event_type="request.completed",
            payload={"event": "request.completed", "n": i},
            **kwargs,
        )
        AgentccWebhookEvent.all_objects.filter(pk=event.pk).update(
            created_at=now - timedelta(minutes=start_minutes_ago) + timedelta(seconds=i)
        )
        events.append(event)
    return events


def _posted_payload_numbers(http):
    import json

    return [json.loads(call.kwargs["data"])["n"] for call in http.post.call_args_list]


def _api_key(org, key_id="ingest-key"):
    return AgentccAPIKey.objects.create(
        gateway_key_id=key_id, name=key_id, organization=org
    )


@pytest.mark.integration
class TestIngestDoesNotDeliver:
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_ingest_with_a_slow_failing_webhook_and_a_backlog_returns_at_once(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/slow-500"
        http = _session({url: 500}, sleep_by_url={url: 2})
        mock_session.return_value = http
        webhook = _webhook(organization, "slow", url)
        _events(webhook, 100)
        _api_key(organization)

        started = time.monotonic()
        count = ingest_request_logs(
            [
                {
                    "request_id": f"fast-{i}",
                    "model": "gpt-4o",
                    "status_code": 200,
                    "auth_key_id": "ingest-key",
                }
                for i in range(5)
            ]
        )
        elapsed = time.monotonic() - started

        assert count == 5
        assert elapsed < 0.5, f"ingest took {elapsed:.2f}s"
        assert http.post.call_count == 0
        assert mock_session.call_count == 0
        new = AgentccWebhookEvent.no_workspace_objects.filter(
            webhook=webhook, payload__request_id__startswith="fast-"
        )
        assert new.count() == 5
        assert set(new.values_list("status", flat=True)) == {"pending"}
        assert (
            AgentccWebhookEvent.no_workspace_objects.filter(
                webhook=webhook, attempts__gt=0
            ).count()
            == 0
        )

    @patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "test-ingest-secret")
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_logs_endpoint_returns_at_once_with_a_failing_webhook(
        self, mock_session, _mock_url_check, api_client, organization
    ):
        url = "https://hooks.example.com/slow-500"
        http = _session({url: 500}, sleep_by_url={url: 2})
        mock_session.return_value = http
        webhook = _webhook(organization, "slow", url)
        _events(webhook, 100)
        _api_key(organization)
        # The first request through the client pays one-time app start-up.
        api_client.post(
            "/agentcc/webhook/logs/",
            {"logs": []},
            format="json",
            HTTP_X_WEBHOOK_SECRET="test-ingest-secret",
        )

        started = time.monotonic()
        response = api_client.post(
            "/agentcc/webhook/logs/",
            {
                "logs": [
                    {
                        "request_id": "endpoint-1",
                        "model": "gpt-4o",
                        "status_code": 500,
                        "auth_key_id": "ingest-key",
                    }
                ]
            },
            format="json",
            HTTP_X_WEBHOOK_SECRET="test-ingest-secret",
        )
        elapsed = time.monotonic() - started

        assert response.status_code == 200, response.content
        assert response.json()["result"] == {"ingested": 1}
        assert elapsed < 0.5, f"ingest took {elapsed:.2f}s"
        assert http.post.call_count == 0


@pytest.mark.integration
class TestDeliveryRun:
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_failing_webhook_delays_only_its_own_events(
        self, mock_session, _mock_url_check, organization
    ):
        bad_url, good_url = "https://a.example.com/500", "https://b.example.com/ok"
        http = _session({bad_url: 500, good_url: 200}, sleep_by_url={bad_url: 0.2})
        mock_session.return_value = http
        bad = _webhook(organization, "a-failing", bad_url)
        good = _webhook(organization, "b-healthy", good_url)
        bad_events = _events(bad, 4)
        good_events = _events(good, 6, start_minutes_ago=90)

        result = deliver_webhook_events()

        # Three failed attempts in a row stop the failing webhook's run.
        assert result == {"delivered": 6, "failed": 3, "retired": 0}
        good_posts = [c for c in http.post.call_args_list if c.args[0] == good_url]
        bad_posts = [c for c in http.post.call_args_list if c.args[0] == bad_url]
        assert len(bad_posts) == 3
        assert len(good_posts) == 6
        # Oldest first.
        assert [c.kwargs["data"] for c in good_posts] == [
            c.kwargs["data"] for c in sorted(good_posts, key=lambda c: c.kwargs["data"])
        ]
        assert set(
            AgentccWebhookEvent.all_objects.filter(webhook=good).values_list(
                "status", flat=True
            )
        ) == {"delivered"}

        tried = AgentccWebhookEvent.all_objects.filter(
            pk__in=[e.pk for e in bad_events[:3]]
        )
        assert set(tried.values_list("status", flat=True)) == {"pending"}
        assert set(tried.values_list("attempts", flat=True)) == {1}
        assert all(
            t > timezone.now() for t in tried.values_list("next_retry_at", flat=True)
        )
        last = AgentccWebhookEvent.all_objects.get(pk=bad_events[3].pk)
        assert (last.status, last.attempts) == ("pending", 0)
        # Its next run waits; the healthy webhook is not paused.
        bad.refresh_from_db()
        good.refresh_from_db()
        assert bad.next_delivery_at > timezone.now() + timedelta(seconds=30)
        assert good.next_delivery_at is None
        assert {e.pk for e in good_events}  # created

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_events_go_out_oldest_first(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "ordered", url)
        _events(webhook, 5)

        deliver_webhook(webhook.pk)

        assert _posted_payload_numbers(http) == [0, 1, 2, 3, 4]

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_run_drains_more_than_one_page(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "backlog", url)
        _events(webhook, 130)

        result = deliver_webhook(webhook.pk)

        assert result["delivered"] == 130
        assert http.post.call_count == 130
        assert mock_session.call_count == 1  # one session for the run
        assert not AgentccWebhookEvent.all_objects.filter(
            webhook=webhook, status="pending"
        ).exists()

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_run_stops_starting_posts_when_its_budget_is_spent(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/slowish"
        http = _session({url: 200}, sleep_by_url={url: 0.2})
        mock_session.return_value = http
        webhook = _webhook(organization, "budget", url)
        _events(webhook, 10)

        result = deliver_webhook(webhook.pk, time_budget_seconds=0.5)

        assert 1 <= result["delivered"] < 10
        assert (
            AgentccWebhookEvent.all_objects.filter(
                webhook=webhook, status="pending", attempts=0
            ).count()
            == 10 - result["delivered"]
        )

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_back_to_back_runs_send_each_event_once(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "once", url)
        _events(webhook, 7)

        deliver_webhook(webhook.pk)
        deliver_webhook(webhook.pk)

        assert sorted(_posted_payload_numbers(http)) == list(range(7))

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_an_event_another_run_holds_is_not_sent_again(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "leased", url)
        held, free = _events(webhook, 2)
        # Another run claimed this one a moment ago and is still sending it.
        assert webhook_delivery._claim(held.pk, timezone.now())

        deliver_webhook(webhook.pk)

        assert _posted_payload_numbers(http) == [1]
        held.refresh_from_db()
        assert held.status == "pending" and held.attempts == 0
        free.refresh_from_db()
        assert free.status == "delivered"

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_crashed_runs_event_is_sent_once_its_lease_ends(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "crashed", url)
        (event,) = _events(webhook, 1)
        long_ago = timezone.now() - timedelta(
            seconds=webhook_delivery.DELIVERY_LEASE_SECONDS + 1
        )
        assert webhook_delivery._claim(event.pk, long_ago)

        deliver_webhook(webhook.pk)

        event.refresh_from_db()
        assert event.status == "delivered"
        assert http.post.call_count == 1

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_post_uses_connect_and_read_timeouts_and_signs(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "signed", url, secret="s3cret")
        _events(webhook, 1)

        deliver_webhook(webhook.pk)

        call = http.post.call_args
        assert call.kwargs["timeout"] == (3, 10)
        assert call.kwargs["headers"]["X-Agentcc-Signature"].startswith("sha256=")
        http.close.assert_called_once()

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_the_backoff_still_doubles_and_dead_letters(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/500"
        mock_session.return_value = _session({url: 500})
        webhook = _webhook(organization, "backoff", url)
        (event,) = _events(webhook, 1, max_attempts=2)

        before = timezone.now()
        deliver_webhook(webhook.pk)
        event.refresh_from_db()
        assert event.attempts == 1
        assert (
            timedelta(seconds=29) < event.next_retry_at - before < timedelta(seconds=31)
        )

        AgentccWebhookEvent.all_objects.filter(pk=event.pk).update(
            next_retry_at=timezone.now()
        )
        deliver_webhook(webhook.pk)
        event.refresh_from_db()
        assert event.status == "dead_letter"
        assert event.attempts == 2

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_manual_retry_is_delivered_by_the_next_run(
        self, mock_session, _mock_url_check, auth_client, user
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(user.organization, "retried", url)
        (event,) = _events(webhook, 1, status="dead_letter", attempts=5)

        response = auth_client.post(f"/agentcc/webhook-events/{event.id}/retry/")
        assert response.status_code == 200, response.content
        deliver_webhook_events()

        event.refresh_from_db()
        assert event.status == "delivered"
        assert http.post.call_count == 1


@pytest.mark.integration
class TestDispatcher:
    def test_one_single_flight_run_per_webhook_with_due_events(self, organization):
        from temporalio.common import WorkflowIDConflictPolicy

        due_a = _webhook(organization, "due-a", "https://a.example.com/x")
        due_b = _webhook(organization, "due-b", "https://b.example.com/x")
        later = _webhook(organization, "later", "https://c.example.com/x")
        _events(due_a, 3)
        _events(due_b, 1)
        _events(later, 2, next_retry_at=timezone.now() + timedelta(minutes=5))

        from django.test import override_settings

        with (
            override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0),
            patch.object(
                webhook_delivery.deliver_webhook_activity, "apply_async"
            ) as start,
        ):
            result = dispatch_webhook_deliveries_activity._original_func()

        assert result == {"webhooks": 2, "started": 2, "running": 0, "waiting": 0}
        calls = {c.kwargs["args"][0]: c.kwargs for c in start.call_args_list}
        assert set(calls) == {str(due_a.pk), str(due_b.pk)}
        for webhook_id, kwargs in calls.items():
            assert kwargs["task_id"].startswith(
                f"agentcc-webhook-delivery-{webhook_id}-"
            )
            assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING

    def test_one_webhook_failing_to_start_does_not_stop_the_others(self, organization):
        first = _webhook(organization, "first", "https://a.example.com/x")
        second = _webhook(organization, "second", "https://b.example.com/x")
        _events(first, 1)
        _events(second, 1)

        from django.test import override_settings

        with (
            override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0),
            patch.object(
                webhook_delivery.deliver_webhook_activity,
                "apply_async",
                side_effect=[RuntimeError("temporal down"), None],
            ),
        ):
            result = dispatch_webhook_deliveries_activity._original_func()

        assert result["started"] == 1
        # The one that failed to start does not keep a lease.
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.delivery_lease_until is None
        assert second.delivery_lease_until > timezone.now()


def test_delivery_schedules_are_registered():
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tfc.temporal.schedules.config import ScheduleOverlapPolicy

    by_id = {s.schedule_id: s for s in ALL_SCHEDULES}
    delivery = by_id["agentcc-webhook-delivery"]
    assert delivery.activity_name == dispatch_webhook_deliveries_activity._activity_name
    assert delivery.interval_seconds == 10
    assert delivery.queue == "default"
    assert delivery.overlap_policy == ScheduleOverlapPolicy.SKIP
    assert "agentcc.services.webhook_delivery" in TEMPORAL_ACTIVITY_MODULES
    for activity in (
        dispatch_webhook_deliveries_activity,
        webhook_delivery.deliver_webhook_activity,
    ):
        assert activity._metadata["max_retries"] == 0
        assert activity._metadata["queue"] == "default"
        assert activity._metadata["time_limit"] <= 120


def test_the_unscheduled_celery_delivery_task_is_gone():
    import agentcc.tasks

    assert not hasattr(agentcc.tasks, "process_webhook_events")


@pytest.mark.integration
class TestLogsEndpointErrors:
    @patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "test-ingest-secret")
    @patch(
        "agentcc.views.webhook.ingest_request_logs",
        side_effect=OperationalError("database went away"),
    )
    def test_a_database_outage_asks_the_gateway_to_retry(
        self, _mock_ingest, api_client
    ):
        response = api_client.post(
            "/agentcc/webhook/logs/",
            {"logs": [{"request_id": "r1", "model": "gpt-4o"}]},
            format="json",
            HTTP_X_WEBHOOK_SECRET="test-ingest-secret",
        )
        assert response.status_code == 503
        assert "database went away" not in response.content.decode()

    @patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "test-ingest-secret")
    def test_a_bad_secret_is_still_a_400(self, api_client):
        response = api_client.post(
            "/agentcc/webhook/logs/",
            {"logs": [{"request_id": "r1", "model": "gpt-4o"}]},
            format="json",
            HTTP_X_WEBHOOK_SECRET="wrong",
        )
        assert response.status_code == 400


@pytest.mark.integration
class TestLateResults:
    """A POST that returns after its event was cancelled or dropped must not
    undo that."""

    @pytest.mark.parametrize("meanwhile", ["delete", "cap"])
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_late_success_keeps_the_terminal_status(
        self, mock_session, _mock_url_check, meanwhile, auth_client, user
    ):
        from django.test import override_settings

        from agentcc.services.webhook_hygiene import enforce_pending_cap

        url = "https://hooks.example.com/ok"
        webhook = _webhook(user.organization, f"late-{meanwhile}", url)
        (event,) = _events(webhook, 1)
        (newer,) = _events(webhook, 1, start_minutes_ago=1)

        def post(*args, **kwargs):
            # While this POST is in flight:
            if meanwhile == "delete":
                assert (
                    auth_client.delete(f"/agentcc/webhooks/{webhook.id}/").status_code
                    == 200
                )
            else:
                with override_settings(AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK=1):
                    enforce_pending_cap([webhook.pk])
            return _response(200)

        http = MagicMock()
        http.post.side_effect = post
        mock_session.return_value = http

        deliver_webhook(webhook.pk, event_ids=[event.pk])

        event.refresh_from_db()
        if meanwhile == "delete":
            assert event.status == "cancelled"
        else:
            # The cap does not drop an event being sent: it drops the oldest
            # one not in flight, and the one sent is recorded delivered.
            assert event.status == "delivered"
            newer.refresh_from_db()
            assert newer.status == "dropped"

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_each_post_names_its_event(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(
            organization, "named", url, headers={"X-Agentcc-Event-Id": "spoofed"}
        )
        (event,) = _events(webhook, 1)

        deliver_webhook(webhook.pk)

        assert http.post.call_args.kwargs["headers"]["X-Agentcc-Event-Id"] == str(
            event.pk
        )


def _flaky_session(url, fail_every):
    """Every ``fail_every``-th POST answers 502; the rest 200."""
    http = MagicMock()
    calls = {"n": 0}

    def post(posted_url, data=None, headers=None, timeout=None, stream=False):
        calls["n"] += 1
        return _response(502 if calls["n"] % fail_every == 0 else 200)

    http.post.side_effect = post
    return http


@pytest.mark.integration
class TestARunGoesOnPastAFailure:
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_receiver_failing_now_and_then_gets_its_events_at_full_speed(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/flaky"
        http = _flaky_session(url, fail_every=10)
        mock_session.return_value = http
        webhook = _webhook(organization, "flaky", url)
        _events(webhook, 200)

        result = deliver_webhook(webhook.pk)

        # One run, not one event per tick: every event got its attempt, and
        # only the 1-in-10 that failed wait for their backoff.
        assert result == {"delivered": 180, "failed": 20, "retired": 0}
        statuses = AgentccWebhookEvent.all_objects.filter(webhook=webhook)
        assert statuses.filter(status="delivered").count() == 180
        failed = statuses.filter(status="pending", attempts=1)
        assert failed.count() == 20
        webhook.refresh_from_db()
        assert webhook.next_delivery_at is None  # not paused
        assert webhook.is_active is True

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_three_failures_in_a_row_stop_the_run_and_pause_the_webhook(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/500"
        http = _session({url: 500})
        mock_session.return_value = http
        webhook = _webhook(organization, "down", url)
        _events(webhook, 10)

        before = timezone.now()
        result = deliver_webhook(webhook.pk)

        assert result["failed"] == 3
        assert http.post.call_count == 3
        webhook.refresh_from_db()
        # 10 s for the first failure of the streak, doubling: 40 s after 3.
        pause = webhook.next_delivery_at - before
        assert timedelta(seconds=39) < pause < timedelta(seconds=42)
        assert webhook.delivery_lease_until is None

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_no_answer_from_the_receiver_stops_the_run_at_once(
        self, mock_session, _mock_url_check, organization
    ):
        import requests

        url = "https://hooks.example.com/hangs"
        http = MagicMock()
        http.post.side_effect = requests.Timeout("read timed out")
        mock_session.return_value = http
        webhook = _webhook(organization, "hangs", url)
        _events(webhook, 10)

        before = timezone.now()
        result = deliver_webhook(webhook.pk)

        # A hanging receiver holds a worker slot for one read timeout per
        # run, and its next run waits.
        assert result["failed"] == 1
        assert http.post.call_count == 1
        webhook.refresh_from_db()
        pause = webhook.next_delivery_at - before
        assert timedelta(seconds=9) < pause < timedelta(seconds=12)

    def test_the_pause_doubles_to_at_most_five_minutes(self):
        from agentcc.services.webhook_delivery import _pause

        assert [_pause(n).total_seconds() for n in (0, 1, 2, 3, 4, 5, 6, 50)] == [
            10,
            10,
            20,
            40,
            80,
            160,
            300,
            300,
        ]

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_webhook_turned_off_mid_run_gets_no_more_posts(
        self, mock_session, _mock_url_check, organization
    ):
        url = "https://hooks.example.com/ok"
        webhook = _webhook(organization, "turned-off", url)
        _events(webhook, 5)

        def post(*args, **kwargs):
            AgentccWebhook.all_objects.filter(pk=webhook.pk).update(is_active=False)
            return _response(200)

        http = MagicMock()
        http.post.side_effect = post
        mock_session.return_value = http

        deliver_webhook(webhook.pk, page_size=1)

        assert http.post.call_count == 1
        assert (
            AgentccWebhookEvent.all_objects.filter(
                webhook=webhook, status="pending"
            ).count()
            == 4
        )

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_url_edited_mid_run_is_used_from_the_next_page(
        self, mock_session, _mock_url_check, organization
    ):
        old_url, new_url = "https://old.example.com/x", "https://new.example.com/x"
        webhook = _webhook(organization, "moved", old_url)
        _events(webhook, 3)

        def post(*args, **kwargs):
            AgentccWebhook.all_objects.filter(pk=webhook.pk).update(url=new_url)
            return _response(200)

        http = MagicMock()
        http.post.side_effect = post
        mock_session.return_value = http

        deliver_webhook(webhook.pk, page_size=1)

        assert [c.args[0] for c in http.post.call_args_list] == [
            old_url,
            new_url,
            new_url,
        ]

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_an_event_past_the_maximum_age_is_not_sent(
        self, mock_session, _mock_url_check, organization
    ):
        from django.test import override_settings

        url = "https://hooks.example.com/ok"
        http = _session({url: 200})
        mock_session.return_value = http
        webhook = _webhook(organization, "aged", url)
        (old,) = _events(webhook, 1, start_minutes_ago=120)
        (fresh,) = _events(webhook, 1, start_minutes_ago=10)

        with override_settings(AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS=1):
            result = deliver_webhook(webhook.pk)

        assert result["delivered"] == 1
        assert AgentccWebhookEvent.all_objects.get(pk=old.pk).status == "pending"
        assert AgentccWebhookEvent.all_objects.get(pk=fresh.pk).status == "delivered"


@pytest.mark.integration
class TestDeliveryConcurrencyCap:
    def _dispatch(self):
        with patch.object(
            webhook_delivery.deliver_webhook_activity, "apply_async"
        ) as start:
            result = dispatch_webhook_deliveries_activity._original_func()
        return result, [c.kwargs["args"][0] for c in start.call_args_list]

    def test_no_more_runs_than_the_cap_are_in_flight(self, organization):
        from django.test import override_settings

        hooks = [
            _webhook(organization, f"hook-{i}", f"https://h{i}.example.com/x")
            for i in range(5)
        ]
        for i, hook in enumerate(hooks):
            _events(hook, 2, start_minutes_ago=60 - i)

        with override_settings(
            AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES=2,
            AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0,
        ):
            first, started = self._dispatch()
            # Oldest waiting events first.
            assert started == [str(hooks[0].pk), str(hooks[1].pk)]
            assert first == {"webhooks": 5, "started": 2, "running": 0, "waiting": 3}

            # Both runs still going: the next tick starts none.
            second, started = self._dispatch()
            assert started == []
            assert second == {"webhooks": 3, "started": 0, "running": 2, "waiting": 3}

            # One run ends and frees its slot.
            with patch(SESSION) as mock_session, patch(URL_CHECK):
                mock_session.return_value = _session({"https://h0.example.com/x": 200})
                deliver_webhook(hooks[0].pk, release_lease=True)
            third, started = self._dispatch()
            assert started == [str(hooks[2].pk)]
            assert third["running"] == 1

    def test_a_failure_streak_does_not_put_a_webhook_last(self, organization):
        """Round 3: a failing webhook out of its pause takes its turn like any
        other (here its event waited longest); only the pause throttles it."""
        from django.test import override_settings

        failing = _webhook(
            organization, "failing", "https://a.example.com/x", consecutive_failures=4
        )
        healthy = _webhook(organization, "healthy", "https://b.example.com/x")
        _events(failing, 1, start_minutes_ago=120)
        _events(healthy, 1, start_minutes_ago=5)

        with override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES=1):
            _, started = self._dispatch()

        assert started == [str(failing.pk)]

    def test_paused_inactive_and_deleted_webhooks_get_no_run(self, organization):
        paused = _webhook(
            organization,
            "paused",
            "https://a.example.com/x",
            next_delivery_at=timezone.now() + timedelta(minutes=2),
        )
        inactive = _webhook(
            organization, "inactive", "https://b.example.com/x", is_active=False
        )
        deleted = _webhook(organization, "deleted", "https://c.example.com/x")
        ready = _webhook(organization, "ready", "https://d.example.com/x")
        for hook in (paused, inactive, deleted, ready):
            _events(hook, 1)
        AgentccWebhook.all_objects.filter(pk=deleted.pk).update(deleted=True)

        result, started = self._dispatch()

        assert started == [str(ready.pk)]
        assert result["webhooks"] == 1

    def test_a_run_holds_its_lease_until_it_ends(self, organization):
        url = "https://hooks.example.com/ok"
        webhook = _webhook(organization, "leased", url)
        _events(webhook, 1)
        seen = []

        def post(*args, **kwargs):
            seen.append(
                AgentccWebhook.all_objects.get(pk=webhook.pk).delivery_lease_until
            )
            return _response(200)

        http = MagicMock()
        http.post.side_effect = post
        with patch(SESSION, return_value=http), patch(URL_CHECK):
            webhook_delivery.deliver_webhook_activity._original_func(str(webhook.pk))

        assert seen[0] > timezone.now()
        webhook.refresh_from_db()
        assert webhook.delivery_lease_until is None

    def test_the_cap_is_a_setting(self):
        from django.conf import settings

        assert settings.AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES == 3
        assert settings.AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES == 60

    def test_a_direct_run_does_not_free_a_scheduled_runs_slot(self, organization):
        url = "https://hooks.example.com/ok"
        webhook = _webhook(organization, "held", url)
        _events(webhook, 1)
        held_until = timezone.now() + timedelta(seconds=100)
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
            delivery_lease_until=held_until
        )

        with patch(SESSION, return_value=_session({url: 200})), patch(URL_CHECK):
            deliver_webhook_events()  # an operator's run, beside the scheduled one

        webhook.refresh_from_db()
        assert webhook.delivery_lease_until == held_until

    def test_no_lease_for_a_webhook_paused_or_turned_off_since_it_was_listed(
        self, organization
    ):
        from agentcc.services.webhook_delivery import _take_delivery_lease

        now = timezone.now()
        paused = _webhook(
            organization,
            "paused-now",
            "https://a.example.com/x",
            next_delivery_at=now + timedelta(seconds=40),
        )
        off = _webhook(
            organization, "off-now", "https://b.example.com/x", is_active=False
        )
        ready = _webhook(organization, "ready-now", "https://c.example.com/x")

        assert not _take_delivery_lease(paused.pk, now)
        assert not _take_delivery_lease(off.pk, now)
        assert _take_delivery_lease(ready.pk, now)
        assert not _take_delivery_lease(ready.pk, now)  # already held

    def test_a_queued_run_cannot_start_after_its_lease_ends(self):
        meta = webhook_delivery.deliver_webhook_activity._metadata
        assert (
            meta["schedule_to_start_timeout"]
            < webhook_delivery.DELIVERY_ACTIVITY_TIME_LIMIT
        )
        assert meta["workflow_run_timeout"] > (
            meta["schedule_to_start_timeout"] + meta["time_limit"]
        )


@pytest.mark.integration
class TestARunStopsWhenItDisablesTheWebhook:
    @patch(URL_CHECK)
    @patch(SESSION)
    def test_no_post_after_the_failure_that_disabled_it(
        self, mock_session, _mock_url_check, organization
    ):
        from django.test import override_settings

        url = "https://hooks.example.com/500"
        http = _session({url: 500})
        mock_session.return_value = http
        webhook = _webhook(organization, "nearly-off", url, consecutive_failures=9)
        _events(webhook, 10)

        with override_settings(
            AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=10,
            AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=0,
        ):
            result = deliver_webhook(webhook.pk)

        assert http.post.call_count == 1
        assert result["failed"] == 1
        webhook.refresh_from_db()
        assert webhook.is_active is False


@pytest.mark.integration
class TestFairDeliverySlots:
    """Round 3: slots rotate across orgs and webhooks, one org cannot hold
    them all, a failing webhook still gets its turn, and a run that ends
    hands its slot on at once."""

    def _dispatch(self):
        with patch.object(
            webhook_delivery.deliver_webhook_activity, "apply_async"
        ) as start:
            dispatch_webhook_deliveries_activity._original_func()
        return [c.kwargs["args"][0] for c in start.call_args_list]

    def _run(self, webhook_id, status_by_url):
        with patch(SESSION, return_value=_session(status_by_url)), patch(URL_CHECK):
            deliver_webhook(webhook_id, release_lease=True)

    def test_a_failing_webhook_gets_a_run_while_healthy_ones_stay_busy(
        self, organization
    ):
        from django.test import override_settings

        failing_url, healthy_url = "https://a.example.com/x", "https://b.example.com/x"
        failing = _webhook(organization, "failing", failing_url, consecutive_failures=1)
        healthy = _webhook(organization, "healthy", healthy_url)
        _events(failing, 1, start_minutes_ago=30)
        dispatched = []
        with override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES=1):
            for _tick in range(4):
                # The healthy webhook has a new event at every tick.
                _events(healthy, 1, start_minutes_ago=0)
                started = self._dispatch()
                dispatched += started
                for webhook_id in started:
                    self._run(webhook_id, {failing_url: 502, healthy_url: 200})

        assert str(failing.pk) in dispatched

    def test_one_org_with_slow_backlogged_webhooks_cannot_take_every_slot(
        self, organization
    ):
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other org")
        slow = [
            _webhook(organization, f"slow-{i}", f"https://slow{i}.example.com/x")
            for i in range(3)
        ]
        for hook in slow:
            _events(hook, 5, start_minutes_ago=120)
        waiting = _webhook(other_org, "waiting", "https://other.example.com/x")
        _events(waiting, 1, start_minutes_ago=1)
        slow_ids = {str(hook.pk) for hook in slow}

        started = self._dispatch()  # the default cap: 3 in all, 1 per org

        assert str(waiting.pk) in started
        assert len(slow_ids & set(started)) == 1
        # The slow org's run still holds its slot: the next tick gives that
        # org no second run.
        assert not slow_ids & set(self._dispatch())

    def test_a_run_rotates_to_the_webhook_that_waited_since_its_last_run(
        self, organization
    ):
        from django.test import override_settings

        url_a, url_b = "https://a.example.com/x", "https://b.example.com/x"
        busy = _webhook(organization, "busy", url_a)
        other = _webhook(organization, "other", url_b)
        _events(busy, 1, start_minutes_ago=120)
        _events(other, 1, start_minutes_ago=5)
        with override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES=1):
            assert self._dispatch() == [str(busy.pk)]
            self._run(busy.pk, {url_a: 200})
            # Its backlog is refilled with events older than the other's.
            _events(busy, 3, start_minutes_ago=110)

            assert self._dispatch() == [str(other.pk)]

    def test_a_run_that_ends_starts_the_next_waiting_webhook(self, organization):
        from django.test import override_settings

        url_a, url_b = "https://a.example.com/x", "https://b.example.com/x"
        first = _webhook(organization, "first", url_a)
        second = _webhook(organization, "second", url_b)
        _events(first, 1, start_minutes_ago=30)
        _events(second, 1, start_minutes_ago=20)
        with override_settings(
            AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES=1,
            AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0,
        ):
            assert self._dispatch() == [str(first.pk)]
            with (
                patch(SESSION, return_value=_session({url_a: 200})),
                patch(URL_CHECK),
                patch.object(
                    webhook_delivery.deliver_webhook_activity, "apply_async"
                ) as start,
            ):
                webhook_delivery.deliver_webhook_activity._original_func(str(first.pk))

        assert [c.kwargs["args"][0] for c in start.call_args_list] == [str(second.pk)]

    def test_the_per_org_cap_and_run_budget_are_settings(self, organization):
        from django.conf import settings
        from django.test import override_settings

        assert settings.AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG == 1
        assert settings.AGENTCC_WEBHOOK_RUN_BUDGET_SECONDS == 60
        hooks = [
            _webhook(organization, f"h-{i}", f"https://h{i}.example.com/x")
            for i in range(3)
        ]
        for hook in hooks:
            _events(hook, 1)
        with override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0):
            assert len(self._dispatch()) == 3

    @patch(URL_CHECK)
    @patch(SESSION)
    def test_a_run_uses_the_run_budget_setting(
        self, mock_session, _mock_url_check, organization
    ):
        from django.test import override_settings

        url = "https://hooks.example.com/slowish"
        mock_session.return_value = _session({url: 200}, sleep_by_url={url: 0.4})
        webhook = _webhook(organization, "budgeted", url)
        _events(webhook, 10)

        with override_settings(AGENTCC_WEBHOOK_RUN_BUDGET_SECONDS=1):
            result = deliver_webhook(webhook.pk)

        assert 1 <= result["delivered"] < 10


@pytest.mark.integration
def test_a_stale_run_does_not_free_a_newer_runs_lease(organization):
    """Round 3: a run that outlived its lease while the webhook was
    dispatched again holds a stale token; ending it leaves the newer run's
    lease alone."""
    url = "https://hooks.example.com/ok"
    webhook = _webhook(organization, "re-dispatched", url)
    _events(webhook, 1)
    stale = timezone.now() - timedelta(minutes=5)
    newer = timezone.now()
    newer_until = newer + timedelta(seconds=100)
    AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
        last_run_started_at=newer, delivery_lease_until=newer_until
    )

    with (
        patch(SESSION, return_value=_session({url: 200})),
        patch(URL_CHECK),
        patch.object(webhook_delivery.deliver_webhook_activity, "apply_async"),
    ):
        webhook_delivery.deliver_webhook_activity._original_func(
            str(webhook.pk), stale.isoformat()
        )

    webhook.refresh_from_db()
    assert webhook.delivery_lease_until == newer_until


@pytest.mark.integration
def test_a_stale_run_does_not_change_a_newer_runs_pause(organization):
    """Round 3 review: a run that outlived its lease and ends after a newer
    run paused the webhook neither clears that pause nor sets its own."""
    url = "https://hooks.example.com/ok"
    webhook = _webhook(organization, "re-dispatched", url)
    _events(webhook, 1)
    stale = timezone.now() - timedelta(minutes=5)
    newer_pause = timezone.now() + timedelta(minutes=3)
    AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
        last_run_started_at=timezone.now(), next_delivery_at=newer_pause
    )

    with patch(SESSION, return_value=_session({url: 200})), patch(URL_CHECK):
        deliver_webhook(webhook.pk, release_lease=True, run_token=stale)

    webhook.refresh_from_db()
    assert webhook.next_delivery_at == newer_pause


@pytest.mark.integration
def test_a_run_against_the_old_receiver_does_not_pause_the_fixed_one(organization):
    """Round 3 review: the owner fixes the URL while a run is still posting
    to the old one; that run's failure does not pause the fixed receiver."""
    old_url, new_url = "https://old.example.com/x", "https://new.example.com/x"
    webhook = _webhook(organization, "being-fixed", old_url)
    _events(webhook, 1)

    def post(*args, **kwargs):
        # The PATCH lands while this POST is in flight.
        AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
            url=new_url, next_delivery_at=None
        )
        raise webhook_delivery.http_requests.ConnectionError("refused")

    http = MagicMock()
    http.post.side_effect = post
    with patch(SESSION, return_value=http), patch(URL_CHECK):
        deliver_webhook(webhook.pk)

    webhook.refresh_from_db()
    assert webhook.next_delivery_at is None


@pytest.mark.integration
def test_a_finishing_run_does_not_redispatch_its_own_webhook(organization):
    """Round 3 review: the redispatch at the end of a run happens while its
    workflow still exists, so the fixed workflow id would reuse it and strand
    a fresh lease. It leaves its own webhook to the next tick and starts the
    next other one."""
    from django.test import override_settings

    url_a, url_b = "https://a.example.com/x", "https://b.example.com/x"
    busy = _webhook(organization, "busy", url_a)
    other = _webhook(organization, "other", url_b)
    _events(busy, 5, start_minutes_ago=30)
    _events(other, 1, start_minutes_ago=20)
    with override_settings(
        AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES=2,
        AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0,
    ):
        with (
            patch.object(
                webhook_delivery.deliver_webhook_activity, "apply_async"
            ) as start,
            # A run that ends on its budget with "busy" still holding due
            # events: it only ends the run and releases its lease.
            patch.object(
                webhook_delivery,
                "deliver_webhook",
                side_effect=lambda webhook_id, release_lease, run_token: (
                    webhook_delivery._end_run(webhook_id, False, True, run_token) and {}
                ),
            ),
        ):
            webhook_delivery.deliver_webhook_activity._original_func(str(busy.pk))

    started = [c.kwargs["args"][0] for c in start.call_args_list]
    assert started == [str(other.pk)]
    busy.refresh_from_db()
    assert busy.delivery_lease_until is None


@pytest.mark.integration
def test_nul_in_a_logged_request_still_creates_its_webhook_event_and_session(
    organization,
):
    """Round 3 review: the webhook payload and the session are built from the
    cleaned entry too, so a NUL from the caller costs neither."""
    from agentcc.models import AgentccSession

    webhook = _webhook(organization, "nul", "https://hooks.example.com/x")
    AgentccAPIKey.objects.create(
        gateway_key_id="nul-key", name="nul-key", organization=organization
    )

    stored = ingest_request_logs(
        [
            {
                "request_id": "nul-req",
                "model": "gpt-4o",
                "status_code": 200,
                "auth_key_id": "nul-key",
                "error_message": "bad\x00byte",
                "session_id": "sess\x00ion",
                "metadata": {"note": "a\x00b"},
            }
        ]
    )

    assert stored == 1
    (event,) = AgentccWebhookEvent.all_objects.filter(webhook=webhook)
    assert "\x00" not in str(event.payload)
    assert AgentccSession.no_workspace_objects.filter(
        organization=organization, session_id="session"
    ).exists()


@pytest.mark.integration
def test_a_dispatch_between_run_end_and_workflow_close_starts_a_real_run(
    organization,
):
    """Round 4 review: a run releases its lease, then redispatches and only
    then does its workflow close. A dispatcher that leases the webhook in that
    window must start a new run, not hit the closing workflow's id (a no-op
    that left the fresh lease with no run for 120 s)."""
    from django.test import override_settings

    webhook = _webhook(organization, "w", "https://a.example.com/x")
    _events(webhook, 3, start_minutes_ago=10)
    first = timezone.now()
    with (
        override_settings(AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG=0),
        patch.object(webhook_delivery.deliver_webhook_activity, "apply_async") as start,
    ):
        webhook_delivery.dispatch_deliveries(now=first)
        # The run ends on its budget with events still due: lease released,
        # workflow not yet closed.
        webhook_delivery._end_run(webhook.pk, False, True, first)
        second = first + timedelta(seconds=1)
        webhook_delivery.dispatch_deliveries(now=second)

    ids = [c.kwargs["task_id"] for c in start.call_args_list]
    assert len(ids) == 2
    assert ids[0] != ids[1], "the second run reused the closing run's workflow id"
    webhook.refresh_from_db()
    assert webhook.last_run_started_at == second


@pytest.mark.integration
def test_a_receiver_dripping_its_body_cannot_hold_a_post_past_its_deadline(
    organization,
):
    """Round 4 review: the read timeout is per read, so a receiver that sends
    its headers and then a byte every few seconds held a POST (and its worker
    thread) indefinitely. A POST now ends at DELIVERY_POST_DEADLINE_SECONDS
    in all, as a failed attempt, and the run goes on."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import requests

    class Drip(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.4)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    webhook = _webhook(organization, "drip", url)
    _events(webhook, 1)
    try:
        with (
            patch.object(webhook_delivery, "DELIVERY_POST_DEADLINE_SECONDS", 1.5),
            patch.object(webhook_delivery, "DELIVERY_TIMEOUT", (3, 1)),
            patch(URL_CHECK),
            patch(SESSION, return_value=requests.Session()),
        ):
            started = time.monotonic()
            result = deliver_webhook(webhook.pk)
            elapsed = time.monotonic() - started
    finally:
        server.shutdown()
    # Each read returns within the per-read timeout, so without a total
    # deadline the 100 bytes take 40 s.
    assert elapsed < 5, elapsed
    assert result["failed"] == 1
    event = AgentccWebhookEvent.all_objects.get(webhook=webhook)
    assert event.status == "pending" and event.attempts == 1
    assert "in time" in event.last_error
