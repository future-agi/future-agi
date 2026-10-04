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
