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

from tfc.logging.sentry import _get_before_send
from tfc.tests.sentry_capture import sentry_capture

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
    "payload",
    [
        "not json",
        "[]",
        "{'event': 'explanation_summary_skipped'}",
        [],
        None,
        {**EXPECTED, "outcome": "unhandled"},
        {**EXPECTED, "reason": "missing_reasons"},
        {**EXPECTED, "event": []},
    ],
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


@pytest.fixture
def transport():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
    ) as transport:
        yield transport


def test_sdk_envelopes_keep_genuine_faults_and_scrub_secrets(transport):
    logger = logging.getLogger(LOGGER)
    logger.error(EXPECTED)
    logger.error(json.dumps(EXPECTED))
    assert transport.events == []
    for exc in (
        ValueError("bad value"),
        ObjectDoesNotExist("missing internal row"),
        ConnectionResetError("database connection lost"),
    ):
        sentry_sdk.capture_exception(exc)
    logger.error(
        "request_finished", extra={"status_code": 500, "api_key": "synthetic-secret"}
    )
    assert len(transport.events) == 4
    assert "synthetic-secret" not in json.dumps(transport.events)
    assert transport.events[-1]["level"] == "error"


@pytest.mark.asyncio
async def test_logged_and_rethrown_activity_has_one_envelope_per_attempt(
    transport, monkeypatch
):
    from tfc.temporal.common import sentry_interceptor

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)

    async def failing_activity():
        try:
            raise ValueError("activity failed")
        except ValueError:
            logging.getLogger("test.activity").exception("activity_failure")
            raise

    next_interceptor = SimpleNamespace(
        execute_activity=AsyncMock(side_effect=lambda _: failing_activity())
    )

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


def test_invalid_host_header_is_not_an_issue_but_other_security_failures_are(
    transport,
):
    from django.core.exceptions import DisallowedHost, SuspiciousFileOperation
    from django.core.handlers.exception import response_for_exception
    from django.test import RequestFactory, override_settings

    with override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"]):
        request = RequestFactory().get("/", HTTP_HOST="scanner.example.invalid")
        with pytest.raises(DisallowedHost) as raised:
            request.get_host()
        # The exact path Django takes for a request with an invalid Host header.
        assert response_for_exception(request, raised.value).status_code == 400
        assert transport.events == []

        response = response_for_exception(
            request, SuspiciousFileOperation("synthetic path traversal")
        )
    assert response.status_code == 400
    assert [event["logger"] for event in transport.events] == [
        "django.security.SuspiciousFileOperation"
    ]


def test_litellm_logger_errors_are_breadcrumbs_and_our_captures_stay_issues(
    transport,
):
    # LiteLLM logs the failure on its own logger and raises that same exception
    # object to our code, which logs it again. Dedupe remembers the object from
    # LiteLLM's record before before_send drops it; the SDK must forget it after
    # the drop, or our own record of the failure would be swallowed as a
    # duplicate of an event that was never sent.
    try:
        raise ValueError("Could not deserialize key data.")
    except ValueError as error:
        logging.getLogger("LiteLLM").exception(
            "Failed to load vertex credentials. Error: %s", error
        )
        assert transport.events == []
        logging.getLogger("model_hub.utils.utils").exception("model_check_failed")
        # Dedupe still merges a second report of the object we already sent.
        sentry_sdk.capture_exception(error)

    [event] = transport.events
    assert event["logger"] == "model_hub.utils.utils"
    assert event["exception"]["values"][-1]["value"] == (
        "Could not deserialize key data."
    )
    assert any(
        crumb.get("category") == "LiteLLM"
        and "Failed to load vertex credentials" in crumb.get("message", "")
        for crumb in event["breadcrumbs"]["values"]
    )

    sentry_sdk.capture_exception(RuntimeError("synthetic genuine fault"))
    assert [event.get("logger") for event in transport.events] == [
        "model_hub.utils.utils",
        None,
    ]


def test_real_litellm_vertex_key_failure_is_not_an_issue(transport):
    pytest.importorskip("litellm")
    from litellm.llms.vertex_ai.vertex_llm_base import VertexBase

    service_account = {
        "type": "service_account",
        "project_id": "synthetic-project",
        "private_key_id": "0",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIBsynthetic\n"
        "-----END PRIVATE KEY-----\n",
        "client_email": "qa@synthetic.example.invalid",
        "client_id": "0",
        "token_uri": "http://127.0.0.1:9/token",
    }
    # LiteLLM logs "Failed to load vertex credentials" on its own logger at
    # ERROR, then raises the key-parsing error to the caller.
    with pytest.raises(ValueError):
        VertexBase().get_access_token(
            credentials=json.dumps(service_account), project_id="synthetic-project"
        )
    assert transport.events == []
    # The record is still there as context for the next genuine event.
    sentry_sdk.capture_message("after vertex validation")
    [event] = transport.events
    assert "LiteLLM" in {
        crumb.get("category") for crumb in event["breadcrumbs"]["values"]
    }


@pytest.mark.asyncio
async def test_expected_activity_denial_is_not_an_issue(transport, monkeypatch):
    from temporalio.exceptions import ApplicationError

    from tfc.temporal.common import sentry_interceptor

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)

    async def activity_fn():
        raise AssertionError("replaced per case")

    def interceptor_raising(error):
        async def execute(_):
            raise error

        return sentry_interceptor.SentryActivityInboundInterceptor(
            SimpleNamespace(execute_activity=execute)
        )

    activity_input = SimpleNamespace(fn=activity_fn, args=[])
    denial = ApplicationError(
        "Synthetic free tier limit reached",
        {"code": "FREE_TIER_LIMIT", "message": "Synthetic free tier limit reached"},
        type="EvaluationDenied",
        non_retryable=True,
    )
    with pytest.raises(ApplicationError):
        await interceptor_raising(denial).execute_activity(activity_input)
    assert transport.events == []

    genuine = [
        RuntimeError("synthetic activity fault"),
        ApplicationError("retryable", type="EvaluationDenied", non_retryable=False),
        ApplicationError(
            "other refusal", type="ProviderMisconfigured", non_retryable=True
        ),
    ]
    for error in genuine:
        with pytest.raises(type(error)):
            await interceptor_raising(error).execute_activity(activity_input)
    assert [
        event["exception"]["values"][-1]["value"] for event in transport.events
    ] == ["synthetic activity fault", "retryable", "other refusal"]
