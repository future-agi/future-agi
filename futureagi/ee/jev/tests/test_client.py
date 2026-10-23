"""R-05, R-06, R-15..R-17: org-key transport, bounded retries, no fallback.

Jev reaches TypeSafe through the AgentCC gateway by default, and calls it
directly only with JEV_TRANSPORT=direct. Behaviour both transports share is
tested on both; the gateway's request shape and error mapping on their own.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from structlog.testing import capture_logs

from ee.jev.client import JevGatewayError, JevSystemOneClient
from ee.jev.mapping import JevMappingError
from ee.licensing.managed_ai import is_managed_model

pytestmark = pytest.mark.requires_ee
ORG_KEY = "org-typesafe-key-CANARY"
INTERNAL_KEY = "internal-gateway-key-CANARY"
GATEWAY_URL = "http://agentcc-gateway.test:8080"
DECISION_CASES = (
    Path(__file__).resolve().parents[4]
    / "api_contracts"
    / "gateway"
    / "decision-cases.json"
)
OK = {
    "model": "jev-1.13.0",
    "answers": {"q1": {"type": "noul", "noul": 0.9}},
    "usage": {"input_tokens": 412, "output_tokens": 6},
}
QUESTIONS = {"q1": {"type": "noul", "instructions": "Judge"}}
# Error bodies echo both keys, as a careless upstream might; none may surface.
ECHO = f"PRIVATE-CANARY {ORG_KEY} {INTERNAL_KEY}"
GATEWAY_ENV = (
    "AGENTCC_GATEWAY_INTERNAL_URL",
    "AGENTCC_INTERNAL_URL",
    "AGENTCC_GATEWAY_URL",
    "AGENTCC_INTERNAL_API_KEY",
)


@pytest.fixture(autouse=True)
def gateway(monkeypatch, settings):
    """A configured gateway and the default transport, whatever the host's
    environment says. Every call is patched; nothing reaches a network."""
    for name in GATEWAY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_GATEWAY_INTERNAL_URL", GATEWAY_URL + "/")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", INTERNAL_KEY)
    if hasattr(settings, "AGENTCC_INTERNAL_API_KEY"):
        del settings.AGENTCC_INTERNAL_API_KEY
    settings.JEV_TRANSPORT = "gateway"


@pytest.fixture(params=["gateway", "direct"])
def transport(request, settings):
    settings.JEV_TRANSPORT = request.param
    return request.param


def client(**kwargs):
    return JevSystemOneClient(api_key=ORG_KEY, **kwargs)


def reply(status, code=None, headers=None):
    return httpx.Response(
        status,
        json=(
            {
                "error": {
                    "type": "upstream_error",
                    "code": code,
                    "message": ECHO,
                    "param": None,
                }
            }
            if code
            else OK
        ),
        headers=headers,
    )


def assert_no_secret(*values):
    for value in values:
        text = value if isinstance(value, str) else json.dumps(value, default=repr)
        for secret in (ORG_KEY, INTERNAL_KEY, "PRIVATE-CANARY"):
            assert secret not in text


def assert_sanitized(error):
    exc = error.value
    assert_no_secret(str(exc), repr(exc), [str(arg) for arg in exc.args])
    assert exc.__cause__ is None
    assert exc.__suppress_context__ or exc.__context__ is None


# --- the gateway transport (default) ----------------------------------------


@pytest.mark.parametrize("model", ["jev-latest", "jev-1.13.0"])
def test_r05_gateway_request_is_exact(model):
    with patch("httpx.post", return_value=reply(200)) as post:
        result = client().evaluate(model, {"input": "value"}, QUESTIONS, timeout=35)
    assert result == OK
    assert post.call_count == 1
    assert post.call_args.args == (f"{GATEWAY_URL}/v1/systemone",)
    assert post.call_args.kwargs == {
        "json": {
            "model": f"typesafe/{model}",
            "state": {"input": "value"},
            "questions": QUESTIONS,
        },
        "timeout": 35,
        "follow_redirects": False,
        "headers": {
            "Authorization": f"Bearer {INTERNAL_KEY}",
            "x-agentcc-provider-key": ORG_KEY,
            "Content-Type": "application/json",
        },
    }
    # The org's key travels in its one header, never in the body.
    assert ORG_KEY not in json.dumps(post.call_args.kwargs["json"])


@pytest.mark.parametrize(
    "env,url",
    [
        ({"AGENTCC_GATEWAY_INTERNAL_URL": "http://gw:8080"}, "http://gw:8080"),
        ({"AGENTCC_INTERNAL_URL": "http://legacy-gw:8080/"}, "http://legacy-gw:8080"),
        ({}, "http://agentcc-gateway:8080"),
    ],
)
def test_gateway_url_comes_from_resolve_gateway_internal_url(monkeypatch, env, url):
    monkeypatch.delenv("AGENTCC_GATEWAY_INTERNAL_URL")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with patch("httpx.post", return_value=reply(200)) as post:
        client().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert post.call_args.args == (f"{url}/v1/systemone",)


def test_internal_key_may_come_from_settings(monkeypatch, settings):
    monkeypatch.delenv("AGENTCC_INTERNAL_API_KEY")
    settings.AGENTCC_INTERNAL_API_KEY = "settings-internal-key"
    with patch("httpx.post", return_value=reply(200)) as post:
        client().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer settings-internal-key"


def test_typesafe_base_url_never_moves_gateway_traffic(settings):
    settings.TYPESAFE_BASE_URL = "https://typesafe.example/"
    with patch("httpx.post", return_value=reply(200)) as post:
        client().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert post.call_args.args == (f"{GATEWAY_URL}/v1/systemone",)


@pytest.mark.parametrize("value", [None, "gateway", "", "gatewy", "managed"])
def test_direct_transport_only_when_asked(settings, value):
    if value is None:
        del settings.JEV_TRANSPORT
    else:
        settings.JEV_TRANSPORT = value
    with patch("httpx.post", return_value=reply(200)) as post:
        client().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert post.call_args.args == (f"{GATEWAY_URL}/v1/systemone",)
    assert post.call_args.kwargs["headers"]["x-agentcc-provider-key"] == ORG_KEY


@pytest.mark.parametrize("internal_key", [None, "", "   "])
def test_missing_internal_key_fails_without_a_call(monkeypatch, settings, internal_key):
    if internal_key is None:
        monkeypatch.delenv("AGENTCC_INTERNAL_API_KEY")
    else:
        monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", internal_key)
    # A TypeSafe URL is configured, so a silent fallback would have somewhere
    # to go. It must not take it.
    settings.TYPESAFE_BASE_URL = "https://typesafe.example"
    sleep = Mock()
    with (
        patch("httpx.post") as post,
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error,
    ):
        client(sleep=sleep).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    post.assert_not_called()
    sleep.assert_not_called()
    assert_sanitized(error)
    [line] = [log for log in logs if log["event"] == "jev_gateway_not_configured"]
    assert line["log_level"] == "error"
    assert line["internal_key_set"] is False
    assert_no_secret(logs)


def test_missing_gateway_url_fails_without_a_call():
    with (
        patch(
            "agentcc.services.gateway_client.resolve_gateway_internal_url",
            return_value="",
        ),
        patch("httpx.post") as post,
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error,
    ):
        client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    post.assert_not_called()
    [line] = [log for log in logs if log["event"] == "jev_gateway_not_configured"]
    assert line["gateway_url_set"] is False
    assert_no_secret(logs)


def test_missing_org_key_is_reported_before_gateway_config(monkeypatch):
    monkeypatch.delenv("AGENTCC_INTERNAL_API_KEY")
    with (
        patch("httpx.post") as post,
        pytest.raises(JevGatewayError, match="JEV_KEY_MISSING"),
    ):
        JevSystemOneClient().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    post.assert_not_called()


# The spec's gateway error table. The code decides first, then the status.
GATEWAY_ERRORS = [
    (403, "provider_key_rejected", "JEV_KEY_INVALID", False),
    (403, "byok_required", "JEV_PROVIDER_ERROR", False),
    (403, "decision_provider_not_configured", "JEV_PROVIDER_ERROR", False),
    (400, "provider_key_header_not_allowed", "JEV_PROVIDER_ERROR", False),
    (429, "rate_limit_exceeded", "JEV_RATE_LIMITED", True),
    (503, "provider_overloaded", "JEV_PROVIDER_ERROR", True),
    (502, "upstream_error", "JEV_PROVIDER_ERROR", True),
    # TypeSafe answered 200, so the org's key was billed: never retried.
    (502, "upstream_invalid_response", "JEV_PROVIDER_ERROR", False),
    (504, "gateway_timeout", "JEV_TIMEOUT", True),
    (400, "upstream_rejected", "JEV_PROVIDER_ERROR", False),
    (400, "invalid_request", "JEV_PROVIDER_ERROR", False),
    (402, "provider_payment_required", "JEV_PROVIDER_ERROR", False),
    (403, "decision_provider_wrong_format", "JEV_PROVIDER_ERROR", False),
    (404, "model_not_found", "JEV_PROVIDER_ERROR", False),
    (413, "request_too_large", "JEV_PROVIDER_ERROR", False),
    (422, "upstream_rejected", "JEV_PROVIDER_ERROR", False),
    (422, "format_mismatch", "JEV_PROVIDER_ERROR", False),
    # Code before status: a refusal stays final whatever status carries it.
    (503, "byok_required", "JEV_PROVIDER_ERROR", False),
    (429, "provider_key_rejected", "JEV_KEY_INVALID", False),
    # Statuses the table does not map are final. A 401 is the gateway refusing
    # the internal key, never the org's key.
    (401, "invalid_api_key", "JEV_PROVIDER_ERROR", False),
    (403, None, "JEV_PROVIDER_ERROR", False),
    (500, "internal_error", "JEV_PROVIDER_ERROR", False),
]


@pytest.mark.parametrize("status,code,expected,retryable", GATEWAY_ERRORS)
def test_gateway_error_mapping(status, code, expected, retryable):
    sleep = Mock()
    headers = {"Retry-After": "2", "x-agentcc-upstream-status": "500"}
    with (
        patch("httpx.post", return_value=reply(status, code, headers)) as post,
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match=expected) as error,
    ):
        jev = client(sleep=sleep)
        jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert error.value.code == expected
    assert error.value.retryable is retryable
    if retryable:
        assert error.value.retry_after == "2"
        assert post.call_count == 3
        assert [call.args[0] for call in sleep.call_args_list] == [2, 2]
    else:
        assert post.call_count == 1
        sleep.assert_not_called()
    assert [attempt["code"] for attempt in jev.attempts] == [expected] * (
        post.call_count
    )
    # Every refusal leaves the gateway's own status and code in the log, so a
    # rotated internal key or an old gateway is not read as TypeSafe failing.
    lines = [log for log in logs if log["event"] == "jev_gateway_error"]
    assert [
        (line["log_level"], line["status"], line["code"], line["upstream_status"])
        for line in lines
    ] == [("warning", status, code, 500)] * post.call_count
    assert_sanitized(error)
    assert_no_secret(jev.attempts, logs)


@pytest.mark.parametrize(
    "upstream_status,retryable",
    [
        # What the direct transport retries when TypeSafe answers with it.
        ("500", True),
        ("502", True),
        # Final there, so final here: a retry cannot help, and could bill the
        # org's key again.
        ("501", False),
        ("415", False),
        ("301", False),
        # No answer from TypeSafe at all: retried, as a dropped connection is.
        (None, True),
        ("not-a-status", True),
    ],
)
def test_gateway_502_is_retried_only_where_typesafe_would_be(
    upstream_status, retryable
):
    headers = {"x-agentcc-upstream-status": upstream_status} if upstream_status else {}
    with patch(
        "httpx.post", return_value=reply(502, "upstream_error", headers)
    ) as post:
        with pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error:
            client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert error.value.retryable is retryable
    assert post.call_count == (3 if retryable else 1)


@pytest.mark.parametrize("status", [500, 501, 415, 302])
def test_gateway_502_matches_the_direct_transport(settings, status):
    """The same TypeSafe answer gets the same outcome on both transports."""
    outcomes = []
    for transport, response in (
        ("direct", reply(status, "upstream_failure")),
        (
            "gateway",
            reply(502, "upstream_error", {"x-agentcc-upstream-status": str(status)}),
        ),
    ):
        settings.JEV_TRANSPORT = transport
        with patch("httpx.post", return_value=response) as post:
            with pytest.raises(JevGatewayError) as error:
                client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
        outcomes.append((error.value.code, error.value.retryable, post.call_count))
    assert outcomes[0] == outcomes[1]


def test_gateway_error_log_never_carries_an_unexpected_code():
    """Only a well-formed gateway code is read, and so logged."""
    body = {"error": {"code": f"provider_key_rejected {ORG_KEY}", "message": ECHO}}
    with (
        patch("httpx.post", return_value=httpx.Response(401, json=body)),
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR"),
    ):
        client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    [line] = [log for log in logs if log["event"] == "jev_gateway_error"]
    assert (line["status"], line["code"], line["upstream_status"]) == (401, None, None)
    assert_no_secret(logs)


def test_provider_key_rejected_is_not_retried():
    sleep = Mock()
    with patch(
        "httpx.post",
        side_effect=[reply(403, "provider_key_rejected"), reply(200)],
    ) as post:
        with pytest.raises(JevGatewayError, match="JEV_KEY_INVALID") as error:
            client(sleep=sleep).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    assert post.call_count == 1
    sleep.assert_not_called()


FIRST_ID, SECOND_ID = "01JAQ8Z3V5W6X7Y8Z9A0B1C2D3", "01JAQ8Z4V5W6X7Y8Z9A0B1C2D4"


def test_each_attempt_keeps_the_gateways_request_id():
    """A disputed answer, or a failed attempt, can be found in the gateway's
    logs by the id it answered with."""
    with (
        patch(
            "httpx.post",
            side_effect=[
                reply(503, "provider_overloaded", {"x-agentcc-request-id": FIRST_ID}),
                reply(200, headers={"x-agentcc-request-id": SECOND_ID}),
            ],
        ),
        capture_logs() as logs,
    ):
        jev = client(sleep=Mock())
        assert jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35) == OK
    assert [a["gateway_request_id"] for a in jev.attempts] == [FIRST_ID, SECOND_ID]
    assert jev.gateway_request_id == SECOND_ID
    [line] = [log for log in logs if log["event"] == "jev_gateway_error"]
    assert line["gateway_request_id"] == FIRST_ID


@pytest.mark.parametrize(
    "header", [f"{FIRST_ID} {ORG_KEY}", "a" * 65, "", "../id;drop"]
)
def test_a_request_id_that_is_not_one_is_not_kept(header):
    with patch(
        "httpx.post", return_value=reply(200, headers={"x-agentcc-request-id": header})
    ):
        jev = client()
        jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert jev.gateway_request_id is None
    assert jev.attempts[0]["gateway_request_id"] is None
    assert_no_secret(jev.attempts)


def test_the_direct_transport_has_no_gateway_request_id(settings):
    settings.JEV_TRANSPORT = "direct"
    with patch(
        "httpx.post",
        return_value=reply(200, headers={"x-agentcc-request-id": FIRST_ID}),
    ):
        jev = client()
        jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert jev.gateway_request_id is None


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"PRIVATE-CANARY", "not_json"),
        (b'["PRIVATE-CANARY"]', "not_object"),
        (b'{"error": {"message": "PRIVATE-CANARY"}}', "error_body"),
        (b" " * (10 * 1024 * 1024 + 1), "too_large"),
    ],
)
def test_an_unusable_answer_is_logged_with_the_gateways_request_id(content, reason):
    """A 200 whose body can't be used fails the answer; the log keeps the
    gateway's id for the call, so what came back can be found in its logs."""
    response = httpx.Response(
        200, content=content, headers={"x-agentcc-request-id": FIRST_ID}
    )
    with (
        patch("httpx.post", return_value=response),
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error,
    ):
        client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    assert_sanitized(error)
    [line] = [log for log in logs if log["event"] == "jev_invalid_response"]
    assert line == {
        "event": "jev_invalid_response",
        "log_level": "warning",
        "reason": reason,
        "code": "JEV_PROVIDER_ERROR",
        "attempt": 1,
        "gateway_request_id": FIRST_ID,
    }
    assert_no_secret(logs)


