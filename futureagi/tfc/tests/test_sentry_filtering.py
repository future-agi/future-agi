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

import pytest

import tfc.logging.sentry as sentry_logging
from tfc.logging.sentry import (
    _REDACTED,
    _get_before_send,
    _is_sensitive_key,
    _scrub,
    _scrub_deployment_telemetry_event,
    _scrub_event,
)

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


# MCP and connector secrets (review fix round 1): the vault's create and
# rotate, the catalogue add and the connection probe carry a customer's secret
# under names the key scrubber cannot tell from ordinary ones.

CANARY = "fake-fr1-sentry-canary-not-real"
MCP_SECRET_PATHS = (
    "/agentcc/mcp/credentials/",
    "/agentcc/mcp/credentials/0b7c9a3e-5f1d-4c39-9d1e-2a6f3b8c4d10/rotate/",
    "/agentcc/connectors/apps/acme-docs/add/",
    "/agentcc/mcp/probe/",
)


def _frame(module, variables):
    return {"module": module, "function": "f", "vars": variables}


def _event_with_frames(frames, request=None, thread_frames=()):
    event = {
        "logger": "django.request",
        "message": "Internal Server Error",
        "exception": {"values": [{"stacktrace": {"frames": list(frames)}}]},
        "threads": {"values": [{"stacktrace": {"frames": list(thread_frames)}}]},
    }
    if request is not None:
        event["request"] = request
    return event


def _secret_route_event(path):
    return _event_with_frames(
        [
            _frame("rest_framework.views", {"request": "<Request>", "body": CANARY}),
            _frame("agentcc.views.mcp_credentials", {"data": {"value": CANARY}}),
        ],
        request={
            "url": f"https://app.example.test{path}",
            "method": "POST",
            "data": {
                "value": CANARY,
                "credential": {"value": CANARY},
                "auth": {"type": "bearer", "value": CANARY},
            },
        },
        thread_frames=[_frame("django.core.handlers.base", {"raw": CANARY})],
    )


def _all_frames(event):
    return [
        frame
        for section in ("exception", "threads")
        for value in event[section]["values"]
        for frame in value["stacktrace"]["frames"]
    ]


@pytest.mark.parametrize("path", MCP_SECRET_PATHS)
def test_before_send_strips_mcp_secret_routes(path):
    out = before_send(_secret_route_event(path), {})
    assert out is not None
    assert CANARY not in json.dumps(out)
    assert "data" not in out["request"]
    assert out["request"]["url"].endswith(path)
    assert all("vars" not in frame for frame in _all_frames(out))


@pytest.mark.parametrize("path", MCP_SECRET_PATHS)
def test_transaction_hook_strips_mcp_secret_routes(path):
    event = _secret_route_event(path)
    event["type"] = "transaction"
    out = sentry_logging._before_send_transaction(event, {})
    assert CANARY not in json.dumps(out)
    assert "data" not in out["request"]


def test_other_routes_keep_their_body_and_frames():
    event = _event_with_frames(
        [_frame("tracer.views.spans", {"value": "kept", "token": "t"})],
        request={
            "url": "https://app.example.test/agentcc/org-configs/",
            "method": "POST",
            "data": {"name": "kept", "token": "t"},
        },
    )
    out = before_send(event, {})
    assert out["request"]["data"] == {"name": "kept", "token": _REDACTED}
    (frame,) = out["exception"]["values"][0]["stacktrace"]["frames"]
    assert frame["vars"] == {"value": "kept", "token": _REDACTED}


def test_agentcc_frames_lose_the_names_that_hold_a_secret():
    """With no request on the event (a push from a task), the app's agentcc
    frames lose value and credential, nested too; other frames keep them."""
    event = _event_with_frames(
        [
            _frame(
                "agentcc.services.mcp_credentials",
                {
                    "value": CANARY,
                    "pushed": {"github": {"type": "bearer", "value": CANARY}},
                    "server_id": "github",
                },
            ),
            _frame(
                "agentcc.views.connector_catalog",
                {
                    "data": {"credential": {"value": CANARY}, "mode": "agent"},
                    "credential": CANARY,
                    "Credentials": [CANARY],
                    "app_id": "acme-docs",
                },
            ),
            _frame("tfc.utils.api_contracts", {"value": "kept"}),
        ],
        thread_frames=[_frame("agentcc.services.config_push", {"value": CANARY})],
    )
    out = before_send(event, {})
    assert CANARY not in json.dumps(out)
    vault, view, contracts = out["exception"]["values"][0]["stacktrace"]["frames"]
    assert vault["vars"] == {
        "value": _REDACTED,
        "pushed": {"github": {"type": "bearer", "value": _REDACTED}},
        "server_id": "github",
    }
    assert view["vars"] == {
        "data": {"credential": _REDACTED, "mode": "agent"},
        "credential": _REDACTED,
        "Credentials": _REDACTED,
        "app_id": "acme-docs",
    }
    assert contracts["vars"] == {"value": "kept"}


def test_agentcc_frames_lose_a_raw_gateway_key():
    """A new gateway key is held in clear in the app's agentcc frames: as
    ``raw_key`` (release's key create, the Omega EE agent key mint) and as
    ``key`` in the gateway's answer to the create. Both are redacted, with or
    without a request on the event; another frame keeps an ordinary ``key``."""
    key = "fake-fr2-gateway-key-canary-not-real"
    event = _event_with_frames(
        [
            _frame(
                "agentcc.services.auth_bridge",
                {
                    "raw_key": key,
                    "result": {"id": "key_1", "key": key, "key_prefix": "fake-fr2"},
                    "name": "CI key",
                },
            ),
            _frame(
                "agentcc.services.mcp_service_provisioning",
                {"raw_key": key, "made": "key_1"},
            ),
            _frame("tracer.views.spans", {"key": "kept"}),
        ],
        thread_frames=[
            _frame("agentcc.views.mcp_service_provisioning", {"raw_key": key})
        ],
    )
    out = before_send(event, {})
    assert key not in json.dumps(out)
    bridge, mint, other = out["exception"]["values"][0]["stacktrace"]["frames"]
    assert bridge["vars"] == {
        "raw_key": _REDACTED,
        "result": {"id": "key_1", "key": _REDACTED, "key_prefix": "fake-fr2"},
        "name": "CI key",
    }
    assert mint["vars"] == {"raw_key": _REDACTED, "made": "key_1"}
    assert other["vars"] == {"key": "kept"}

    served = _event_with_frames(
        [_frame("agentcc.views.api_key", {"raw_key": key, "result": {"key": key}})],
        request={
            "url": "https://app.example.test/agentcc/api-keys/",
            "method": "POST",
            "data": {"name": "CI key"},
        },
    )
    out = before_send(served, {})
    assert key not in json.dumps(out)
    assert out["request"]["data"] == {"name": "CI key"}
