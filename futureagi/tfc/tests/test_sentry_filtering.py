"""Tests for Sentry noise filtering and PII scrubbing in `tfc.logging.sentry`.

These exercise the pure `before_send` pipeline and the scrubbing helpers, which
import without the Sentry SDK or Django settings. They lock in the two things
that keep the issue stream clean and safe:

- Infrastructure / expected events are dropped (logger prefix, ignored
  explicitly classified log-only outcomes).
- Real errors survive, with their secrets/PII redacted before send.
"""

from __future__ import annotations

from types import SimpleNamespace

from sentry_sdk.scope import global_event_processors

from tfc.logging.sentry import (
    _REDACTED,
    _event_scrubber,
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


def _log_hint(logger_name, exc=None):
    """The hint Sentry's logging integration passes for a log record."""
    hint = _hint(exc) if exc is not None else {}
    hint["log_record"] = SimpleNamespace(name=logger_name)
    return hint


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


def test_keeps_disconnects_and_security_failures():
    from django.core.exceptions import (
        DisallowedHost,
        SuspiciousFileOperation,
        SuspiciousOperation,
    )

    for error in (
        ConnectionResetError(104, "reset"),
        BrokenPipeError(),
        SuspiciousOperation(),
        DisallowedHost("Invalid HTTP_HOST header"),
    ):
        assert before_send({}, _hint(error)) is not None

    # Django logs each security failure on django.security.<ClassName>. Only
    # the invalid Host header (scanner traffic answered with 400) is expected.
    for error in (SuspiciousOperation("tampered"), SuspiciousFileOperation("path")):
        name = f"django.security.{type(error).__name__}"
        event = {"logger": name, "message": str(error)}
        assert before_send(event, _log_hint(name, error)) is not None
    name = "django.security.DisallowedHost"
    error = DisallowedHost("Invalid HTTP_HOST header: 'scanner.example.invalid'.")
    event = {"logger": name, "message": str(error)}
    assert before_send(event, _log_hint(name, error)) is None
    # A child or similarly named logger is not the expected one.
    for other in ("django.security.DisallowedHost.extra", "django.security"):
        assert before_send({"logger": other}, _log_hint(other, error)) is not None


def test_keeps_http_errors_without_explicit_outcome_classification():
    for code in (400, 401, 403, 404, 422, 429, 500, 503):
        assert before_send({}, _hint(_Exc(code))) is not None


def test_keeps_messages_that_only_resemble_expected_outcomes():
    for message in (
        "trace_payload_not_found_in_redis",
        "Failed to load vertex credentials",
    ):
        assert before_send({"message": message}, {}) is not None


def test_drops_litellm_own_logger_events_but_keeps_ours():
    """LiteLLM logs a credential failure at ERROR before raising it to our code,
    which owns the outcome. Its logger is filtered by name, not by message."""
    error = ValueError("Could not deserialize key data.")
    message = f"Failed to load vertex credentials. Error: {error}"
    event = {"logger": "LiteLLM", "message": message}
    assert before_send(event, _log_hint("LiteLLM", error)) is None
    # The same failure logged or captured by our own code is still an issue.
    for name in ("model_hub.utils.utils", "LiteLLM Router", "litellm"):
        event = {"logger": name, "message": message}
        assert before_send(event, _log_hint(name, error)) is not None
    # Only records LiteLLM itself logged qualify, not a direct capture.
    assert before_send({"logger": "LiteLLM"}, _hint(error)) is not None


def test_expected_outcome_loggers_are_dropped_by_before_send_alone():
    """Every client init_sentry creates gets the drop through before_send; no
    import-time global processor applies it to unrelated (test) clients."""
    assert not [
        processor
        for processor in global_event_processors
        if getattr(processor, "__module__", "").startswith("tfc.")
    ]
    for name in ("django.security.DisallowedHost", "LiteLLM"):
        assert before_send({"logger": name}, _log_hint(name)) is None


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