def test_an_unusable_direct_answer_is_logged_without_a_request_id(settings):
    settings.JEV_TRANSPORT = "direct"
    response = httpx.Response(
        200, json=["PRIVATE-CANARY"], headers={"x-agentcc-request-id": FIRST_ID}
    )
    with (
        patch("httpx.post", return_value=response),
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR"),
    ):
        client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    [line] = [log for log in logs if log["event"] == "jev_invalid_response"]
    assert (line["reason"], line["gateway_request_id"]) == ("not_object", None)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"error": "flat string"}',
        b'{"error": {"code": 7}}',
        b'{"error": {"code": "provider_key_rejected"}}' + b" " * (10 * 1024 * 1024),
    ],
)
def test_unreadable_gateway_error_falls_back_to_its_status(body):
    with patch("httpx.post", return_value=httpx.Response(403, content=body)) as post:
        with pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error:
            client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    assert post.call_count == 1


def _table(status, code, upstream_status=None):
    """The Jev side of the spec's table, written out independently of the
    client so the shared fixture checks one against the other. A 502 carrying
    TypeSafe's own answer is retried only where the direct transport would
    retry that answer."""
    if code == "provider_key_rejected":
        return "JEV_KEY_INVALID", False
    if code in (
        "byok_required",
        "decision_provider_not_configured",
        "provider_key_header_not_allowed",
    ):
        return "JEV_PROVIDER_ERROR", False
    if status == 429:
        return "JEV_RATE_LIMITED", True
    if status == 504:
        return "JEV_TIMEOUT", True
    if status == 502 and upstream_status is not None:
        return "JEV_PROVIDER_ERROR", upstream_status in (500, 502, 503)
    return "JEV_PROVIDER_ERROR", status in (502, 503)


