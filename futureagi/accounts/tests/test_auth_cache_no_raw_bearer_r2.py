"""Exercise every access-cache issuer and the shared digest contract.

PYTEST_DONT_REWRITE: assertion output must never include credentials or payloads.
"""
import hashlib
import hmac
import importlib
import importlib.util
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.test import RequestFactory
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.authentication import decode_token, decrypt_message, generate_encrypted_message
from accounts.models.auth_token import AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES, AuthToken, AuthTokenType
from accounts.tests.test_invitation_expiry import pending_invitation  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_cache(settings, monkeypatch):
    settings.CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "auth-cache-digest-r2-" + uuid4().hex,
    }}
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    from analytics.posthog_util import posthog_tracker
    monkeypatch.setattr(posthog_tracker, "client", None)
    cache.clear()
    yield
    cache.clear()


def digest_only(bearer, user, *, classified=False):
    token_id = decrypt_message(bearer)["id"]
    payload = cache.get(f"access_token_{token_id}")
    assert isinstance(payload, dict), "issuance must populate access cache"
    assert not any(value == bearer for value in payload.values()), "raw bearer retained"
    assert "token" not in payload, "legacy raw field retained"
    assert payload.get("token_digest") == hashlib.sha256(bearer.encode()).hexdigest(), "digest mismatch"
    assert payload["user"].pk == user.pk, "cache user changed"
    assert (payload.get("non_saml_v1") is True) is classified, "classification changed"
    assert set(payload) == ({"token_digest", "user", "non_saml_v1"} if classified else {"token_digest", "user"})


@pytest.mark.parametrize("issuer", ["issue_tokens", "issue_sos_tokens"])
def test_token_service_issuance_has_no_raw_bearer(user, issuer):
    from accounts.services import token_service
    pair = getattr(token_service, issuer)(user)
    digest_only(pair["access"], user)


def test_login_issuance_has_no_raw_bearer(user):
    from accounts.views.user import CustomTokenObtainPairView
    from accounts.models.organization_membership import OrganizationMembership
    password = uuid4().hex + "aA1!"
    user.set_password(password)
    user.save(update_fields=["password"])
    request = RequestFactory().post("/accounts/token/", HTTP_HOST="localhost")
    request.validated_data = {"email": user.email, "password": password}
    empty_membership = MagicMock()
    empty_membership.select_related.return_value.order_by.return_value.first.return_value = None
    with patch.object(OrganizationMembership.no_workspace_objects, "filter", return_value=empty_membership):
        response = inspect.unwrap(CustomTokenObtainPairView.post)(CustomTokenObtainPairView(), request)
    assert response.status_code == 200, "login did not issue"
    assert "access" in response.data, "login skipped issuance"
    digest_only(response.data["access"], user)


def test_refresh_issuance_has_no_raw_bearer(user):
    from accounts.services.token_service import issue_tokens
    from accounts.views.user import CustomTokenRefreshView
    pair = issue_tokens(user)
    request = RequestFactory().post("/accounts/token/refresh/", HTTP_HOST="localhost")
    request.validated_data = {"refresh": pair["refresh"]}
    response = inspect.unwrap(CustomTokenRefreshView.post)(CustomTokenRefreshView(), request)
    assert response.status_code == 200, "refresh did not issue"
    digest_only(response.data["access"], user)


def test_marketplace_issuance_has_no_raw_bearer(user, settings):
    from accounts.aws_marketplace_utils import _create_access_token
    settings.AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES = 13
    with patch.object(cache, "set", wraps=cache.set) as write:
        bearer = _create_access_token(user)
    digest_only(bearer, user)
    assert write.call_count == 1
    assert write.call_args.kwargs["timeout"] == 13 * 60, "marketplace TTL changed"


@pytest.mark.parametrize("view_name", ["Auth0CallbackView", "GithubCallbackView", "MicrosoftCallbackView"])
def test_sso_callback_issuance_has_no_raw_bearer(user, view_name):
    from saml2_auth import views
    request = RequestFactory().get("/saml2/oauth-cache-probe/", HTTP_HOST="localhost")
    request.session = {}
    request.validated_query_data = {"code": uuid4().hex}
    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"access_token": uuid4().hex, "id_token": uuid4().hex}
    profile_response = MagicMock(status_code=200)
    profile_response.json.return_value = {
        "email": user.email, "name": user.name, "userPrincipalName": user.email,
        "displayName": user.name,
    }
    with (
        patch.object(views.requests, "post", return_value=token_response),
        patch.object(views.requests, "get", return_value=profile_response),
        patch.object(views, "_verified_google_id_token", return_value={"email": user.email, "name": user.name}),
        patch.object(views, "read_oauth_state", return_value=None),
        patch.object(views, "resolve_sso_user", return_value=(user, "https://example.test/signed-in", "false")),
        patch.object(views, "get_mixpanel_properties", return_value={}),
        patch.object(views, "track_mixpanel_event"),
    ):
        view = getattr(views, view_name)
        response = inspect.unwrap(view.get)(view(), request)
    assert response.status_code == 302, "callback did not redirect"
    query = parse_qs(urlsplit(response["Location"]).query)
    assert response["new_org"] == "false", "callback did not issue"
    if hasattr(views, "_sso_handoff_query"):
        # c26 already carries a one-time code; redeem the real issued payload.
        from accounts.services.handoff_exchange import KIND_SSO, redeem
        assert "sso_token" not in query, "handoff leaked bearer in URL"
        assert "code" in query and query.get("kind") == [KIND_SSO], "handoff absent"
        bearer = redeem(query["code"][0], KIND_SSO)["access"]
    else:
        # c25 retains its existing direct-token handoff contract.
        assert "sso_token" in query, "callback did not issue"
        bearer = query["sso_token"][0]
    digest_only(bearer, user)


