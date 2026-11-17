"""R7-ALERTS NG-18 (N27 dispatcher): email alerts are really sent.

At the base an email alert was saved and counted against the
GATEWAY_EMAIL_ALERTS plan limit and never sent: send_alert_email's only caller
was the console's Send test (probe: test_b1_email_alert_never_reaches_a_transport_
for_matching_events). Now:

- the request-level events (error.occurred, guardrail.triggered,
  budget.exceeded) are recorded on each matching active alert at ingestion and
  sent off the request thread (a Temporal start; the every-minute schedule is
  the fallback);
- the every-minute evaluator computes error.rate_spike, latency.spike and
  cost.threshold from the org's own request logs against the alert's thresholds;
- each alert sends at most one email per cooldown_minutes; events inside the
  cooldown wait and go out together in the next email; last_triggered_at is the
  last real send;
- a provider failure is logged with the alert id, kept for the next tick and
  never raised into ingestion.

Every transport here is fake or a loopback SMTP server on 127.0.0.1: no mail
leaves the process.
"""

import socket
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from django.urls import resolve
from django.utils import timezone

from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.tests.r7_clock import (
    month_start,
    moving_clock,
    next_month_start,
    utc_text,
)
from integrations.services.credentials import CredentialManager

KEY_ID = "key_r7_alert_dispatch"
SIX_EVENTS = [
    "error.occurred",
    "budget.exceeded",
    "guardrail.triggered",
    "error.rate_spike",
    "latency.spike",
    "cost.threshold",
]
SMTP_CONFIG = {
    "host": "smtp.invalid",
    "port": 587,
    "username": "u",
    "password": "p",
    "from_email": "alerts@example.com",
}


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=KEY_ID, name="r7-alert-key", organization=organization
    )


@pytest.fixture
def no_network():
    """Fail loudly if anything tries a real SMTP connection or HTTP call."""

    def _refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach a real mail host")

    with (
        patch("smtplib.SMTP", side_effect=_refuse),
        patch("smtplib.SMTP_SSL", side_effect=_refuse),
        patch("agentcc.services.email_sender.requests.post", side_effect=_refuse),
    ):
        yield


@pytest.fixture
def mail(no_network):
    """The three provider transports, faked: each call is recorded as sent."""
    sent = []

    def _fake(provider):
        def _send(config, recipients, subject, body):
            sent.append(
                {
                    "provider": provider,
                    "config": config,
                    "to": list(recipients),
                    "subject": subject,
                    "body": body,
                }
            )
            return {"success": True}

        return _send

    with (
        patch(
            "agentcc.services.email_sender._send_via_smtp", side_effect=_fake("smtp")
        ),
        patch(
            "agentcc.services.email_sender._send_via_sendgrid",
            side_effect=_fake("sendgrid"),
        ),
        patch(
            "agentcc.services.email_sender._send_via_resend",
            side_effect=_fake("resend"),
        ),
    ):
        yield sent


@pytest.fixture
def kicks():
    """Temporal starts the ingestion thread requests, captured, not run."""
    started = []

    def _start(activity_name, args=(), kwargs=None, **options):
        started.append({"activity": activity_name, "args": tuple(args), **options})
        return f"task-{activity_name}"

    with patch("tfc.temporal.drop_in.runner.start_activity", side_effect=_start):
        yield started


def _alert(organization, events, provider="smtp", config=None, **fields):
    fields.setdefault("cooldown_minutes", 0)
    return AgentccEmailAlert.no_workspace_objects.create(
        organization=organization,
        name=fields.pop("name", f"r7-{provider}-{'-'.join(events)}"[:200]),
        recipients=fields.pop("recipients", ["ops@example.com"]),
        events=events,
        provider=provider,
        is_active=fields.pop("is_active", True),
        encrypted_config=CredentialManager.encrypt(config or SMTP_CONFIG),
        **fields,
    )


def _now_iso(offset=timedelta(0)):
    return (timezone.now() - offset).isoformat().replace("+00:00", "Z")