def _shared_cases():
    """The hand-written gateway contract fixture the Go tests also read."""
    return json.loads(DECISION_CASES.read_text()) if DECISION_CASES.exists() else None


SHARED = _shared_cases()
NO_SHARED = pytest.mark.skipif(
    SHARED is None, reason=f"{DECISION_CASES.name} is not in this checkout"
)


def _shared_error_cases():
    """What the gateway returns: after a vendor's answer, or on its own."""
    if SHARED is None:
        return [pytest.param(None, marks=NO_SHARED, id="no-fixture")]
    return [
        pytest.param(case, id=case["name"])
        for key in ("error_cases", "gateway_error_cases")
        for case in SHARED[key]
    ]


@NO_SHARED
def test_shared_request_is_what_the_client_sends():
    request = SHARED["request"]
    provider, model = request["model"].split("/", 1)
    assert provider == "typesafe"
    with patch("httpx.post", return_value=reply(200)) as post:
        JevSystemOneClient(api_key=SHARED["api_key"]).evaluate(
            model, request["state"], request["questions"], timeout=35
        )
    assert post.call_args.kwargs["json"] == request
    headers = post.call_args.kwargs["headers"]
    assert headers["x-agentcc-provider-key"] == SHARED["api_key"]
    assert headers["Authorization"] == f"Bearer {INTERNAL_KEY}"