def test_invitation_issuance_has_no_raw_bearer(pending_invitation):
    from accounts.views.signup import _accept_invitation_mail
    user, _, _ = pending_invitation
    user.refresh_from_db()
    invitation = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    password = uuid4().hex + "aA1!"
    request = RequestFactory().post("/accounts/accept-invitation/", HTTP_HOST="localhost")
    request.validated_data = {"new_password": password, "repeat_password": password}
    response = _accept_invitation_mail(request, uid, invitation)
    assert response.status_code == 200, "invitation did not issue"
    digest_only(response.data["access"], user)


def helper():
    assert importlib.util.find_spec("accounts.services.auth_cache") is not None, "shared helper missing"
    return importlib.import_module("accounts.services.auth_cache")


def test_matches_calls_constant_time_compare(monkeypatch, user):
    module = helper()
    bearer = uuid4().hex
    expected = hashlib.sha256(bearer.encode()).hexdigest()
    compare = MagicMock(wraps=hmac.compare_digest)
    monkeypatch.setattr(module.hmac, "compare_digest", compare)
    assert module.matches({"token_digest": expected, "user": user}, bearer)
    assert compare.call_count == 1, "constant-time comparator not used"
    assert compare.call_args.args == (expected, expected), "comparison did not use digests"
    assert not module.matches({"token_digest": hashlib.sha256(uuid4().bytes).hexdigest()}, bearer)
    assert compare.call_count == 2


@pytest.mark.parametrize("kind", ["legacy", "missing", "none", "bytes", "unicode", "short"])
def test_matches_rejects_legacy_and_malformed_entries(kind):
    module = helper()
    bearer = uuid4().hex
    payloads = {
        "legacy": {"token": bearer, "non_saml_v1": True},
        "missing": {}, "none": None,
        "bytes": {"token_digest": b"a" * 64},
        "unicode": {"token_digest": "☃" * 64},
        "short": {"token_digest": "a"},
    }
    assert module.matches(payloads[kind], bearer) is False, "invalid cache entry accepted"


@pytest.mark.parametrize("classified", [False, True])
def test_helper_preserves_key_timeout_and_marker(user, classified):
    module = helper()
    bearer, token_id = uuid4().hex, uuid4()
    with patch.object(cache, "set", wraps=cache.set) as write:
        module.cache_access_token(token_id, bearer, user, non_saml=classified, timeout=123)
    payload = cache.get(f"access_token_{token_id}")
    assert not any(value == bearer for value in payload.values()), "raw bearer retained"
    assert payload["token_digest"] == hashlib.sha256(bearer.encode()).hexdigest()
    assert (payload.get("non_saml_v1") is True) is classified
    assert write.call_count == 1
    assert write.call_args.kwargs["timeout"] == 123


@pytest.fixture
def session(user):
    row = AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now())
    bearer = generate_encrypted_message({"id": str(row.pk), "user_id": str(user.pk)})
    return SimpleNamespace(user=user, row=row, bearer=bearer, key=f"access_token_{row.pk}")


def test_reader_rejects_legacy_authority_and_rewrites(session):
    session.user.cache_r2_marker = True
    cache.set(session.key, {"token": session.bearer, "user": session.user, "non_saml_v1": True})
    user, _, _ = decode_token(session.bearer)
    assert not getattr(user, "cache_r2_marker", False), "legacy authority accepted"
    digest_only(session.bearer, session.user, classified=True)


def test_reader_uses_shared_constant_time_match(session, monkeypatch):
    module = helper()
    decode_token(session.bearer)
    compare = MagicMock(wraps=hmac.compare_digest)
    monkeypatch.setattr(module.hmac, "compare_digest", compare)
    decode_token(session.bearer)
    assert compare.call_count == 1, "reader bypasses shared constant-time match"
    digest_only(session.bearer, session.user, classified=True)


def test_other_digest_cannot_authorize_missing_token(session):
    from rest_framework.exceptions import AuthenticationFailed
    session.row.delete()
    cache.set(session.key, {"token_digest": hashlib.sha256(uuid4().bytes).hexdigest(),
                            "user": session.user, "non_saml_v1": True})
    with pytest.raises(AuthenticationFailed):
        decode_token(session.bearer)
    assert cache.get(session.key) is None, "failed cache authority retained"
