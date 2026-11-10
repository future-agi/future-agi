from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from django.test import RequestFactory

from tfc.deployment_telemetry.payloads import (
    build_heartbeat_payload,
    build_minimal_registration_payload,
)
from tfc.deployment_telemetry.schema import (
    HEARTBEAT_FIELDS,
    REGISTRATION_FIELDS,
)
from tfc.logging.sentry import _scrub_deployment_telemetry_event
from tfc.telemetry.middleware import OTelContextMiddleware


def test_otel_does_not_capture_deployment_telemetry_request_body():
    request = RequestFactory().post(
        "/telemetry/register/",
        data='{"users":[{"email":"secret@example.com"}]}',
        content_type="application/json",
    )
    span = MagicMock()

    OTelContextMiddleware(lambda request: None)._capture_request(span, request)

    captured_attributes = [call.args[0] for call in span.set_attribute.call_args_list]
    assert "http.request.body" not in captured_attributes


def test_otel_does_not_capture_agent_secret_values_request_body():
    request = RequestFactory().post(
        "/simulate/api/harness-jobs/secret-values/",
        data='{"environment_values":{"DATABASE_URL":"postgres://u:pw@h/db"}}',
        content_type="application/json",
    )
    span = MagicMock()

    OTelContextMiddleware(lambda request: None)._capture_request(span, request)

    captured_attributes = [call.args[0] for call in span.set_attribute.call_args_list]
    assert "http.request.body" not in captured_attributes


def test_staged_credential_files_are_treated_as_sensitive_bodies():
    request = RequestFactory().post(
        "/simulate/api/harness-jobs/secret-files/",
        data='{"private_key":"-----BEGIN PRIVATE KEY-----x"}',
        content_type="application/json",
    )
    span = MagicMock()

    OTelContextMiddleware(lambda request: None)._capture_request(span, request)

    captured_attributes = [call.args[0] for call in span.set_attribute.call_args_list]
    assert "http.request.body" not in captured_attributes
    event = {
        "request": {
            "url": "https://api.futureagi.com/simulate/api/harness-jobs/secret-files/",
            "data": {"environment_name": "GOOGLE_APPLICATION_CREDENTIALS"},
        },
        "exception": {
            "values": [
                {"stacktrace": {"frames": [{"vars": {"content": "PRIVATE KEY"}}]}}
            ]
        },
    }

    scrubbed = _scrub_deployment_telemetry_event(event)

    assert "data" not in scrubbed["request"]
    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_otel_redacts_secret_value_maps_in_any_request_body():
    request = RequestFactory().post(
        "/simulate/api/run-tests/rerun/",
        data=(
            '{"environment_values":{"DATABASE_URL":"postgres://u:pw@h/db"},'
            '"credential_values":{"SLACK_WEBHOOK":"https://hooks.example/x"},'
            '"name":"kept"}'
        ),
        content_type="application/json",
    )
    span = MagicMock()

    OTelContextMiddleware(lambda request: None)._capture_request(span, request)

    bodies = [
        call.args[1]
        for call in span.set_attribute.call_args_list
        if call.args[0] == "http.request.body"
    ]
    assert len(bodies) == 1
    assert "postgres://" not in bodies[0]
    assert "hooks.example" not in bodies[0]
    assert '"environment_values": "[REDACTED]"' in bodies[0]
    assert '"credential_values": "[REDACTED]"' in bodies[0]
    assert "kept" in bodies[0]


def test_sentry_scrubs_deployment_telemetry_request_data_and_frame_variables():
    event = {
        "request": {
            "url": "https://api.futureagi.com/telemetry/register/",
            "data": {"users": [{"email": "secret@example.com"}]},
        },
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [{"vars": {"payload": "secret@example.com"}}]
                    }
                }
            ]
        },
    }

    scrubbed = _scrub_deployment_telemetry_event(event)

    assert "data" not in scrubbed["request"]
    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_sentry_scrubs_outbound_telemetry_request_breadcrumb_body():
    """The HTTP-integration breadcrumb for the outbound POST to /telemetry/
    register/ carries the registration body in ``data.body``. The earlier
    scrubber only walked the inbound ``request.url``, so the registration
    payload leaked to Sentry via this back channel even though the inbound
    request body was stripped."""
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "category": "httplib",
                    "data": {
                        "url": "https://api.futureagi.com/telemetry/register/",
                        "method": "POST",
                        "body": '{"users":[{"email":"secret@example.com"}]}',
                    },
                },
                {
                    "category": "httplib",
                    "data": {
                        "url": "https://api.futureagi.com/some/other/endpoint",
                        "method": "POST",
                        "body": '{"keep":"me"}',
                    },
                },
            ]
        },
        "request": {
            "url": "https://example.com/unrelated/endpoint",
        },
    }

    scrubbed = _scrub_deployment_telemetry_event(event)

    telemetry_crumb, other_crumb = scrubbed["breadcrumbs"]["values"]
    assert "body" not in telemetry_crumb["data"]
    assert telemetry_crumb["data"]["url"].endswith("/telemetry/register/")
    # Unrelated breadcrumb bodies must survive — we only scrub /telemetry/.
    assert other_crumb["data"]["body"] == '{"keep":"me"}'


def test_minimal_disabled_registration_payload_drops_user_data():
    """The opt-out path must carry only the four documented fields plus
    ``telemetry_disabled=true``. ``users`` is the field most often added by a
    careless refactor and the most damaging leak — assert it's absent."""
    payload = build_minimal_registration_payload(uuid4())

    assert payload["telemetry_disabled"] is True
    assert "users" not in payload
    assert "user_emails" not in payload
    assert "user_domains" not in payload
    # Every field in the minimal payload must be on the registration contract.
    assert set(payload).issubset(REGISTRATION_FIELDS)


@pytest.mark.parametrize(
    "counts",
    [
        {field: 0 for field in HEARTBEAT_FIELDS if field.endswith("_count")},
        {field: None for field in HEARTBEAT_FIELDS if field.endswith("_count")},
    ],
)
def test_heartbeat_payload_shape_matches_contract(counts):
    """Any field added to the heartbeat payload that isn't on the documented
    HEARTBEAT_FIELDS contract is a wire leak — fail loudly instead of letting
    it ship to PostHog/HubSpot."""
    from datetime import datetime, timezone

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)

    payload = build_heartbeat_payload(uuid4(), start, end, counts)

    extras = set(payload) - HEARTBEAT_FIELDS
    assert not extras, f"heartbeat payload added off-contract fields: {extras}"


def test_opt_out_disclosure_names_every_field_the_minimal_ping_sends(monkeypatch):
    """The log line an operator reads after opting out must not understate
    what still leaves the install."""
    from structlog.testing import capture_logs

    from tfc.deployment_telemetry import sender

    monkeypatch.setattr(sender, "_disclosure_logged", False)
    monkeypatch.setattr(sender, "telemetry_is_disabled", lambda: True)
    with capture_logs() as records:
        sender._log_disclosure()

    (record,) = [r for r in records if r["event"] == "deployment_telemetry_disclosure"]
    sent = set(build_minimal_registration_payload(uuid4()))
    # schema_version and telemetry_disabled describe the ping, not the install.
    for field in sent - {"schema_version", "telemetry_disabled"}:
        assert field.replace("_", " ") in record["sends"], field