@pytest.mark.parametrize("case", _shared_error_cases())
def test_shared_gateway_error_case(case):
    returned = case["returned"]
    status, code = returned["status"], returned["code"]
    retry_after = returned.get("retry_after")
    upstream_status = case["upstream"]["status"] if "upstream" in case else None
    expected, retryable = _table(status, code, upstream_status)
    headers = {"Retry-After": retry_after} if retry_after else {}
    if "upstream" in case:
        headers["x-agentcc-upstream-status"] = str(case["upstream"]["status"])
    body = {
        "error": {
            "type": "upstream_error",
            "code": code,
            # The fixture's safe message where it has one; otherwise one that
            # echoes every secret the request carried.
            "message": returned.get("message")
            or f"{SHARED['api_key']} {INTERNAL_KEY} private-state-CANARY",
            "param": None,
        }
    }
    sleep = Mock()
    request = SHARED["request"]
    with (
        patch(
            "httpx.post",
            return_value=httpx.Response(status, json=body, headers=headers),
        ) as post,
        capture_logs() as logs,
        pytest.raises(JevGatewayError) as error,
    ):
        jev = JevSystemOneClient(
            api_key=SHARED["api_key"], sleep=sleep, jitter=lambda upper: upper
        )
        jev.evaluate("jev-1.13.0", request["state"], request["questions"], timeout=35)
    assert (error.value.code, error.value.retryable) == (expected, retryable)
    if retryable:
        assert post.call_count == 3
        assert error.value.retry_after == retry_after
        # Retry-After is honoured up to 10s; past that the jittered backoff wins.
        delay = float(retry_after) if retry_after and float(retry_after) <= 10 else None
        assert [call.args[0] for call in sleep.call_args_list] == (
            [delay, delay] if delay is not None else [1, 2]
        )
    else:
        assert post.call_count == 1
        sleep.assert_not_called()
    recorded = json.dumps(
        [str(error.value), repr(error.value), jev.attempts, logs], default=repr
    )
    assert SHARED["api_key"] not in recorded
    assert INTERNAL_KEY not in recorded
    assert "CANARY" not in recorded


