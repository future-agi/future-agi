"""Behavioral security regression tests for SAML/OAuth authentication hardening.

SAML tests assert on the configured Saml2Config and actually drive
``prepare_for_authenticate()`` (which caught the missing-SP-key crash) rather
than matching source text. They need the ``xmlsec1`` binary, which is present in
the deploy/CI image. Auth0 callback tests use a mocked JWKS plus real signed and
forged RS256 ID tokens.
"""

import base64
import datetime
import hashlib
import shutil
import time
import types

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from jose import jwt as jose_jwt
from jose.utils import calculate_at_hash
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from saml2_auth import views

xmlsec1 = shutil.which("xmlsec1")
needs_xmlsec1 = pytest.mark.skipif(
    xmlsec1 is None, reason="xmlsec1 binary not available"
)


def _generate_keypair(cn="sp.example.com"):
    """Generate a throwaway RSA keypair + self-signed cert."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return key, cert


@pytest.fixture(scope="module")
def sp_keypair(tmp_path_factory):
    """SP signing keypair written as PEM files on disk."""
    d = tmp_path_factory.mktemp("sp-keys")
    key, cert = _generate_keypair()
    key_file = str(d / "sp.key")
    cert_file = str(d / "sp.crt")
    with open(key_file, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return key_file, cert_file


@pytest.fixture(scope="module")
def idp_metadata_file(tmp_path_factory):
    """Minimal IdP metadata XML pointing at a fake IdP SSO endpoint."""
    d = tmp_path_factory.mktemp("idp-metadata")
    key, cert = _generate_keypair(cn="idp.example.com")
    cert_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    metadata = f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    entityID="https://idp.example.com/metadata">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo><ds:X509Data><ds:X509Certificate>{cert_b64}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>
    </md:KeyDescriptor>
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.example.com/sso/"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""
    path = d / "idp.xml"
    path.write_text(metadata)
    return str(path)


def _build_saml_client(monkeypatch, meta_file, key_file="", cert_file=""):
    """Build a Saml2Client exactly as _get_saml_client does, minus the DB."""
    monkeypatch.setattr(views, "SAML_SP_KEY_FILE", key_file)
    monkeypatch.setattr(views, "SAML_SP_CERT_FILE", cert_file)
    monkeypatch.setattr(
        views, "_get_metadata", lambda alias: ({"local": [meta_file]}, 1)
    )
    return views._get_saml_client(1, "https://app.example.com/acs/")


@needs_xmlsec1
def test_sp_config_requires_signed_requests_and_responses(
    sp_keypair, idp_metadata_file, monkeypatch
):
    """SP config must reject unsolicited assertions and require signatures."""
    key_file, cert_file = sp_keypair
    client, identity_type = _build_saml_client(
        monkeypatch, idp_metadata_file, key_file, cert_file
    )
    config = client.config

    assert identity_type == 1
    assert config._sp_allow_unsolicited is False
    assert config._sp_authn_requests_signed is True
    assert config._sp_want_assertions_or_response_signed is True
    assert config._sp_want_assertions_signed is True
    assert config._sp_key_file == key_file
    assert config._sp_cert_file == cert_file
    # top-level key/cert required by SecurityContext for signing
    assert config.key_file == key_file
    assert config.cert_file == cert_file


@needs_xmlsec1
def test_sp_config_falls_back_to_unsigned_without_keypair(
    idp_metadata_file, monkeypatch
):
    """Without an SP keypair, SP-initiated login must still work (no crash)."""
    client, _ = _build_saml_client(monkeypatch, idp_metadata_file)
    config = client.config

    assert config._sp_authn_requests_signed is False
    assert getattr(config, "_sp_key_file", None) is None

    _, info = client.prepare_for_authenticate()
    location = dict(info["headers"])["Location"]
    assert "SAMLRequest=" in location


@needs_xmlsec1
def test_prepare_for_authenticate_succeeds_with_signing_key(
    sp_keypair, idp_metadata_file, monkeypatch
):
    """prepare_for_authenticate must not crash once keys are provisioned."""
    key_file, cert_file = sp_keypair
    client, _ = _build_saml_client(monkeypatch, idp_metadata_file, key_file, cert_file)

    _, info = client.prepare_for_authenticate()
    location = dict(info["headers"])["Location"]
    assert location.startswith("https://idp.example.com/sso/")
    assert "SAMLRequest=" in location


# ---------------------------------------------------------------------------
# Auth0 callback JWT verification
# ---------------------------------------------------------------------------


class _FakeCache:
    def get(self, key, default=None):
        return default

    def set(self, *args, **kwargs):
        pass


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _rsa_jwk(public_key, kid="test-kid-1"):
    pn = public_key.public_numbers()

    def _b64url(n):
        return (
            base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big"))
            .rstrip(b"=")
            .decode()
        )

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(pn.n),
        "e": _b64url(pn.e),
    }


def _make_id_token(private_key, kid="test-kid-1"):
    """Build a real RS256 ID token for the (patched) Auth0 domain/client."""
    access_token = "dummy-access-token"
    claims = {
        "iss": f"https://{views.AUTH0_DOMAIN}/",
        "aud": views.AUTH0_CLIENT_ID,
        "sub": "auth0|test-user",
        "email": "test@example.com",
        "name": "Test User",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "at_hash": calculate_at_hash(access_token, hashlib.sha256),
    }
    token = jose_jwt.encode(
        claims,
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        algorithm="RS256",
        headers={"kid": kid},
    )
    return token, access_token


def _auth0_request(code="test-code"):
    factory = APIRequestFactory()
    django_request = factory.get("/saml2_auth/auth/callback/", {"code": code})
    django_request.session = {}
    return Request(django_request)


def _patch_auth0(monkeypatch, jwks_keys, sign_key, kid="test-kid-1"):
    """Patch the Auth0 domain/client/cache/JWKS, then sign an ID token with
    ``sign_key`` for the patched domain and stub the token-exchange POST."""
    monkeypatch.setattr(views, "AUTH0_DOMAIN", "test.auth0.example")
    monkeypatch.setattr(views, "AUTH0_CLIENT_ID", "test-client")
    monkeypatch.setattr(views, "cache", _FakeCache())
    monkeypatch.setattr(
        views.requests, "get", lambda *a, **k: _FakeHttpResponse({"keys": jwks_keys})
    )
    id_token, access_token = _make_id_token(sign_key, kid)
    monkeypatch.setattr(
        views.requests,
        "post",
        lambda *a, **k: _FakeHttpResponse(
            {"id_token": id_token, "access_token": access_token}
        ),
    )
    return id_token, access_token


def test_auth0_callback_rejects_forged_token(monkeypatch):
    """A token signed by a key not in the JWKS must be rejected."""
    jwks_key, _ = _generate_keypair()
    forged_key, _ = _generate_keypair()
    _patch_auth0(monkeypatch, [_rsa_jwk(jwks_key.public_key())], forged_key)

    response = views.Auth0CallbackView().get(_auth0_request())

    assert response.status_code == 302
    assert "denied=true" in response["Location"]


def test_auth0_callback_accepts_valid_token(monkeypatch):
    """A token signed by the JWKS key with matching claims must be accepted."""
    key, _ = _generate_keypair()
    _patch_auth0(monkeypatch, [_rsa_jwk(key.public_key())], key)

    monkeypatch.setattr(
        views.User.objects,
        "get",
        lambda email: (_ for _ in ()).throw(views.User.DoesNotExist),
    )
    monkeypatch.setattr(
        views, "first_signup", lambda data, mode=None: types.SimpleNamespace(id="u1")
    )
    monkeypatch.setattr(
        views.AuthToken.objects,
        "create",
        lambda **kwargs: types.SimpleNamespace(id="t1"),
    )
    monkeypatch.setattr(views, "generate_encrypted_message", lambda payload: "enc-tok")
    monkeypatch.setattr(views, "track_mixpanel_event", lambda *a, **k: None)

    response = views.Auth0CallbackView().get(_auth0_request())

    assert response.status_code == 302
    assert "sso_token=enc-tok" in response["Location"]
    assert "is_new_user=true" in response["Location"]
