"""The entry the gateway sends for a budget block raises budget.exceeded.

Django raises budget.exceeded (webhook and email alert) only from a log entry's
top-level error_code (log_ingestion._budget_error_code), and the gateway's
request-log batch carries it (GW-E2E-012).

The fixture is the gateway's own entry for a key-level org budget block: the Go
test TestLogBatchCarriesTheBudgetRefusalErrorCode
(agentcc-gateway/internal/plugins/logging/flusher_error_code_test.go) pins the
flusher's wire output to it field for field, so both suites read one entry.
The pipeline fixtures (an org Block, an org Throttle and a config.yaml budget's
refusal) are the entries the budget plugin, the engine, the logging plugin and
the flusher really send, pinned the same way by the Go tests in
agentcc-gateway/internal/plugins/budget/refusal_log_batch_test.go.

Every webhook POST goes to a fake session and every mail transport is faked:
nothing leaves the process.
"""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator

from agentcc.models import AgentccAPIKey
from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.log_ingestion import _parse_datetime, ingest_request_logs
from agentcc.tests.alert_clock import moving_clock
from integrations.services.credentials import CredentialManager

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "api_contracts" / "gateway"
FIXTURE = CONTRACTS_DIR / "fixtures" / "log-entry-v2-budget-refusal.json"
SCHEMA = CONTRACTS_DIR / "examples" / "log-entry-v2.schema.json"
# The entries the gateway's pipeline sends for an org budget's Block and
# Throttle refusals and for a config.yaml budget's refusal. The Go tests in
# agentcc-gateway/internal/plugins/budget/refusal_log_batch_test.go run the
# budget plugin, the engine, the logging plugin and the flusher's HTTP batch,
# and pin each entry to its fixture field for field.
PIPELINE_REFUSALS = [
    pytest.param(
        "log-entry-v2-budget-org-block.json",
        "org_budget_exceeded",
        "org",
        "monthly",
        "2026-10-01T00:00:00Z",
        "block",
        id="org-block",
    ),
    pytest.param(
        "log-entry-v2-budget-throttled.json",
        "budget_throttled",
        "org",
        "monthly",
        "2026-10-01T00:00:00Z",
        "throttle",
        id="org-throttle",
    ),
    pytest.param(
        "log-entry-v2-budget-exceeded-global.json",
        "budget_exceeded",
        "team:eng",
        "total",
        None,
        "block",
        id="global-block",
    ),
]
SMTP_CONFIG = {
    "host": "smtp.invalid",
    "port": 587,
    "username": "u",
    "password": "p",
    "from_email": "alerts@example.com",
}


def gateway_entry():
    """The entry the gateway sends for the budget block, as it sends it."""
    return json.loads(FIXTURE.read_text())


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=gateway_entry()["auth_key_id"],
        name="log-batch-key-b",
        organization=organization,
    )