# --- what Sentry would report ---------------------------------------------------


def _sentry_event(call):
    """The event Sentry would build for the error `call` raises, with the
    backend's settings: frame locals on (init_sentry sets
    include_local_variables), then its scrubber and before_send. Returns the
    module the error was raised in, and the event before any scrubbing and as
    it would be sent."""
    import sys

    import sentry_sdk.consts
    from sentry_sdk.utils import event_from_exception

    from tfc.logging.sentry import _event_scrubber, _get_before_send

    try:
        call()
    except JevGatewayError:
        exc_info = sys.exc_info()
    event, hint = event_from_exception(
        exc_info,
        client_options={
            **sentry_sdk.consts.DEFAULT_OPTIONS,
            "include_local_variables": True,
            "include_source_context": False,
        },
    )
    [value] = event["exception"]["values"]
    raised_in = value["stacktrace"]["frames"][-1]["module"]
    raw = json.dumps(event, default=repr)
    _event_scrubber().scrub_event(event)
    return raised_in, raw, json.dumps(_get_before_send()(event, hint), default=repr)


@pytest.mark.parametrize(
    "outcome",
    [
        reply(403, "provider_key_rejected"),
        reply(503, "provider_overloaded"),
        reply(502, "upstream_invalid_response", {"x-agentcc-upstream-status": "200"}),
        reply(401, "invalid_api_key"),
        httpx.ConnectError("unreachable"),
        httpx.TimeoutException("slow"),
    ],
    ids=["rejected", "overloaded", "invalid", "internal-key", "connect", "timeout"],
)
def test_a_failed_call_leaves_no_key_in_what_sentry_reports(transport, outcome):
    """A failed run is logged with logger.exception, which Sentry reports with
    every frame's locals. Its scrubber matches whole names, and
    x-agentcc-provider-key was not one of them, so neither key may sit in a
    local of any frame the error keeps."""
    patched = (
        {"side_effect": outcome}
        if isinstance(outcome, Exception)
        else {"return_value": outcome}
    )
    with patch("httpx.post", **patched) as post:
        raised_in, raw, sent = _sentry_event(
            lambda: client(sleep=Mock(), jitter=lambda _: 0).evaluate(
                "jev-latest", "x", QUESTIONS, timeout=35
            )
        )
    assert post.call_count >= 1
    assert raised_in == "ee.jev.client"
    for event in (raw, sent):
        assert ORG_KEY not in event
        assert INTERNAL_KEY not in event


