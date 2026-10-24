"""budget.exceeded events and webhook retries.

Budget Exceeded is produced from the log entry's top-level error_code, which the
gateway copies from its APIError (plugins/logging/record.go), never from caller
metadata; once per (org, budget_exceeded_level, budget_period_start) when the
entry's metadata carries them, else once per (org, error_code, UTC day).

A Temporal schedule delivers due events every 30 s, honouring next_retry_at and
the backoff, and Retry starts a delivery at once off the request thread.

Every webhook POST here goes to a fake session: nothing leaves the process.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.webhook_delivery import deliver_webhook_events
from agentcc.tests.alert_clock import (
    day_start,
    month_start,
    moving_clock,
    next_month_start,
    utc_text,
)

KEY_ID = "key_webhook_events"
ALL_EVENTS = [
    "request.completed",
    "error.occurred",
    "guardrail.triggered",
    "budget.exceeded",
    "batch.completed",
]


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=KEY_ID, name="webhook-key", organization=organization
    )


@pytest.fixture
def delivered():
    """Every webhook POST the delivery service makes, captured, never sent."""
    posts = []
    session = MagicMock()

    def _post(url, data=None, headers=None, timeout=None):
        posts.append({"url": url, "body": json.loads(data)})
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        return resp

    session.post.side_effect = _post
    with (
        patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
        patch(
            "agentcc.services.webhook_delivery.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        yield posts


@pytest.fixture
def kicks():
    """Temporal starts requested by the request thread, captured, not run."""
    started = []

    def _start(activity_name, args=(), kwargs=None, **options):
        started.append({"activity": activity_name, "args": tuple(args), **options})
        return f"task-{activity_name}"

    with patch("tfc.temporal.drop_in.runner.start_activity", side_effect=_start):
        yield started


def _hook(organization, name="hook", events=None):
    return AgentccWebhook.no_workspace_objects.create(
        organization=organization,
        name=name,
        url=f"https://hooks.invalid/{name}",
        events=events or ALL_EVENTS,
    )


def _entry(request_id, **fields):
    return {
        "request_id": request_id,
        "timestamp": "2026-10-21T09:00:00Z",
        "model": "gpt-4o",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "status_code": 200,
        "latency_ms": 100,
        "auth_key_id": KEY_ID,
        **fields,
    }


def _refusal(request_id, code="org_budget_exceeded", metadata=None, **fields):
    """The entry the gateway logs for a budget refusal: 429, error_code from the
    APIError (record.go: rec.ErrorCode = apiErr.Code) and rc.Metadata copied."""
    return _entry(
        request_id,
        status_code=429,
        is_error=True,
        error_code=code,
        error_message="org budget exceeded: spent $150.00 of $100.00 monthly limit",
        metadata={
            "org_budget_blocked": "true",
            "org_budget_warning": "true",
            "org_budget_remaining": "0.00",
            **(metadata or {}),
        },
        **fields,
    )


def _budget_meta(level="org", period_start="2026-10-01T00:00:00Z", period="monthly"):
    return {
        "budget_exceeded_level": level,
        "budget_period": period,
        "budget_period_start": period_start,
        "budget_action": "block",
    }


def _budget_events(hook):
    return list(
        AgentccWebhookEvent.no_workspace_objects.filter(
            webhook=hook, event_type="budget.exceeded"
        ).order_by("created_at")
    )


# --- budget.exceeded ----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
class TestBudgetExceeded:
    def test_budget_refusal_emits_budget_exceeded(
        self, organization, gateway_key, delivered
    ):
        """A budget refusal emits budget.exceeded."""
        hook = _hook(organization)
        entries = [
            _refusal("req_budget_real"),
            # A caller's metadata cannot claim a budget refusal.
            _entry(
                "req_budget_code",
                status_code=429,
                is_error=True,
                error_message="org_budget_exceeded: Organization budget exceeded",
                metadata={"error_code": "org_budget_exceeded"},
            ),
            _entry(
                "req_budget_warn",
                metadata={"org_budget_warning": "true", "org_budget_remaining": "15"},
            ),
        ]
        assert ingest_request_logs(entries) == 3
        from agentcc.tasks import process_webhook_events

        process_webhook_events()

        (event,) = _budget_events(hook)
        assert event.payload["event"] == "budget.exceeded"
        assert event.payload["request_id"] == "req_budget_real"
        assert event.payload["error_code"] == "org_budget_exceeded"
        assert event.status == AgentccWebhookEvent.DELIVERED
        sent = [p["body"] for p in delivered if p["body"]["event"] == "budget.exceeded"]
        assert [body["request_id"] for body in sent] == ["req_budget_real"]

    def test_caller_metadata_error_code_never_emits_budget_exceeded(
        self, organization, gateway_key, delivered
    ):
        hook = _hook(organization)
        entry = _entry(
            "req_forged",
            status_code=400,
            is_error=True,
            metadata={"error_code": "budget_exceeded", **_budget_meta()},
        )
        assert ingest_request_logs([entry]) == 1

        assert _budget_events(hook) == []

    @pytest.mark.parametrize(
        "code", ["budget_exceeded", "org_budget_exceeded", "budget_throttled"]
    )
    def test_each_budget_refusal_code_emits(
        self, organization, gateway_key, delivered, code
    ):
        hook = _hook(organization)
        assert ingest_request_logs([_refusal(f"req_{code}", code=code)]) == 1

        (event,) = _budget_events(hook)
        assert event.payload["error_code"] == code

    def test_once_per_level_and_period_start(
        self, organization, gateway_key, delivered
    ):
        """Once per (org, budget_exceeded_level, budget_period_start).

        Dates come from the clock at the start (this month, then the next one),
        so the test holds on any day.
        """
        with moving_clock() as clock:
            hook = _hook(organization)
            this_month = month_start(clock.at)
            next_month = next_month_start(clock.at)

            def refusal(request_id, level, period_start, **fields):
                """Refused now, in the budget period starting at period_start."""
                return _refusal(
                    request_id,
                    metadata=_budget_meta(level, utc_text(period_start)),
                    timestamp=utc_text(clock.at),
                    **fields,
                )

            assert ingest_request_logs([refusal("lv-a", "org", this_month)]) == 1
            # Same level and period: a second batch and a second refusal in one batch.
            assert (
                ingest_request_logs(
                    [
                        refusal("lv-b", "org", this_month),
                        refusal("lv-c", "org", this_month),
                    ]
                )
                == 2
            )
            assert len(_budget_events(hook)) == 1
            # Another level in the same period.
            ingest_request_logs([refusal("lv-d", "team:t1", this_month)])
            # The org level again once the next period has begun.
            clock.to(next_month + timedelta(hours=9))
            ingest_request_logs(
                [refusal("lv-e", "org", next_month, code="budget_throttled")]
            )
            # Another code, same level and period: the budget level and period key
            # names no code, so it is not sent again.
            ingest_request_logs(
                [refusal("lv-f", "org", next_month, code="org_budget_exceeded")]
            )

        events = _budget_events(hook)
        assert [e.payload["request_id"] for e in events] == ["lv-a", "lv-d", "lv-e"]
        first = events[0].payload
        assert first["budget_level"] == "org"
        assert first["budget_period"] == "monthly"
        assert first["budget_period_start"] == utc_text(this_month)
        assert first["budget_action"] == "block"
        assert events[1].payload["budget_level"] == "team:t1"
        # The new period's refusal carries well-formed budget level and period
        # keys and is keyed by them.
        new_period = events[2].payload
        assert new_period["error_code"] == "budget_throttled"
        assert new_period["budget_level"] == "org"
        assert new_period["budget_period_start"] == utc_text(next_month)

    def test_without_level_keys_once_per_code_per_utc_day(
        self, organization, gateway_key, delivered
    ):
        """A refusal without the budget level and period keys: once per
        (org, error_code, UTC day).

        The two days are the next two UTC days from the clock at the start; each
        refusal is logged a few seconds after it happened.
        """
        with moving_clock() as clock:
            hook = _hook(organization)
            day1 = day_start(clock.at) + timedelta(days=1)
            day2 = day1 + timedelta(days=1)

            def refusal_at(request_id, at, **fields):
                clock.to(at + timedelta(seconds=5))
                return _refusal(request_id, timestamp=utc_text(at), **fields)

            day1_morning = day1 + timedelta(minutes=10)
            day1_night = day1 + timedelta(hours=23, minutes=50)
            ingest_request_logs([refusal_at("day-a", day1_morning)])
            ingest_request_logs([refusal_at("day-b", day1_night)])
            ingest_request_logs(
                [refusal_at("day-c", day1_night, code="budget_exceeded")]
            )
            ingest_request_logs([refusal_at("day-d", day2 + timedelta(minutes=5))])

        events = _budget_events(hook)
        assert [e.payload["request_id"] for e in events] == ["day-a", "day-c", "day-d"]
        assert [e.payload["budget_level"] for e in events] == [None, None, None]

    def test_budget_exceeded_is_per_org(self, organization, gateway_key, delivered):
        from accounts.models import Organization

        other = Organization.objects.create(name="Other org")
        AgentccAPIKey.objects.create(
            gateway_key_id="key_other", name="other", organization=other
        )
        mine = _hook(organization)
        theirs = _hook(other, name="other-hook")

        ingest_request_logs(
            [
                _refusal("org-a", metadata=_budget_meta()),
                {
                    **_refusal("org-b", metadata=_budget_meta()),
                    "auth_key_id": "key_other",
                },
            ]
        )

        assert [e.payload["request_id"] for e in _budget_events(mine)] == ["org-a"]
        assert [e.payload["request_id"] for e in _budget_events(theirs)] == ["org-b"]

    def test_unsubscribed_webhook_gets_no_budget_event(
        self, organization, gateway_key, delivered
    ):
        hook = _hook(organization, events=["error.occurred"])

        ingest_request_logs([_refusal("unsub-a")])

        assert _budget_events(hook) == []
        assert AgentccWebhookEvent.no_workspace_objects.filter(
            webhook=hook, event_type="error.occurred"
        ).exists()


# Days a CI run may fall on: a UTC midnight, a day after a month's key expired,
# the year's last second, a leap day.
OTHER_DAYS = [
    "2026-10-23T00:00:01Z",
    "2026-11-03T12:00:00Z",
    "2026-12-31T23:59:59Z",
    "2028-02-29T12:00:00Z",
]


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("today", OTHER_DAYS)
@pytest.mark.parametrize(
    "name",
    [
        "test_once_per_level_and_period_start",
        "test_without_level_keys_once_per_code_per_utc_day",
    ],
)
def test_budget_dedupe_tests_hold_on_any_day(
    organization, gateway_key, delivered, name, today
):
    """The dedupe tests hold whatever day they run on."""
    when = datetime.fromisoformat(today.replace("Z", "+00:00"))
    with patch.object(timezone, "now", return_value=when):
        getattr(TestBudgetExceeded(), name)(organization, gateway_key, delivered)


# --- webhook retries ------------------------------------------------------------


def test_alert_schedules_are_registered():
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tfc.temporal.schedules.agentcc_alerts import AGENTCC_ALERT_SCHEDULES

    by_id = {schedule.schedule_id: schedule for schedule in AGENTCC_ALERT_SCHEDULES}
    assert set(by_id) == {"agentcc-webhook-delivery", "agentcc-alerts-evaluator"}
    assert by_id["agentcc-webhook-delivery"].interval_seconds == 30
    assert by_id["agentcc-alerts-evaluator"].interval_seconds == 60
    for schedule in AGENTCC_ALERT_SCHEDULES:
        assert schedule in ALL_SCHEDULES
        assert schedule.activity_name in _ACTIVITY_REGISTRY


def _event(organization, hook, **fields):
    return AgentccWebhookEvent.no_workspace_objects.create(
        organization=organization,
        webhook=hook,
        event_type="error.occurred",
        payload={"event": "error.occurred", "request_id": fields.pop("rid", "r")},
        **fields,
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestWebhookRetries:
    def test_schedule_delivers_due_events_and_honours_next_retry_at(
        self, organization, delivered
    ):
        from tfc.temporal.schedules import agentcc_alerts

        hook = _hook(organization, events=["error.occurred"])
        now = timezone.now()
        fresh = _event(organization, hook, rid="fresh")
        due = _event(
            organization,
            hook,
            rid="due",
            attempts=1,
            next_retry_at=now - timedelta(seconds=1),
        )
        later = _event(
            organization,
            hook,
            rid="later",
            attempts=1,
            next_retry_at=now + timedelta(minutes=5),
        )

        agentcc_alerts.deliver_agentcc_webhook_events_activity._original_func()

        assert sorted(p["body"]["request_id"] for p in delivered) == ["due", "fresh"]
        for event in (fresh, due, later):
            event.refresh_from_db()
        assert fresh.status == due.status == AgentccWebhookEvent.DELIVERED
        assert later.status == AgentccWebhookEvent.PENDING
        assert later.attempts == 1

    def test_schedule_applies_the_backoff_after_a_failure(self, organization):
        from tfc.temporal.schedules import agentcc_alerts

        hook = _hook(organization, events=["error.occurred"])
        event = _event(organization, hook, rid="flaky")
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=503, text="busy")
        with (
            patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
            patch(
                "agentcc.services.webhook_delivery.build_ssrf_safe_session",
                return_value=session,
            ),
        ):
            agentcc_alerts.deliver_agentcc_webhook_events_activity._original_func()
            event.refresh_from_db()
            assert event.status == AgentccWebhookEvent.PENDING
            assert event.attempts == 1
            first_wait = event.next_retry_at - event.last_attempt_at
            assert timedelta(seconds=29) < first_wait <= timedelta(seconds=31)

            # The next tick inside the backoff sends nothing.
            agentcc_alerts.deliver_agentcc_webhook_events_activity._original_func()
            assert session.post.call_count == 1

            # Once due, the next tick retries and doubles the wait.
            AgentccWebhookEvent.no_workspace_objects.filter(id=event.id).update(
                next_retry_at=timezone.now() - timedelta(seconds=1)
            )
            agentcc_alerts.deliver_agentcc_webhook_events_activity._original_func()
            event.refresh_from_db()
            assert session.post.call_count == 2
            assert event.attempts == 2
            second_wait = event.next_retry_at - event.last_attempt_at
            assert timedelta(seconds=59) < second_wait <= timedelta(seconds=61)

    def test_retry_delivers_at_once_off_the_request_thread(
        self,
        organization,
        auth_client,
        delivered,
        kicks,
        django_capture_on_commit_callbacks,
    ):
        """Retry delivers at once, off the request thread."""
        from tfc.temporal.schedules import agentcc_alerts

        hook = _hook(organization, events=["error.occurred"])
        dead = _event(
            organization,
            hook,
            rid="dead",
            status=AgentccWebhookEvent.DEAD_LETTER,
            attempts=5,
            last_error="HTTP 503",
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(f"/agentcc/webhook-events/{dead.id}/retry/")

        assert response.status_code == 200, response.content
        assert response.json()["result"]["status"] == AgentccWebhookEvent.PENDING
        # Nothing is sent on the request thread: one delivery was started instead.
        assert delivered == []
        assert [(k["activity"], k["args"]) for k in kicks] == [
            ("deliver_agentcc_webhook_event_activity", (str(dead.id),))
        ]

        # The worker runs what was started.
        agentcc_alerts.deliver_agentcc_webhook_event_activity._original_func(
            *kicks[0]["args"]
        )
        dead.refresh_from_db()
        assert dead.status == AgentccWebhookEvent.DELIVERED
        assert dead.attempts == 1
        assert [p["body"]["request_id"] for p in delivered] == ["dead"]

    def test_retry_when_temporal_is_down_is_delivered_by_the_schedule(
        self, organization, auth_client, delivered, django_capture_on_commit_callbacks
    ):
        from tfc.temporal.schedules import agentcc_alerts

        hook = _hook(organization, events=["error.occurred"])
        dead = _event(
            organization,
            hook,
            rid="dead2",
            status=AgentccWebhookEvent.DEAD_LETTER,
            attempts=5,
        )
        with (
            patch(
                "tfc.temporal.drop_in.runner.start_activity",
                side_effect=RuntimeError("temporal unreachable"),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = auth_client.post(f"/agentcc/webhook-events/{dead.id}/retry/")

        assert response.status_code == 200, response.content
        assert delivered == []
        agentcc_alerts.deliver_agentcc_webhook_events_activity._original_func()
        dead.refresh_from_db()
        assert dead.status == AgentccWebhookEvent.DELIVERED

    def test_an_event_in_flight_is_not_delivered_twice(self, organization):
        """The schedule, a Retry and ingestion can all run delivery at once."""
        hook = _hook(organization, events=["error.occurred"])
        event = _event(organization, hook, rid="once")
        session = MagicMock()
        nested = []

        def _post(url, data=None, headers=None, timeout=None):
            # A second worker runs while the first one's POST is in flight.
            if not nested:
                nested.append(deliver_webhook_events(limit=10))
            return MagicMock(status_code=200, text="ok")

        session.post.side_effect = _post
        with (
            patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
            patch(
                "agentcc.services.webhook_delivery.build_ssrf_safe_session",
                return_value=session,
            ),
        ):
            result = deliver_webhook_events(limit=10)

        assert session.post.call_count == 1
        assert nested == [{"delivered": 0, "failed": 0}]
        assert result == {"delivered": 1, "failed": 0}
        event.refresh_from_db()
        assert event.status == AgentccWebhookEvent.DELIVERED
        assert event.attempts == 1


def test_budget_timestamp_helpers_are_utc():
    """Guard for the fallback key: the UTC day of the entry's own timestamp."""
    from agentcc.services.log_ingestion import _budget_dedupe_key

    late = _refusal("tz", timestamp="2026-10-21T23:30:00-07:00")
    assert _budget_dedupe_key(late) == ("code", "org_budget_exceeded", "2026-10-22")
    keyed = _refusal("lv", metadata=_budget_meta("key:abc", "2026-10-21T00:00:00Z"))
    assert _budget_dedupe_key(keyed) == ("level", "key:abc", "2026-10-21T00:00:00Z")
    assert datetime(2026, 10, 22, tzinfo=UTC)  # keeps the import used


