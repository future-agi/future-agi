"""Tests for Sentry noise filtering and PII scrubbing in `tfc.logging.sentry`.

These exercise the pure `before_send` pipeline and the scrubbing helpers, which
import without the Sentry SDK or Django settings. They lock in the two things
that keep the issue stream clean and safe:

- Infrastructure / expected events are dropped (logger prefix, ignored
  exception types, expected 4xx, expected log messages).
- Real errors survive, with their secrets/PII redacted before send.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import pickle
import random
import secrets
import signal
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import redis
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.scope import ScopeType, use_isolation_scope, use_scope
from sentry_sdk.transport import Transport
from sentry_sdk.utils import iter_event_frames
from temporalio.worker import ExecuteActivityInput

from tfc.logging import sentry as sentry_module
from tfc.logging.sentry import (
    _MAX_FORMATTED,
    _REDACTED,
    _event_scrubber,
    _formatted,
    _get_before_send,
    _is_sensitive_key,
    _scrub,
    _scrub_deployment_telemetry_event,
    _scrub_event,
    _scrub_strings,
    _template_text,
)
from tfc.temporal.common.sentry_interceptor import SentryActivityInboundInterceptor


@pytest.fixture(autouse=True)
def _log_at_info(caplog):
    # The log-based tests need INFO and ERROR records from logger "x", whatever
    # level an earlier test left behind.
    caplog.set_level(logging.INFO, logger="x")


before_send = _get_before_send()


class _Exc(Exception):
    """Exception carrying a status_code attribute, like DRF/HTTP exceptions."""

    def __init__(self, status_code):
        super().__init__("boom")
        self.status_code = status_code


def _hint(exc):
    return {"exc_info": (type(exc), exc, None)}


# Noise: dropped


def test_drops_opentelemetry_logger_events():
    event = {"logger": "opentelemetry.sdk.metrics._internal.export", "message": "x"}
    assert before_send(event, {}) is None


def test_drops_opentelemetry_child_loggers_by_prefix():
    event = {"logger": "opentelemetry.exporter.otlp.proto.grpc.exporter"}
    assert before_send(event, {}) is None


def test_drops_saml_dependency_loggers_but_keeps_saml2_auth():
    """The pysaml2 prefix must not swallow the app's own saml2_auth loggers."""

    assert before_send({"logger": "saml2.response", "message": "x"}, {}) is None
    for name in ("saml2_auth.views", "saml2_auth.security"):
        assert before_send({"logger": name, "message": "boom"}, {}) is not None


def test_drops_ignored_exception_types():
    assert before_send({}, _hint(ConnectionResetError(104, "reset"))) is None
    assert before_send({}, _hint(BrokenPipeError())) is None


def test_drops_expected_4xx_but_keeps_auth_failures():
    assert before_send({}, _hint(_Exc(404))) is None
    assert before_send({}, _hint(_Exc(429))) is None
    # 401/403 are kept for security monitoring.
    assert before_send({}, _hint(_Exc(401))) is not None
    assert before_send({}, _hint(_Exc(403))) is not None
    # 5xx is always kept.
    assert before_send({}, _hint(_Exc(500))) is not None


def test_drops_known_expected_messages():
    event = {"message": "trace_payload_not_found_in_redis"}
    assert before_send(event, {}) is None


# Real errors: kept


def test_keeps_real_application_errors():
    event = {"logger": "tracer.utils.eval", "message": "unexpected boom"}
    assert before_send(event, {}) is not None


def test_keeps_real_exception():
    assert before_send({}, _hint(ValueError("genuine bug"))) is not None


# Scrubbing


def test_sensitive_key_detection():
    assert _is_sensitive_key("Authorization")
    assert _is_sensitive_key("X-Api-Key")
    assert _is_sensitive_key("password")
    assert _is_sensitive_key("environment_values")
    assert _is_sensitive_key("credential_values")
    # Loops over submitted values name their local for this rule.
    assert _is_sensitive_key("secret_value")
    assert not _is_sensitive_key("user_id")


def test_scrub_redacts_an_agent_check_payloads_values():
    scrubbed = _scrub(
        {"agent": {"connector": "livekit"}, "credential_values": {"K": "v"}}
    )
    assert scrubbed == {
        "agent": {"connector": "livekit"},
        "credential_values": _REDACTED,
    }


def test_scrub_redacts_nested_secrets():
    scrubbed = _scrub({"ok": 1, "token": "abc", "nested": {"password": "p"}})
    assert scrubbed["ok"] == 1
    assert scrubbed["token"] == _REDACTED
    assert scrubbed["nested"]["password"] == _REDACTED


def test_before_send_scrubs_request_and_extra():
    event = {
        "logger": "tracer.views.foo",
        "message": "real error",
        "request": {
            "headers": {"Authorization": "Bearer secret", "Accept": "json"},
            "cookies": {"sessionid": "xyz"},
            "query_string": "api_key=leak&page=1",
        },
        "extra": {"api_key": "leak", "count": 5},
    }
    out = before_send(event, {})
    assert out is not None
    assert out["request"]["headers"]["Authorization"] == _REDACTED
    assert out["request"]["headers"]["Accept"] == "json"
    assert out["request"]["query_string"] == _REDACTED
    assert out["request"]["cookies"] == _REDACTED  # cookies redacted wholesale
    assert out["extra"]["api_key"] == _REDACTED
    assert out["extra"]["count"] == 5


def test_scrubber_redacts_the_gateway_provider_key_in_frame_locals():
    """The scrubber init_sentry installs matches whole key names, so the AgentCC
    header that carries an org's own provider key must be listed in full."""
    headers = {
        "Authorization": "Bearer internal-secret",
        "X-AgentCC-Provider-Key": "org-provider-secret",
        "Content-Type": "application/json",
    }
    event = {
        "exception": {
            "values": [{"stacktrace": {"frames": [{"vars": {"headers": headers}}]}}]
        },
        "request": {"headers": dict(headers)},
    }
    _event_scrubber().scrub_event(event)
    [value] = event["exception"]["values"]
    scrubbed = value["stacktrace"]["frames"][0]["vars"]["headers"]
    assert scrubbed["Content-Type"] == "application/json"
    for sent in (scrubbed, event["request"]["headers"]):
        assert "internal-secret" not in str(sent)
        assert "org-provider-secret" not in str(sent)
    # The substring scrubber used by before_send agrees.
    assert _scrub(headers)["X-AgentCC-Provider-Key"] == _REDACTED


def test_scrub_event_is_resilient_to_unexpected_shapes():
    # Non-dict request/extra must not raise.
    event = {"request": "not-a-dict", "extra": None, "message": "x"}
    _scrub_event(event)  # should be a no-op, not an error


def test_before_send_handles_unexpected_logentry_shape():
    event = {"logger": "tracer.views.foo", "message": "real error", "logentry": "x"}
    assert before_send(event, {}) is not None


def test_transaction_scrubber_accepts_list_breadcrumbs():
    event = {
        "breadcrumbs": [
            {
                "data": {
                    "url": "https://example.test/telemetry/registration/",
                    "body": "secret",
                    "data": {"token": "secret"},
                    "method": "POST",
                }
            },
            "unexpected-shape",
            {"data": "unexpected-shape"},
        ]
    }

    out = _scrub_deployment_telemetry_event(event)

    assert out is event
    assert out["breadcrumbs"][0]["data"] == {
        "url": "https://example.test/telemetry/registration/",
        "method": "POST",
    }


def test_transaction_scrubber_keeps_mapping_breadcrumb_contract():
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "data": {
                        "url": "https://example.test/telemetry/heartbeat/",
                        "request_body": "secret",
                    }
                }
            ]
        }
    }

    out = _scrub_deployment_telemetry_event(event)

    assert out["breadcrumbs"]["values"][0]["data"] == {
        "url": "https://example.test/telemetry/heartbeat/"
    }


# Breadcrumbs


def _crumb(secret):
    return {
        "message": "Activity started: do_work",
        "category": "temporal.activity",
        "data": {"customer_api_key": secret, "room_name": "room-1"},
    }


@pytest.mark.parametrize("shape", ["values", "list"])
def test_scrub_event_redacts_sensitive_keys_in_breadcrumb_data(shape):
    crumbs = [_crumb("leak"), "unexpected-shape", {"message": "no data"}]
    event = {"breadcrumbs": {"values": crumbs} if shape == "values" else crumbs}

    _scrub_event(event)

    out = event["breadcrumbs"]["values"] if shape == "values" else event["breadcrumbs"]
    assert out[0]["data"] == {"customer_api_key": _REDACTED, "room_name": "room-1"}
    assert out[0]["message"] == "Activity started: do_work"
    assert out[0]["category"] == "temporal.activity"
    assert out[1] == "unexpected-shape"
    assert out[2] == {"message": "no data"}


def test_scrub_event_redacts_a_breadcrumb_message_that_names_a_secret():
    event = {"breadcrumbs": [{"message": "using customer_api_key=abc", "data": {}}]}
    _scrub_event(event)
    assert event["breadcrumbs"][0]["message"] == _REDACTED


def test_scrub_strings_redacts_strings_that_name_a_sensitive_key():
    scrubbed = _scrub_strings(
        {
            "repr": "Input(metadata={'customer_livekit_api_secret': 'x'})",
            "url": "https://h/?token=abc",
            "room_name": "r",
            "count": 3,
            "nested": ["fine", "secret='y'"],
        }
    )
    assert scrubbed == {
        "repr": _REDACTED,
        "url": _REDACTED,
        "room_name": "r",
        "count": 3,
        "nested": ["fine", _REDACTED],
    }
    # `_scrub` stays key-only: free text such as a request body is kept.
    assert _scrub({"note": "my token is here"}) == {"note": "my token is here"}


def test_scrub_strings_matches_names_in_any_case_and_with_dashes():
    assert _scrub_strings(["Customer_API_Key=x", "headers={'xi-api-key': 'k'}"]) == [
        _REDACTED,
        _REDACTED,
    ]


def test_scrub_strings_drops_what_is_nested_too_deep_to_check():
    deep = {"note": "fine"}
    for _ in range(8):
        deep = {"inner": deep}
    scrubbed = _scrub_strings(deep)
    for _ in range(7):
        scrubbed = scrubbed["inner"]
    assert scrubbed == _REDACTED


def test_scrub_event_redacts_log_call_arguments():
    event = {
        "logentry": {
            "message": "bad input %s",
            "params": ["Input(customer_api_key='k')", "room r"],
        }
    }
    _scrub_event(event)
    assert event["logentry"] == {
        "message": "bad input %s",
        "params": [_REDACTED, "room r"],
    }