def test_an_unconfigured_gateway_leaves_no_key_in_what_sentry_reports():
    """The internal key is set but the gateway URL is not: the error is raised
    where the internal key is read."""
    with (
        patch(
            "agentcc.services.gateway_client.resolve_gateway_internal_url",
            return_value="",
        ),
        patch("httpx.post") as post,
    ):
        raised_in, raw, sent = _sentry_event(
            lambda: client(sleep=Mock()).evaluate(
                "jev-latest", "x", QUESTIONS, timeout=35
            )
        )
    post.assert_not_called()
    assert raised_in == "ee.jev.client"
    for event in (raw, sent):
        assert ORG_KEY not in event
        assert INTERNAL_KEY not in event


# --- the direct transport (JEV_TRANSPORT=direct) ------------------------------


def test_r05_direct_calls_typesafe_with_the_org_key(settings):
    settings.JEV_TRANSPORT = "direct"
    with patch("httpx.post", return_value=reply(200)) as post:
        result = client().evaluate(
            "jev-latest", {"input": "value"}, QUESTIONS, timeout=35
        )
    assert result == OK
    assert post.call_args.args == ("https://api.typesafe.ai/v1/systemone",)
    assert post.call_args.kwargs == {
        "json": {
            "model": "jev-latest",
            "state": {"input": "value"},
            "questions": QUESTIONS,
        },
        "timeout": 35,
        "follow_redirects": False,
        # The internal key never leaves the backend on this path.
        "headers": {
            "Authorization": f"Bearer {ORG_KEY}",
            "Content-Type": "application/json",
        },
    }


def test_r05_direct_base_url_setting(settings):
    settings.JEV_TRANSPORT = "direct"
    settings.TYPESAFE_BASE_URL = "https://typesafe.example/"
    with patch("httpx.post", return_value=reply(200)) as post:
        client().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert post.call_args.args == ("https://typesafe.example/v1/systemone",)


