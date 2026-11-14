"""Gateway webhook delivery runs on a schedule, per webhook, never inside log ingest."""

import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
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
    resp = MagicMock()
    resp.status_code = status
    resp.text = "server error" if status >= 400 else "ok"
    return resp


def _session(status_by_url, sleep_by_url=None):
    """A fake SSRF-safe session answering by URL, recording each POST."""
    http = MagicMock()
    sleep_by_url = sleep_by_url or {}

    def post(url, data=None, headers=None, timeout=None):
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

        assert result == {"delivered": 6, "failed": 1, "retired": 0}
        good_posts = [c for c in http.post.call_args_list if c.args[0] == good_url]
        bad_posts = [c for c in http.post.call_args_list if c.args[0] == bad_url]
        assert len(bad_posts) == 1
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

        first = AgentccWebhookEvent.all_objects.get(pk=bad_events[0].pk)
        assert first.status == "pending"
        assert first.attempts == 1
        assert first.next_retry_at > timezone.now()
        rest = AgentccWebhookEvent.all_objects.filter(
            pk__in=[e.pk for e in bad_events[1:]]
        )
        assert list(rest.values_list("attempts", flat=True)) == [0, 0, 0]
        assert set(rest.values_list("status", flat=True)) == {"pending"}
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

        with patch.object(
            webhook_delivery.deliver_webhook_activity, "apply_async"
        ) as start:
            result = dispatch_webhook_deliveries_activity._original_func()

        assert result == {"webhooks": 2, "started": 2}
        calls = {c.kwargs["args"][0]: c.kwargs for c in start.call_args_list}
        assert set(calls) == {str(due_a.pk), str(due_b.pk)}
        for webhook_id, kwargs in calls.items():
            assert kwargs["task_id"] == f"agentcc-webhook-delivery-{webhook_id}"
            assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING

    def test_one_webhook_failing_to_start_does_not_stop_the_others(self, organization):
        first = _webhook(organization, "first", "https://a.example.com/x")
        second = _webhook(organization, "second", "https://b.example.com/x")
        _events(first, 1)
        _events(second, 1)

        with patch.object(
            webhook_delivery.deliver_webhook_activity,
            "apply_async",
            side_effect=[RuntimeError("temporal down"), None],
        ):
            result = dispatch_webhook_deliveries_activity._original_func()

        assert result == {"webhooks": 2, "started": 1}


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
        side_effect=RuntimeError("database went away"),
    )
    def test_an_unexpected_ingest_error_asks_the_gateway_to_retry(
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
        _events(webhook, 1, start_minutes_ago=1)  # a newer one for the cap to keep

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
        assert event.status == ("cancelled" if meanwhile == "delete" else "dropped")

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
