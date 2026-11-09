"""Fail-open capture policy, tested with real SDK envelopes rather than grouping."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests
import sentry_sdk
from django.core.exceptions import ObjectDoesNotExist
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from tfc.logging.sentry import _get_before_send

LOGGER = "ee.agenthub.explanation_agent.exp_agent"
EXPECTED = {
    "event": "explanation_summary_skipped",
    "reason": "insufficient_samples",
    "outcome": "handled",
    "input_count": 2,
    "usable_count": 2,
    "minimum_required": 5,
}


@pytest.mark.parametrize("shape", ["dict", "json", "formatted", "message"])
def test_expected_log_shapes_are_dropped(shape):
    event = {"logger": LOGGER}
    hint = {}
    if shape == "dict":
        hint["log_record"] = SimpleNamespace(msg=EXPECTED)
    elif shape == "json":
        hint["log_record"] = SimpleNamespace(msg=json.dumps(EXPECTED))
    elif shape == "formatted":
        event["logentry"] = {"message": "%s", "formatted": json.dumps(EXPECTED)}
    else:
        event["message"] = EXPECTED
    assert _get_before_send()(event, hint) is None


@pytest.mark.parametrize(
    "payload", ["not json", "[]", "{'event': 'explanation_summary_skipped'}", [], None,
                {**EXPECTED, "outcome": "unhandled"}, {**EXPECTED, "reason": "missing_reasons"},
                {**EXPECTED, "event": []}]
)
def test_unknown_payloads_are_kept(payload):
    event = {"logger": LOGGER, "logentry": {"formatted": payload}, "request": "invalid"}
    assert _get_before_send()(event, {}) is event


def test_expected_event_name_does_not_suppress_another_logger_or_exception():
    event = {"logger": "other.module", "message": EXPECTED}
    assert _get_before_send()(event, {}) is event
    event["logger"] = LOGGER
    exc = ValueError("unexpected")
    assert _get_before_send()(event, {"exc_info": (type(exc), exc, None)}) is event
    event["exception"] = {"values": [{"mechanism": {"handled": False}}]}
    assert _get_before_send()(event, {}) is event


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, 503])
def test_downstream_http_errors_are_actionable(status):
    response = requests.Response()
    response.status_code = status
    exc = requests.HTTPError("downstream failure", response=response)
    assert _get_before_send()({}, {"exc_info": (type(exc), exc, None)}) is not None


class EnvelopeTransport(Transport):
    def __init__(self):
        super().__init__()
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.headers.get("type") == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def transport():
    transport = EnvelopeTransport()
    with sentry_sdk.init(
        dsn="https://public@example.test/1",
        transport=transport,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR), DedupeIntegration()],
        before_send=_get_before_send(),
    ):
        yield transport


def test_sdk_envelopes_keep_genuine_faults_and_scrub_secrets(transport):
    logger = logging.getLogger(LOGGER)
    logger.error(EXPECTED)
    logger.error(json.dumps(EXPECTED))
    assert transport.events == []
    for exc in (ValueError("bad value"), ObjectDoesNotExist("missing internal row"),
                ConnectionResetError("database connection lost")):
        sentry_sdk.capture_exception(exc)
    logger.error("request_finished", extra={"status_code": 500, "api_key": "synthetic-secret"})
    assert len(transport.events) == 4
    assert "synthetic-secret" not in json.dumps(transport.events)
    assert transport.events[-1]["level"] == "error"


@pytest.mark.asyncio
async def test_logged_and_rethrown_activity_has_one_envelope_per_attempt(transport, monkeypatch):
    from tfc.temporal.common import sentry_interceptor

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)

    async def failing_activity():
        try:
            raise ValueError("activity failed")
        except ValueError:
            logging.getLogger("test.activity").exception("activity_failure")
            raise

    next_interceptor = SimpleNamespace(execute_activity=AsyncMock(side_effect=lambda _: failing_activity()))
    # AsyncMock does not await a coroutine returned by a synchronous side effect.
    async def execute(_):
        return await failing_activity()
    next_interceptor.execute_activity.side_effect = execute
    interceptor = sentry_interceptor.SentryActivityInboundInterceptor(next_interceptor)
    activity_input = SimpleNamespace(fn=failing_activity, args=[])
    for attempt in range(2):
        with pytest.raises(ValueError, match="activity failed"):
            await interceptor.execute_activity(activity_input)
        assert len(transport.events) == attempt + 1