def test_direct_transport_needs_no_gateway(monkeypatch, settings):
    settings.JEV_TRANSPORT = "direct"
    for name in GATEWAY_ENV:
        monkeypatch.delenv(name, raising=False)
    with patch("httpx.post", return_value=reply(200)) as post:
        assert client().evaluate("jev-latest", "x", QUESTIONS, timeout=35) == OK
    assert post.call_count == 1


@pytest.mark.parametrize("status", [401, 403])
def test_direct_rejected_org_key_is_not_retried(settings, status):
    settings.JEV_TRANSPORT = "direct"
    sleep = Mock()
    with patch("httpx.post", return_value=reply(status, "invalid_api_key")) as post:
        with pytest.raises(JevGatewayError, match="JEV_KEY_INVALID") as error:
            client(sleep=sleep).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert_sanitized(error)
    assert post.call_count == 1
    sleep.assert_not_called()


# --- both transports ------------------------------------------------------------


def test_missing_org_key_never_calls_out(transport):
    with (
        patch("httpx.post") as post,
        pytest.raises(JevGatewayError, match="JEV_KEY_MISSING") as error,
    ):
        JevSystemOneClient().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    post.assert_not_called()


@pytest.mark.parametrize(
    "model", ["jev-preview", "jev-2.0.0", "JEV-LATEST", "turing_large"]
)
def test_r06_unknown_model_zero_transport(transport, model):
    with (
        patch("httpx.post") as post,
        pytest.raises(JevMappingError, match="JEV_MODEL_UNKNOWN"),
    ):
        client().evaluate(model, "x", QUESTIONS, timeout=35)
    post.assert_not_called()


@pytest.mark.parametrize(
    "transport,status,code",
    [
        ("direct", 400, "upstream_rejected"),
        ("direct", 402, "payment_required"),
        ("direct", 404, "model_not_found"),
        ("direct", 413, "request_too_large"),
        ("direct", 422, "invalid_question"),
        ("direct", 501, "not_supported"),
        ("gateway", 400, "invalid_request"),
        ("gateway", 402, "provider_payment_required"),
        ("gateway", 404, "model_not_found"),
        ("gateway", 413, "request_too_large"),
        ("gateway", 422, "invalid_question"),
        ("gateway", 501, "not_supported"),
    ],
    indirect=["transport"],
)
def test_r15_r16_no_retry_or_content_leak(transport, status, code):
    sleep = Mock()
    with patch("httpx.post", return_value=reply(status, code)) as post:
        with pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error:
            client(sleep=sleep).evaluate(
                "jev-latest", "PRIVATE-CANARY", QUESTIONS, timeout=35
            )
    assert not error.value.retryable
    assert_sanitized(error)
    assert post.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "transport,status,code",
    [
        ("direct", 429, "rate_limit_exceeded"),
        ("direct", 500, "server_error"),
        ("direct", 502, "bad_gateway"),
        ("direct", 503, "overloaded"),
        ("direct", 504, "timeout"),
        ("gateway", 429, "rate_limit_exceeded"),
        ("gateway", 502, "upstream_error"),
        ("gateway", 503, "provider_overloaded"),
        ("gateway", 504, "gateway_timeout"),
    ],
    indirect=["transport"],
)
def test_r16_transient_retry_success(transport, status, code):
    sleep = Mock()
    with patch(
        "httpx.post",
        side_effect=[reply(status, code, {"Retry-After": "2"}), reply(200)],
    ) as post:
        jev = client(sleep=sleep)
        assert jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35) == OK
    assert post.call_count == 2
    sleep.assert_called_once_with(2)
    assert jev.attempt == 2
    assert jev.attempts[0]["outcome"] == "error"
    assert jev.attempts[1]["usage"]["output_tokens"] == 6
    assert_no_secret(jev.attempts)