def _entry(request_id, **fields):
    return {
        "request_id": request_id,
        "timestamp": _now_iso(timedelta(seconds=30)),
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


def _refusal(request_id, **metadata):
    return _entry(
        request_id,
        status_code=429,
        is_error=True,
        error_code="org_budget_exceeded",
        error_message="org budget exceeded: spent $150.00 of $100.00 monthly limit",
        metadata={"org_budget_blocked": "true", **metadata},
    )


def _ingest(entries, capture):
    """Ingest as the webhook view does, running what commits schedule."""
    with capture(execute=True):
        return ingest_request_logs(entries)


def _run_kicks(kicks):
    """Run the Temporal starts the ingestion thread requested, as a worker would."""
    from tfc.temporal.schedules import agentcc_alerts

    for kick in list(kicks):
        getattr(agentcc_alerts, kick["activity"])._original_func(*kick["args"])
    kicks.clear()


def _tick(now=None):
    """One run of the every-minute evaluator schedule."""
    from tfc.temporal.schedules import agentcc_alerts

    if now is None:
        return agentcc_alerts.evaluate_agentcc_email_alerts_activity._original_func()
    from agentcc.services.alert_dispatcher import evaluate_email_alerts

    return evaluate_email_alerts(now=now)


def _log(organization, request_id, ago=timedelta(minutes=1), **fields):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        request_id=request_id,
        model="gpt-4o",
        provider="openai",
        status_code=fields.pop("status_code", 200),
        latency_ms=fields.pop("latency_ms", 100),
        cost=Decimal(str(fields.pop("cost", "0"))),
        started_at=timezone.now() - ago,
        **fields,
    )


def _events_in(message):
    return {event for event in SIX_EVENTS if event in message["body"]}


# --- the probe, inverted ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_r7_email_alert_reaches_the_transport_for_each_matching_event(
    organization,
    gateway_key,
    auth_client,
    mail,
    kicks,
    django_capture_on_commit_callbacks,
):
    """test_b1_email_alert_never_reaches_a_transport_for_matching_events, inverted."""
    alert = _alert(
        organization,
        SIX_EVENTS,
        thresholds={
            "error_rate_percent": 1,
            "latency_ms": 100,
            "cost_usd": 0.01,
            "min_requests": 1,
            "window_minutes": 5,
        },
    )
    entries = [
        _entry("req_r7_err", status_code=500, is_error=True, error_message="boom"),
        _refusal("req_r7_429"),
        _entry("req_r7_guard", guardrail_triggered=True),
        _entry("req_r7_slow", latency_ms=600000),
        _entry("req_r7_cost", cost=999.0, cost_source="gateway"),
    ]

    assert _ingest(entries, django_capture_on_commit_callbacks) == 5
    assert mail == []  # nothing is sent on the ingestion thread
    _run_kicks(kicks)
    _tick()

    assert {m["provider"] for m in mail} == {"smtp"}
    assert set().union(*(_events_in(m) for m in mail)) == set(SIX_EVENTS)
    assert all(m["to"] == ["ops@example.com"] for m in mail)
    assert mail[0]["config"]["host"] == "smtp.invalid"
    alert.refresh_from_db()
    assert alert.last_triggered_at is not None
    assert alert.last_error == ""