@pytest.mark.parametrize(
    "metadata",
    [
        # Not a budget level: falls back to the per-code, per-day key.
        _budget_meta(level="workspace:w1"),
        _budget_meta(level="team:"),
        _budget_meta(level="x" * 300),
        # Not a period start, or one that begins after the request.
        _budget_meta(period_start="soon"),
        _budget_meta(period_start="2026-12-01T00:00:00Z"),
    ],
)
def test_malformed_level_keys_fall_back_to_the_day_key(metadata):
    from agentcc.services.log_ingestion import _budget_dedupe_key, _budget_fields

    entry = _refusal("bad-lv", metadata=metadata, timestamp="2026-10-21T09:00:00Z")

    assert _budget_dedupe_key(entry) == ("code", "org_budget_exceeded", "2026-10-21")
    assert _budget_fields(entry) == {
        "budget_level": None,
        "budget_period": None,
        "budget_period_start": None,
        "budget_action": None,
    }


def test_period_keys_expire_with_their_period():
    """A key is remembered until its period ends; a total budget never ends."""
    from agentcc.services.log_ingestion import _budget_key_expiry

    def expiry(period, start="2026-10-01T00:00:00Z"):
        # The refused request falls inside its period.
        return _budget_key_expiry(
            _refusal(
                "exp",
                metadata=_budget_meta(period=period, period_start=start),
                timestamp=start,
            )
        )

    assert expiry("daily") == datetime(2026, 10, 2, tzinfo=UTC)
    assert expiry("weekly") == datetime(2026, 10, 8, tzinfo=UTC)
    assert expiry("monthly") == datetime(2026, 11, 1, tzinfo=UTC)
    assert expiry("monthly", "2026-11-01T00:00:00Z") == datetime(
        2026, 12, 1, tzinfo=UTC
    )
    assert expiry("monthly", "2026-12-01T00:00:00Z") == datetime(2027, 1, 1, tzinfo=UTC)
    assert expiry("total") is None
    # Without the budget level and period keys: the end of the entry's UTC day.
    assert _budget_key_expiry(
        _refusal("exp-day", timestamp="2026-10-21T23:30:00-07:00")
    ) == datetime(2026, 10, 23, tzinfo=UTC)
