"""Tests for Sentry noise filtering and PII scrubbing in `tfc.logging.sentry`.

These exercise the pure `before_send` pipeline and the scrubbing helpers, which
import without the Sentry SDK or Django settings. They lock in the two things
that keep the issue stream clean and safe:

- Infrastructure / expected events are dropped (logger prefix, ignored
  exception types, expected 4xx, expected log messages).
- Real errors survive, with their secrets/PII redacted before send.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
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
        "logentry": {"message": "progress 100%", "params": [], "formatted": "progress 100%"}
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
        max_value_length=16_384,
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
        "max_value_length": _MAX_FORMATTED,
    }
    options.update(overrides)
    client = sentry_sdk.Client(**options)
    isolation = sentry_sdk.Scope(client=client, ty=ScopeType.ISOLATION)
    with use_isolation_scope(isolation), use_scope(sentry_sdk.Scope(client=client)):
        action()
    client.flush()
    return sent


@pytest.fixture
def scrub_logger_ignored():
    # init_sentry ignores this logger; the tests that make the scrub fail need
    # it ignored too, or its own error record would re-enter the hook.
    ignore_logger(sentry_module.SCRUB_LOGGER_NAME)


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


def test_a_failing_scrub_drops_the_event_and_logs_only_the_failure(
    monkeypatch, caplog, scrub_logger_ignored
):
    secret = secrets.token_hex(16)
    monkeypatch.setattr(sentry_module, "_scrub_strings", _boom)

    def action():
        sentry_sdk.add_breadcrumb(message="m", data={"customer_api_key": secret})
        logging.getLogger("x").error("failed")

    with caplog.at_level(logging.INFO):
        sent = _sent_through_client(action)
    assert sent == []
    records = [r for r in caplog.records if r.name == sentry_module.SCRUB_LOGGER_NAME]
    assert [r.getMessage() for r in records] == ["sentry_scrub_failed: TypeError"]
    assert records[0].exc_info is None
    assert secret not in caplog.text


def test_a_failing_scrub_drops_a_transaction(
    monkeypatch, caplog, scrub_logger_ignored
):
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
    assert "sentry_scrub_failed: TypeError" in caplog.text


def test_a_failing_telemetry_scrub_also_drops_the_event(
    monkeypatch, scrub_logger_ignored
):
    monkeypatch.setattr(sentry_module, "_scrub_deployment_telemetry_event", _boom)
    assert before_send({"message": "x"}, {}) is None
    assert sentry_module._get_before_send_transaction()({"type": "t"}, {}) is None


def _init_sentry_kwargs(monkeypatch):
    seen = {}
    ignored = []
    monkeypatch.setattr(sentry_module, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry_module, "SENTRY_DSN", "http://k@localhost.invalid/1")
    monkeypatch.setattr(sentry_module, "_initialized_components", set())
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(
        "sentry_sdk.integrations.logging.ignore_logger", ignored.append
    )
    assert sentry_module.init_sentry(component="temporal")
    return seen, ignored


def test_init_sentry_ignores_the_scrub_logger_and_wires_the_hooks(monkeypatch):
    seen, ignored = _init_sentry_kwargs(monkeypatch)
    assert sentry_module.SCRUB_LOGGER_NAME in ignored
    assert seen["max_value_length"] == _MAX_FORMATTED
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