def test_r16_retry_exhaustion_and_bounded_jitter(transport):
    sleep = Mock()
    with patch(
        "httpx.post",
        return_value=reply(429, "rate_limit_exceeded", {"Retry-After": "11"}),
    ) as post:
        with pytest.raises(JevGatewayError, match="JEV_RATE_LIMITED"):
            client(sleep=sleep, jitter=lambda upper: upper).evaluate(
                "jev-latest", "x", QUESTIONS, timeout=35
            )
    assert post.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError(f"PRIVATE-CANARY {ORG_KEY} {INTERNAL_KEY}"),
        httpx.TimeoutException(f"PRIVATE-CANARY {ORG_KEY} {INTERNAL_KEY}"),
    ],
)
def test_r16_transport_retries(transport, error):
    with patch("httpx.post", side_effect=[error, reply(200)]) as post:
        assert (
            client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
            == OK
        )
    assert post.call_count == 2


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ConnectError(ECHO), "JEV_PROVIDER_ERROR"),
        (httpx.TimeoutException(ECHO), "JEV_TIMEOUT"),
    ],
)
def test_unreachable_upstream_is_retried_then_sanitized(transport, error, code):
    with (
        patch("httpx.post", side_effect=error) as post,
        capture_logs() as logs,
        pytest.raises(JevGatewayError, match=code) as raised,
    ):
        jev = client(sleep=Mock(), jitter=lambda _: 0)
        jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert raised.value.retryable
    assert post.call_count == 3
    assert_sanitized(raised)
    assert_no_secret(jev.attempts, logs)


def test_r16_total_deadline_and_cancel(transport):
    now = [0.0]

    def delayed(*args, **kwargs):
        assert kwargs["timeout"] <= 35
        now[0] += kwargs["timeout"]
        raise httpx.TimeoutException("slow")

    with patch("httpx.post", side_effect=delayed) as post:
        with pytest.raises(JevGatewayError, match="JEV_TIMEOUT"):
            client(
                clock=lambda: now[0],
                sleep=lambda delay: now.__setitem__(0, now[0] + delay),
                jitter=lambda _: 0,
            ).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert now[0] == 90
    assert post.call_count == 3
    with (
        patch("httpx.post", side_effect=KeyboardInterrupt) as post,
        pytest.raises(KeyboardInterrupt),
    ):
        client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert post.call_count == 1


def test_a_200_read_past_the_deadline_is_kept_not_retried(transport):
    """httpx timeouts are per operation, so a slow read can land just past the
    total deadline. The answer was charged to the org's key: it is final."""
    now = [0.0]

    def slow_ok(*args, **kwargs):
        now[0] = 90.5
        return reply(200)

    with patch("httpx.post", side_effect=slow_ok) as post:
        jev = client(clock=lambda: now[0], sleep=Mock(), jitter=lambda _: 0)
        assert jev.evaluate("jev-latest", "x", QUESTIONS, timeout=35) == OK
    assert post.call_count == 1
    assert [a["outcome"] for a in jev.attempts] == ["success"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"PRIVATE-CANARY"),
        httpx.Response(200, json=["PRIVATE-CANARY"]),
        httpx.Response(200, json={"error": {"message": "PRIVATE-CANARY"}}),
        httpx.Response(200, json={"error": {"code": "provider_key_rejected"}}),
        httpx.Response(200, content=b" " * (10 * 1024 * 1024 + 1)),
    ],
)
def test_r12_malformed_error_or_oversized_body_is_sanitized_error(transport, response):
    with patch("httpx.post", return_value=response) as post:
        with pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error:
            client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    assert_sanitized(error)
    assert post.call_count == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_refused(transport, status):
    response = httpx.Response(status, headers={"Location": "https://elsewhere.test"})
    with patch("httpx.post", return_value=response) as post:
        with pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR") as error:
            client(sleep=Mock()).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert not error.value.retryable
    assert post.call_count == 1
    assert post.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize("model", ["jev-latest", "jev-1.13.0"])
def test_jev_is_not_a_futureagi_managed_model(model):
    # Third-party models are bring your own key; only Turing models run on
    # FutureAGI credentials, so Jev must never route through the managed gateway.
    assert not is_managed_model(model)


def test_r16_concurrency_bound(transport):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    lock = threading.Lock()
    active = 0
    maximum = 0

    def dispatch(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
        sleep(0.02)
        with lock:
            active -= 1
        return reply(200)

    with (
        patch("httpx.post", side_effect=dispatch),
        ThreadPoolExecutor(max_workers=20) as pool,
    ):
        results = list(
            pool.map(
                lambda _: client().evaluate("jev-latest", "x", QUESTIONS, timeout=35),
                range(20),
            )
        )
    assert len(results) == 20
    assert 1 <= maximum <= 8