def test_scrub_event_rebuilds_the_formatted_log_line_from_scrubbed_arguments():
    event = {
        "logentry": {
            "message": "bad input %s in %s",
            "params": ["Input(customer_api_key='k')", "room r"],
            "formatted": "bad input Input(customer_api_key='k') in room r",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["formatted"] == f"bad input {_REDACTED} in room r"


def test_scrub_event_rebuilds_a_formatted_line_with_named_arguments():
    event = {
        "logentry": {
            "message": "calling %(url)s with %(api_key)s",
            "params": {"url": "https://example.test", "api_key": "sk-123"},
            "formatted": "calling https://example.test with sk-123",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["formatted"] == (
        f"calling https://example.test with {_REDACTED}"
    )


def test_scrub_event_keeps_a_log_line_with_no_arguments():
    event = {
        "logentry": {
            "message": "progress 100%",
            "params": [],
            "formatted": "progress 100%",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["formatted"] == "progress 100%"


def test_scrub_event_falls_back_to_the_template_when_a_line_does_not_format():
    event = {
        "logentry": {
            "message": "took %d ms",
            "params": ["Input(api_key='k')"],
            "formatted": "took Input(api_key='k') ms",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["formatted"] == "took %d ms"


def test_scrub_event_keeps_a_line_whose_arguments_are_harmless():
    event = {
        "logentry": {
            "message": "took %d ms",
            "params": ["12"],
            "formatted": "took 12 ms",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["formatted"] == "took 12 ms"


def test_a_rebuilt_log_line_keeps_the_size_cap():
    event = {
        "logentry": {
            "message": "%s %s",
            "params": ["Input(api_key='k')", "x" * 20_000],
            "formatted": "irrelevant",
        }
    }
    _scrub_event(event)
    assert len(event["logentry"]["formatted"]) == 16_384


def test_scrub_event_drops_a_logged_text_that_names_a_key():
    line = "bad input Input(customer_api_key='k')"
    event = {"logentry": {"message": line, "params": [], "formatted": line}}
    _scrub_event(event)
    assert event["logentry"] == {
        "message": _REDACTED,
        "params": [],
        "formatted": _REDACTED,
    }


def test_scrub_event_keeps_a_template_that_names_a_key():
    event = {
        "logentry": {
            "message": "calling with api_key=%s",
            "params": ["sk-1"],
            "formatted": "calling with api_key=sk-1",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["message"] == "calling with api_key=%s"
    assert event["logentry"]["formatted"] == _REDACTED


@pytest.mark.parametrize(
    "text",
    [
        "API Key: abc",
        "access-key=abc",
        "access key abc",
        "Private-Key abc",
        "-----BEGIN PRIVATE KEY-----",
        "cfg = {'accessKey': 'abc'}",
        "postgres://app:abc@db:5432/app",
        "redis://:abc@cache:6379/0",
        "amqp://guest:abc@mq",
        "<saml:Assertion ID='a'>x</saml:Assertion>",
    ],
)
def test_spaced_and_hyphenated_key_names_are_recognised(text):
    assert _scrub_strings(text) == _REDACTED


# Through the Temporal interceptor and a client set up like init_sentry


@dataclass
class _CallInput:
    customer_api_key: str
    metadata: dict
    url: str
    system_prompt: str
    room_name: str


@dataclass
class _Usage:
    prompt_tokens: int


def _new_secret_values():
    return {name: secrets.token_hex(16) for name in ("metadata", "url", "flat")}


def _call_input(secret_values):
    return _CallInput(
        customer_api_key=secret_values["flat"],
        metadata={"customer_livekit_api_secret": secret_values["metadata"]},
        url=f"https://h/?token={secret_values['url']}",
        system_prompt="p" * 25_000,
        room_name="r",
    )


class _RaisingNext:
    async def execute_activity(self, input):
        raise RuntimeError("activity failed")


class _LoggingInputNext:
    """Logs the activity's input as a log argument, the way a handler might."""

    async def execute_activity(self, input):
        logging.getLogger("x").error("bad input %s", input.args[0])


class _FStringLoggingNext:
    """Logs the activity's input already formatted, with no arguments."""

    async def execute_activity(self, input):
        logging.getLogger("x").error(f"bad input {input.args[0]}")


class _JsonLoggingNext:
    """Logs the input inside a JSON line, as the Temporal workers' structlog does."""

    async def execute_activity(self, input):
        logging.getLogger("x").error(
            json.dumps({"event": "failed", "call_input": repr(input.args[0])})
        )


class _LoggingNext:
    """Logs an error without exc_info, which sends an event with `threads`."""

    async def execute_activity(self, input):
        room_name = "r"
        attempts = 3
        # A token count is harmless and must reach Sentry.
        max_tokens = 4096
        usage_info = _Usage(prompt_tokens=10)
        logging.getLogger("x").info("calling with customer_api_key=%s", "k")
        logging.getLogger("x").error("failed")
        return room_name, attempts, usage_info, max_tokens


async def _run_activity(next_interceptor, secret_values, before=None):
    """Run the interceptor under a client configured the way init_sentry is."""
    sent = []

    async def do_work(arg):
        return None

    client = sentry_sdk.Client(
        dsn="http://public@localhost.invalid/1",
        transport=sent.append,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
        default_integrations=False,
        before_send=_get_before_send(),
        event_scrubber=_event_scrubber(),
        send_default_pii=True,
        attach_stacktrace=True,
        max_breadcrumbs=100,
        include_source_context=True,
        include_local_variables=True,
        max_value_length=_configured_max_value_length(),
    )
    interceptor = SentryActivityInboundInterceptor(next_interceptor)
    activity_input = ExecuteActivityInput(
        fn=do_work, args=[_call_input(secret_values)], executor=None, headers={}
    )
    # Own isolation scope, so breadcrumbs from other tests are not carried in.
    isolation = sentry_sdk.Scope(client=client, ty=ScopeType.ISOLATION)
    with use_isolation_scope(isolation), use_scope(sentry_sdk.Scope(client=client)):
        if before:
            before()
        try:
            await interceptor.execute_activity(activity_input)
        except RuntimeError:
            pass
    client.flush()
    assert sent
    return sent


def _assert_no_secret(sent, secret_values):
    payload = json.dumps(sent, default=str)
    for value in secret_values.values():
        assert value not in payload


async def test_a_raising_activity_leaks_no_input_secret():
    secret_values = _new_secret_values()
    sent = await _run_activity(_RaisingNext(), secret_values)
    _assert_no_secret(sent, secret_values)
    assert "do_work" in json.dumps(sent)


async def test_a_logged_error_event_leaks_no_input_secret():
    secret_values = _new_secret_values()
    sent = await _run_activity(_LoggingNext(), secret_values)
    assert "threads" in sent[0]
    _assert_no_secret(sent, secret_values)


async def test_a_logged_input_leaks_no_secret_in_the_formatted_line():
    secret_values = _new_secret_values()
    sent = await _run_activity(_LoggingInputNext(), secret_values)
    _assert_no_secret(sent, secret_values)
    assert sent[0]["logentry"]["message"] == "bad input %s"


@pytest.mark.parametrize("next_interceptor", [_FStringLoggingNext, _JsonLoggingNext])
async def test_a_preformatted_log_line_leaks_no_secret(next_interceptor):
    secret_values = _new_secret_values()
    sent = await _run_activity(next_interceptor(), secret_values)
    _assert_no_secret(sent, secret_values)


async def test_secrets_are_scrubbed_when_the_activity_breadcrumb_is_not_first():
    secret_values = _new_secret_values()

    def before():
        for i in range(3):
            sentry_sdk.add_breadcrumb(message=f"earlier {i}", data={"step": i})

    sent = await _run_activity(_RaisingNext(), secret_values, before=before)
    _assert_no_secret(sent, secret_values)
    messages = [c["message"] for c in sent[0]["breadcrumbs"]["values"]]
    assert messages[:3] == ["earlier 0", "earlier 1", "earlier 2"]
    assert messages[3].startswith("Activity started")


async def test_a_log_breadcrumb_naming_a_secret_is_replaced():
    sent = await _run_activity(_LoggingNext(), _new_secret_values())
    crumbs = sent[0]["breadcrumbs"]["values"]
    [logged] = [c for c in crumbs if c.get("category") == "x"]
    assert logged["message"] == _REDACTED


async def test_harmless_frame_locals_and_token_counts_are_kept():
    sent = await _run_activity(_LoggingNext(), _new_secret_values())
    local_vars = [f["vars"] for f in iter_event_frames(sent[0]) if f.get("vars")]
    [frame_vars] = [v for v in local_vars if "usage_info" in v]
    assert frame_vars["room_name"] == "'r'"
    assert frame_vars["attempts"] == "3"
    assert frame_vars["max_tokens"] == "4096"
    assert "prompt_tokens=10" in frame_vars["usage_info"]


def test_token_count_names_are_kept_but_a_text_that_also_names_a_key_is_not():
    assert _scrub_strings(
        {"max_tokens": 4096, "usage": {"prompt_tokens": 12}, "n": "total_tokens=3"}
    ) == {"max_tokens": 4096, "usage": {"prompt_tokens": 12}, "n": "total_tokens=3"}
    for name in (
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "token_count",
        "tokens_used",
        "num_tokens",
    ):
        assert _scrub_strings(f"Usage({name}=1)") == f"Usage({name}=1)"
        assert _scrub_strings(f"Usage({name}=1, api_key='k')") == _REDACTED
        assert _scrub_strings(f"{name}=1 client_secret=k") == _REDACTED
        assert _scrub_strings({name: 1, "api_key": "k"}) == {
            name: 1,
            "api_key": _REDACTED,
        }
    # Other token names, and `candidate`, still match as before.
    assert _scrub_strings("access_token=abc") == _REDACTED
    assert _scrub_strings("GenerateContentResponse(candidates=[])") == _REDACTED


# Shared helpers for the tests below


class _CollectingTransport(Transport):
    """Collects what the client sends; a callable transport never sees transactions."""

    def __init__(self, sent):
        super().__init__()
        self.sent = sent

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type in ("event", "transaction"):
                self.sent.append(item.payload.json)


def _configured_max_value_length():
    """The `max_value_length` init_sentry hands the SDK, so a test client cuts
    strings where the real one does."""
    with pytest.MonkeyPatch.context() as patch:
        seen, _ = _init_sentry_kwargs(patch)
    return seen["max_value_length"]


def _sent_through_client(action, **overrides):
    """Run `action` under a client configured like init_sentry; return what it sent."""
    sent = []
    options = {
        "dsn": "http://public@localhost.invalid/1",
        "transport": _CollectingTransport(sent),
        "integrations": [
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
        "default_integrations": False,
        "before_send": _get_before_send(),
        "event_scrubber": _event_scrubber(),
        "send_default_pii": True,
        "attach_stacktrace": True,
        "max_breadcrumbs": 100,
        "include_source_context": True,
        "include_local_variables": True,
        "max_value_length": _configured_max_value_length(),
    }
    options.update(overrides)
    client = sentry_sdk.Client(**options)
    isolation = sentry_sdk.Scope(client=client, ty=ScopeType.ISOLATION)
    with use_isolation_scope(isolation), use_scope(sentry_sdk.Scope(client=client)):
        action()
    client.flush()
    return sent


# Transactions (4597165438)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_a_sampled_transaction_leaks_no_secret_in_span_data_or_extra():
    secret = secrets.token_hex(16)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def action():
        with sentry_sdk.start_transaction(name="t", op="task"):
            sentry_sdk.set_extra("call_input", f"Input(customer_api_key='{secret}')")
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/u?access_token={secret}"
            ).read()

    try:
        sent = _sent_through_client(
            action,
            integrations=[StdlibIntegration()],
            traces_sample_rate=1.0,
            before_send_transaction=sentry_module._get_before_send_transaction(),
        )
    finally:
        server.shutdown()
        server.server_close()
    [transaction] = [e for e in sent if e.get("type") == "transaction"]
    assert transaction["spans"], "the outbound call should be a span"
    assert any("http.query" in (s.get("data") or {}) for s in transaction["spans"])
    assert secret not in json.dumps(sent, default=str)


def test_the_transaction_hook_scrubs_the_same_places_as_error_events():
    event = {
        "type": "transaction",
        "request": {"query_string": "api_key=leak", "cookies": {"s": "x"}},
        "extra": {"call_input": "Input(customer_api_key='leak')", "n": 1},
        "contexts": {"c": {"password": "leak"}},
        "breadcrumbs": [{"data": {"customer_api_key": "leak"}}],
        "spans": [
            {"data": {"http.query": "access_token=leak", "http.method": "GET"}},
            {
                "op": "db.redis",
                "description": "SET 'cache:k' b'\\x80leak' 'PX' 1000",
                "tags": {"redis.key": "aws_onboard:leak", "redis.command": "SET"},
            },
            {"data": "unexpected-shape"},
            {"op": "no data"},
            "unexpected-span",
        ],
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert "leak" not in json.dumps(out)
    assert out["spans"][0]["data"] == {
        "http.query": _REDACTED,
        "http.method": "GET",
    }
    assert out["extra"]["n"] == 1
    redis_span = out["spans"][1]
    assert redis_span["description"] == "SET 'cache:k'"
    assert redis_span["tags"] == {
        "redis.key": f"aws_onboard:{_REDACTED}",
        "redis.command": "SET",
    }


# Exception values and the message (4597165450)


def test_an_exception_value_that_names_a_key_is_replaced():
    secret = secrets.token_hex(16)

    def action():
        try:
            raise ValueError(f"Key (api_key)=({secret}) is bad")
        except ValueError as exc:
            sentry_sdk.capture_exception(exc)

    [event] = _sent_through_client(action)
    [value] = event["exception"]["values"]
    assert value["type"] == "ValueError"
    assert value["value"] == _REDACTED
    assert secret not in json.dumps(event, default=str)


def test_logger_exception_on_an_error_holding_a_secret_leaks_nothing():
    secret = secrets.token_hex(16)

    def action():
        try:
            raise RuntimeError(f"upstream rejected token {secret}")
        except RuntimeError:
            logging.getLogger("x").exception("call failed")

    [event] = _sent_through_client(action)
    assert secret not in json.dumps(event, default=str)
    assert event["exception"]["values"][0]["type"] == "RuntimeError"


def test_a_captured_message_that_names_a_key_is_replaced():
    secret = secrets.token_hex(16)
    [event] = _sent_through_client(
        lambda: sentry_sdk.capture_message(f"retrying with token={secret}")
    )
    assert secret not in json.dumps(event, default=str)
    assert event["message"] == _REDACTED


def test_an_exception_value_and_message_without_a_key_are_kept():
    event = {
        "message": "plain",
        "exception": {"values": [{"type": "ValueError", "value": "bad max_tokens"}]},
    }
    _scrub_event(event)
    assert event["message"] == "plain"
    assert event["exception"]["values"][0]["value"] == "bad max_tokens"


@pytest.mark.parametrize(
    "event",
    [
        {"exception": {"values": ["x", {"value": 3}, {"type": "E"}]}},
        {"message": {"formatted": "m"}},
    ],
)
def test_odd_exception_and_message_shapes_do_not_raise(event):
    _scrub_event(event)


# Fail closed (4597165465)


def _boom(*args, **kwargs):
    raise TypeError("planted bug")


def test_a_failing_scrub_drops_the_event_and_reports_only_the_failure(
    monkeypatch, capfd, caplog
):
    secret = secrets.token_hex(16)
    monkeypatch.setattr(sentry_module, "_scrub_strings", _boom)

    def action():
        sentry_sdk.add_breadcrumb(message="m", data={"customer_api_key": secret})
        logging.getLogger("x").error("failed")

    sent = _sent_through_client(action)
    assert sent == []
    # On stderr, not through logging: Python 3.13.4+ drops a record logged
    # while another record on the same thread is being handled, and the hook
    # runs inside the handler of the record that made the event.
    captured = capfd.readouterr()
    assert captured.err == "sentry_scrub_failed: TypeError\n"
    assert secret not in captured.err + captured.out + caplog.text


def test_a_failing_scrub_drops_a_transaction(monkeypatch, capfd):
    monkeypatch.setattr(sentry_module, "_scrub_strings", _boom)

    def action():
        with sentry_sdk.start_transaction(name="t", op="task"):
            sentry_sdk.set_extra("call_input", "x")

    sent = _sent_through_client(
        action,
        traces_sample_rate=1.0,
        before_send_transaction=sentry_module._get_before_send_transaction(),
    )
    assert sent == []
    captured = capfd.readouterr()
    assert captured.err == "sentry_scrub_failed: TypeError\n"
    assert "planted bug" not in captured.err + captured.out


def test_a_failing_telemetry_scrub_also_drops_the_event(monkeypatch, capfd):
    monkeypatch.setattr(sentry_module, "_scrub_deployment_telemetry_event", _boom)
    assert before_send({"message": "x"}, {}) is None
    assert sentry_module._get_before_send_transaction()({"type": "t"}, {}) is None
    assert capfd.readouterr().err == "sentry_scrub_failed: TypeError\n" * 2


def _failing_hooks(monkeypatch):
    """The two hooks, with a scrub step that raises."""
    monkeypatch.setattr(sentry_module, "_scrub_deployment_telemetry_event", _boom)
    return (
        lambda: _get_before_send()({"message": "x"}, {}),
        lambda: sentry_module._get_before_send_transaction()({"type": "t"}, {}),
    )


def test_the_failure_report_goes_to_the_process_stderr_not_sys_stderr(
    monkeypatch, capfd
):
    # In a Celery worker `sys.stderr` is a proxy that logs what it is given, and
    # the report would go through logging again.
    proxy = io.StringIO()
    monkeypatch.setattr(sys, "stderr", proxy)
    for hook in _failing_hooks(monkeypatch):
        assert hook() is None
    assert proxy.getvalue() == ""
    assert capfd.readouterr().err == "sentry_scrub_failed: TypeError\n" * 2


def test_the_failure_report_falls_back_to_sys_stderr_without_an_original(
    monkeypatch,
):
    fallback = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", None)
    monkeypatch.setattr(sys, "stderr", fallback)
    for hook in _failing_hooks(monkeypatch):
        assert hook() is None
    assert fallback.getvalue() == "sentry_scrub_failed: TypeError\n" * 2


class _UnwritableStream:
    def write(self, text):
        raise OSError("closed")

    def flush(self):
        raise OSError("closed")


@pytest.mark.parametrize(
    "original, current",
    [
        (None, None),
        (_UnwritableStream(), _UnwritableStream()),
        (None, _UnwritableStream()),
        (_UnwritableStream(), None),
    ],
    ids=["both-none", "both-raise", "none-then-raise", "raise-then-none"],
)
def test_the_hooks_still_drop_the_event_when_the_report_cannot_be_written(
    monkeypatch, original, current
):
    monkeypatch.setattr(sys, "__stderr__", original)
    monkeypatch.setattr(sys, "stderr", current)
    for hook in _failing_hooks(monkeypatch):
        assert hook() is None


def _init_sentry_kwargs(monkeypatch):
    seen = {}
    ignored = []
    monkeypatch.setattr(sentry_module, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry_module, "SENTRY_DSN", "http://k@localhost.invalid/1")
    monkeypatch.setattr(sentry_module, "_initialized_components", set())
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr("sentry_sdk.integrations.logging.ignore_logger", ignored.append)
    # init_sentry tags the global scope; keep that out of the other tests.
    monkeypatch.setattr(sentry_sdk, "set_tags", lambda *a, **kw: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **kw: None)
    assert sentry_module.init_sentry(component="temporal")
    return seen, ignored


def test_init_sentry_ignores_only_the_noisy_loggers_and_wires_the_hooks(monkeypatch):
    seen, ignored = _init_sentry_kwargs(monkeypatch)
    # The scrub reports a failure on stderr, so there is no logger of its own.
    assert ignored == list(sentry_module.IGNORED_LOGGERS)
    assert not hasattr(sentry_module, "SCRUB_LOGGER_NAME")
    # Well above the final cap, so a key name that follows a long text is still
    # in view of the scrub; the hooks cut every string to the cap afterwards.
    assert seen["max_value_length"] == 262_144
    assert seen["max_value_length"] > _MAX_FORMATTED
    long_text = "x" * 50_000
    assert len(seen["before_send"]({"extra": {"k": long_text}}, {})["extra"]["k"]) == (
        _MAX_FORMATTED
    )
    event = {"type": "transaction", "spans": [{"data": {"q": "token=leak"}}]}
    out = seen["before_send_transaction"](event, {})
    assert out["spans"][0]["data"] == {"q": _REDACTED}


# Over-matching and narrow catch (4597165486, 4597165488)


def test_accesskey_and_password_urls_are_sensitive_but_plain_urls_are_not():
    assert _is_sensitive_key("accessKey")
    assert _scrub_strings("postgres://app:pw@db/app") == _REDACTED
    for harmless in (
        "https://example.test:8443/path?x=1",
        "http://user@host/path",
        "see http://a/b@c and://x",
    ):
        assert _scrub_strings(harmless) == harmless


@pytest.mark.parametrize(
    "text",
    [
        "://a:" + "b" * 300_000,
        "://" + "a:" * 150_000,
        "://a:b" * 60_000,
        "x" * 300_000,
    ],
)
def test_the_password_url_check_stays_linear_on_long_strings(text):
    started = time.monotonic()
    _scrub_strings(text)
    assert time.monotonic() - started < 2


class _TookTooLong(BaseException):
    pass


@contextlib.contextmanager
def _within(seconds):
    """Fail the test after `seconds` instead of hanging on a quadratic regex
    (the regex engine checks for signals while it runs)."""

    def expired(signum, frame):
        raise _TookTooLong(f"still running after {seconds}s")

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _seconds_for(action, limit=1.0, abort_after=8):
    started = time.monotonic()
    with _within(abort_after):
        action()
    elapsed = time.monotonic() - started
    assert elapsed < limit, f"took {elapsed:.2f}s"


# The longest string the SDK hands a hook (init_sentry's max_value_length).
_SDK_LIMIT = 262_144
_HOSTILE_TEMPLATES = {
    "zero flags": "%" + "0" * _SDK_LIMIT,
    "open mapping keys": "%(" * (_SDK_LIMIT // 2),
    "mapping keys with names": "%(a" * (_SDK_LIMIT // 3),
    "zero flags in runs": ("%" + "0" * 50) * (_SDK_LIMIT // 51),
}


@pytest.mark.parametrize("name", list(_HOSTILE_TEMPLATES))
def test_a_hostile_log_template_is_read_in_linear_time(name):
    template = _HOSTILE_TEMPLATES[name]
    _seconds_for(lambda: sentry_module._names_key_in_template(template))
    _seconds_for(lambda: sentry_module._template_leaks(template))


@pytest.mark.parametrize("name", ["zero flags", "open mapping keys"])
def test_a_hostile_f_string_error_is_sent_in_linear_time(name):
    # An f-string with user text in it: the logging frame's `msg` is read for
    # every log call.
    user_text = _HOSTILE_TEMPLATES[name]

    def action():
        logging.getLogger("x").error(f"invalid dataset name {user_text}")

    sent = []
    # The whole client takes part here (capture, serialize, hooks), so the bound
    # is looser than the regex-only ones above; quadratic growth on this input
    # is minutes, and `_seconds_for` aborts it at 8 s.
    _seconds_for(lambda: sent.extend(_sent_through_client(action)), limit=3)
    assert len(sent) == 1


@pytest.mark.parametrize(
    "template, text",
    [
        ("%s", "\x00"),
        ("%05d", "\x00"),
        ("%(a)05.1f", "\x00"),
        ("%010.3f", "\x00"),
        ("%*d", "\x00"),
        ("%-8.*f|%+d|% d|%#x|%ld|%.2s", "\x00|\x00|\x00|\x00|\x00|\x00"),
        ("%(api_key)s and 100%% of %s", "\x00 and 100 of \x00"),
        ("%z %", "%z %"),
    ],
)
def test_the_conversions_a_log_template_can_hold_are_still_recognised(template, text):
    assert _template_text(template) == text


def test_a_template_naming_a_key_is_still_read_around_its_conversions():
    assert sentry_module._names_key_in_template("api_key=%010.3f")
    assert not sentry_module._names_key_in_template("took %05d ms for %(a)05.1f")


@pytest.mark.parametrize(
    "message, params",
    [("took %d ms", ["x"]), ("%(a)s", {}), ("%z", [1])],
)
def test_a_line_that_does_not_format_falls_back_to_the_template(message, params):
    assert _formatted(message, params) == message


def test_an_unexpected_error_while_formatting_is_not_swallowed():
    class Bad:
        def __str__(self):
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        _formatted("value %s", [Bad()])


# Celery job extras (4597165493)


def test_celery_job_extras_are_scrubbed_as_strings():
    event = {"extra": {"celery-job": {"args": ["Input(customer_api_key='k')"]}}}
    _scrub_event(event)
    assert event["extra"]["celery-job"]["args"] == [_REDACTED]


# Transaction spans: description and tags


def _span_transaction(*spans):
    return {"type": "transaction", "spans": list(spans)}


def test_a_redis_span_description_leaks_no_secret():
    secret = secrets.token_hex(16)
    value_secret = secrets.token_hex(16)
    pickled = pickle.dumps({"token": secret})

    def action():
        with sentry_sdk.start_transaction(name="t", op="task"):
            with sentry_sdk.start_span(
                op="db.redis",
                name=f"SET 'aws_onboard:{value_secret}' {pickled!r} 'PX' 1000",
            ) as span:
                span.set_tag("redis.key", f"aws_onboard:{value_secret}")

    sent = _sent_through_client(
        action,
        traces_sample_rate=1.0,
        before_send_transaction=sentry_module._get_before_send_transaction(),
    )
    [transaction] = [e for e in sent if e.get("type") == "transaction"]
    [span] = [s for s in transaction["spans"] if s.get("op") == "db.redis"]
    assert span["description"] == f"SET 'aws_onboard:{_REDACTED}'"
    assert span["tags"]["redis.key"] == f"aws_onboard:{_REDACTED}"
    assert secret not in json.dumps(sent, default=str)
    assert value_secret not in json.dumps(sent, default=str)


def test_a_real_redis_write_leaks_no_secret_in_the_sent_transaction(monkeypatch):
    # The default RedisIntegration, with PII on, writes every argument of a
    # command into the span description and the key into a tag.
    secret = secrets.token_hex(16)
    key_secret = secrets.token_hex(16)
    monkeypatch.setattr(redis.Redis, "_execute_command", lambda self, *a, **kw: True)

    def action():
        with sentry_sdk.start_transaction(name="t", op="task"):
            redis.Redis().set(
                f"mcp_oauth_code:{key_secret}", pickle.dumps({"token": secret}), px=1000
            )
            redis.Redis().set("cache:plain", pickle.dumps({"n": 1}), px=1000)

    sent = _sent_through_client(
        action,
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        before_send_transaction=sentry_module._get_before_send_transaction(),
    )
    [transaction] = [e for e in sent if e.get("type") == "transaction"]
    spans = [s for s in transaction["spans"] if s.get("op") == "db.redis"]
    assert [s["description"] for s in spans] == [
        f"SET 'mcp_oauth_code:{_REDACTED}'",
        "SET 'cache:plain'",
    ]
    assert spans[0]["tags"]["redis.key"] == f"mcp_oauth_code:{_REDACTED}"
    assert spans[1]["tags"]["redis.key"] == "cache:plain"
    assert secret not in json.dumps(sent, default=str)
    assert key_secret not in json.dumps(sent, default=str)


@pytest.mark.parametrize(
    "prefix",
    ["aws_onboard", "gcp_onboard", "gcp_onboarding", "mcp_oauth_code"]
    + ["mcp_oauth_approve", "mcp_oauth_access", "mcp_oauth_refresh"],
)
def test_a_cache_key_that_embeds_a_token_is_redacted_everywhere_it_is_sent(prefix):
    secret = secrets.token_hex(16)
    key = f"{prefix}:{secret}"
    event = {
        "type": "transaction",
        "spans": [
            {"op": "cache.get", "description": key, "data": {"cache.key": [key]}},
            {
                "op": "db.redis",
                "description": f"GET '{key}'",
                "tags": {"redis.key": key},
            },
        ],
        "breadcrumbs": [{"message": f"GET '{key}'", "data": {"key": key}}],
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert secret not in json.dumps(out)
    assert out["spans"][0]["description"] == f"{prefix}:{_REDACTED}"
    assert out["spans"][1]["tags"] == {"redis.key": f"{prefix}:{_REDACTED}"}


def test_a_redis_pipeline_span_keeps_only_command_and_key():
    secret = secrets.token_hex(16)
    event = _span_transaction(
        {
            "op": "db.redis",
            "description": "redis.pipeline.execute",
            "data": {
                "redis.commands": {
                    "count": 2,
                    "first_ten": [
                        f"SET 'cache:a' b'{secret}' 'PX' 1000",
                        "GET 'cache:b'",
                    ],
                }
            },
        }
    )
    out = sentry_module._get_before_send_transaction()(event, {})
    [span] = out["spans"]
    assert span["data"]["redis.commands"]["first_ten"] == [
        "SET 'cache:a'",
        "GET 'cache:b'",
    ]
    assert secret not in json.dumps(out)


def test_other_span_descriptions_and_tags_are_scrubbed_as_strings():
    secret = secrets.token_hex(16)
    event = _span_transaction(
        {
            "op": "subprocess",
            "description": f"curl -H 'Authorization: Bearer {secret}' http://h/",
        },
        {"op": "http.client", "description": f"GET https://user:{secret}@h/path"},
        {
            "op": "http.client",
            "description": "POST https://hooks.slack.com/services/T0/B0/" + secret,
            "tags": {"http.url": f"https://u:{secret}@h/", "plain": "ok"},
        },
        {"op": "http.client", "description": "GET https://example.test/path"},
    )
    out = sentry_module._get_before_send_transaction()(event, {})
    assert secret not in json.dumps(out)
    assert [s["description"] for s in out["spans"]] == [
        _REDACTED,
        _REDACTED,
        _REDACTED,
        "GET https://example.test/path",
    ]
    assert out["spans"][2]["tags"] == {"http.url": _REDACTED, "plain": "ok"}


_CODE_SPAN_DESCRIPTIONS = [
    (
        "db",
        'SELECT "accounts_user"."id", "accounts_user"."password" FROM "accounts_user" '
        'WHERE "accounts_user"."id" = %s',
    ),
    ("middleware.django", "django.middleware.csrf.CsrfViewMiddleware.process_view"),
    ("http.client", "POST https://oauth2.googleapis.com/token"),
    ("function", "accounts.views.get_user_from_token"),
]


@pytest.mark.parametrize("op, description", _CODE_SPAN_DESCRIPTIONS)
def test_a_span_that_names_code_or_sql_keeps_its_description(op, description):
    event = _span_transaction({"op": op, "description": description})
    out = sentry_module._get_before_send_transaction()(event, {})
    assert out["spans"][0]["description"] == description


def test_code_spans_keep_their_description_when_sent():
    def action():
        with sentry_sdk.start_transaction(name="t", op="task"):
            for op, description in _CODE_SPAN_DESCRIPTIONS:
                with sentry_sdk.start_span(op=op, name=description) as span:
                    span.set_data("code.function", "get_user_from_token")

    sent = _sent_through_client(
        action,
        traces_sample_rate=1.0,
        before_send_transaction=sentry_module._get_before_send_transaction(),
    )
    [transaction] = [e for e in sent if e.get("type") == "transaction"]
    spans = [s for s in transaction["spans"] if s.get("op") != "task"]
    assert [(s["op"], s["description"]) for s in spans] == _CODE_SPAN_DESCRIPTIONS
    assert all(s["data"]["code.function"] == "get_user_from_token" for s in spans)


def test_the_code_keys_of_span_data_are_kept_and_the_rest_is_still_scrubbed():
    secret = secrets.token_hex(16)
    data = {
        "code.function": "get_user_from_token",
        "code.filepath": "/app/accounts/token_views.py",
        "code.namespace": "accounts.token_views",
        "db.statement": f"api_key={secret}",
        "customer_api_key": secret,
    }
    event = _span_transaction({"op": "function", "data": data})
    out = sentry_module._get_before_send_transaction()(event, {})
    assert out["spans"][0]["data"] == {
        "code.function": "get_user_from_token",
        "code.filepath": "/app/accounts/token_views.py",
        "code.namespace": "accounts.token_views",
        "db.statement": _REDACTED,
        "customer_api_key": _REDACTED,
    }
    assert secret not in json.dumps(out)


@pytest.mark.parametrize(
    "op, description",
    [
        ("db", "SELECT 1 -- https://app:hunter2@db.internal/app"),
        ("function", "https://hooks.slack.com/services/T0/B0/xyz"),
        ("middleware.django", "<saml:Assertion>x</saml:Assertion>"),
        ("function", "SAMLResponse=abc"),
    ],
)
def test_a_span_description_that_holds_a_credential_by_shape_is_redacted(
    op, description
):
    event = _span_transaction({"op": op, "description": description})
    out = sentry_module._get_before_send_transaction()(event, {})
    assert out["spans"][0]["description"] == _REDACTED


def test_an_http_span_description_still_loses_token_path_and_query_values():
    secret = secrets.token_hex(16)
    event = _span_transaction(
        {"op": "http.client", "description": f"GET https://h/shared/{secret}"},
        {"op": "http.client", "description": f"GET https://h/x?api_key={secret}"},
    )
    out = sentry_module._get_before_send_transaction()(event, {})
    assert [s["description"] for s in out["spans"]] == [
        f"GET https://h/shared/{_REDACTED}",
        f"GET https://h/x?{_REDACTED}",
    ]


def test_a_subprocess_span_description_with_a_secret_is_scrubbed_when_sent():
    secret = secrets.token_hex(16)

    def action():
        with sentry_sdk.start_transaction(name="t", op="task"):
            with sentry_sdk.start_span(
                op="subprocess", name=f"deploy --api_key={secret}"
            ) as span:
                span.set_tag("cmd", f"deploy --api_key={secret}")

    sent = _sent_through_client(
        action,
        traces_sample_rate=1.0,
        before_send_transaction=sentry_module._get_before_send_transaction(),
    )
    [transaction] = [e for e in sent if e.get("type") == "transaction"]
    assert [s["description"] for s in transaction["spans"]] == [_REDACTED]
    assert secret not in json.dumps(sent, default=str)


# Keys and paths that name a secret in span descriptions (L1)


def _sent_span_descriptions(*spans):
    out = sentry_module._get_before_send_transaction()(_span_transaction(*spans), {})
    return [s["description"] for s in out["spans"]]


@pytest.mark.parametrize(
    "op, description, expected",
    [
        ("db.redis", "GET 'password_reset:abc'", f"GET {_REDACTED}"),
        ("cache.get", "GET 'password_reset:abc'", f"GET {_REDACTED}"),
        ("db.redis", "SET 'api_key:rawvalue' b'x' 'PX' 1000", f"SET {_REDACTED}"),
        ("cache.get", "api_key:rawvalue", _REDACTED),
        ("cache.set", "password_reset:abc", _REDACTED),
        ("db.redis", "GET 'cache:plain'", "GET 'cache:plain'"),
        ("cache.get", "user:42:profile", "user:42:profile"),
        ("cache.get", "GET 'feature_flags'", "GET 'feature_flags'"),
        ("db.redis", "redis.pipeline.execute", "redis.pipeline.execute"),
    ],
)
def test_a_cache_key_that_names_a_secret_is_replaced_and_other_keys_are_kept(
    op, description, expected
):
    assert _sent_span_descriptions({"op": op, "description": description}) == [expected]


def test_every_key_of_a_multi_key_cache_span_is_read():
    secret = secrets.token_hex(16)
    [description] = _sent_span_descriptions(
        {"op": "cache.get_many", "description": f"password_reset:{secret}, other:1"}
    )
    assert secret not in description


@pytest.mark.parametrize(
    "description, expected",
    [
        ("GET https://x/token/abc123", f"GET https://x/token/{_REDACTED}"),
        (
            "GET https://x/v1/api_key/abc123/x",
            f"GET https://x/v1/api_key/{_REDACTED}/x",
        ),
        ("GET /secret/abc?z=1", f"GET /secret/{_REDACTED}?z=1"),
        ("GET https://x/v1/users/42", "GET https://x/v1/users/42"),
        ("GET https://x/token", "GET https://x/token"),
        (
            "POST https://oauth2.googleapis.com/token",
            "POST https://oauth2.googleapis.com/token",
        ),
        ("GET https://x/max_tokens/5", "GET https://x/max_tokens/5"),
        ("GET https://x/candidates/42", "GET https://x/candidates/42"),
        ("GET https://x/authors/7", "GET https://x/authors/7"),
        ("GET https://x/api-key/abc", f"GET https://x/api-key/{_REDACTED}"),
    ],
)
def test_an_http_path_segment_after_a_key_name_is_replaced(description, expected):
    assert _sent_span_descriptions(
        {"op": "http.client", "description": description}
    ) == [expected]


# Span descriptions are cut before they are scrubbed (L2)


def test_a_huge_span_description_is_cut_before_it_is_scrubbed_and_capped(monkeypatch):
    secret = secrets.token_hex(16)
    seen = []
    real = sentry_module._is_secret_value
    monkeypatch.setattr(
        sentry_module, "_is_secret_value", lambda v: seen.append(len(v)) or real(v)
    )
    huge = "ok " * 1_000 + f"--api_key={secret}" + " pad" * 500_000
    plain = "q " * 1_000_000
    assert len(huge) > 2_000_000 and len(plain) >= 2_000_000
    event = _span_transaction(
        {"op": "subprocess", "description": huge},
        {"op": "db", "description": plain},
    )
    started = time.monotonic()
    out = sentry_module._get_before_send_transaction()(event, {})
    assert time.monotonic() - started < 1
    assert seen and max(seen) <= sentry_module._SDK_MAX_VALUE_LENGTH
    first, second = (s["description"] for s in out["spans"])
    assert first == _REDACTED
    assert len(second) == _MAX_FORMATTED
    assert secret not in json.dumps(out)


# The db values of span and breadcrumb data are not sent (L5)


def test_the_params_and_result_of_a_db_span_are_replaced_and_their_keys_kept():
    secret = secrets.token_hex(16)
    data = {
        "db.system": "clickhouse",
        "db.params": [[f"user-{secret}@example.test", 42]],
        "db.result": [("row", secret)],
        "query": "SELECT 1",
    }
    event = _span_transaction({"op": "db", "description": "q", "data": data})
    event["breadcrumbs"] = [{"category": "query", "message": "SELECT 1", "data": data}]
    out = sentry_module._get_before_send_transaction()(event, {})
    expected = {
        "db.system": "clickhouse",
        "db.params": _REDACTED,
        "db.result": _REDACTED,
        "query": "SELECT 1",
    }
    assert out["spans"][0]["data"] == expected
    assert out["breadcrumbs"][0]["data"] == expected
    assert secret not in json.dumps(out)


# Cheap guards in the text scrub (L4)


class _CountingRegex:
    def __init__(self, regex):
        self.regex = regex
        self.calls = 0

    def sub(self, *args, **kwargs):
        self.calls += 1
        return self.regex.sub(*args, **kwargs)

    def search(self, *args, **kwargs):
        self.calls += 1
        return self.regex.search(*args, **kwargs)


_GUARDED = ("_COUNT_FIELD", "_COUNT_PHRASE", "_TOKEN_KEY_PREFIX", "_URL_PASSWORD")


def _count_regex_calls(monkeypatch, action):
    spies = {name: _CountingRegex(getattr(sentry_module, name)) for name in _GUARDED}
    for name, spy in spies.items():
        monkeypatch.setattr(sentry_module, name, spy)
    action()
    return {name: spy.calls for name, spy in spies.items()}


@pytest.mark.parametrize(
    "scrub",
    [sentry_module._scrub_text, sentry_module._template_leaks],
    ids=["text", "template"],
)
def test_text_that_cannot_match_does_not_run_the_regexes(monkeypatch, scrub):
    calls = _count_regex_calls(
        monkeypatch, lambda: scrub("prompt text, 12 words " * 50)
    )
    assert calls == dict.fromkeys(_GUARDED, 0)


@pytest.mark.parametrize(
    "text, ran",
    [
        ("max_tokens is 5", {"_COUNT_FIELD", "_COUNT_PHRASE"}),
        ("a 5 tokens note", {"_COUNT_FIELD", "_COUNT_PHRASE"}),
        ("see https://u:pw@h/x", {"_URL_PASSWORD"}),
        ("key aws_onboard:xyz", {"_TOKEN_KEY_PREFIX"}),
        ("key mcp_oauth_code:xyz", {"_TOKEN_KEY_PREFIX"}),
    ],
)
def test_the_regexes_still_run_when_a_plain_check_hits(monkeypatch, text, ran):
    calls = _count_regex_calls(monkeypatch, lambda: sentry_module._scrub_text(text))
    assert {name for name, n in calls.items() if n} >= ran


_FRAGMENTS = [
    "api", "key", "_", "-", " ", "max_tokens", "12 tokens", "130,000 tokens",
    "tokens per min", "token", "password", "secret", "://", "u:p@", "h/",
    "aws_onboard:", "gcp_onboarding:", "mcp_oauth_code:", "mcp_oauth_x:",
    "hooks.slack.com/services/", "SAMLResponse=", "<saml:", "accountkey=",
    "Authorization", "PRIVATE", "private", "access", "x", "1", "%s", "=",
]  # fmt: skip


def _reference_names_key(lowered):
    lowered = sentry_module._COUNT_FIELD.sub(" ", lowered)
    lowered = sentry_module._COUNT_PHRASE.sub(" ", lowered)
    return any(s in lowered for s in sentry_module.SENSITIVE_KEY_SUBSTRINGS)


def _reference_scrub_text(text):
    """`_scrub_text` as it was before the guards: every regex on every text."""
    module = sentry_module
    if (
        _reference_names_key(text.lower())
        or any(m in text for m in module._SAML_VALUE_MARKERS)
        or any(m in text for m in module._SECRET_URL_MARKERS)
        or module._URL_PASSWORD.search(text)
    ):
        return _REDACTED
    return module._TOKEN_KEY_PREFIX.sub(r"\1:" + _REDACTED, text)


def _reference_template_leaks(template):
    module = sentry_module
    text = module._template_text(template)
    if (
        any(m in text for m in module._SAML_VALUE_MARKERS)
        or any(m in text for m in module._SECRET_URL_MARKERS)
        or module._URL_PASSWORD.search(text)
    ):
        return True
    lowered = module._COUNT_PHRASE.sub(" ", module._COUNT_FIELD.sub(" ", text.lower()))
    position = 0
    while (mention := module._KEY_MENTION.search(lowered, position)) is not None:
        label = module._LABEL_THEN_SPEC.match(lowered, mention.end())
        if label is None:
            return True
        position = label.end()
    return False


def test_the_guards_leave_the_output_of_the_text_checks_unchanged():
    rng = random.Random(20261117)
    for _ in range(4_000):
        text = "".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(1, 9)))
        assert sentry_module._scrub_text(text) == _reference_scrub_text(text), text
        assert sentry_module._template_leaks(text) == _reference_template_leaks(text), (
            text
        )


def test_sixty_long_strings_in_frame_vars_are_scrubbed_in_time():
    # Digits among non-ASCII text are the slowest input the regexes meet.
    text = ("1 你" * 90_000)[:_SDK_LIMIT]
    assert len(text) == _SDK_LIMIT
    # Called directly, so no event budget stands in for the guards: all sixty
    # strings are read.
    frame = {"vars": {f"v{i}": text for i in range(60)}}
    _seconds_for(lambda: sentry_module._scrub_frame_vars(frame), limit=2)
    assert len(frame["vars"]) == 60
    assert all(len(v) == _SDK_LIMIT for v in frame["vars"].values())


# A per-event budget on the string data the scrub reads (L4)

_BUDGET = 8 * 1024 * 1024


def test_an_event_past_the_scrub_budget_sends_no_unread_string():
    size = _SDK_LIMIT
    kept_count = _BUDGET // size
    markers = [secrets.token_hex(8) for _ in range(kept_count + 8)]
    extra = {f"k{i:02d}": m + "x" * (size - len(m)) for i, m in enumerate(markers)}
    extra["z_small"] = "short"
    out = _get_before_send()({"extra": extra}, {})["extra"]
    for i, marker in enumerate(markers[:kept_count]):
        assert out[f"k{i:02d}"].startswith(marker)
    for i in range(kept_count, len(markers)):
        assert out[f"k{i:02d}"] == _REDACTED
    assert out["z_small"] == _REDACTED
    assert not any(m in json.dumps(out) for m in markers[kept_count:])


def test_the_scrub_budget_covers_span_descriptions_of_a_transaction():
    size = _SDK_LIMIT
    kept_count = _BUDGET // size
    markers = [secrets.token_hex(8) for _ in range(kept_count + 4)]
    event = _span_transaction(
        *({"op": "function", "description": m + "x" * (size - len(m))} for m in markers)
    )
    out = sentry_module._get_before_send_transaction()(event, {})
    sent = [s["description"] for s in out["spans"]]
    assert all(
        d.startswith(m) for d, m in zip(sent[:kept_count], markers, strict=False)
    )
    assert sent[kept_count:] == [_REDACTED] * 4


def test_each_event_gets_its_own_scrub_budget():
    size = _SDK_LIMIT
    event = {"extra": {f"k{i}": "y" * size for i in range(_BUDGET // size)}}
    for _ in range(2):
        out = _get_before_send()(json.loads(json.dumps(event)), {})
        assert _REDACTED not in out["extra"].values()


# Token counts in LLM errors are kept (M1)


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 128000 tokens. However, you "
        "requested 130000 tokens (120,000 in the messages, 10000 in the completion).",
        "prompt is too long: 210000 tokens > 200000 maximum",
        "Rate limit reached for gpt-4o on tokens per min (TPM): Limit 30000, "
        "Used 28000, Requested 5000.",
        "Rate limit reached on tokens per minute: Limit 30000",
        "The input is 128000 input tokens, over the 100000 output tokens limit",
    ],
)
def test_an_llm_error_that_quotes_token_counts_is_kept(message):
    event = {
        "message": message,
        "exception": {"values": [{"type": "BadRequestError", "value": message}]},
    }
    _scrub_event(event)
    assert event["message"] == message
    assert event["exception"]["values"][0]["value"] == message
    assert _scrub_strings(message) == message


@pytest.mark.parametrize(
    "message",
    [
        "prompt is too long: 210000 tokens (api_key=sk-x)",
        "maximum context length is 128000 tokens; client_secret rejected",
        "Rate limit on tokens per min (TPM) for Bearer access_token abc",
        "128000 input tokens, password=hunter2",
    ],
)
def test_a_token_count_message_that_also_names_a_key_is_still_redacted(message):
    event = {
        "message": message,
        "exception": {"values": [{"type": "E", "value": message}]},
    }
    _scrub_event(event)
    assert event["message"] == _REDACTED
    assert event["exception"]["values"][0]["value"] == _REDACTED


@pytest.mark.parametrize(
    "text",
    [
        "1" * 300_000 + " tokens",
        "1," * 150_000,
        "tokens per " * 50_000,
        "a1 " * 100_000,
    ],
)
def test_the_token_count_removal_stays_linear_on_long_strings(text):
    started = time.monotonic()
    _scrub_strings(text)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "name",
    ["max_tokens_api_key", "prompt_tokensecret", "xmax_tokens", "total_tokens2"],
)
def test_a_count_name_is_removed_only_as_a_whole_word(name):
    assert _is_sensitive_key(name)
    assert _scrub_strings(f"{name}=1") == _REDACTED


# request.url (M2)


_F = _REDACTED


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://a.test/tracer/shared/TOK/", f"https://a.test/tracer/shared/{_F}/"),
        ("/tracer/shared/TOK/sidebar/", f"/tracer/shared/{_F}/sidebar/"),
        (
            "/tracer/shared/TOK/traces/abc-1/?page=2",
            f"/tracer/shared/{_F}/traces/abc-1/?page=2",
        ),
        ("/accounts/activate/MQ/c1-TOK/", f"/accounts/activate/{_F}/{_F}/"),
        (
            "/accounts/password-reset-confirm/MQ/c1-TOK/",
            f"/accounts/password-reset-confirm/{_F}/{_F}/",
        ),
        (
            "/accounts/accept-invitation/MQ/c1-TOK/",
            f"/accounts/accept-invitation/{_F}/{_F}/",
        ),
        (
            "/simulate/api/harness/ingress/TOK/",
            f"/simulate/api/harness/ingress/{_F}/",
        ),
        (
            "/simulate/api/harness/ingress/TOK/a/b",
            f"/simulate/api/harness/ingress/{_F}/a/b",
        ),
        ("/tracer/shared/TOK/?sig=zz&a=1", f"/tracer/shared/{_F}/?sig={_F}&a=1"),
        ("/accounts/user/deactivate/", "/accounts/user/deactivate/"),
        (
            "/simulate/agent-definitions/1/versions/2/activate/",
            "/simulate/agent-definitions/1/versions/2/activate/",
        ),
        ("https://a.test:8443/path?x=1", "https://a.test:8443/path?x=1"),
    ],
)
def test_the_request_url_hides_token_path_segments(url, expected):
    errors = {"request": {"url": url}}
    _scrub_event(errors)
    assert errors["request"]["url"] == expected
    transaction = {"type": "transaction", "request": {"url": url}}
    out = sentry_module._get_before_send_transaction()(transaction, {})
    assert out["request"]["url"] == expected


def test_the_request_url_is_redacted_when_it_carries_a_password():
    event = {"request": {"url": "https://user:hunter2@host/path"}}
    _scrub_event(event)
    assert event["request"]["url"] == _REDACTED


# Connection strings, webhooks and key-less query credentials (M3, M4)


@pytest.mark.parametrize(
    "name",
    [
        "connection_string",
        "AZURE_STORAGE_CONNECTION_STRING",
        "connectionString",
        "accountKey",
        "SharedAccessKey",
        "SharedAccessSignature",
        "webhook_url",
        "slack_webhook_url",
    ],
)
def test_connection_string_and_webhook_names_are_sensitive(name):
    assert _is_sensitive_key(name)
    assert _scrub_strings({name: "v"}) == {name: _REDACTED}
    assert _scrub_strings(f"{name}=v") == _REDACTED


@pytest.mark.parametrize(
    "value",
    [
        "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=SECRET;"
        "EndpointSuffix=core.windows.net",
        "BlobEndpoint=https://a.blob.core.windows.net/;"
        "SharedAccessSignature=sv=1&sig=SECRET",
        "Endpoint=sb://a.servicebus.windows.net/;SharedAccessKeyName=n;"
        "SharedAccessKey=SECRET",
    ],
)
def test_an_azure_connection_string_under_a_neutral_key_is_redacted(value):
    assert _scrub_strings({"storage": value}) == {"storage": _REDACTED}
    event = {"extra": {"storage": value}, "message": value}
    _scrub_event(event)
    assert "SECRET" not in json.dumps(event)


def test_a_slack_webhook_url_is_redacted_wherever_it_appears():
    url = "https://hooks.slack.com/services/T000/B000/XXXXXXXX"
    assert _scrub_strings(url) == _REDACTED
    assert _scrub_strings(f"POST {url} failed") == _REDACTED
    assert _scrub_strings({"url": url, "n": 1}) == {"url": _REDACTED, "n": 1}
    other = "https://hooks.slack.com/other"
    assert _scrub_strings(other) == other
    event = {
        "type": "transaction",
        "spans": [
            {"op": "http.client", "data": {"url": url}, "description": f"POST {url}"}
        ],
        "breadcrumbs": [{"data": {"url": url}, "message": f"POST {url}"}],
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert "XXXXXXXX" not in json.dumps(out)


def test_query_credentials_without_a_key_name_are_redacted_in_the_query_string():
    event = {
        "request": {
            "query_string": "a=1&key=K1&sig=S1&Signature=S2&X-Amz-Signature=S3"
            "&x-goog-signature=S4&code=C1&state=T1&max_tokens=5"
        }
    }
    _scrub_event(event)
    assert event["request"]["query_string"] == (
        f"a=1&key={_F}&sig={_F}&Signature={_F}&X-Amz-Signature={_F}"
        f"&x-goog-signature={_F}&code={_F}&state={_F}&max_tokens=5"
    )


def test_query_credentials_without_a_key_name_are_redacted_in_urls_and_span_data():
    event = {
        "type": "transaction",
        "request": {"url": "https://a.test/cb?code=C1&state=T1&page=2"},
        "spans": [
            {
                "op": "http.client",
                "data": {
                    "url": "https://s3.test/b/o?X-Amz-Signature=S3&x=1",
                    "http.query": "key=K1&x=1",
                    "http.fragment": "state=T1",
                },
            }
        ],
        "breadcrumbs": [
            {
                "data": {
                    "url": "https://g.test/o?x-goog-signature=S4",
                    "http.query": "sig=S",
                }
            }
        ],
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert "S3" not in json.dumps(out) and "K1" not in json.dumps(out)
    assert out["request"]["url"] == f"https://a.test/cb?code={_F}&state={_F}&page=2"
    assert out["spans"][0]["data"] == {
        "url": f"https://s3.test/b/o?X-Amz-Signature={_F}&x=1",
        "http.query": f"key={_F}&x=1",
        "http.fragment": f"state={_F}",
    }
    assert out["breadcrumbs"][0]["data"] == {
        "url": f"https://g.test/o?x-goog-signature={_F}",
        "http.query": f"sig={_F}",
    }


def test_the_query_parameter_rule_does_not_touch_general_text():
    text = "press a key to continue; state=ok, code of conduct, signature page"
    assert _scrub_strings(text) == text
    assert _scrub_strings({"note": "key=1&sig=2"}) == {"note": "key=1&sig=2"}


# Lows


def test_saml_markers_make_free_text_sensitive():
    assert sentry_module._mentions_sensitive_key("<saml:Assertion ID='x'>")
    assert sentry_module._mentions_sensitive_key("body SAMLResponse=PHNhbWw")
    event = {
        "message": "<samlp:Response>x</samlp:Response>",
        "exception": {"values": [{"type": "E", "value": "bad <saml:Assertion>"}]},
        "breadcrumbs": [{"message": "got <saml:Assertion>"}],
        "logentry": {"formatted": "<saml:Assertion>", "message": "<saml:Assertion>"},
    }
    _scrub_event(event)
    assert event["message"] == _REDACTED
    assert event["exception"]["values"][0]["value"] == _REDACTED
    assert event["breadcrumbs"][0]["message"] == _REDACTED
    assert event["logentry"] == {"formatted": _REDACTED, "message": _REDACTED}


def test_the_query_string_uses_the_same_name_matching_as_other_text():
    event = {"request": {"query_string": "x=1&client_secret=2"}}
    _scrub_event(event)
    assert event["request"]["query_string"] == _REDACTED
    # Count names are kept, as they are in other text.
    event = {"request": {"query_string": "max_tokens=5&page=2"}}
    _scrub_event(event)
    assert event["request"]["query_string"] == "max_tokens=5&page=2"
    event = {"request": {"query_string": "u=postgres://a:pw@h/db"}}
    _scrub_event(event)
    assert event["request"]["query_string"] == _REDACTED


@pytest.mark.parametrize(
    "body, expected",
    [
        (["Input(customer_api_key='k')", "ok"], [_REDACTED, "ok"]),
        ("customer_api_key=k&x=1", _REDACTED),
        ([{"password": "p", "n": 1}], [{"password": _REDACTED, "n": 1}]),
        ("plain text body", "plain text body"),
    ],
)
def test_a_request_body_that_is_a_list_or_a_string_is_scrubbed(body, expected):
    event = {"request": {"data": body}}
    _scrub_event(event)
    assert event["request"]["data"] == expected


@pytest.mark.parametrize(
    "event",
    [
        {"exception": "x"},
        {"exception": {"values": [{"stacktrace": "x"}, 3, {"stacktrace": {}}]}},
        {"exception": {"values": [{"stacktrace": {"frames": "x"}}]}},
        {"exception": {"values": [{"stacktrace": {"frames": [3, {"vars": 1}]}}]}},
        {"threads": "x"},
        {"threads": {"values": [3, {"stacktrace": ["a"]}]}},
        {"stacktrace": ["a"]},
        {"stacktrace": {"frames": [None]}},
    ],
)
def test_odd_stack_trace_shapes_do_not_raise(event):
    _scrub_event(event)


def test_frames_are_still_scrubbed_in_every_stack_trace_the_event_holds():
    def frames():
        return {"frames": [{"vars": {"v": "Input(customer_api_key='k')"}}]}

    event = {
        "exception": {"values": [{"stacktrace": frames()}]},
        "threads": {"values": [{"stacktrace": frames()}]},
        "stacktrace": frames(),
    }
    _scrub_event(event)
    assert "customer_api_key" not in json.dumps(event)


def test_the_dynamic_sampling_context_keeps_the_transaction_name():
    dsc = {"transaction": "/api/token/refresh/", "public_key": "pk", "trace_id": "t"}
    event = {
        "type": "transaction",
        "contexts": {
            "trace": {"trace_id": "t", "dynamic_sampling_context": dict(dsc)},
            "c": {"password": "leak"},
        },
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert out["contexts"]["trace"]["dynamic_sampling_context"] == dsc
    assert out["contexts"]["trace"]["trace_id"] == "t"
    assert out["contexts"]["c"] == {"password": _REDACTED}


# Review 2: redis breadcrumbs, secret-looking values, encoded queries


@pytest.mark.parametrize(
    "key, expected_message",
    [
        ("cache:plain", "SET 'cache:plain'"),
        ("mcp_oauth_code:{key_secret}", f"SET 'mcp_oauth_code:{_REDACTED}'"),
    ],
)
def test_a_redis_breadcrumb_on_an_error_event_keeps_only_command_and_key(
    monkeypatch, key, expected_message
):
    # The SDK turns every redis span into a breadcrumb whose message is the
    # description, with each argument (the pickled value) in it.
    value_secret = secrets.token_hex(16)
    key_secret = secrets.token_hex(16)
    key = key.format(key_secret=key_secret)
    monkeypatch.setattr(redis.Redis, "_execute_command", lambda self, *a, **kw: True)

    def action():
        redis.Redis().set(key, pickle.dumps({"code": value_secret}), px=1000)
        logging.getLogger("probe").error("failed")

    sent = _sent_through_client(
        action,
        integrations=[
            RedisIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    [event] = [e for e in sent if e.get("type") != "transaction"]
    crumbs = [c for c in event["breadcrumbs"]["values"] if c.get("category") == "redis"]
    assert [c["message"] for c in crumbs] == [expected_message]
    assert value_secret not in json.dumps(sent, default=str)
    assert key_secret not in json.dumps(sent, default=str)


def test_a_redis_breadcrumb_with_command_data_is_cut_back_too():
    event = {
        "breadcrumbs": [
            {
                "category": "redis",
                "message": "SET 'k' b'\\x80LEAK'",
                "data": {
                    "redis.key": "aws_onboard:KEYLEAK",
                    "redis.commands": {
                        "count": 1,
                        "first_ten": ["SET 'k' b'\\x80DATALEAK'", 3],
                    },
                },
            },
            {"category": "query", "message": "SET 'k' plain text"},
        ]
    }
    _scrub_event(event)
    redis_crumb, other = event["breadcrumbs"]
    assert redis_crumb["message"] == "SET 'k'"
    assert redis_crumb["data"]["redis.key"] == f"aws_onboard:{_REDACTED}"
    assert redis_crumb["data"]["redis.commands"] == {
        "count": 1,
        "first_ten": ["SET 'k'", _REDACTED],
    }
    assert "LEAK" not in json.dumps(redis_crumb)
    # Only redis crumbs are cut.
    assert other["message"] == "SET 'k' plain text"


_NEUTRAL_SECRET_VALUES = [
    "https://hooks.slack.com/services/T0/B0/XYZ",
    "postgres://app:pw-XYZ@db.internal:5432/app",
    "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=XYZ;EndpointSuffix=x",
    "BlobEndpoint=https://a.blob.core.windows.net/;SharedAccessSignature=sv=1&sig=XYZ",
    "Endpoint=sb://a.servicebus.windows.net/;SharedAccessKeyName=n;SharedAccessKey=XYZ",
]


def test_a_slack_webhook_under_its_own_field_name_is_redacted():
    url = "https://hooks.slack.com/services/T0/B0/XYZ"
    event = {"request": {"data": {"notify_slack_webhook": url}}}
    _scrub_event(event)
    assert "XYZ" not in json.dumps(event)
    assert _is_sensitive_key("notify_slack_webhook")


@pytest.mark.parametrize("value", _NEUTRAL_SECRET_VALUES)
@pytest.mark.parametrize("section", ["headers", "data", "env"])
def test_a_secret_value_under_a_neutral_key_is_redacted_in_the_request(section, value):
    event = {
        "request": {
            section: {"target": value, "nested": {"list": [value, "ok"]}, "n": "ok"}
        }
    }
    _scrub_event(event)
    assert "XYZ" not in json.dumps(event)
    assert event["request"][section]["n"] == "ok"
    assert event["request"][section]["nested"]["list"][1] == "ok"


def test_a_plain_url_value_is_kept_in_the_request():
    event = {"request": {"data": {"target": "https://example.com/a?b=1"}}}
    _scrub_event(event)
    assert event["request"]["data"] == {"target": "https://example.com/a?b=1"}


@pytest.mark.parametrize(
    "query",
    [
        "x=1&api%5Fkey=K1",
        "x=1&client%5Fsecret=K1",
        "x=1&api+key=K1",
        "x=1&API%5FKEY=K1",
    ],
)
def test_an_encoded_credential_name_in_the_query_string_is_caught(query):
    event = {"request": {"query_string": query}}
    _scrub_event(event)
    assert event["request"]["query_string"] == _REDACTED
    assert sentry_module._scrub_url(f"https://a.test/p?{query}").endswith(
        f"?{_REDACTED}"
    )


def test_an_encoded_keyless_credential_parameter_is_redacted():
    event = {"request": {"query_string": "a=1&%73ig=S1&Sig%6Eature=S2&max%5Ftokens=5"}}
    _scrub_event(event)
    assert event["request"]["query_string"] == (
        f"a=1&%73ig={_REDACTED}&Sig%6Eature={_REDACTED}&max%5Ftokens=5"
    )


def test_a_logged_line_with_no_arguments_loses_the_token_in_a_cache_key():
    token = secrets.token_hex(16)
    event = {
        "logentry": {
            "message": f"onboarding failed aws_onboard:{token}",
            "formatted": f"onboarding failed aws_onboard:{token}",
        }
    }
    _scrub_event(event)
    assert event["logentry"] == {
        "message": f"onboarding failed aws_onboard:{_REDACTED}",
        "formatted": f"onboarding failed aws_onboard:{_REDACTED}",
    }


def test_an_fstring_error_line_with_a_cache_key_token_leaks_nothing():
    token = secrets.token_hex(16)

    def action():
        logging.getLogger("probe").error(f"onboarding failed aws_onboard:{token}")

    sent = _sent_through_client(action)
    assert token not in json.dumps(sent, default=str)
    assert any(f"aws_onboard:{_REDACTED}" in json.dumps(e) for e in sent)


def test_a_url_source_transaction_name_loses_its_path_token():
    token = secrets.token_hex(16)
    name = f"/tracer/shared/{token}"
    event = {
        "type": "transaction",
        "transaction": name,
        "transaction_info": {"source": "url"},
        "contexts": {
            "trace": {
                "trace_id": "t",
                "dynamic_sampling_context": {"transaction": name, "public_key": "pk"},
            }
        },
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert out["transaction"] == f"/tracer/shared/{_REDACTED}"
    dsc = out["contexts"]["trace"]["dynamic_sampling_context"]
    assert dsc == {"transaction": f"/tracer/shared/{_REDACTED}", "public_key": "pk"}
    assert token not in json.dumps(out)


def test_a_url_source_transaction_name_is_scrubbed_on_error_events_too():
    token = secrets.token_hex(16)
    event = {
        "transaction": f"/accounts/activate/{token}/{token}",
        "transaction_info": {"source": "url"},
    }
    _scrub_event(event)
    assert token not in json.dumps(event)


@pytest.mark.parametrize("source", ["route", "view", "component", None])
def test_a_route_source_transaction_name_is_left_alone(source):
    name = "/tracer/shared/{token}"
    event = {"transaction": name, "transaction_info": {"source": source}}
    if source is None:
        event = {"transaction": name}
    _scrub_event(event)
    assert event["transaction"] == name


@pytest.mark.parametrize("name", ["input_tokens", "output_tokens"])
def test_input_and_output_token_counts_are_kept(name):
    assert not _is_sensitive_key(name)
    assert not _is_sensitive_key(f"gen_ai.usage.{name}")
    assert _scrub_strings({f"gen_ai.usage.{name}": 12}) == {f"gen_ai.usage.{name}": 12}
    assert _scrub_strings(f"{name}=12") == f"{name}=12"
    # A text that also names a real key is still caught, and the name is
    # removed only as a whole word.
    assert _scrub_strings(f"{name}=12 api_key=k") == _REDACTED
    assert _is_sensitive_key(f"x{name}")
    assert _is_sensitive_key(f"{name}2")
    assert _is_sensitive_key(f"{name}_secret")


def test_llm_span_usage_data_survives_the_transaction_hook():
    event = {
        "type": "transaction",
        "spans": [
            {
                "op": "gen_ai.chat",
                "data": {
                    "gen_ai.usage.input_tokens": 5,
                    "gen_ai.usage.output_tokens": 7,
                    "gen_ai.usage.api_key": "k",
                },
            }
        ],
    }
    out = sentry_module._get_before_send_transaction()(event, {})
    assert out["spans"][0]["data"] == {
        "gen_ai.usage.input_tokens": 5,
        "gen_ai.usage.output_tokens": 7,
        "gen_ai.usage.api_key": _REDACTED,
    }


# A template that names a key, a template that mixes an f-string with `%s`, and
# strings cut before the scrub (TH-9835)


def test_a_template_that_names_a_key_loses_every_argument():
    event = {
        "logentry": {
            "message": "calling with api_key=%s",
            "params": ["sk-1"],
            "formatted": "calling with api_key=sk-1",
        }
    }
    _scrub_event(event)
    assert event["logentry"]["params"] == [_REDACTED]
    assert event["logentry"]["message"] == "calling with api_key=%s"
    assert "sk-1" not in json.dumps(event)


def test_a_template_that_names_a_key_loses_every_named_argument():
    event = {
        "logentry": {
            "message": "calling %(url)s with api_key=%(k)s",
            "params": {"url": "https://example.test", "k": "sk-1"},
        }
    }
    _scrub_event(event)
    assert event["logentry"]["params"] == {"url": _REDACTED, "k": _REDACTED}
    assert "sk-1" not in json.dumps(event)


def test_a_real_log_call_with_a_key_in_its_template_leaks_nothing():
    secret = secrets.token_hex(16)

    def action():
        logging.getLogger("probe").error("api_key=%s", secret)

    sent = _sent_through_client(action)
    assert sent
    assert secret not in json.dumps(sent, default=str)
    assert sent[0]["logentry"]["message"] == "api_key=%s"
    assert sent[0]["logentry"]["params"] == [_REDACTED]


def test_the_logging_modules_frames_lose_the_arguments_of_a_key_naming_template():
    frame = {
        "module": "logging",
        "function": "error",
        "vars": {"self": "<Logger probe>", "msg": "'api_key=%s'", "args": "('sk-1',)"},
    }
    other = {
        "module": "app.views",
        "function": "error",
        "vars": {"msg": "'api_key=%s'", "args": "('sk-1',)"},
    }
    event = {"exception": {"values": [{"stacktrace": {"frames": [frame, other]}}]}}
    _scrub_event(event)
    assert frame["vars"]["args"] == _REDACTED
    assert frame["vars"]["self"] == "<Logger probe>"
    assert "sk-1" not in json.dumps(frame)


def test_the_logging_modules_frames_keep_the_arguments_of_a_plain_template():
    frame = {
        "module": "logging",
        "function": "error",
        "vars": {"msg": "'took %d ms'", "args": "(12,)"},
    }
    event = {"stacktrace": {"frames": [frame]}}
    _scrub_event(event)
    assert frame["vars"] == {"msg": "'took %d ms'", "args": "(12,)"}


def test_a_template_that_mixes_an_fstring_secret_with_a_percent_s_leaks_nothing():
    secret = secrets.token_hex(16)

    def action():
        logging.getLogger("probe").error(f"token {secret} for %s", "bob")

    sent = _sent_through_client(action)
    assert sent
    assert secret not in json.dumps(sent, default=str)
    assert sent[0]["logentry"]["message"] == _REDACTED


@pytest.mark.parametrize(
    "template",
    [
        "onboarding aws_onboard:{s} for %s",
        "posting to https://hooks.slack.com/services/{s} for %s",
        "connecting to postgres://app:{s}@db:5432/app for %s",
    ],
)
def test_a_template_with_a_cache_key_or_a_secret_value_loses_it(template):
    secret = secrets.token_hex(16)
    line = template.format(s=secret)

    def action():
        logging.getLogger("probe").error(line, "bob")

    sent = _sent_through_client(action)
    assert sent
    assert secret not in json.dumps(sent, default=str)


def test_a_plain_template_with_arguments_is_kept_whole():
    def action():
        logging.getLogger("probe").error("user %s hit %d errors", "bob", 3)

    sent = _sent_through_client(action)
    assert sent[0]["logentry"]["message"] == "user %s hit %d errors"
    assert sent[0]["logentry"]["params"] == ["bob", 3]
    assert sent[0]["logentry"]["formatted"] == "user bob hit 3 errors"


@dataclass
class _LongInput:
    # The key name comes after the long text in the repr.
    system_prompt: str
    user_api_key: str


def test_a_key_after_a_long_text_is_found_through_the_configured_limit():
    secret = secrets.token_hex(16)

    def action():
        holder = _LongInput(system_prompt="prompt text " * 2_000, user_api_key=secret)
        logging.getLogger("probe").error("failed")
        return holder

    sent = _sent_through_client(action)
    payload = json.dumps(sent, default=str)
    assert secret not in payload
    # Built here, so the test's own source lines in the event do not match it.
    assert "prompt text " * 2 not in payload
    [frame_vars] = [
        f["vars"]
        for f in iter_event_frames(sent[0])
        if "holder" in (f.get("vars") or {})
    ]
    assert frame_vars["holder"] == _REDACTED


def test_a_long_harmless_string_is_capped_in_the_sent_event():
    def action():
        blob = "x" * 100_000
        logging.getLogger("probe").error("failed")
        return blob

    sent = _sent_through_client(action)
    [frame_vars] = [
        f["vars"] for f in iter_event_frames(sent[0]) if "blob" in (f.get("vars") or {})
    ]
    assert len(frame_vars["blob"]) == _MAX_FORMATTED
    assert frame_vars["blob"].endswith("...")
    assert max(len(s) for s in _strings_in(sent)) <= _MAX_FORMATTED


def _strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_in(item)


def test_both_hooks_cap_every_string_to_the_final_limit():
    long_text = "y" * 50_000
    error = sentry_module._get_before_send()(
        {
            "extra": {"a": [{"b": long_text}]},
            "breadcrumbs": {"values": [{"message": "m", "data": {"d": long_text}}]},
            "exception": {
                "values": [{"stacktrace": {"frames": [{"vars": {"v": long_text}}]}}]
            },
        },
        {},
    )
    transaction = sentry_module._get_before_send_transaction()(
        {
            "type": "transaction",
            "extra": {"a": long_text},
            "spans": [{"op": "x", "data": {"d": long_text}, "description": "q"}],
        },
        {},
    )
    for event in (error, transaction):
        strings = list(_strings_in(event))
        assert strings
        assert max(len(s) for s in strings) == _MAX_FORMATTED
    assert error["extra"]["a"][0]["b"] == "y" * (_MAX_FORMATTED - 3) + "..."


def test_the_final_cap_counts_bytes_like_the_sdk():
    out = sentry_module._get_before_send()({"extra": {"k": "\u00e9" * 20_000}}, {})
    cut = out["extra"]["k"]
    assert len(cut.encode("utf-8")) <= _MAX_FORMATTED
    assert cut.endswith("...")
    # A text that fits is left alone.
    short = sentry_module._get_before_send()({"extra": {"k": "\u00e9" * 8_000}}, {})
    assert short["extra"]["k"] == "\u00e9" * 8_000
