"""The Google ("AUTH0_*") sign-in callback must verify the ID token it is handed.

The callback exchanges the authorization code with Google and then signs the
user in as whoever the returned ID token names. These tests pin that the token
is only trusted after its RS256 signature checks out against Google's published
keys and its issuer, audience, lifetime and email_verified claims are right;
anything else takes the existing error redirect and issues no session.

DB-free: the account lookup, session minting and cache are mocked, and Google's
token endpoint and JWKS endpoint are faked in-process, so no test touches a
database or the network. Tokens are signed with locally generated RSA keys.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory
from jwt.algorithms import RSAAlgorithm
from structlog.testing import capture_logs

from saml2_auth import views

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


class _FakeJwksEndpoint:
    """Stands in for Google's JWKS endpoint inside PyJWT's fetch."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.requested_urls = []

    def open(self, request, timeout=None):
        self.requested_urls.append(request.full_url)
        return io.BytesIO(json.dumps({"keys": self.keys}).encode())


@pytest.fixture
def jwks():
    endpoint = _FakeJwksEndpoint([GOOGLE_JWK])
    # A fresh client per test so one test's cached key set or refresh cooldown
    # does not leak into the next. raising=False keeps the fixture usable on a
    # tree that does not have the module-level client yet.
    fresh_client = jwt.PyJWKClient(GOOGLE_JWKS_URL, lifespan=3600, timeout=10)
    with (
        patch.object(views, "_GOOGLE_JWKS_CLIENT", fresh_client, create=True),
        patch(
            "jwt.jwks_client.urllib.request.build_opener",
            return_value=endpoint,
        ),
    ):
        yield endpoint


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
    assert "sso_token=encrypted-session" in response["Location"]
    assert resolve_sso_user.call_args.args[:2] == ("person@example.com", "Test Person")
    auth_token_model.objects.create.assert_called_once()
    assert failures == []


def test_bare_issuer_form_google_documents_is_accepted(jwks):
    response, resolve_sso_user, _, _ = _call_callback(
        _rs256(_claims(iss="accounts.google.com"))
    )

    assert response["Location"].startswith(SUCCESS_NEXT_URL)
    resolve_sso_user.assert_called_once()


def test_token_signed_by_a_key_google_did_not_publish_is_rejected(jwks):
    _assert_rejected(_rs256(_claims(), key=ATTACKER_KEY), "InvalidSignatureError")


def test_wrong_issuer_is_rejected(jwks):
    _assert_rejected(
        _rs256(_claims(iss="https://attacker.example")), "InvalidIssuerError"
    )


def test_wrong_audience_is_rejected(jwks):
    _assert_rejected(
        _rs256(_claims(aud="someone-elses-client.apps.googleusercontent.com")),
        "InvalidAudienceError",
    )


def test_expired_token_is_rejected(jwks):
    now = int(time.time())
    _assert_rejected(
        _rs256(_claims(iat=now - 7200, exp=now - 3600)), "ExpiredSignatureError"
    )


def test_unsigned_alg_none_token_is_rejected(jwks):
    _assert_rejected(_unsigned(_claims()), "InvalidAlgorithmError")


def test_hs256_token_keyed_with_the_public_key_is_rejected(jwks):
    _assert_rejected(_hs256_with_public_key(_claims()), "InvalidAlgorithmError")


def test_unverified_email_is_rejected_before_account_lookup(jwks):
    _assert_rejected(_rs256(_claims(email_verified=False)), "PermissionDenied")


def test_missing_email_verified_claim_is_rejected(jwks):
    _assert_rejected(_rs256(_claims(email_verified=None)), "PermissionDenied")


def test_string_true_email_verified_is_not_treated_as_verified(jwks):
    _assert_rejected(_rs256(_claims(email_verified="true")), "PermissionDenied")


def test_rotated_google_key_is_fetched_and_accepted(jwks):
    first, _, _, _ = _call_callback(_rs256(_claims()))
    assert first["Location"].startswith(SUCCESS_NEXT_URL)

    # Google rotates: the cached set predates the new key. Step past PyJWT's
    # refresh cooldown, as a real rotation would be, so the unknown kid
    # triggers one refetch.
    jwks.keys = [GOOGLE_JWK, ROTATED_JWK]
    views._GOOGLE_JWKS_CLIENT._last_successful_fetch = time.monotonic() - 3600

    response, resolve_sso_user, _, failures = _call_callback(
        _rs256(_claims(), key=ROTATED_KEY, kid="google-key-2")
    )

    assert response["Location"].startswith(SUCCESS_NEXT_URL)
    resolve_sso_user.assert_called_once()
    assert failures == []
    assert jwks.requested_urls == [GOOGLE_JWKS_URL, GOOGLE_JWKS_URL]


def test_production_jwks_client_targets_google_and_caches_the_key_set():
    client = views._GOOGLE_JWKS_CLIENT

    assert isinstance(client, jwt.PyJWKClient)
    assert client.uri == GOOGLE_JWKS_URL
    assert client.jwk_set_cache is not None
    assert client.jwk_set_cache.lifespan == 3600