@pytest.fixture
def clock():
    """timezone.now() a few seconds after the refusal, when the flusher sends it."""
    with moving_clock() as moving:
        moving.to(_parse_datetime(gateway_entry()["timestamp"]) + timedelta(seconds=4))
        yield moving


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
def mail():
    """The three provider transports, faked; a real SMTP or HTTP send fails the test."""
    sent = []

    def _refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach a real mail host")

    def _fake(provider):
        def _send(config, recipients, subject, body):
            sent.append({"provider": provider, "subject": subject, "body": body})
            return {"success": True}

        return _send

    with (
        patch("smtplib.SMTP", side_effect=_refuse),
        patch("smtplib.SMTP_SSL", side_effect=_refuse),
        patch("agentcc.services.email_sender.requests.post", side_effect=_refuse),
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
        started.append({"activity": activity_name, "args": tuple(args)})
        return f"task-{activity_name}"

    with patch("tfc.temporal.drop_in.runner.start_activity", side_effect=_start):
        yield started


def _run_kicks(kicks):
    """Run the Temporal starts the ingestion thread requested, as a worker would."""
    from tfc.temporal.schedules import agentcc_alerts

    for kick in list(kicks):
        getattr(agentcc_alerts, kick["activity"])._original_func(*kick["args"])
    kicks.clear()


def _hook(organization):
    return AgentccWebhook.no_workspace_objects.create(
        organization=organization,
        name="log-batch-hook",
        url="https://hooks.invalid/log-batch",
        events=["budget.exceeded", "error.occurred"],
    )


def _alert(organization):
    return AgentccEmailAlert.no_workspace_objects.create(
        organization=organization,
        name="log-batch-budget",
        recipients=["ops@example.com"],
        events=["budget.exceeded"],
        provider="smtp",
        is_active=True,
        cooldown_minutes=0,
        encrypted_config=CredentialManager.encrypt(SMTP_CONFIG),
    )


def _events(hook, event_type):
    return list(
        AgentccWebhookEvent.no_workspace_objects.filter(
            webhook=hook, event_type=event_type
        )
    )


@pytest.mark.integration
def test_fixture_is_a_budget_refusal_on_contract():
    """The fixture is a log-entry-v2 the schema accepts, refused by the gateway's
    budget plugin: its error code and the refusal metadata's level and period."""
    entry = gateway_entry()
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    assert [e.message for e in validator.iter_errors(entry)] == []
    assert entry["error_code"] == "org_budget_exceeded"
    assert entry["status_code"] == 429
    assert entry["metadata"]["budget_exceeded_level"] == "key:log-batch-key-b"


@pytest.mark.integration
@pytest.mark.django_db
class TestGatewayBudgetLogEntry:
    def test_gateway_budget_block_raises_budget_exceeded_webhook(
        self, organization, gateway_key, delivered, clock
    ):
        hook = _hook(organization)

        assert ingest_request_logs([gateway_entry()]) == 1
        from agentcc.tasks import process_webhook_events

        process_webhook_events()

        (event,) = _events(hook, "budget.exceeded")
        assert event.status == AgentccWebhookEvent.DELIVERED
        payload = event.payload
        assert payload["request_id"] == "req_log_batch_budget_block"
        assert payload["error_code"] == "org_budget_exceeded"
        assert payload["status_code"] == 429
        assert payload["budget_level"] == "key:log-batch-key-b"
        assert payload["budget_period"] == "monthly"
        assert payload["budget_period_start"] == "2026-10-01T00:00:00Z"
        assert payload["budget_action"] == "block"
        sent = [p["body"] for p in delivered if p["body"]["event"] == "budget.exceeded"]
        assert [body["request_id"] for body in sent] == ["req_log_batch_budget_block"]
        # The refusal is also an error.
        assert len(_events(hook, "error.occurred")) == 1

    @pytest.mark.parametrize(
        "fixture, code, level, period, period_start, action", PIPELINE_REFUSALS
    )
    def test_pipeline_budget_refusal_raises_budget_exceeded_webhook(
        self,
        organization,
        gateway_key,
        delivered,
        fixture,
        code,
        level,
        period,
        period_start,
        action,
    ):
        """The pipeline's own Block and Throttle refusals each raise one
        DELIVERED budget.exceeded with their code and refusal metadata."""
        entry = json.loads((CONTRACTS_DIR / "fixtures" / fixture).read_text())
        validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
        assert [e.message for e in validator.iter_errors(entry)] == []
        hook = _hook(organization)

        with moving_clock() as moving:
            moving.to(_parse_datetime(entry["timestamp"]) + timedelta(seconds=4))
            assert ingest_request_logs([entry]) == 1
            from agentcc.tasks import process_webhook_events

            process_webhook_events()

        (event,) = _events(hook, "budget.exceeded")
        assert event.status == AgentccWebhookEvent.DELIVERED
        payload = event.payload
        assert payload["request_id"] == entry["request_id"]
        assert payload["error_code"] == code
        assert payload["status_code"] == 429
        assert payload["budget_level"] == level
        assert payload["budget_period"] == period
        assert payload["budget_action"] == action
        if period_start is not None:
            assert payload["budget_period_start"] == period_start
        else:
            # A total period starts at the zero time, which the gateway sends
            # as 0001-01-01T00:00:00Z. Django re-formats it with strftime,
            # whose %Y is not zero-padded on glibc (1-01-01T00:00:00Z on
            # Linux), so only its presence is checked here.
            assert payload["budget_period_start"]
        sent = [p["body"] for p in delivered if p["body"]["event"] == "budget.exceeded"]
        assert [body["request_id"] for body in sent] == [entry["request_id"]]

    def test_gateway_budget_block_raises_budget_exceeded_email(
        self,
        organization,
        gateway_key,
        mail,
        kicks,
        clock,
        django_capture_on_commit_callbacks,
    ):
        alert = _alert(organization)

        with django_capture_on_commit_callbacks(execute=True):
            assert ingest_request_logs([gateway_entry()]) == 1
        assert mail == []  # nothing is sent on the ingestion thread
        _run_kicks(kicks)

        (message,) = mail
        assert "(budget.exceeded)" in message["body"]
        assert "key:log-batch-key-b" in message["body"]
        assert "org_budget_exceeded" in message["body"]
        alert.refresh_from_db()
        assert alert.last_triggered_at is not None

    def test_the_entry_without_error_code_raises_no_budget_event(
        self,
        organization,
        gateway_key,
        delivered,
        mail,
        kicks,
        clock,
        django_capture_on_commit_callbacks,
    ):
        """The same refusal without an error_code: an error, and no
        budget.exceeded by webhook or email."""
        hook = _hook(organization)
        _alert(organization)
        entry = gateway_entry()
        del entry["error_code"]

        with django_capture_on_commit_callbacks(execute=True):
            assert ingest_request_logs([entry]) == 1
        _run_kicks(kicks)

        assert _events(hook, "budget.exceeded") == []
        assert len(_events(hook, "error.occurred")) == 1
        assert mail == []
