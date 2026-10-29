"""Regression tests for #3596 — OAuth email login name resolution.

Email (database-connection) logins through Auth0 frequently omit the
``name`` claim. The old fallback called Google's userinfo endpoint with
the Auth0 access token, which never succeeds for a non-Google identity
and turned every email login into "Unable to process OAuth callback".
"""

from unittest.mock import MagicMock, patch

import pytest

from saml2_auth.views import _resolve_oauth_name

pytestmark = pytest.mark.unit


def _resp(status: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


def test_name_claim_wins():
    decoded = {"name": "João Silva", "nickname": "joao", "email": "j@x.com"}
    with patch("saml2_auth.views.requests.get") as get:
        assert _resolve_oauth_name(decoded, "tok", "j@x.com") == "João Silva"
        get.assert_not_called()


def test_nickname_claim_used_when_name_missing():
    decoded = {"nickname": "joao.silva", "email": "j@x.com"}
    with patch("saml2_auth.views.requests.get") as get:
        assert _resolve_oauth_name(decoded, "tok", "j@x.com") == "joao.silva"
        get.assert_not_called()


def test_auth0_userinfo_fallback_when_no_claims():
    decoded = {"email": "j@x.com"}
    with patch("saml2_auth.views.requests.get") as get:
        get.return_value = _resp(200, {"name": "Joao"})
        assert _resolve_oauth_name(decoded, "tok", "j@x.com") == "Joao"
        url = get.call_args[0][0]
        assert "userinfo" in url
        assert "googleapis.com" not in url
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_email_localpart_when_userinfo_fails():
    """Email login must not hard-fail when userinfo rejects the token."""
    decoded = {"email": "joao.silva@corp.com"}
    with patch("saml2_auth.views.requests.get") as get:
        get.return_value = _resp(401, {})
        assert _resolve_oauth_name(decoded, "tok", "joao.silva@corp.com") == "joao.silva"


def test_email_localpart_when_userinfo_throws():
    import requests as real_requests

    decoded = {"email": "maria@corp.com"}
    with patch("saml2_auth.views.requests.get") as get:
        get.side_effect = real_requests.ConnectionError("down")
        assert _resolve_oauth_name(decoded, "tok", "maria@corp.com") == "maria"


def test_generic_fallback_when_no_email():
    decoded = {}
    with patch("saml2_auth.views.requests.get") as get:
        get.return_value = _resp(500, {})
        assert _resolve_oauth_name(decoded, "tok", None) == "User"