# --- one test per event type -------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
class TestR7EachEventType:
    def test_r7_error_occurred_is_sent_over_smtp(
        self, organization, gateway_key, mail, kicks, django_capture_on_commit_callbacks
    ):
        alert = _alert(organization, ["error.occurred"])

        _ingest([_entry("ok-1")], django_capture_on_commit_callbacks)
        _run_kicks(kicks)
        assert mail == []

        _ingest(
            [_entry("err-1", status_code=502, is_error=True, error_message="upstream")],
            django_capture_on_commit_callbacks,
        )
        assert [k["activity"] for k in kicks] == [
            "dispatch_agentcc_email_alerts_activity"
        ]
        assert kicks[0]["args"] == (str(organization.id),)
        _run_kicks(kicks)

        (message,) = mail
        assert message["provider"] == "smtp"
        assert "Error Occurred" in message["subject"]
        assert "err-1" in message["body"] and "upstream" in message["body"]
        alert.refresh_from_db()
        assert alert.last_triggered_at is not None

    def test_r7_guardrail_triggered_is_sent_over_sendgrid(
        self,
        organization,
        gateway_key,
        no_network,
        kicks,
        django_capture_on_commit_callbacks,
    ):
        alert = _alert(
            organization,
            ["guardrail.triggered"],
            provider="sendgrid",
            config={"api_key": "SG.fake-key", "from_email": "alerts@example.com"},
        )
        calls = []

        def _post(url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": headers, "json": json})
            return MagicMock(status_code=202, text="")

        _ingest(
            [_entry("guard-1", guardrail_triggered=True)],
            django_capture_on_commit_callbacks,
        )
        with patch("agentcc.services.email_sender.requests.post", side_effect=_post):
            _run_kicks(kicks)

        (call,) = calls
        assert call["url"] == "https://api.sendgrid.com/v3/mail/send"
        assert call["headers"]["Authorization"] == "Bearer SG.fake-key"
        assert call["json"]["personalizations"] == [
            {"to": [{"email": "ops@example.com"}]}
        ]
        assert "Guardrail Triggered" in call["json"]["subject"]
        assert "guard-1" in call["json"]["content"][0]["value"]
        alert.refresh_from_db()
        assert alert.last_triggered_at is not None

    def test_r7_budget_exceeded_is_sent_over_resend_once_per_level_and_period(
        self,
        organization,
        gateway_key,
        no_network,
        kicks,
        django_capture_on_commit_callbacks,
    ):
        _alert(
            organization,
            ["budget.exceeded"],
            provider="resend",
            config={"api_key": "re_fake", "from_email": "alerts@example.com"},
        )
        calls = []

        def _post(url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "json": json})
            return MagicMock(status_code=200, text="")

        with (
            moving_clock() as clock,
            patch("agentcc.services.email_sender.requests.post", side_effect=_post),
        ):
            # Periods from the clock at the start: the test holds on any day.
            this_month = month_start(clock.at)
            next_month = next_month_start(clock.at)
            k1 = {
                "budget_exceeded_level": "org",
                "budget_period": "monthly",
                "budget_period_start": utc_text(this_month),
                "budget_action": "block",
            }
            _ingest([_refusal("b-1", **k1)], django_capture_on_commit_callbacks)
            _run_kicks(kicks)
            # The same level and period again: no second email (cooldown is 0).
            _ingest([_refusal("b-2", **k1)], django_capture_on_commit_callbacks)
            _run_kicks(kicks)
            _tick()
            # A caller's metadata alone never claims a refusal.
            _ingest(
                [
                    _entry(
                        "b-3",
                        status_code=400,
                        is_error=True,
                        metadata={"error_code": "budget_exceeded"},
                    )
                ],
                django_capture_on_commit_callbacks,
            )
            _run_kicks(kicks)
            # A new period is a new budget.exceeded, once the period has begun.
            clock.to(next_month + timedelta(hours=9))
            _ingest(
                [
                    _refusal(
                        "b-4", **{**k1, "budget_period_start": utc_text(next_month)}
                    )
                ],
                django_capture_on_commit_callbacks,
            )
            _run_kicks(kicks)

        assert [c["url"] for c in calls] == ["https://api.resend.com/emails"] * 2
        assert "b-1" in calls[0]["json"]["html"]
        assert "b-4" in calls[1]["json"]["html"]
        assert "Budget Exceeded" in calls[0]["json"]["subject"]
        assert "org" in calls[0]["json"]["html"]
        # Each email names its period (K1), so the second was keyed by the new one.
        assert utc_text(this_month) in calls[0]["json"]["html"]
        assert utc_text(next_month) in calls[1]["json"]["html"]
        assert utc_text(this_month) not in calls[1]["json"]["html"]

    @pytest.mark.parametrize(
        "today",
        [
            "2026-10-23T00:00:01Z",
            "2026-11-03T12:00:00Z",
            "2026-12-31T23:59:59Z",
            "2028-02-29T12:00:00Z",
        ],
    )
    def test_r7_budget_email_test_holds_on_any_day(
        self,
        organization,
        gateway_key,
        no_network,
        kicks,
        django_capture_on_commit_callbacks,
        today,
    ):
        """The Resend budget test holds whatever day it runs on (review R7-ALERTS:
        its fixed period starts sent a third email from 2026-11-03)."""
        when = datetime.fromisoformat(today.replace("Z", "+00:00"))
        with patch.object(timezone, "now", return_value=when):
            self.test_r7_budget_exceeded_is_sent_over_resend_once_per_level_and_period(
                organization,
                gateway_key,
                no_network,
                kicks,
                django_capture_on_commit_callbacks,
            )

    def test_r7_error_rate_spike_is_evaluated_from_the_request_logs(
        self, organization, mail
    ):
        alert = _alert(
            organization,
            ["error.rate_spike"],
            thresholds={
                "error_rate_percent": 20,
                "min_requests": 5,
                "window_minutes": 5,
            },
        )
        for i in range(9):
            _log(organization, f"rate-ok-{i}")
        _log(organization, "rate-err-0", status_code=500, is_error=True)
        # Old errors are outside the 5-minute window.
        for i in range(5):
            _log(
                organization, f"rate-old-{i}", ago=timedelta(minutes=30), is_error=True
            )

        _tick()
        assert mail == []  # 1 of 10 = 10%

        _log(organization, "rate-err-1", status_code=503, is_error=True)
        _log(organization, "rate-err-2", status_code=500, is_error=True)
        _tick()  # 3 of 12 = 25%

        (message,) = mail
        assert "Error Rate Spike" in message["subject"]
        assert "25" in message["body"] and "20" in message["body"]
        alert.refresh_from_db()
        assert alert.last_triggered_at is not None

    def test_r7_latency_spike_is_evaluated_from_the_request_logs(
        self, organization, mail
    ):
        _alert(
            organization,
            ["latency.spike"],
            thresholds={"latency_ms": 1000, "min_requests": 3, "window_minutes": 5},
        )
        for i in range(3):
            _log(organization, f"lat-ok-{i}", latency_ms=100)
        _tick()
        assert mail == []

        for i in range(3):
            _log(organization, f"lat-slow-{i}", latency_ms=4000)
        _tick()

        (message,) = mail
        assert "Latency Spike" in message["subject"]
        assert "4000" in message["body"] and "1000" in message["body"]

    def test_r7_cost_threshold_is_evaluated_from_the_request_logs(
        self, organization, mail
    ):
        from accounts.models import Organization

        _alert(
            organization,
            ["cost.threshold"],
            thresholds={"cost_usd": 1.0, "window_minutes": 60},
        )
        _log(organization, "cost-a", cost="0.50")
        _log(organization, "cost-old", cost="5.00", ago=timedelta(hours=3))
        # Another org's spend never counts.
        other = Organization.objects.create(name="R7 other org")
        _log(other, "cost-other", cost="9.00")
        _tick()
        assert mail == []

        _log(organization, "cost-b", cost="0.60")
        _tick()

        (message,) = mail
        assert "Cost Threshold" in message["subject"]
        assert "1.1" in message["body"]


