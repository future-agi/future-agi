"""Tests for Sentry noise filtering and PII scrubbing in `tfc.logging.sentry`.

These exercise the pure `before_send` pipeline and the scrubbing helpers, which
import without the Sentry SDK or Django settings. They lock in the two things
that keep the issue stream clean and safe:

- Infrastructure / expected events are dropped (logger prefix, ignored
  explicitly classified log-only outcomes).
- Real errors survive, with their secrets/PII redacted before send.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import grpc
import pytest
import sentry_sdk
from google.auth.exceptions import InvalidValue, MalformedError, RefreshError
from google.oauth2 import service_account
from sentry_sdk.scope import global_event_processors

from tfc.logging import sentry
from tfc.logging.sentry import (
    _REDACTED,
    _before_breadcrumb,
    _event_scrubber,
    _get_before_send,
    _is_sensitive_key,
    _scrub,
    _scrub_event,
)
from tfc.tests.sentry_capture import EnvelopeTransport

before_send = _get_before_send()


class _Exc(Exception):
    """Exception carrying a status_code attribute, like DRF/HTTP exceptions."""

    def __init__(self, status_code):
        super().__init__("boom")
        self.status_code = status_code


def _hint(exc):
    return {"exc_info": (type(exc), exc, None)}


def _log_hint(logger_name, exc=None, msg=None):
    """The hint Sentry's logging integration passes for a log record."""
    hint = _hint(exc) if exc is not None else {}
    hint["log_record"] = SimpleNamespace(
        name=logger_name, msg=msg, exc_info=hint.get("exc_info")
    )
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
    for code in (400, 401, 402, 403, 404, 422, 429, 500, 503):
        assert before_send({}, _hint(_Exc(code))) is not None


def test_feature_denial_requires_the_actual_exception_type():
    class FeatureUnavailable(Exception):
        status_code = 402

    assert before_send({}, _hint(FeatureUnavailable("synthetic"))) is not None
    event = {
        "exception": {"values": [{"type": "FeatureUnavailable", "value": "synthetic"}]}
    }
    # No live exception in the hint: serialized names are not a type check.
    for hint in ({}, {"exc_info": (None,)}, {"exc_info": "FeatureUnavailable"}):
        assert before_send(event, hint) is event


def test_keeps_messages_that_only_resemble_expected_outcomes():
    for message in (
        "trace_payload_not_found_in_redis",
        "Failed to load vertex credentials",
    ):
        assert before_send({"message": message}, {}) is not None


def test_only_drops_litellm_credential_records_from_its_own_logger():
    with pytest.raises(MalformedError, match="missing fields token_uri") as caught:
        service_account.Credentials.from_service_account_info(
            {"type": "service_account", "client_email": "test@example.invalid"}
        )
    error = caught.value
    msg = (
        "Failed to load vertex credentials. Check to see if credentials "
        f"containing partial/invalid information. Error: {error}"
    )
    event = {"logger": "LiteLLM", "message": msg}
    assert before_send(event, _log_hint("LiteLLM", error, msg)) is None
    # LiteLLM also catches SDK setup and network failures at this call site.
    for unexpected in (None, ImportError(), ConnectionError()):
        assert before_send(event, _log_hint("LiteLLM", unexpected, msg)) is event
    # The same words in a direct capture or elsewhere in an error aren't enough.
    assert before_send(event, _hint(error)) is event
    for other_msg in (f"unexpected parsing error: {msg}", None, {"message": msg}):
        assert before_send(event, _log_hint("LiteLLM", error, other_msg)) is event
    for name in ("model_hub.utils.utils", "LiteLLM Router", "LiteLLM.child", "litellm"):
        event = {"logger": name, "message": msg}
        assert before_send(event, _log_hint(name, error, msg)) is event


