from __future__ import annotations

import logging

import pytest

from saml2_auth.tests.log_capture import capture_security_logging

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_no_traceback_with_request_body_on_deny(capsys):
    """SAML dependency diagnostics must not serialize an assertion-like marker."""

    marker = "SAMLResponse=<saml:Assertion>private-marker@example.test</saml:Assertion>"
    dependency = logging.getLogger("saml2.response")
    try:
        raise ValueError(marker)
    except ValueError:
        with capture_security_logging() as captured:
            dependency.error("signature processing failed: %s", marker, exc_info=True)

    stderr = capsys.readouterr().err
    rendered = captured.rendered + stderr
    assert "private-marker@example.test" not in rendered
    assert "SAMLResponse" not in rendered
    assert "Traceback" not in rendered


def test_acs_store_failure_scrubs_assertion_from_sentry_locals(monkeypatch):
    """Review finding 1 (P1): an uncaught database error while storing the ACS
    candidate must not export the raw assertion through Sentry stack locals.

    Sentry captures local variables (include_local_variables=True). The ACS
    view holds the decoded assertion in `saml_response` and `payload` when it
    calls store_candidate; if that raises, those locals ride the exception
    stack frame. The scrubber must redact them before the event leaves.
    """

    import base64
    import json

    from tfc.logging.sentry import _scrub_event

    marker = "<saml:Assertion>private-marker@example.test</saml:Assertion>"
    event = {
        "exception": {
            "values": [
                {
                    "type": "OperationalError",
                    "value": "database unavailable",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "saml2_auth/views.py",
                                "function": "post",
                                # As the view holds them: the posted field is
                                # still base64, so it carries no XML marker.
                                "vars": {
                                    "saml_response": base64.b64encode(
                                        marker.encode()
                                    ).decode(),
                                    "payload": repr(marker.encode()),
                                    "relay_state": "server-issued-relay",
                                },
                            }
                        ]
                    },
                }
            ]
        }
    }
    _scrub_event(event)
    rendered = json.dumps(event)
    assert "private-marker@example.test" not in rendered
    assert "<saml:Assertion>" not in rendered
    assert base64.b64encode(marker.encode()).decode() not in rendered
    assert "server-issued-relay" not in rendered


def test_acs_body_is_not_copied_onto_the_tracing_span():
    """Verifier finding 1 (P1): the ACS form body must never reach a tracing span.

    OTelContextMiddleware copies request.POST onto the current span unless the
    path is in SENSITIVE_BODY_PATH_PREFIXES. The ACS endpoint receives the raw
    SAMLResponse, so /saml2_auth/ must be treated as sensitive or the assertion
    is exported on every request, success or failure.
    """

    from tfc.telemetry.middleware import SENSITIVE_BODY_PATH_PREFIXES

    assert any(
        "/saml2_auth/acs/".startswith(prefix) for prefix in SENSITIVE_BODY_PATH_PREFIXES
    ), "ACS path is not excluded from request-body span capture"


def test_denials_log_their_own_reason(api_client, idp_a):
    """The response stays generic; the security log says what actually happened."""

    with capture_security_logging() as captured:
        api_client.get("/saml2_auth/idp-login/", {"email": "nobody@example.test"})
        api_client.post(
            "/saml2_auth/acs/",
            {"RelayState": "never-issued", "SAMLResponse": "PHNhbWxwOlJlc3BvbnNlLz4="},
        )

    assert "identity_missing" in captured.rendered
    assert "attempt_unavailable" in captured.rendered
