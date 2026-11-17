"""The Google ("AUTH0_*") sign-in callback must verify the ID token it is handed.

The callback exchanges the authorization code with Google and then signs the
user in as whoever the returned ID token names. These tests pin that the token
is only trusted after its RS256 signature checks out against Google's published
keys and its issuer, audience, lifetime and email_verified claims are right;
anything else takes the existing error redirect and issues no session.

DB-free: the account lookup, session minting and cache are mocked, and Google's
token endpoint and key fetch are faked in-process, so no test touches a
database or the network. Tokens are signed with locally generated RSA keys.

The callback verifies through views._verified_google_id_token, which calls
saml2_auth.google_oauth.verify_google_id_token (python-jose) and adds two rules
of its own: the token carries ``iat``, and ``email_verified`` is the boolean
true. The verifier's key cache and rotation are pinned by
saml2_auth/tests/test_google_oauth.py; here every call fetches the key set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory
from jwt.algorithms import RSAAlgorithm
from structlog.testing import capture_logs

from accounts.services.handoff_exchange import redeem
from saml2_auth import google_oauth, views

CLIENT_ID = "test-client.apps.googleusercontent.com"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
SUCCESS_NEXT_URL = "https://app.test/dashboard/develop"


def _new_key(kid):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private_key, jwk


GOOGLE_KEY, GOOGLE_JWK = _new_key("google-key-1")
ROTATED_KEY, ROTATED_JWK = _new_key("google-key-2")
ATTACKER_KEY, _ = _new_key("google-key-1")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _claims(**overrides):
    now = int(time.time())
    claims = {
        "iss": GOOGLE_ISSUER,
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "person@example.com",
        "email_verified": True,
        "name": "Test Person",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _rs256(claims, key=GOOGLE_KEY, kid="google-key-1"):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _unsigned(claims, kid="google-key-1"):
    header = {"alg": "none", "typ": "JWT", "kid": kid}
    return (
        f"{_b64url(json.dumps(header).encode())}."
        f"{_b64url(json.dumps(claims).encode())}."
    )


def _hs256_with_public_key(claims, kid="google-key-1"):
    """The classic RS256->HS256 confusion: HMAC keyed with the public key PEM."""
    secret = GOOGLE_KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    signing_input = (
        f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    )
    signature = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


@pytest.fixture
def jwks(settings):
    """Google's key set as saml2_auth.google_oauth fetches it, from the URL the
    settings name (read through django.conf). The verifier's cache is replaced
    by an always-empty one, so every call fetches and no test sees another's
    key set. Yields the URLs fetched."""
    settings.GOOGLE_OAUTH_JWKS_URL = GOOGLE_JWKS_URL
    fetched = []

    def fetch(url, cache_key):
        fetched.append(url)
        return [GOOGLE_JWK]

    empty_cache = MagicMock()
    empty_cache.get.return_value = None
    with (
        patch.object(google_oauth, "_fetch_keys", side_effect=fetch),
        patch.object(google_oauth, "cache", empty_cache),
    ):
        yield fetched


@contextmanager
def _callback_harness(id_token):
    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {
        "id_token": id_token,
        "access_token": "ya29.test-access-token",
    }
    user = MagicMock(id=uuid.uuid4())
    auth_token_model = MagicMock()
    auth_token_model.objects.create.return_value = MagicMock(id=uuid.uuid4())
    with (
        patch.object(views, "AUTH0_CLIENT_ID", CLIENT_ID),
        patch.object(views.requests, "post", return_value=token_response),
        patch.object(
            views.requests, "get", side_effect=AssertionError("no userinfo call")
        ),
        patch.object(
            views,
            "resolve_sso_user",
            return_value=(user, SUCCESS_NEXT_URL, "false"),
        ) as resolve_sso_user,
        patch.object(views, "AuthToken", auth_token_model),
        patch.object(
            views, "generate_encrypted_message", return_value="encrypted-session"
        ),
        patch.object(views, "cache"),
    ):
        yield resolve_sso_user, auth_token_model


def _call_callback(id_token):
    request = RequestFactory().get(
        "/saml2_auth/auth/callback/", {"code": "authorization-code"}
    )
    request.session = {}
    with (
        capture_logs() as logs,
        _callback_harness(id_token) as (
            resolve_sso_user,
            auth_token_model,
        ),
    ):
        response = views.Auth0CallbackView.as_view()(request)
    failures = [
        entry.get("error_class")
        for entry in logs
        if entry.get("event") == "oauth_callback_failed"
    ]
    return response, resolve_sso_user, auth_token_model, failures


def _assert_rejected(id_token, expected_error):
    response, resolve_sso_user, auth_token_model, failures = _call_callback(id_token)
    location = response["Location"]
    assert response.status_code == 302
    assert location.startswith(views.default_error_next_url), location
    assert "sso_token=" not in location
    resolve_sso_user.assert_not_called()
    auth_token_model.objects.create.assert_not_called()
    assert failures == [expected_error]


def test_valid_google_id_token_signs_the_user_in(jwks):
    response, resolve_sso_user, auth_token_model, failures = _call_callback(
        _rs256(_claims())
    )

    assert response.status_code == 302
    assert response["Location"].startswith(SUCCESS_NEXT_URL)
    # A team test's expected value changes: the redirect carries a one-time
    # handoff code (kind sso) instead of sso_token; it redeems to the session.
    codes = parse_qs(urlsplit(response["Location"]).query).get("code")
    assert codes and len(codes) == 1, response["Location"]
    assert redeem(codes[0], "sso") == {"access": "encrypted-session", "refresh": None}
    assert resolve_sso_user.call_args.args[:2] == ("person@example.com", "Test Person")
    auth_token_model.objects.create.assert_called_once()
    assert failures == []
    assert jwks == [GOOGLE_JWKS_URL]


def test_bare_issuer_form_google_documents_is_accepted(jwks):
    response, resolve_sso_user, _, _ = _call_callback(
        _rs256(_claims(iss="accounts.google.com"))
    )

    assert response["Location"].startswith(SUCCESS_NEXT_URL)
    resolve_sso_user.assert_called_once()


def test_token_signed_by_a_key_google_did_not_publish_is_rejected(jwks):
    _assert_rejected(_rs256(_claims(), key=ATTACKER_KEY), "JWTError")


def test_wrong_issuer_is_rejected(jwks):
    _assert_rejected(_rs256(_claims(iss="https://attacker.example")), "JWTClaimsError")


def test_wrong_audience_is_rejected(jwks):
    _assert_rejected(
        _rs256(_claims(aud="someone-elses-client.apps.googleusercontent.com")),
        "JWTClaimsError",
    )


def test_expired_token_is_rejected(jwks):
    now = int(time.time())
    _assert_rejected(
        _rs256(_claims(iat=now - 7200, exp=now - 3600)), "ExpiredSignatureError"
    )


def test_unsigned_alg_none_token_is_rejected(jwks):
    _assert_rejected(_unsigned(_claims()), "JWTError")


def test_hs256_token_keyed_with_the_public_key_is_rejected(jwks):
    _assert_rejected(_hs256_with_public_key(_claims()), "JWTError")


def test_unverified_email_is_rejected_before_account_lookup(jwks):
    _assert_rejected(_rs256(_claims(email_verified=False)), "JWTError")


def test_missing_email_verified_claim_is_rejected(jwks):
    _assert_rejected(_rs256(_claims(email_verified=None)), "JWTError")


def test_string_true_email_verified_is_not_treated_as_verified(jwks):
    _assert_rejected(_rs256(_claims(email_verified="true")), "PermissionDenied")


def test_token_without_iat_is_rejected(jwks):
    _assert_rejected(_rs256(_claims(iat=None)), "JWTClaimsError")