# --- cooldown, failures, scope ------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
class TestR7DispatchRules:
    def test_r7_cooldown_holds_across_ticks_then_one_email_carries_the_rest(
        self, organization, gateway_key, mail, kicks, django_capture_on_commit_callbacks
    ):
        alert = _alert(organization, ["error.occurred"], cooldown_minutes=10)
        failing = {"status_code": 500, "is_error": True}

        _ingest([_entry("cd-1", **failing)], django_capture_on_commit_callbacks)
        _run_kicks(kicks)
        assert len(mail) == 1
        alert.refresh_from_db()
        first_sent = alert.last_triggered_at

        _ingest(
            [_entry("cd-2", **failing), _entry("cd-3", **failing)],
            django_capture_on_commit_callbacks,
        )
        _run_kicks(kicks)
        _tick()
        _tick(now=first_sent + timedelta(minutes=9))
        assert len(mail) == 1  # inside the cooldown, across ticks

        _tick(now=first_sent + timedelta(minutes=10, seconds=1))
        assert len(mail) == 2
        assert "cd-3" in mail[1]["body"] and "2" in mail[1]["body"]
        alert.refresh_from_db()
        assert alert.last_triggered_at > first_sent

        _tick(now=first_sent + timedelta(minutes=30))
        assert len(mail) == 2  # nothing new to send

    def test_r7_provider_failure_never_breaks_ingestion_and_is_retried_next_tick(
        self,
        organization,
        gateway_key,
        no_network,
        kicks,
        django_capture_on_commit_callbacks,
    ):
        alert = _alert(organization, ["error.occurred"])
        with (
            patch(
                "agentcc.services.email_sender._send_via_smtp",
                side_effect=OSError("connection refused"),
            ),
            patch("agentcc.services.alert_dispatcher.logger") as logger,
        ):
            stored = _ingest(
                [_entry("fail-1", status_code=500, is_error=True)],
                django_capture_on_commit_callbacks,
            )
            _run_kicks(kicks)

        assert stored == 1
        assert AgentccRequestLog.no_workspace_objects.filter(
            request_id="fail-1"
        ).exists()
        alert.refresh_from_db()
        assert alert.last_triggered_at is None
        # A fixed reason, never the server's text (ALERT-ACCESS-1 round 2).
        from agentcc.services.email_sender import SMTP_CONNECT_FAILED

        assert alert.last_error == SMTP_CONNECT_FAILED
        assert alert.last_error_at is not None
        logged = [c.kwargs.get("alert_id") for c in logger.warning.call_args_list]
        assert str(alert.id) in logged

        sent = []
        with patch(
            "agentcc.services.email_sender._send_via_smtp",
            side_effect=lambda *a: sent.append(a) or {"success": True},
        ):
            _tick()

        assert len(sent) == 1
        assert "fail-1" in sent[0][3]
        alert.refresh_from_db()
        assert alert.last_triggered_at is not None
        assert alert.last_error == ""

    def test_r7_a_provider_rejection_is_kept_for_the_next_tick(
        self,
        organization,
        gateway_key,
        no_network,
        kicks,
        django_capture_on_commit_callbacks,
    ):
        alert = _alert(organization, ["error.occurred"])
        with patch(
            "agentcc.services.email_sender._send_via_smtp",
            return_value={"success": False, "error": "550 mailbox unavailable"},
        ):
            _ingest(
                [_entry("rej-1", status_code=500, is_error=True)],
                django_capture_on_commit_callbacks,
            )
            _run_kicks(kicks)
        alert.refresh_from_db()
        # A fixed reason, never the server's text (ALERT-ACCESS-1 round 2).
        from agentcc.services.email_sender import SEND_FAILED

        assert alert.last_error == SEND_FAILED

        with patch(
            "agentcc.services.email_sender._send_via_smtp",
            return_value={"success": True},
        ) as smtp:
            _tick()
        assert smtp.call_count == 1

    def test_r7_temporal_down_still_sends_on_the_next_tick(
        self, organization, gateway_key, mail, django_capture_on_commit_callbacks
    ):
        _alert(organization, ["guardrail.triggered"])
        with patch(
            "tfc.temporal.drop_in.runner.start_activity",
            side_effect=RuntimeError("temporal unreachable"),
        ):
            stored = _ingest(
                [_entry("td-1", guardrail_triggered=True)],
                django_capture_on_commit_callbacks,
            )
        assert stored == 1
        assert mail == []

        _tick()
        assert len(mail) == 1

    def test_r7_only_active_alerts_of_the_org_with_the_event_are_sent(
        self, organization, gateway_key, mail, kicks, django_capture_on_commit_callbacks
    ):
        from accounts.models import Organization

        other = Organization.objects.create(name="R7 other org")
        _alert(organization, ["error.occurred"], name="inactive", is_active=False)
        deleted = _alert(organization, ["error.occurred"], name="deleted")
        deleted.deleted = True
        deleted.save(update_fields=["deleted"])
        _alert(organization, ["guardrail.triggered"], name="other-event")
        _alert(other, ["error.occurred"], name="other-org")
        live = _alert(
            organization,
            ["error.occurred"],
            name="live",
            recipients=["live@example.com"],
        )

        _ingest(
            [_entry("scope-1", status_code=500, is_error=True)],
            django_capture_on_commit_callbacks,
        )
        _run_kicks(kicks)
        _tick()

        assert [m["to"] for m in mail] == [["live@example.com"]]
        live.refresh_from_db()
        assert live.last_triggered_at is not None

    def test_r7_send_test_neither_needs_nor_spends_the_cooldown(
        self,
        organization,
        gateway_key,
        auth_client,
        mail,
        kicks,
        django_capture_on_commit_callbacks,
    ):
        from agentcc.services import email_sender

        alert = _alert(organization, ["error.occurred"], cooldown_minutes=60)
        url = f"/agentcc/email-alerts/{alert.id}/test/"
        # Django may retain a sender imported inside a sys.modules stand-in.
        # Route its actual SMTP lookup to the same transport fake as the digest.
        sender = resolve(url).func.cls.test.__globals__["send_test_email"]
        smtp = email_sender._send_via_smtp  # installed by the mail fixture
        with patch.dict(sender.__globals__, {"_send_via_smtp": smtp}):
            tested = auth_client.post(url, {}, format="json")
        smtp.assert_called_once()
        assert tested.status_code == 200, tested.content
        alert.refresh_from_db()
        assert alert.last_triggered_at is None

        _ingest(
            [_entry("after-test", status_code=500, is_error=True)],
            django_capture_on_commit_callbacks,
        )
        _run_kicks(kicks)

        assert len(mail) == 2
        assert "after-test" in mail[1]["body"]


