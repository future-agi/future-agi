"""Google's real callback and verifier, with an owned HTTP identity boundary.

No token decoder, HTTP client, key lookup, or identity resolution is mocked.
The server only returns synthetic identities and public test signing keys.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.urls import reverse
from django.utils.http import urlsafe_base64_decode
from rest_framework.test import APIClient

from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.user import User
from saml2_auth import google_oauth, views

CLIENT_ID = "synthetic-google-client"
CODE = "synthetic-authorization-code-marker"
ACCESS_TOKEN = "synthetic-access-token-marker"
PROFILE = "synthetic-userinfo-marker"
EMAIL = "google-oauth@example.test"
CLAIM_MARKER = "synthetic-signed-subject-marker"


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _base64_number(number):
    value = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def signing_keys():
    pairs = []
    for kid in ("current", "rotated"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = key.public_key().public_numbers()
        public = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": kid,
            "n": _base64_number(numbers.n),
            "e": _base64_number(numbers.e),
        }
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pairs.append((private, public))
    return pairs


@pytest.fixture
def identity_server(signing_keys, settings, monkeypatch, tmp_path):
    state = {
        "keys": [signing_keys[0][1]],
        "requests": [],
        "status": 200,
        "profile_status": 200,
        "jwks_status": 200,
        "provider": "google",
        "exchange_access_token": True,
    }

    def signed_claims(*, claims=None, name=True, omit=()):
        payload = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": CLAIM_MARKER,
            "email": EMAIL,
            "email_verified": True,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "at_hash": base64.urlsafe_b64encode(
                hashlib.sha256(ACCESS_TOKEN.encode()).digest()[:16]
            )
            .rstrip(b"=")
            .decode(),
        }
        if name:
            payload["name"] = PROFILE
        payload.update(claims or {})
        for claim in omit:
            payload.pop(claim, None)
        return payload

    def issue(*, claims=None, key=0, kid=None, name=True, omit=()):
        payload = signed_claims(claims=claims, name=name, omit=omit)
        state["id_token"] = jwt.encode(
            payload,
            signing_keys[key][0],
            algorithm="RS256",
            headers={"kid": kid or signing_keys[key][1]["kid"]},
        )
        return state["id_token"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Avoid test-server access logs, which are not application logs.
            pass

        def send_json(self, status, data):
            encoded = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers["Content-Type"] == "application/json":
                body = json.loads(raw)
            else:
                body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            state["requests"].append(("POST", self.path, body))
            assert self.path in ("/token", "/access_token")
            assert body["code"] == CODE
            assert body["client_id"] == CLIENT_ID
            tokens = {"id_token": state["id_token"], "error_description": PROFILE}
            if state["exchange_access_token"]:
                tokens["access_token"] = ACCESS_TOKEN
            self.send_json(state["status"], tokens)

        def do_GET(self):
            state["requests"].append(("GET", self.path, dict(self.headers)))
            if self.path == "/certs":
                self.send_json(state["jwks_status"], {"keys": state["keys"]})
            elif urlparse(self.path).path in ("/userinfo", "/user", "/me"):
                self.send_json(
                    state["profile_status"],
                    {
                        "name": PROFILE,
                        "displayName": PROFILE,
                        "email": EMAIL,
                        "mail": EMAIL,
                    },
                )
            else:
                self.send_json(404, {})

    tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(tls_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "tls.pem", tmp_path / "tls-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        tls_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"https://127.0.0.1:{server.server_port}"
    settings.GOOGLE_OAUTH_JWKS_URL = f"{base}/certs"
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert_path))
    monkeypatch.setattr(views, "AUTH0_DOMAIN", f"127.0.0.1:{server.server_port}")
    monkeypatch.setattr(views, "AUTH0_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(views, "GOOGLE_USERINFO_API", f"{base}/userinfo")
    for provider in ("GITHUB", "MICROSOFT"):
        monkeypatch.setattr(views, f"{provider}_OAUTH_URL", base)
        monkeypatch.setattr(views, f"{provider}_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(views, "GITHUB_API_ENDPOINT", base)
    monkeypatch.setattr(views, "MICROSOFT_GRAPH_API", base)
    cache.clear()
    issue()
    state["issue"] = issue
    state["signed_claims"] = signed_claims
    yield state
    server.shutdown()
    server.server_close()
    thread.join()
    cache.clear()


@pytest.fixture
def google_user(db):
    org = Organization.objects.create(name="Synthetic Google OAuth")
    return User.objects.create_user(
        email=EMAIL,
        name=PROFILE,
        password="synthetic-password",
        organization=org,
    )


def _callback():
    client = APIClient()
    response = client.get(reverse("auth0_callback"), {"code": CODE})
    return client, response


def _assert_denied(client, response, before):
    assert response.status_code == 302
    query = parse_qs(urlparse(response["Location"]).query)
    assert query["denied"] == ["true"]
    assert (
        urlsafe_base64_decode(query["reason"][0]) == b"Unable to process OAuth callback"
    )
    assert "sso_token" not in query
    assert "_auth_user_id" not in client.session
    assert AuthToken.objects.count() == before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "issuer", ["accounts.google.com", "https://accounts.google.com"]
)
def test_google_callback_signs_in_with_google_issuer(
    identity_server, google_user, issuer
):
    identity_server["issue"](claims={"iss": issuer})
    before = AuthToken.objects.filter(user=google_user).count()
    _, response = _callback()
    query = parse_qs(urlparse(response["Location"]).query)
    assert response.status_code == 302
    assert query["sso_token"]
    assert query["is_new_user"] == ["false"]
    assert (
        AuthToken.objects.filter(user=google_user, is_active=True).count() == before + 1
    )
    assert [r[:2] for r in identity_server["requests"]] == [
        ("POST", "/token"),
        ("GET", "/certs"),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["signature", "issuer", "audience", "expired"])
def test_google_callback_refuses_invalid_identity(
    identity_server, google_user, invalid
):
    kwargs = {
        "signature": {"key": 1, "kid": "current"},
        "issuer": {"claims": {"iss": "https://other.example.test"}},
        "audience": {"claims": {"aud": "another-client"}},
        "expired": {"claims": {"exp": int(time.time()) - 60}},
    }[invalid]
    identity_server["issue"](**kwargs)
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert [r[:2] for r in identity_server["requests"]] == [
        ("POST", "/token"),
        ("GET", "/certs"),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("exchange_status", [200, 400])
def test_google_callback_keeps_secrets_out_of_logs(
    identity_server, google_user, caplog, exchange_status
):
    identity_server["issue"](name=False)
    identity_server["status"] = exchange_status
    caplog.set_level(logging.DEBUG)
    before = AuthToken.objects.count()
    client, response = _callback()
    if exchange_status == 200:
        assert "sso_token=" in response["Location"]
        request = identity_server["requests"][-1]
        assert request[1] == "/userinfo?alt=json"
        assert request[2]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    else:
        _assert_denied(client, response, before)
    for marker in (
        CODE,
        ACCESS_TOKEN,
        identity_server["id_token"],
        PROFILE,
        CLAIM_MARKER,
    ):
        assert marker not in caplog.text


@pytest.mark.django_db
def test_google_keys_are_cached_and_refetched_on_rotation(
    identity_server, signing_keys, google_user
):
    for _ in range(2):
        _, response = _callback()
        assert "sso_token=" in response["Location"]
    assert sum(r[1] == "/certs" for r in identity_server["requests"]) == 1
    identity_server["keys"] = [signing_keys[1][1]]
    identity_server["issue"](key=1)
    _, response = _callback()
    assert "sso_token=" in response["Location"]
    assert sum(r[1] == "/certs" for r in identity_server["requests"]) == 2


@pytest.mark.django_db
def test_google_unknown_key_refetches_once_then_denies(identity_server, google_user):
    _, response = _callback()
    assert "sso_token=" in response["Location"]
    identity_server["issue"](key=1)
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert sum(r[1] == "/certs" for r in identity_server["requests"]) == 2


def test_google_production_keys_endpoint(settings):
    # Google's published endpoint, not derived from AUTH0_DOMAIN or an issuer.
    assert (
        settings.GOOGLE_OAUTH_JWKS_URL == "https://www.googleapis.com/oauth2/v3/certs"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("claim", ["exp", "aud", "iss", "sub"])
def test_google_callback_requires_identity_claims(identity_server, google_user, claim):
    identity_server["issue"](omit=(claim,))
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)


UNVERIFIED_EMAIL = {
    "false": {"claims": {"email_verified": False}},
    "false-string": {"claims": {"email_verified": "false"}},
    "missing": {"omit": ("email_verified",)},
}


@pytest.mark.django_db
@pytest.mark.parametrize("case", list(UNVERIFIED_EMAIL))
def test_google_callback_requires_verified_email_for_existing_user(
    identity_server, google_user, case
):
    identity_server["issue"](**UNVERIFIED_EMAIL[case])
    before = AuthToken.objects.count()
    users = User.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert not AuthToken.objects.filter(user=google_user).exists()
    assert User.objects.count() == users


@pytest.mark.django_db
@pytest.mark.parametrize("case", list(UNVERIFIED_EMAIL))
def test_google_callback_requires_verified_email_for_new_user(identity_server, case):
    options = UNVERIFIED_EMAIL[case]
    claims = {**options.get("claims", {}), "email": "google-new@example.test"}
    identity_server["issue"](claims=claims, omit=options.get("omit", ()))
    before = AuthToken.objects.count()
    users = User.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert User.objects.count() == users
    assert not User.objects.filter(email="google-new@example.test").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", [400, 503])
def test_google_key_service_failure_is_closed(identity_server, google_user, status):
    identity_server["jwks_status"] = status
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert sum(r[1] == "/certs" for r in identity_server["requests"]) == 1


@pytest.mark.django_db
def test_google_unknown_key_cold_cache_fetches_once(identity_server, google_user):
    identity_server["issue"](key=1)
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert sum(r[1] == "/certs" for r in identity_server["requests"]) == 1


@pytest.mark.django_db
def test_google_retains_access_token_hash_validation(identity_server, google_user):
    identity_server["issue"](claims={"at_hash": "wrong-access-token-hash"})
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)


def _assert_signed_in(response, user, before):
    assert response.status_code == 302
    query = parse_qs(urlparse(response["Location"]).query)
    assert query["sso_token"]
    assert "denied" not in query
    assert AuthToken.objects.filter(user=user, is_active=True).count() == before + 1


@pytest.mark.django_db
@pytest.mark.parametrize("access_token_returned", [True, False])
def test_google_callback_accepts_token_without_access_token_hash(
    identity_server, google_user, access_token_returned
):
    identity_server["exchange_access_token"] = access_token_returned
    identity_server["issue"](omit=("at_hash",))
    before = AuthToken.objects.filter(user=google_user).count()
    _, response = _callback()
    _assert_signed_in(response, google_user, before)


@pytest.mark.django_db
def test_google_callback_requires_access_token_for_access_token_hash(
    identity_server, google_user
):
    # The ID token carries at_hash, but the token exchange returns no access
    # token to compare it with.
    identity_server["exchange_access_token"] = False
    identity_server["issue"]()
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)


@pytest.mark.django_db
@pytest.mark.parametrize("at_hash", ["", "wrong-\u00e9", 12345, None, ["list"]])
def test_google_callback_refuses_malformed_access_token_hash(
    identity_server, google_user, at_hash
):
    identity_server["issue"](claims={"at_hash": at_hash})
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)


@pytest.mark.django_db
def test_google_callback_accepts_issued_at_within_clock_skew(
    identity_server, google_user
):
    identity_server["issue"](claims={"iat": int(time.time()) + 5})
    before = AuthToken.objects.filter(user=google_user).count()
    _, response = _callback()
    _assert_signed_in(response, google_user, before)


@pytest.mark.django_db
def test_google_callback_refuses_issued_at_beyond_clock_skew(
    identity_server, google_user
):
    identity_server["issue"](claims={"iat": int(time.time()) + 120})
    before = AuthToken.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)


def _compact_token(header, payload, sign):
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode())
        for part in (header, payload)
    )
    return signing_input + "." + _b64url(sign(signing_input.encode()))


def _other_algorithm_token(private_pem, payload, algorithm):
    """Build a token by hand whose header names ``algorithm`` and kid
    "current", so no JWT library decides what may be produced."""
    private = serialization.load_pem_private_key(private_pem, password=None)
    if algorithm == "HS256":
        # HMAC keyed with the published RSA public key, as SubjectPublicKeyInfo
        # PEM.
        secret = private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def sign(data):
            return hmac.new(secret, data, hashlib.sha256).digest()

    elif algorithm == "none":

        def sign(_data):
            return b""

    else:

        def sign(data):
            return private.sign(data, padding.PKCS1v15(), hashes.SHA512())

    header = {"alg": algorithm, "typ": "JWT", "kid": "current"}
    return _compact_token(header, payload, sign)


OTHER_ALGORITHMS = ["HS256", "none", "RS512"]


@pytest.mark.django_db
@pytest.mark.parametrize("algorithm", OTHER_ALGORITHMS)
def test_google_callback_refuses_other_signing_algorithms(
    identity_server, signing_keys, google_user, algorithm
):
    identity_server["id_token"] = _other_algorithm_token(
        signing_keys[0][0], identity_server["signed_claims"](), algorithm
    )
    before = AuthToken.objects.count()
    users = User.objects.count()
    client, response = _callback()
    _assert_denied(client, response, before)
    assert User.objects.count() == users


def _unit_claims():
    now = int(time.time())
    return {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": CLAIM_MARKER,
        "iat": now,
        "exp": now + 300,
    }


@pytest.mark.parametrize("algorithm", OTHER_ALGORITHMS)
def test_google_decode_accepts_only_rs256_whatever_the_header_says(
    signing_keys, algorithm
):
    # The decode step alone, without the callback's header pre-check: the
    # algorithm comes from the verifier, never from the token.
    private, public = signing_keys[0]
    token = _other_algorithm_token(private, _unit_claims(), algorithm)
    with pytest.raises(jwt.InvalidTokenError):
        google_oauth._decode_claims(token, public, CLIENT_ID)


def test_google_decode_verifies_rs256_with_the_published_key(signing_keys):
    private, public = signing_keys[0]
    claims = _unit_claims()
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "current"})
    assert google_oauth._decode_claims(token, public, CLIENT_ID) == claims
    with pytest.raises(jwt.InvalidTokenError):
        google_oauth._decode_claims(token, signing_keys[1][1], CLIENT_ID)


@pytest.mark.parametrize(
    "key",
    [
        {"kty": "RSA", "kid": "current"},
        {"kty": "oct", "kid": "current", "k": "c3ludGhldGlj"},
    ],
    ids=["rsa-without-modulus", "symmetric"],
)
def test_google_decode_refuses_unusable_signing_key(signing_keys, key):
    private, _ = signing_keys[0]
    token = jwt.encode(
        _unit_claims(), private, algorithm="RS256", headers={"kid": "current"}
    )
    with pytest.raises(jwt.InvalidTokenError):
        google_oauth._decode_claims(token, key, CLIENT_ID)


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["github", "microsoft"])
@pytest.mark.parametrize("failure", [None, "token", "profile"])
def test_other_oauth_callbacks_do_not_log_secrets(
    identity_server,
    google_user,
    caplog,
    provider,
    failure,
):
    identity_server["status"] = 400 if failure == "token" else 200
    identity_server["profile_status"] = 400 if failure == "profile" else 200
    caplog.set_level(logging.DEBUG)
    client = APIClient()
    before = AuthToken.objects.count()
    response = client.get(reverse(f"{provider}-callback"), {"code": CODE})
    if failure:
        _assert_denied(client, response, before)
    else:
        assert "sso_token=" in response["Location"]
        assert AuthToken.objects.filter(user=google_user).count() == before + 1
    for marker in (
        CODE,
        ACCESS_TOKEN,
        identity_server["id_token"],
        PROFILE,
        CLAIM_MARKER,
    ):
        assert marker not in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize("valid", [True, False])
def test_google_marketplace_callback_preserves_organization(
    identity_server,
    monkeypatch,
    valid,
):
    from accounts import gcp_marketplace_utils as marketplace
    from accounts.models.gcp_marketplace import GCPMarketplaceAccount
    from accounts.models.organization_membership import OrganizationMembership

    org = Organization.objects.create(name="Synthetic Marketplace")
    account = GCPMarketplaceAccount.objects.create(
        procurement_account_id="synthetic-procurement-account",
        organization=org,
    )
    token = marketplace._mint_onboarding_token(account)
    # Only ancillary procurement and post-registration jobs are replaced. The
    # callback, HTTP token exchange, JWT verifier, state reader and signup run.
    monkeypatch.setattr(
        marketplace.gcp_procurement, "get_account", lambda _: {"approvals": []}
    )
    monkeypatch.setattr(marketplace, "process_post_registration", lambda *_: None)
    if not valid:
        identity_server["issue"](key=1, kid="current")
    client = APIClient()
    before = AuthToken.objects.count()
    response = client.get(
        reverse("auth0_callback"),
        {
            "code": CODE,
            "state": marketplace.encode_oauth_state(token),
        },
    )
    if valid:
        query = parse_qs(urlparse(response["Location"]).query)
        assert query["sso_token"]
        assert query["is_new_user"] == ["true"]
        user = User.objects.get(email=EMAIL)
        assert user.organization_id == org.id
        assert Organization.objects.count() == 1
        assert OrganizationMembership.objects.filter(
            user=user,
            organization=org,
            is_active=True,
        ).exists()
        assert AuthToken.objects.filter(user=user).count() == before + 1
        account.refresh_from_db()
        assert account.approved_at is not None
    else:
        _assert_denied(client, response, before)
        assert not User.objects.filter(email=EMAIL).exists()
        account.refresh_from_db()
        assert account.approved_at is None
        assert marketplace.read_onboarding_token(token)["organization_id"] == str(
            org.id
        )


@pytest.fixture
def preserve_root_logging():
    # The shared capture helper restores named loggers. These DEBUG cases also
    # restore pytest's root handlers, so later tests and shutdown never write to
    # a production handler whose captured output stream has already closed.
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["google", "github", "microsoft"])
@pytest.mark.parametrize("valid", [True, False])
def test_production_callback_logging_redacts_query_and_signed_claims(
    identity_server,
    google_user,
    settings,
    monkeypatch,
    tmp_path,
    provider,
    valid,
    preserve_root_logging,
):
    from saml2_auth.tests.log_capture import capture_security_logging
    from tfc.settings import settings as production

    # Execute the production middleware and both real structured handlers, not
    # the reduced pytest middleware or a structlog.testing processor.
    settings.MIDDLEWARE = production.MIDDLEWARE
    settings.BASE_DIR = tmp_path
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    identity_server["issue"](name=True)
    identity_server["status"] = 200 if valid else 400
    callback = "auth0_callback" if provider == "google" else f"{provider}-callback"
    client = APIClient()
    before = AuthToken.objects.count()
    with capture_security_logging() as logs:
        response = client.get(
            reverse(callback),
            {
                "code": CODE,
                "id_token": identity_server["id_token"],
                "access_token": ACCESS_TOKEN,
                "customToken": "synthetic-custom-token-marker",
                "TOKEN_value": "synthetic-uppercase-token-marker",
            },
        )
        console = logs.rendered
        file_log = (tmp_path / "logs" / "wsgi.log").read_text()
    if valid:
        assert response.status_code == 302
        assert "sso_token=" in response["Location"]
        assert AuthToken.objects.filter(user=google_user).count() == before + 1
    else:
        _assert_denied(client, response, before)
    for rendered in (console, file_log):
        events = [json.loads(line) for line in rendered.splitlines() if line.strip()]
        requests = [e for e in events if e.get("event") == "request_started"]
        assert len(requests) == 1
        assert requests[0]["request"] == f"GET {reverse(callback)}"
        assert any(e.get("event") == "request_finished" for e in events)
        assert any(e.get("event") == "oauth_callback_received" for e in events)
        if valid and provider == "google":
            assert any(e.get("event") == "oauth_identity_decoded" for e in events)
        for marker in (
            CODE,
            ACCESS_TOKEN,
            identity_server["id_token"],
            PROFILE,
            CLAIM_MARKER,
            "synthetic-custom-token-marker",
            "synthetic-uppercase-token-marker",
        ):
            assert marker not in rendered


def test_request_logging_preserves_non_sso_queries_and_callback_input():
    from django.test import RequestFactory

    from tfc.logging.middleware import CallbackRequestMiddleware

    request = RequestFactory().get("/saml2_auth/auth/callback/", {"code": CODE})
    assert CallbackRequestMiddleware.format_request(request) == (
        "GET /saml2_auth/auth/callback/"
    )
    assert request.GET["code"] == CODE
    ordinary = RequestFactory().get("/health/", {"detail": "true"})
    assert CallbackRequestMiddleware.format_request(ordinary) == (
        "GET /health/?detail=true"
    )
