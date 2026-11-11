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
from dataclasses import dataclass

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scope import ScopeType, use_isolation_scope, use_scope
from sentry_sdk.utils import iter_event_frames
from temporalio.worker import ExecuteActivityInput

from tfc.logging.sentry import (
    _REDACTED,
    _event_scrubber,
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


class _LoggingNext:
    """Logs an error without exc_info, which sends an event with `threads`."""

    async def execute_activity(self, input):
        room_name = "r"
        attempts = 3
        # Deliberate: a repr that mentions a sensitive substring is wiped
        # whole, so this harmless token count is lost with the secrets.
        usage_info = _Usage(prompt_tokens=10)
        logging.getLogger("x").info("calling with customer_api_key=%s", "k")
        logging.getLogger("x").error("failed")
        return room_name, attempts, usage_info


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


async def test_harmless_frame_locals_are_kept_and_token_count_reprs_are_wiped():
    sent = await _run_activity(_LoggingNext(), _new_secret_values())
    local_vars = [f["vars"] for f in iter_event_frames(sent[0]) if f.get("vars")]
    [frame_vars] = [v for v in local_vars if "usage_info" in v]
    assert frame_vars["room_name"] == "'r'"
    assert frame_vars["attempts"] == "3"
    assert frame_vars["usage_info"] == _REDACTED