# --- a real SMTP conversation, on loopback --------------------------------------------


class _LoopbackSMTP(threading.Thread):
    """A one-message SMTP server on 127.0.0.1 (no TLS, no auth)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(20)
        self.port = self.sock.getsockname()[1]
        self.messages = []

    def run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn, conn.makefile("rwb") as stream:

            def reply(line):
                stream.write(line.encode() + b"\r\n")
                stream.flush()

            reply("220 loopback ESMTP")
            envelope = {"from": None, "to": []}
            while True:
                raw = stream.readline()
                if not raw:
                    break
                command = raw.decode().strip()
                verb = command.upper()
                if verb.startswith(("EHLO", "HELO")):
                    reply("250 loopback")
                elif verb.startswith("MAIL FROM"):
                    envelope["from"] = command[10:].strip()
                    reply("250 OK")
                elif verb.startswith("RCPT TO"):
                    envelope["to"].append(command[8:].strip())
                    reply("250 OK")
                elif verb == "DATA":
                    reply("354 End data with <CR><LF>.<CR><LF>")
                    lines = []
                    while (line := stream.readline()) not in (b".\r\n", b""):
                        lines.append(line)
                    self.messages.append({**envelope, "data": b"".join(lines).decode()})
                    reply("250 queued")
                elif verb == "QUIT":
                    reply("221 bye")
                    break
                else:
                    reply("250 OK")
        self.sock.close()


@pytest.mark.integration
@pytest.mark.django_db
def test_r7_smtp_alert_is_delivered_to_a_loopback_server(
    organization, gateway_key, kicks, django_capture_on_commit_callbacks
):
    server = _LoopbackSMTP()
    server.start()
    _alert(
        organization,
        ["error.occurred"],
        config={
            "host": "mail.loopback.test",
            "port": server.port,
            "from_email": "alerts@example.com",
            "use_tls": False,
        },
        recipients=["oncall@example.com"],
    )

    # The send checks the host and connects to the addresses the check answers
    # (ALERT-ACCESS-1). A real check refuses loopback (test_email_alert_access),
    # so this one answers the loopback server's address for the name.
    with patch(
        "agentcc.services.email_sender.smtp_connect_addresses",
        return_value=["127.0.0.1"],
    ) as checked:
        _ingest(
            [
                _entry(
                    "smtp-real-1",
                    status_code=500,
                    is_error=True,
                    error_message="kaboom",
                )
            ],
            django_capture_on_commit_callbacks,
        )
        _run_kicks(kicks)
    server.join(timeout=20)
    checked.assert_called_once_with("mail.loopback.test", server.port)

    (message,) = server.messages
    assert message["from"] == "<alerts@example.com>"
    assert message["to"] == ["<oncall@example.com>"]
    assert "Subject: [Agentcc] Alert: Error Occurred" in message["data"]
    assert "smtp-real-1" in message["data"]


def test_r7_the_pinned_smtp_connection_keeps_the_server_name():
    """ALERT-ACCESS-1: the socket goes to the checked address; STARTTLS still
    sends the name smtplib was given (SNI)."""
    from agentcc.services.email_sender import _PinnedSMTP

    ours, theirs = socket.socketpair()
    theirs.sendall(b"220 synthetic ESMTP\r\n")
    dialed = []

    def create_connection(address, timeout=None, source_address=None):
        dialed.append(address)
        return ours

    with patch("socket.create_connection", create_connection):
        server = _PinnedSMTP(
            ["93.184.216.34"], host="mail.pinned.test", port=587, timeout=5
        )
    try:
        assert dialed == [("93.184.216.34", 587)]
        assert server._host == "mail.pinned.test"
    finally:
        server.close()
        theirs.close()


# --- thresholds are validated where they are saved -------------------------------------


@pytest.mark.django_db
class TestR7Thresholds:
    @pytest.mark.parametrize(
        "thresholds",
        [
            {"error_rate_percent": 0},
            {"error_rate_percent": 101},
            {"latency_ms": -5},
            {"cost_usd": "lots"},
            {"window_minutes": 0},
            {"window_minutes": 1441},
            {"min_requests": 0},
            {"latency_ms": True},
        ],
    )
    def test_r7_invalid_thresholds_are_refused(self, thresholds):
        from agentcc.serializers.email_alert import AgentccEmailAlertWriteSerializer

        serializer = AgentccEmailAlertWriteSerializer(
            data={
                "name": "r7-thresholds",
                "recipients": ["ops@example.com"],
                "events": ["error.rate_spike", "latency.spike", "cost.threshold"],
                "provider": "smtp",
                "thresholds": thresholds,
            }
        )
        assert not serializer.is_valid()
        assert "thresholds" in serializer.errors

    def test_r7_valid_thresholds_and_defaults(self):
        from agentcc.serializers.email_alert import AgentccEmailAlertWriteSerializer
        from agentcc.services.alert_dispatcher import (
            DEFAULT_THRESHOLDS,
            effective_thresholds,
        )

        serializer = AgentccEmailAlertWriteSerializer(
            data={
                "name": "r7-thresholds-ok",
                "recipients": ["ops@example.com"],
                "events": ["error.rate_spike"],
                "provider": "smtp",
                "thresholds": {"error_rate_percent": 12.5, "window_minutes": 15},
            }
        )
        assert serializer.is_valid(), serializer.errors
        alert = AgentccEmailAlert(thresholds={"error_rate_percent": 12.5})
        assert effective_thresholds(alert) == {
            **DEFAULT_THRESHOLDS,
            "error_rate_percent": 12.5,
        }

    def test_r7_read_serializer_reports_the_last_delivery_error(self, organization):
        from agentcc.serializers.email_alert import AgentccEmailAlertSerializer
        from agentcc.services.email_sender import NO_RECIPIENTS

        alert = _alert(organization, ["error.occurred"])
        # A stored error is one of the sender's fixed messages (ALERT-ACCESS-1
        # round 2; test_delivery_errors_value_free covers a raw one).
        alert.last_error = NO_RECIPIENTS
        alert.last_error_at = timezone.now()
        alert.save(update_fields=["last_error", "last_error_at"])

        data = AgentccEmailAlertSerializer(alert).data
        assert data["last_error"] == NO_RECIPIENTS
        assert data["last_error_at"] is not None
        assert "dispatch_state" not in data
