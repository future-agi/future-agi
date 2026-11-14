"""Pure regressions for the SAML-only structured logging boundary."""

import logging

import pytest

from tfc.logging.processors import SamlDependencyRedactFilter, scrub_saml_event


@pytest.mark.parametrize(
    "logger_name",
    [
        "saml2_auth.views",
        "saml2_auth.oauth",
        "saml2_auth.security.child",
        "accounts.views",
    ],
)
def test_non_security_logger_preserves_oauth_diagnostics(logger_name):
    event = {
        "logger": logger_name,
        "event": "oauth_callback_failed",
        "provider": "github",
        "http_status": 502,
        "retryable": True,
        "error_class": "ConnectionError",
        "source_function": "get",
    }

    result = scrub_saml_event(None, "warning", event)

    assert result is event
    assert result["http_status"] == 502
    assert result["retryable"] is True
    assert result["error_class"] == "ConnectionError"


def test_exact_saml_security_logger_scrubs_protocol_and_exception_values():
    marker = "private-protocol-marker"
    error = ValueError(marker)
    event = {
        "logger": "saml2_auth.security",
        "event": "saml_credential_denied",
        "reason": "idp_generation",
        "transport": "http",
        "exc_type": "ValueError",
        "org_id": "org-a",
        "assertion": marker,
        "authorization": marker,
        "token": marker,
        "user_email": marker,
        "exc_info": (type(error), error, None),
        "exception": marker,
        "stack": marker,
        "unexpected_context": marker,
    }

    result = scrub_saml_event(None, "warning", event)

    assert result == {
        "logger": "saml2_auth.security",
        "event": "saml_credential_denied",
        "reason": "idp_generation",
        "transport": "http",
        "exc_type": "ValueError",
        "org_id": "org-a",
    }
    assert marker not in repr(result)


def test_saml_security_logger_preserves_formatter_metadata_and_bounds_fields():
    record = object()
    result = scrub_saml_event(
        None,
        "warning",
        {
            "logger": "saml2_auth.security",
            "event": "saml_credential_denied",
            "reason": "x" * 256,
            "_record": record,
            "_from_structlog": True,
        },
    )

    assert result["_record"] is record
    assert result["_from_structlog"] is True
    assert result["reason"] == "x" * 128


@pytest.mark.parametrize(
    "logger_name", ["saml2", "saml2.response", "xmlschema.validators"]
)
def test_dependency_logs_still_drop_raw_fields(logger_name):
    result = scrub_saml_event(
        None,
        "warning",
        {
            "logger": logger_name,
            "event": "signature processing failed",
            "level": "warning",
            "assertion": "private-protocol-marker",
            "exc_info": True,
        },
    )

    assert result["event"] == "saml_dependency_event"
    assert "assertion" not in result
    assert "exc_info" not in result
    assert "private-protocol-marker" not in repr(result)


def test_dependency_filter_removes_exception_message_before_rendering():
    marker = "private-protocol-marker"
    error = ValueError(marker)
    record = logging.LogRecord(
        "saml2.response",
        logging.ERROR,
        __file__,
        1,
        "signature failed: %s",
        (marker,),
        (type(error), error, None),
    )
    record.exc_text = marker
    record.stack_info = marker

    assert SamlDependencyRedactFilter().filter(record)
    assert record.getMessage() == "saml dependency event"
    assert record.saml_dep_class == "ValueError"
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
    assert marker not in logging.Formatter().format(record)