@pytest.fixture
def vertex_capture(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from litellm.llms.vertex_ai.vertex_llm_base import VertexBase

    # Keep the production hooks, frame policy and scrubber. Replace the
    # transport and don't auto-instrument unrelated packages in the test process.
    transport = EnvelopeTransport()
    real_init = sentry_sdk.init

    def recording_init(**options):
        return real_init(**options, transport=transport, default_integrations=False)

    monkeypatch.setattr(sentry_sdk, "init", recording_init)
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry, "SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(sentry, "_initialized_components", set())
    scope = sentry_sdk.get_global_scope()
    previous = scope.client
    with sentry_sdk.isolation_scope() as isolated:
        isolated.clear_breadcrumbs()
        try:
            assert sentry.init_sentry(component="temporal")
            assert not sentry_sdk.get_client().options["include_local_variables"]
            yield VertexBase(), transport.events
        finally:
            if scope.client is not previous:
                scope.client.close()
            scope.set_client(previous)


# Deliberately invalid test values, never signing material or usable tokens.
_TEST_KEY = "SENTINEL-PK-vertex-privacy"
_TEST_SECRET = "SENTINEL-CS-vertex-privacy"
_TEST_REFRESH = "SENTINEL-RT-vertex-privacy"


def _capture_later_failure():
    # A separate traceback, so the only shared context is the breadcrumb trail.
    try:
        raise LookupError("unrelated failure")
    except LookupError:
        sentry_sdk.capture_exception()


@pytest.mark.parametrize(
    ("endpoint_result", "error_type", "match", "event_count"),
    [
        pytest.param(
            (
                True,
                {"access_token": "test-token", "expires_in": "not an integer"},
                False,
            ),
            ValueError,
            "not an integer",
            1,
            id="malformed-expires-in",
        ),
        pytest.param(
            (
                False,
                {"error": "invalid_grant", "error_description": "Token revoked"},
                False,
            ),
            RefreshError,
            "invalid_grant",
            0,
            id="invalid-grant",
        ),
        pytest.param(
            (False, {"error": "invalid_client"}, False),
            RefreshError,
            "invalid_client",
            0,
            id="invalid-client",
        ),
        pytest.param(
            (
                False,
                {
                    "error": "server_error",
                    "error_description": "invalid_grant: Token revoked",
                },
                True,
            ),
            RefreshError,
            "server_error",
            1,
            id="server-error",
        ),
        pytest.param(
            (
                False,
                {"error": "invalid_grant", "error_description": "Token revoked"},
                True,
            ),
            RefreshError,
            "invalid_grant",
            1,
            id="retryable-invalid-grant",
        ),
        pytest.param(None, InvalidValue, _TEST_KEY, 1, id="invalid-key-value"),
        pytest.param(
            RuntimeError("unexpected token endpoint failure"),
            RuntimeError,
            "unexpected token endpoint failure",
            1,
            id="unexpected-runtime-error",
        ),
    ],
)
def test_vertex_token_endpoint_sentry_events(
    vertex_capture, endpoint_result, error_type, match, event_count
):
    vertex, events = vertex_capture
    if error_type is InvalidValue:
        # google-auth's key converter includes the rejected value in its diagnostic.
        credentials = {
            "type": "service_account",
            "client_email": "test@example.invalid",
            "token_uri": "https://example.invalid/token",
            "private_key": [_TEST_KEY],
        }
    else:
        credentials = {
            "type": "authorized_user",
            "client_id": "test-id",
            "client_secret": _TEST_SECRET,
            "refresh_token": _TEST_REFRESH,
        }
    with patch("google.oauth2._client._token_endpoint_request_no_throw") as endpoint:
        if isinstance(endpoint_result, Exception):
            endpoint.side_effect = endpoint_result
        else:
            endpoint.return_value = endpoint_result
        with pytest.raises(error_type, match=match):
            vertex.get_access_token(credentials, project_id="test-project")
        assert endpoint.call_count == int(error_type is not InvalidValue)

    # Compare only counts/types, never print envelopes or raw frame locals on failure.
    reported = len(events)
    assert reported == event_count
    if reported:
        error = events[0]["exception"]["values"][-1]["type"]
        assert error == error_type.__name__

    _capture_later_failure()
    total = len(events)
    assert total == reported + 1
    later_type = events[-1]["exception"]["values"][-1]["type"]
    assert later_type == "LookupError"
    for index, event in enumerate(events):
        serialized = json.dumps(event)
        if any(
            marker in serialized for marker in (_TEST_KEY, _TEST_SECRET, _TEST_REFRESH)
        ):
            pytest.fail(
                f"Credential marker present in serialized event {index + 1}/{total}"
            )


def test_expected_outcome_loggers_are_dropped_by_before_send_alone():
    """Every client init_sentry creates gets the drop through before_send; no
    import-time global processor applies it to unrelated (test) clients."""
    assert not [
        processor
        for processor in global_event_processors
        if getattr(processor, "__module__", "").startswith("tfc.")
    ]
    name = "django.security.DisallowedHost"
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


@pytest.mark.parametrize(
    "text",
    [
        "upstream rejected Bearer qa-opaque-value",
        "retry with BEARER eyJhbGciOiJIUzI1NiJ9.e30.c2ln",
        "upstream rejected Bearer abcdefghijklmnopqrstuvwxyz",
        'upstream rejected Bearer "a b"',
        "upstream rejected Bearer %r",
        "upstream rejected `Bearer qa-opaque-value`",
        "upstream rejected Bearer\tqa-opaque-value",
        "upstream rejected Bearer\nqa-opaque-value",
        "upstream rejected Bearer   qa-opaque-value",
        "bearer:qa-opaque-value",
        "bearer=abc",
        "bearer => abc",
        '{"bearer": "qa-opaque-value"}',
        "bearer='qa-opaque-value'",
        "\u201cbearer\u201d: qa-opaque-value",
        "[('Bearer', 'qa-opaque-value')]",
        '["Bearer", "qa-opaque-value"]',
        "first the bearer of the news, then Bearer qa-opaque-value",
        # URL- or form-encoded, or glued by a non-word separator.
        "callback rejected Bearer%20eyJ0eXAi1",
        "callback rejected Bearer%3Dabc",
        "callback rejected Bearer+tok-123",
        "callback rejected Bearer+abc",
        "upstream rejected Bearer;abc",
        "upstream rejected Bearer(abc)",
        "upstream rejected Bearer|abc",
        "upstream rejected Bearer/abc",
        "upstream rejected Bearer%r",
    ],
)
def test_scrub_text_redacts_a_bearer_value(text):
    assert sentry._scrub_text(text) == _REDACTED


@pytest.mark.parametrize(
    "text",
    [
        "standard-bearer of the release",
        "the bearer bonds matured",
        "flag bearer: the team lead",
        "bearer, the person",
        "who is the bearer?",
        "the bearer is.",
        "bearers 12 left",
        "Bearer scheme rejected",
        "unsupported auth scheme bearer",
        # Source excerpts that only name the scheme's prefix literal.
        '        if not auth_header.startswith("Bearer "):',
        "    expected 'Bearer ' prefix",
    ],
)
def test_scrub_text_keeps_ordinary_uses_of_bearer(text):
    assert sentry._scrub_text(text) == text


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
            "headers": {"Authorization": "Bearer secret", "Accept": "application/json"},
            "cookies": {"sessionid": "xyz"},
            "query_string": "api_key=leak&page=1",
        },
        "extra": {"api_key": "leak", "count": 5},
    }
    out = before_send(event, {})
    assert out is not None
    assert out["request"]["headers"] == {"Accept": "application/json"}
    assert out["request"]["query_string"] == _REDACTED
    assert out["request"]["cookies"] == _REDACTED  # cookies redacted wholesale
    assert "api_key" not in out["extra"]
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

    out = _scrub_event(event)

    assert out is event
    assert out["breadcrumbs"][0]["data"] == {
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

    out = _scrub_event(event)

    assert out["breadcrumbs"]["values"][0]["data"] == {}


def test_a_frame_with_no_module_drops_every_frames_vars():
    """Unknown modules are no exception to the no-locals policy."""
    frames = [
        {"function": "<generated>", "vars": {"batch": "span text"}},
        {"module": "accounts.views", "vars": {"user_id": "1"}},
    ]
    event = {"threads": {"values": [{"stacktrace": {"frames": frames}}]}}

    out = before_send(event, {})

    assert out is not None
    assert [
        f for f in out["threads"]["values"][0]["stacktrace"]["frames"] if "vars" in f
    ] == []


@pytest.mark.parametrize(
    "module",
    [
        "tracer.utils.trace_ingestion",
        "tracer.utils.otel.media",
        "tracer.utils.otel_metrics",
        "tracer.utils",
        "<template>",
    ],
)
def test_supplied_frames_keep_location_not_locals_regardless_of_module(module):
    frames = [
        {"module": module, "lineno": 7, "vars": {"batch": "span text"}},
        {"module": "accounts.views", "vars": {"user_id": "1"}},
    ]
    event = {"exception": {"values": [{"stacktrace": {"frames": frames}}]}}

    out = before_send(event, {})

    assert out is not None
    [value] = out["exception"]["values"]
    assert value["stacktrace"]["frames"] == [
        {"module": module, "lineno": 7},
        {"module": "accounts.views"},
    ]


def test_frame_budget_is_shared_and_checked_before_iteration(monkeypatch):
    class NonIterableFrames(list):
        def __iter__(self):
            pytest.fail("the hook iterated uninspectable frames before checking bounds")

    monkeypatch.setattr(sentry, "_MAX_FRAMES", 3)
    event = {
        "stacktrace": {"frames": [{"lineno": 2, "vars": {}}, {"lineno": 3}]},
        "exception": {
            "values": [
                {
                    "type": "LookupError",
                    "value": "unverified detail",
                    "raw_stacktrace": {"frames": [{"vars": {"detail": "raw"}}]},
                    "stacktrace": {"frames": [{"vars": {}}, {"vars": {}}]},
                }
            ]
        },
        "threads": {
            "values": [
                {"stacktrace": {"frames": NonIterableFrames([{}, {}])}},
                {"stacktrace": {"frames": [{"lineno": 9, "vars": {}}]}},
            ]
        },
    }
    out = before_send(event, {})
    assert out is not None
    assert out["stacktrace"]["frames"] == [{"lineno": 3}]
    [error] = out["exception"]["values"]
    assert error == {
        "type": "LookupError",
        "value": _REDACTED,
        "stacktrace": {"frames": [{}, {}]},
    }
    assert out["threads"]["values"] == [
        {"stacktrace": {}},
        {"stacktrace": {}},
    ]


def test_an_event_whose_stacks_cannot_be_walked_is_dropped():
    """Stacks it cannot read may hold a span batch it cannot remove."""
    assert before_send({"message": "boom", "threads": ["not-a-mapping"]}, {}) is None


# One breadcrumb hook, two scrubs. Each test fails if its half is removed.


@pytest.mark.parametrize(
    "status",
    [
        grpc.StatusCode.UNAUTHENTICATED,
        repr(grpc.StatusCode.UNAUTHENTICATED),
        "UNAUTHENTICATED",
    ],
)
def test_the_breadcrumb_hook_drops_the_grpc_framework_request(status):
    """django_socio_grpc's request container holds the raw spans. The crumb keeps
    its record without it, and a framework crumb it cannot read is dropped."""
    crumb = {
        "category": "django_socio_grpc.request",
        "level": "warning",
        "message": "Unauthenticated : ObservationSpan/Export",
        "data": {
            "request": "GRPCRequestContainer(grpc_request=resource_spans {...})",
            "status_code": status,
        },
    }

    out = _before_breadcrumb(crumb, {})

    assert out is not None
    assert out["data"] == {"status_code": "UNAUTHENTICATED"}
    assert out["message"] == "Unauthenticated : ObservationSpan/Export"
    unreadable = {"category": "django_socio_grpc.services", "data": "[raw request]"}
    assert _before_breadcrumb(unreadable, {}) is None


@pytest.mark.parametrize("status", ["UNKNOWN and customer detail", True, {"raw": 7}])
def test_free_form_grpc_fields_are_not_diagnostics(status):
    marker = "unlabelled customer body"
    data = {"status_code": status, "request": marker}
    category = "django_socio_grpc.request"
    crumb = {"category": category, "message": marker, "data": data}
    event = {
        "logger": category,
        "extra": data,
        "exception": {"values": [{"type": "RuntimeError", "value": marker}]},
        "breadcrumbs": [crumb],
    }
    out = before_send(event, {})
    assert out is not None
    assert out["extra"] == {}
    assert out["breadcrumbs"] == [{"category": category, "data": {}}]
    assert out["exception"]["values"][0]["type"] == "RuntimeError"
    assert marker not in json.dumps(out)


def test_the_breadcrumb_hook_keeps_the_vertex_credential_scrub():
    """The Vertex credential load failure's crumb loses its message and data."""
    rejected = '{"private_key": "SENTINEL-PK-breadcrumb"}'
    crumb = {
        "category": "LiteLLM",
        "message": sentry._VERTEX_LOAD_MESSAGE + rejected,
        "data": {"args": [rejected]},
    }

    out = _before_breadcrumb(crumb, {})

    assert out == {"category": "LiteLLM", "message": sentry._VERTEX_SAFE_MESSAGE}
