"""CR-07: no bearer, access or refresh token may travel in a URL.

Every login
handoff (OAuth SSO, SAML, SOS) now redirects with a one-time opaque code that
the frontend redeems by a JSON POST from the app's own origin; the tokens come
back only in that response body, which is never cached.

These tests pin:

- no issuer URL (Google, GitHub, Microsoft, SAML, SOS browser and copy-link)
  carries a token value or a legacy token parameter, only the code;
- the code is at least 128 bits, lives at most 60 seconds and redeems once;
- a second use, an expired or unknown code, a wrong kind, a wrong or missing
  Origin and a malformed body all get the same categorical refusal;
- the redeem response is no-store and no-referrer;
- the same session is still minted and the same destination still reached.

The OAuth and SAML callbacks are driven DB-free with their collaborators
faked, as the existing saml2_auth tests do. The cache is a real, isolated
LocMemCache so single use and expiry are exercised, not mocked.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from itertools import count
from threading import Event
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from accounts.admin import SOSLoginProxy
from accounts.models.auth_token import AuthToken
from accounts.models.user import User
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache, caches
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.services import handoff_exchange
from accounts.services.handoff_exchange import HandoffRefused, issue, redeem
from saml2_auth import views as saml_views

APP_HOST = "app.handoff.test"
APP_ORIGIN = f"https://{APP_HOST}"
SUCCESS_NEXT_URL = f"{APP_ORIGIN}/dashboard/develop"
LEGACY_TOKEN_PARAMS = (
    "sso_token",
    "access_token",
    "refresh_token",
    "access",
    "refresh",
)

# synthetic test value
SYNTHETIC_SESSION = "synthetic-encrypted-session-not-a-credential"
# synthetic test value
SYNTHETIC_SAML_SESSION = "synthetic-saml-session-not-a-credential"
# synthetic test value
SYNTHETIC_ACCESS = "synthetic-access-not-a-credential"
# synthetic test value
SYNTHETIC_REFRESH = "synthetic-refresh-not-a-credential"
# synthetic test value
SYNTHETIC_PROVIDER_ACCESS = "synthetic-provider-access-not-a-credential"
# synthetic test value
SYNTHETIC_ID_TOKEN = "synthetic-id-token-not-a-credential"
# synthetic test value
SYNTHETIC_OAUTH_CODE = "synthetic-oauth-authorization-code"
# synthetic test value; code-shaped but never issued
SYNTHETIC_UNISSUED_CODE = "synthetic-unissued-code-shape-0000000000000"
# synthetic test value
SYNTHETIC_UNKNOWN_CODE = "synthetic-never-issued-code"
# synthetic test value
SYNTHETIC_MALFORMED_CODE = "synthetic code!"
# synthetic test value; runtime-built oversized filler
SYNTHETIC_OVERSIZED_CODE = "FAKE-oversized-code-not-a-credential" * 100
# synthetic test value
SYNTHETIC_SAML_CANDIDATE = "synthetic-saml-candidate-key"
# synthetic test value
SYNTHETIC_SAML_BINDER = "synthetic-saml-binder-cookie"
# synthetic test value
SYNTHETIC_SAML_REQUEST_ID = "synthetic-saml-request-id"
# synthetic test value
SYNTHETIC_SAML_RELAY_KEY = "synthetic-saml-relay-key"
# synthetic test value
SYNTHETIC_SAML_RESPONSE = b"synthetic-saml-response"
# synthetic test value
SYNTHETIC_IDP_METADATA = "<synthetic-idp-metadata/>"


@pytest.fixture(autouse=True)
def isolated_handoff_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"handoff-cr07-{uuid.uuid4()}",
        }
    }
    settings.APP_URL = APP_HOST
    settings.APP_BASE_URL = APP_ORIGIN
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def synthetic_handoff_codes(monkeypatch):
    counter = count()

    def mint_code(_bytes):
        # synthetic test value
        return f"FAKE{next(counter):039d}"

    monkeypatch.setattr(handoff_exchange.secrets, "token_urlsafe", mint_code)


def _query(url):
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def _assert_no_token_in_url(url, *token_values):
    for value in token_values:
        assert value not in url
    query = _query(url)
    for key in LEGACY_TOKEN_PARAMS:
        assert key not in query, key


def _code_from(url):
    codes = _query(url).get("code")
    assert codes and len(codes) == 1, url
    return codes[0]


@contextmanager
def _expired_cache_clock(seconds_from_now):
    """Move LocMemCache's own clock; the expiry it stored used the real one."""
    target = time.time() + seconds_from_now
    with patch("django.core.cache.backends.locmem.time") as fake_time:
        fake_time.time.return_value = target
        yield


# --- the shared helper ------------------------------------------------------


def test_code_is_an_opaque_urlsafe_value_of_at_least_128_bits():
    with patch.object(
        handoff_exchange.secrets,
        "token_urlsafe",
        wraps=handoff_exchange.secrets.token_urlsafe,
    ) as minted:
        code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})

    assert minted.call_args.args[0] >= 16
    assert re.fullmatch(r"[A-Za-z0-9_-]{22,}", code)
    assert SYNTHETIC_SESSION not in code
    assert code != issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})


def test_code_lives_at_most_sixty_seconds():
    assert 0 < handoff_exchange.HANDOFF_CODE_TTL_SECONDS <= 60


def test_code_redeems_once_for_its_kind_and_payload():
    code = issue("sos", {"access": SYNTHETIC_ACCESS, "refresh": SYNTHETIC_REFRESH})

    assert redeem(code, "sos") == {
        "access": SYNTHETIC_ACCESS,
        "refresh": SYNTHETIC_REFRESH,
    }
    with pytest.raises(HandoffRefused):
        redeem(code, "sos")


@pytest.mark.parametrize("resume_after_ttl", [False, True])
def test_concurrent_redeems_have_one_winner_even_after_claim_expiry(
    resume_after_ttl,
):
    """Pause the first reader *after get*, then let the other redeem finish."""
    backend = caches["default"]
    first_read = Event()
    resume = Event()
    original_get = backend.get

    def interleaved_get(key, *args, **kwargs):
        record = original_get(key, *args, **kwargs)
        if not first_read.is_set():
            first_read.set()
            assert resume.wait(5), "paused reader was not released"
        return record

    # Bind the same real backend instance in both threads, not a thread-local
    # cache proxy whose patched method would only apply in the parent thread.
    with patch.object(handoff_exchange, "cache", backend):
        code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
        with (
            patch.object(backend, "get", side_effect=interleaved_get),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            paused = pool.submit(redeem, code, "sso")
            try:
                assert first_read.wait(5), "first reader never reached get"
                winner = pool.submit(redeem, code, "sso")
                assert winner.result(timeout=5) == {
                    "access": SYNTHETIC_SESSION,
                    "refresh": None,
                }
                clock = (
                    _expired_cache_clock(handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1)
                    if resume_after_ttl
                    else nullcontext()
                )
                with clock:
                    resume.set()
                    with pytest.raises(HandoffRefused) as refused:
                        paused.result(timeout=5)
                assert str(refused.value) == ""
            finally:
                resume.set()


def test_record_expiring_between_get_and_delete_cannot_redeem():
    backend = caches["default"]
    original_get = backend.get
    future = time.time() + handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1
    with patch.object(handoff_exchange, "cache", backend):
        code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
        with patch.object(handoff_exchange.time, "time", wraps=time.time) as clock:

            def expire_after_read(key, *args, **kwargs):
                record = original_get(key, *args, **kwargs)
                clock.return_value = future
                return record

            with (
                patch.object(backend, "get", side_effect=expire_after_read),
                pytest.raises(HandoffRefused),
            ):
                redeem(code, "sso")


@pytest.mark.parametrize("delete_result", [None, False, 0, 2, "1", 1.0])
def test_ambiguous_or_losing_delete_result_is_refused(delete_result):
    backend = caches["default"]
    with patch.object(handoff_exchange, "cache", backend):
        code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
        with (
            patch.object(backend, "delete", return_value=delete_result),
            pytest.raises(HandoffRefused),
        ):
            redeem(code, "sso")


@pytest.mark.parametrize("as_count", [False, True])
def test_atomic_delete_accepts_boolean_or_single_record_count(as_count):
    backend = caches["default"]
    original_delete = backend.delete

    def delete_result(key):
        deleted = original_delete(key)
        return int(deleted) if as_count else deleted

    with patch.object(handoff_exchange, "cache", backend):
        code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
        with patch.object(backend, "delete", side_effect=delete_result):
            assert redeem(code, "sso")["access"] == SYNTHETIC_SESSION
            with pytest.raises(HandoffRefused):
                redeem(code, "sso")


def test_code_is_not_stored_under_its_own_value():
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})

    assert not any(code in key for key in cache._cache)


def test_code_still_redeems_just_inside_its_lifetime():
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})

    with _expired_cache_clock(handoff_exchange.HANDOFF_CODE_TTL_SECONDS - 1):
        assert redeem(code, "sso")["access"] == SYNTHETIC_SESSION


def test_expired_code_is_refused():
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})

    with (
        _expired_cache_clock(handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1),
        pytest.raises(HandoffRefused),
    ):
        redeem(code, "sso")


def test_wrong_kind_is_refused_and_spends_the_code():
    code = issue("saml", {"access": SYNTHETIC_SAML_SESSION, "refresh": None})

    with pytest.raises(HandoffRefused):
        redeem(code, "sso")
    with pytest.raises(HandoffRefused):
        redeem(code, "saml")


@pytest.mark.parametrize(
    "code",
    [
        None,
        "",
        42,
        SYNTHETIC_UNKNOWN_CODE,
        SYNTHETIC_UNISSUED_CODE,
        SYNTHETIC_OVERSIZED_CODE,
        SYNTHETIC_MALFORMED_CODE,
    ],
)
def test_unknown_or_malformed_code_is_refused(code):
    with pytest.raises(HandoffRefused):
        redeem(code, "sso")


@pytest.mark.parametrize("kind", ["", None, "marketplace", "SSO"])
def test_issue_refuses_an_unknown_kind(kind):
    with pytest.raises(ValueError):
        issue(kind, {"access": SYNTHETIC_SESSION, "refresh": None})


def test_issue_refuses_a_payload_without_a_session():
    with pytest.raises(ValueError):
        issue("sso", {"access": "", "refresh": None})


# --- the redeem endpoint ----------------------------------------------------


def _post_redeem(code, kind, origin=APP_ORIGIN, body=None, content_type=None):
    headers = {} if origin is None else {"HTTP_ORIGIN": origin}
    return APIClient().post(
        reverse("handoff-redeem"),
        data=json.dumps({"code": code, "kind": kind}) if body is None else body,
        content_type=content_type or "application/json",
        **headers,
    )


def _refusal(response):
    return response.status_code, response.json()


def _reference_refusal():
    return _refusal(_post_redeem(SYNTHETIC_UNKNOWN_CODE, "sso"))


def _assert_never_cached(response):
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.django_db
def test_redeem_returns_the_tokens_only_in_a_no_store_body():
    code = issue("sos", {"access": SYNTHETIC_ACCESS, "refresh": SYNTHETIC_REFRESH})

    response = _post_redeem(code, "sos")

    assert response.status_code == 200
    assert response.json() == {
        "access": SYNTHETIC_ACCESS,
        "refresh": SYNTHETIC_REFRESH,
    }
    _assert_never_cached(response)


@pytest.mark.django_db
def test_second_redeem_gets_the_categorical_refusal():
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
    assert _post_redeem(code, "sso").status_code == 200

    second = _post_redeem(code, "sso")

    assert second.status_code == 400
    assert _refusal(second) == _reference_refusal()
    assert SYNTHETIC_SESSION not in second.content.decode()
    _assert_never_cached(second)


@pytest.mark.django_db
def test_expired_code_gets_the_categorical_refusal():
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
    reference = _reference_refusal()

    with _expired_cache_clock(handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1):
        expired = _post_redeem(code, "sso")

    assert _refusal(expired) == reference
    _assert_never_cached(expired)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "https://attacker.example",
        f"{APP_ORIGIN}/",
        f"http://{APP_HOST}",
        f"{APP_ORIGIN}:8443",
        f"https://{APP_HOST.upper()}",
        f"https://evil.{APP_HOST}",
    ],
)
def test_wrong_or_missing_origin_gets_the_categorical_refusal(origin):
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
    reference = _reference_refusal()

    refused = _post_redeem(code, "sso", origin=origin)

    assert _refusal(refused) == reference
    assert SYNTHETIC_SESSION not in refused.content.decode()
    _assert_never_cached(refused)


@pytest.mark.django_db
def test_a_refused_cross_origin_attempt_does_not_spend_the_code():
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})

    refused = _post_redeem(code, "sso", origin="https://attacker.example")
    assert refused.status_code == 400
    response = _post_redeem(code, "sso")

    assert response.status_code == 200
    assert response.json()["access"] == SYNTHETIC_SESSION


@pytest.mark.django_db
def test_wrong_kind_and_unknown_code_get_the_categorical_refusal():
    code = issue("saml", {"access": SYNTHETIC_SAML_SESSION, "refresh": None})
    reference = _reference_refusal()

    assert _refusal(_post_redeem(code, "sso")) == reference
    assert _refusal(_post_redeem(code, "saml")) == reference
    assert _refusal(_post_redeem("", "saml")) == reference


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body,content_type",
    [
        ("code=x&kind=sso", "application/x-www-form-urlencoded"),
        ("{not json", "application/json"),
        ('["code", "kind"]', "application/json"),
        ("{}", "application/json"),
    ],
)
def test_non_json_or_malformed_body_gets_the_categorical_refusal(body, content_type):
    reference = _reference_refusal()

    refused = _post_redeem(None, None, body=body, content_type=content_type)

    assert _refusal(refused) == reference
    _assert_never_cached(refused)


@pytest.mark.django_db
def test_redeem_is_post_only():
    response = APIClient().get(reverse("handoff-redeem"), HTTP_ORIGIN=APP_ORIGIN)

    assert response.status_code == 405
    _assert_never_cached(response)


@pytest.mark.django_db
def test_unconfigured_app_url_refuses_every_redeem(settings):
    code = issue("sso", {"access": SYNTHETIC_SESSION, "refresh": None})
    reference = _reference_refusal()
    settings.APP_BASE_URL = ""

    for origin in (APP_ORIGIN, "", "null"):
        assert _refusal(_post_redeem(code, "sso", origin=origin)) == reference


# --- CR-07b: OAuth SSO callbacks -----------------------------------------------


def _account():
    return MagicMock(id=uuid.uuid4(), email="person@example.test", is_active=True)


@contextmanager
def _session_minting():
    auth_token_model = MagicMock()
    auth_token_model.objects.create.return_value = MagicMock(id=uuid.uuid4())
    with (
        patch.object(saml_views, "AuthToken", auth_token_model),
        patch.object(
            saml_views, "generate_encrypted_message", return_value=SYNTHETIC_SESSION
        ),
        patch.object(saml_views, "cache"),
        patch.object(saml_views, "read_oauth_state", return_value=None),
        patch.object(saml_views, "get_mixpanel_properties", return_value={}),
        patch.object(saml_views, "track_mixpanel_event"),
    ):
        yield auth_token_model


def _ok(payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    return response


@contextmanager
def _google(account, new_org):
    with (
        patch.object(
            saml_views.requests,
            "post",
            return_value=_ok(
                {
                    "id_token": SYNTHETIC_ID_TOKEN,
                    "access_token": SYNTHETIC_PROVIDER_ACCESS,
                }
            ),
        ),
        patch.object(
            saml_views.requests,
            "get",
            side_effect=AssertionError("no userinfo call"),
        ),
        patch.object(
            saml_views,
            "_verified_google_id_token",
            return_value={"email": account.email, "name": "Test Person"},
        ),
        patch.object(
            saml_views,
            "resolve_sso_user",
            return_value=(account, SUCCESS_NEXT_URL, new_org),
        ),
    ):
        yield saml_views.Auth0CallbackView, "/saml2_auth/auth/callback/"


@contextmanager
def _github(account, new_org):
    with (
        patch.object(
            saml_views.requests,
            "post",
            return_value=_ok({"access_token": SYNTHETIC_PROVIDER_ACCESS}),
        ),
        patch.object(
            saml_views.requests,
            "get",
            return_value=_ok({"email": account.email, "name": "Test Person"}),
        ),
        patch.object(
            saml_views,
            "resolve_sso_user",
            return_value=(account, SUCCESS_NEXT_URL, new_org),
        ),
    ):
        yield saml_views.GithubCallbackView, "/saml2_auth/github/callback/"


@contextmanager
def _microsoft(account, new_org):
    user_model = MagicMock()
    user_model.DoesNotExist = User.DoesNotExist
    user_model.objects.get.return_value = account
    with (
        patch.object(
            saml_views.requests,
            "post",
            return_value=_ok({"access_token": SYNTHETIC_PROVIDER_ACCESS}),
        ),
        patch.object(
            saml_views.requests,
            "get",
            return_value=_ok(
                {"userPrincipalName": account.email, "displayName": "Test Person"}
            ),
        ),
        patch.object(saml_views, "User", user_model),
    ):
        yield saml_views.MicrosoftCallbackView, "/saml2_auth/microsoft/callback/"


PROVIDERS = {"google": _google, "github": _github, "microsoft": _microsoft}


def _sso_callback(provider, *, new_org="false", login_next_url=None):
    account = _account()
    with (
        _session_minting() as auth_token_model,
        PROVIDERS[provider](account, new_org) as (view, path),
    ):
        request = RequestFactory().get(path, {"code": SYNTHETIC_OAUTH_CODE})
        request.session = {"login_next_url": login_next_url} if login_next_url else {}
        response = view.as_view()(request)
    return response, auth_token_model, account


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_sso_callback_redirects_with_a_code_and_no_token(provider):
    response, auth_token_model, account = _sso_callback(provider)
    location = response["Location"]

    assert response.status_code == 302
    _assert_no_token_in_url(location, SYNTHETIC_SESSION)
    query = _query(location)
    assert query["kind"] == ["sso"]
    assert query["is_new_user"] == ["false"]
    assert redeem(_code_from(location), "sso") == {
        "access": SYNTHETIC_SESSION,
        "refresh": None,
    }
    auth_token_model.objects.create.assert_called_once()
    assert auth_token_model.objects.create.call_args.kwargs["user"] is account


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_sso_callback_keeps_its_destination_and_next(provider):
    response, _, _ = _sso_callback(provider, login_next_url="/dashboard/runs")
    location = response["Location"]
    expected = (
        saml_views.default_next_url if provider == "microsoft" else SUCCESS_NEXT_URL
    )

    assert location.startswith(f"{expected}?")
    assert _query(location)["next"] == ["/dashboard/runs"]
    _assert_no_token_in_url(location, SYNTHETIC_SESSION)


@pytest.mark.parametrize("provider", ["google", "github"])
def test_new_sso_user_flag_still_travels(provider):
    response, _, _ = _sso_callback(provider, new_org="true")

    assert _query(response["Location"])["is_new_user"] == ["true"]


def test_sso_code_cannot_be_redeemed_as_another_kind():
    response, _, _ = _sso_callback("github")
    code = _code_from(response["Location"])

    with pytest.raises(HandoffRefused):
        redeem(code, "sos")


# --- CR-07c: SAML completion -------------------------------------------------


def _not_found(name):
    return type(name, (Exception,), {})


@contextmanager
def _saml_completion(next_path):
    attempt_id = uuid.uuid4()
    meta = SYNTHETIC_IDP_METADATA
    attempt = MagicMock(
        id=attempt_id,
        idp_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        idp_generation=1,
        idp_meta_sha256=hashlib.sha256(meta.encode()).hexdigest(),
        request_id=SYNTHETIC_SAML_REQUEST_ID,
        relay_key=SYNTHETIC_SAML_RELAY_KEY,
        next_path=next_path,
    )
    candidate_model = MagicMock()
    candidate_model.DoesNotExist = _not_found("CandidateDoesNotExist")
    candidate_model.objects.only.return_value.get.return_value = MagicMock(
        attempt_id=attempt_id
    )
    attempt_model = MagicMock()
    attempt_model.objects.only.return_value.get.return_value = MagicMock(id=attempt_id)
    metadata_model = MagicMock()
    metadata_model.DoesNotExist = _not_found("MetadataDoesNotExist")
    metadata_model.objects.get.return_value = MagicMock(
        meta=meta, organization_id=attempt.organization_id
    )
    parsed = MagicMock()
    parsed.get_identity.return_value = {}
    parsed.get_subject.return_value = MagicMock(text="person@example.test")
    sp_client = MagicMock()
    sp_client.parse_authn_request_response.return_value = parsed
    issued = MagicMock(return_value=SYNTHETIC_SAML_SESSION)
    with (
        patch.object(saml_views, "SamlResponseCandidate", candidate_model),
        patch.object(saml_views, "SamlLoginAttempt", attempt_model),
        patch.object(
            saml_views,
            "claim_attempt",
            return_value=(attempt, SYNTHETIC_SAML_RESPONSE),
        ),
        patch.object(saml_views, "SAMLMetadataModel", metadata_model),
        patch.object(saml_views, "build_sp_client", return_value=sp_client),
        patch.object(
            saml_views,
            "verify_assertion_bindings",
            return_value=MagicMock(not_on_or_after=None),
        ),
        patch.object(saml_views, "resolve_assertion_user", return_value=_account()),
        patch.object(saml_views, "require_active_membership"),
        patch.object(saml_views, "issue_token", issued),
        patch.object(saml_views, "record_failure") as record_failure,
    ):
        request = RequestFactory().get(
            "/saml2_auth/complete/", {"c": SYNTHETIC_SAML_CANDIDATE}
        )
        request.COOKIES[f"fai_saml_b_{attempt_id.hex[:16]}"] = SYNTHETIC_SAML_BINDER
        yield saml_views.CompleteView.as_view()(request), issued, record_failure


def test_saml_completion_redirects_with_a_code_and_no_token(settings):
    settings.SAML_LOGIN_ENABLED = True

    with _saml_completion("/dashboard/runs") as (response, issued, record_failure):
        location = response["Location"]

    assert response.status_code == 302
    assert location.startswith(f"{saml_views.default_next_url}?")
    _assert_no_token_in_url(location, SYNTHETIC_SAML_SESSION)
    query = _query(location)
    assert query["kind"] == ["saml"]
    assert query["next"] == ["/dashboard/runs"]
    assert redeem(_code_from(location), "saml") == {
        "access": SYNTHETIC_SAML_SESSION,
        "refresh": None,
    }
    issued.assert_called_once()
    record_failure.assert_not_called()


def test_saml_code_cannot_be_redeemed_as_sso(settings):
    settings.SAML_LOGIN_ENABLED = True

    with _saml_completion("/dashboard/runs") as (response, _, _):
        code = _code_from(response["Location"])

    with pytest.raises(HandoffRefused):
        redeem(code, "sso")


def test_saml_completion_fails_closed_when_the_code_cannot_be_issued(settings):
    settings.SAML_LOGIN_ENABLED = True

    with (
        patch.object(saml_views, "issue_handoff_code", side_effect=RuntimeError),
        _saml_completion("/dashboard/runs") as (response, _, record_failure),
    ):
        location = response["Location"]

    _assert_no_token_in_url(location, SYNTHETIC_SAML_SESSION)
    assert "code" not in _query(location)
    record_failure.assert_called_once()


# --- CR-07d: SOS browser and copy-link handoffs --------------------------------


@pytest.fixture
def sos_admin():
    return admin.site._registry[SOSLoginProxy]


@pytest.fixture
def sos_operator(db, organization):
    return User.objects.create(
        email=f"operator-{uuid.uuid4().hex[:8]}@futureagi.com",
        name="Operator",
        organization=organization,
        is_active=True,
        is_staff=True,
        is_superuser=False,
    )


def _admin_request(url, operator, user_id):
    request = RequestFactory().post(url, {"user_id": user_id})
    request.user = operator
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _sos_handoff_url(flow, sos_admin, sos_operator, target):
    if flow == "browser":
        response = sos_admin.start_sos_session_view(
            _admin_request(
                "/admin/accounts/sosloginproxy/login/", sos_operator, str(target.id)
            )
        )
        assert response.status_code == 302
        return response["Location"]
    response = sos_admin.copy_sos_link_view(
        _admin_request(
            "/admin/accounts/sosloginproxy/copy-link/", sos_operator, str(target.id)
        )
    )
    assert response.status_code == 200
    return json.loads(response.content)["url"]


@pytest.mark.django_db
@pytest.mark.parametrize("flow", ["browser", "copy_link"])
def test_sos_handoff_url_carries_only_the_code(flow, sos_admin, sos_operator, user):
    url = _sos_handoff_url(flow, sos_admin, sos_operator, user)

    assert url.startswith(f"{APP_ORIGIN}/sos?")
    assert set(_query(url)) == {"code"}
    tokens = redeem(_code_from(url), "sos")
    assert set(tokens) == {"access", "refresh"}
    assert tokens["access"] and tokens["refresh"]
    _assert_no_token_in_url(url, tokens["access"], tokens["refresh"])
    assert AuthToken.objects.filter(user=user, is_active=True).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("flow", ["browser", "copy_link"])
def test_sos_code_redeems_once_through_the_endpoint(
    flow, sos_admin, sos_operator, user
):
    code = _code_from(_sos_handoff_url(flow, sos_admin, sos_operator, user))
    reference = _reference_refusal()

    first = _post_redeem(code, "sos")
    second = _post_redeem(code, "sos")

    assert first.status_code == 200
    assert set(first.json()) == {"access", "refresh"}
    _assert_never_cached(first)
    assert _refusal(second) == reference


@pytest.mark.django_db
def test_sos_code_cannot_be_redeemed_as_a_browser_sign_in(
    sos_admin, sos_operator, user
):
    code = _code_from(_sos_handoff_url("browser", sos_admin, sos_operator, user))

    with pytest.raises(HandoffRefused):
        redeem(code, "sso")


# --- CR-07f and original marketplace issuer (r3 tests first) ------------------


def _ws_payload():
    return {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "session_id": "00000000-0000-0000-0000-000000000002",
        "path": "/ws/falcon-ai/",
        "query": {"workspace_id": "FAKE-workspace"},
    }


def test_ws_ticket_carries_identity_not_bearers_and_redeems_once():
    payload = _ws_payload()
    code = issue("ws", payload)
    assert redeem(code, "ws") == payload
    with pytest.raises(HandoffRefused):
        redeem(code, "ws")
    assert not {"access", "refresh", "token"}.intersection(payload)


@pytest.mark.parametrize("kind", ["sso", "aws_marketplace"])
def test_ws_wrong_kind_spends_ticket(kind):
    code = issue("ws", _ws_payload())
    with pytest.raises(HandoffRefused):
        redeem(code, kind)
    with pytest.raises(HandoffRefused):
        redeem(code, "ws")


def test_expired_ws_ticket_refused():
    code = issue("ws", _ws_payload())
    with _expired_cache_clock(61), pytest.raises(HandoffRefused):
        redeem(code, "ws")


def test_ws_ticket_racing_redeems_select_one_winner():
    backend = caches["default"]
    read = Event()
    resume = Event()
    original_get = backend.get

    def pause_first(key, *args, **kwargs):
        record = original_get(key, *args, **kwargs)
        if not read.is_set():
            read.set()
            assert resume.wait(5)
        return record

    with patch.object(handoff_exchange, "cache", backend):
        code = issue("ws", _ws_payload())
        with (
            patch.object(backend, "get", side_effect=pause_first),
            ThreadPoolExecutor(2) as pool,
        ):
            first = pool.submit(redeem, code, "ws")
            try:
                assert read.wait(5)
                assert pool.submit(redeem, code, "ws").result(5) == _ws_payload()
                resume.set()
                with pytest.raises(HandoffRefused):
                    first.result(5)
            finally:
                resume.set()


@pytest.mark.django_db
def test_public_redeem_does_not_expose_ws_or_onboarding_payload():
    for kind, payload in [
        ("ws", _ws_payload()),
        ("aws_marketplace_onboarding", {"onboarding_token": "FAKE-onboarding"}),
    ]:
        code = issue(kind, payload)
        assert _refusal(_post_redeem(code, kind)) == _reference_refusal()


@pytest.mark.django_db
def test_ticket_mint_requires_header_and_returns_no_store_identity_ticket(user):
    from accounts.views import handoff_exchange as views

    client = APIClient()
    path = reverse("handoff-ws-ticket")
    body = {"path": "/ws/falcon-ai/", "query": {"workspace_id": "FAKE-workspace"}}
    no_header = client.post(path, body, format="json", HTTP_ORIGIN=APP_ORIGIN)
    assert no_header.status_code == 401
    _assert_never_cached(no_header)
    with (
        patch.object(
            views, "decode_token", return_value=(user, SYNTHETIC_ACCESS, None)
        ),
        patch.object(
            views,
            "decrypt_message",
            return_value={
                "user_id": str(user.pk),
                "id": _ws_payload()["session_id"],
            },
        ),
    ):
        response = client.post(
            path,
            body,
            format="json",
            HTTP_ORIGIN=APP_ORIGIN,
            HTTP_AUTHORIZATION="Bearer FAKE-access",
        )
    assert response.status_code == 200
    _assert_never_cached(response)
    assert set(response.json()) == {"ticket"}
    payload = redeem(response.json()["ticket"], "ws")
    assert payload == {**_ws_payload(), "user_id": str(user.pk)}
    assert SYNTHETIC_ACCESS not in json.dumps(payload)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body,origin",
    [
        ({"path": "/ws/connect/", "query": {"token": "FAKE-bearer"}}, APP_ORIGIN),
        ({"path": "/ws/not-allowed/", "query": {}}, APP_ORIGIN),
        ({"path": "/ws/connect/", "query": {}}, "https://evil.example"),
    ],
)
def test_ticket_mint_refuses_untrusted_path_query_or_origin(user, body, origin):
    from accounts.views import handoff_exchange as views

    with (
        patch.object(
            views, "decode_token", return_value=(user, SYNTHETIC_ACCESS, None)
        ),
        patch.object(
            views,
            "decrypt_message",
            return_value={
                "user_id": str(user.pk),
                "id": _ws_payload()["session_id"],
            },
        ),
    ):
        response = APIClient().post(
            reverse("handoff-ws-ticket"),
            body,
            format="json",
            HTTP_ORIGIN=origin,
            HTTP_AUTHORIZATION="Bearer FAKE-access",
        )
    assert response.status_code == 400
    _assert_never_cached(response)


@pytest.mark.parametrize(
    "query",
    [
        b"token=FAKE-bearer",
        b"access_token=FAKE-bearer",
        b"sso_token=FAKE-bearer",
        b"token=",
        b"%74oken=FAKE-bearer",
        b"ticket=FAKE-ticket&refresh_token=",
        b"ticket=FAKE-ticket&ticket=FAKE-ticket-2",
        b"",
    ],
)
def test_ws_bearer_and_ambiguous_queries_close_before_redeem(query):
    import asyncio
    from unittest.mock import AsyncMock

    from tfc.middleware.jwt_auth import JWTAuthMiddleware

    inner = AsyncMock()
    send = AsyncMock()
    middleware = JWTAuthMiddleware(inner)
    middleware.get_user_from_ticket = AsyncMock()
    asyncio.run(
        middleware({"query_string": query, "path": "/ws/connect/"}, AsyncMock(), send)
    )
    inner.assert_not_called()
    middleware.get_user_from_ticket.assert_not_called()
    assert send.call_args.args[0]["type"] == "websocket.close"
    assert send.call_args.args[0]["code"] == 4003


@pytest.mark.django_db(transaction=True)
def test_ws_connect_checks_bound_session_liveness_and_spends_ticket(user):
    import asyncio

    from django.utils import timezone

    from tfc.middleware.jwt_auth import JWTAuthMiddleware

    row = AuthToken.objects.create(
        user=user, auth_type="access", last_used_at=timezone.now()
    )
    payload = {**_ws_payload(), "user_id": str(user.pk), "session_id": str(row.pk)}
    middleware = JWTAuthMiddleware(None)
    code = issue("ws", payload)
    result = asyncio.run(middleware.get_user_from_ticket(code, payload["path"]))
    assert result[0].pk == user.pk
    assert result[2] == str(row.pk)
    assert result[3] == payload["query"]
    assert asyncio.run(middleware.get_user_from_ticket(code, payload["path"])) is None
    row.is_active = False
    row.save(update_fields=["is_active"])
    code = issue("ws", payload)
    assert asyncio.run(middleware.get_user_from_ticket(code, payload["path"])) is None


@pytest.mark.django_db(transaction=True)
def test_ws_ticket_for_other_route_is_refused_and_spent():
    import asyncio

    from tfc.middleware.jwt_auth import JWTAuthMiddleware

    code = issue("ws", _ws_payload())
    middleware = JWTAuthMiddleware(None)
    assert asyncio.run(middleware.get_user_from_ticket(code, "/ws/connect/")) is None
    with pytest.raises(HandoffRefused):
        redeem(code, "ws")


def test_marketplace_launch_redirect_carries_code_only():
    from accounts.views import aws_marketplace as views

    with patch.object(
        views,
        "process_aws_launch_software",
        return_value=(
            {
                "access": SYNTHETIC_ACCESS,
                "refresh": SYNTHETIC_REFRESH,
                "new_org": True,
            },
            None,
        ),
    ):
        response = views.aws_marketplace_launch_software(
            RequestFactory().post(
                "/accounts/aws-marketplace/launch-software/",
                "x-amzn-marketplace-token=FAKE-registration",
                content_type="application/x-www-form-urlencoded",
            )
        )
    assert response.status_code == 302
    _assert_no_token_in_url(response["Location"], SYNTHETIC_ACCESS, SYNTHETIC_REFRESH)
    assert _query(response["Location"])["kind"] == ["aws_marketplace"]
    assert _query(response["Location"])["new_org"] == ["True"]
    assert redeem(_code_from(response["Location"]), "aws_marketplace") == {
        "access": SYNTHETIC_ACCESS,
        "refresh": SYNTHETIC_REFRESH,
    }
    _assert_never_cached(response)


@pytest.mark.parametrize("entry", ["verify", "launch"])
def test_marketplace_onboarding_redirect_exchanges_original_capability(entry):
    from accounts.views import aws_marketplace as views

    original = "FAKE-original-onboarding"
    with (
        patch.object(views, "aws_marketplace_service") as service,
        patch.object(views, "onboard_aws_customer", return_value=(original, False)),
        patch.object(
            views,
            "process_aws_launch_software",
            return_value=(
                {"action": "signup", "onboarding_token": original},
                None,
            ),
        ),
    ):
        service.resolve_customer_token.return_value = (
            "FAKE-customer",
            "FAKE-account",
            "FAKE-product",
        )
        view = (
            views.aws_marketplace_verify_token
            if entry == "verify"
            else views.aws_marketplace_launch_software
        )
        response = view(
            RequestFactory().post(
                "/",
                "x-amzn-marketplace-token=FAKE-registration",
                content_type="application/x-www-form-urlencoded",
            )
        )
    assert response.status_code == 302
    assert original not in response["Location"]
    code = _query(response["Location"])["onboarding_token"][0]
    assert redeem(code, "aws_marketplace_onboarding") == {"onboarding_token": original}
    with pytest.raises(HandoffRefused):
        redeem(code, "aws_marketplace_onboarding")


def test_marketplace_returned_error_is_categorical():
    from accounts.views import aws_marketplace as views

    with patch.object(
        views, "process_aws_launch_software", return_value=(None, "FAKE-private-detail")
    ):
        response = views.aws_marketplace_launch_software(
            RequestFactory().post(
                "/",
                "x-amzn-marketplace-token=FAKE-registration",
                content_type="application/x-www-form-urlencoded",
            )
        )
    assert response.status_code == 400
    assert "FAKE-private-detail" not in str(response.data)


def test_marketplace_signup_consumes_onboarding_code_before_processing():
    from accounts.views import aws_marketplace as views

    code = issue(
        "aws_marketplace_onboarding", {"onboarding_token": "FAKE-original-onboarding"}
    )
    body = json.dumps(
        {
            "onboarding_token": code,
            "email": "person@example.test",
            "full_name": "Test Person",
        }
    )
    with patch.object(
        views, "process_aws_onboarding", return_value="person@example.test"
    ) as process:
        first = views.aws_marketplace_signup(
            RequestFactory().post("/", body, content_type="application/json")
        )
        second = views.aws_marketplace_signup(
            RequestFactory().post("/", body, content_type="application/json")
        )
    assert first.status_code == 200
    assert second.status_code == 400
    assert "Invalid or expired onboarding token" in str(second.data)
    process.assert_called_once_with(
        "FAKE-original-onboarding", "person@example.test", "Test Person"
    )


@pytest.mark.parametrize("state", ["expired", "replayed", "failed_first_submission"])
def test_marketplace_signup_spent_code_has_fixed_terminal_category(state):
    from accounts.views import aws_marketplace as views

    # synthetic test value
    code = issue(
        "aws_marketplace_onboarding", {"onboarding_token": "FAKE-original-onboarding"}
    )
    body = json.dumps(
        {
            "onboarding_token": code,
            "email": "person@example.test",
            "full_name": "Test Person",
        }
    )
    if state == "replayed":
        redeem(code, "aws_marketplace_onboarding")
    with patch.object(
        views,
        "process_aws_onboarding",
        side_effect=ValueError("FAKE-private-business-detail"),
    ) as process:
        if state == "failed_first_submission":
            first = views.aws_marketplace_signup(
                RequestFactory().post("/", body, content_type="application/json")
            )
            assert first.status_code == 400
            assert "Signup failed" in str(first.data)
            assert "FAKE-private-business-detail" not in str(first.data)
        clock = _expired_cache_clock(61) if state == "expired" else nullcontext()
        with clock:
            response = views.aws_marketplace_signup(
                RequestFactory().post("/", body, content_type="application/json")
            )
    assert response.status_code == 400
    assert "Invalid or expired onboarding token" in str(response.data)
    assert "FAKE-private-business-detail" not in str(response.data)
    assert code not in str(response.data)
    assert process.call_count == (1 if state == "failed_first_submission" else 0)


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("aws_marketplace", {"access": SYNTHETIC_ACCESS, "refresh": SYNTHETIC_REFRESH}),
        (
            "aws_marketplace_onboarding",
            {"onboarding_token": "FAKE-original-onboarding"},
        ),
    ],
)
def test_marketplace_code_expires_and_wrong_kind_is_refused(kind, payload):
    code = issue(kind, payload)
    with _expired_cache_clock(61), pytest.raises(HandoffRefused):
        redeem(code, kind)
    code = issue(kind, payload)
    with pytest.raises(HandoffRefused):
        redeem(code, "ws")
    with pytest.raises(HandoffRefused):
        redeem(code, kind)


def test_ws_valid_ticket_restores_only_authenticated_query_and_keeps_guard():
    import asyncio
    from unittest.mock import AsyncMock

    from tfc.middleware import jwt_auth

    user = _account()
    inner = AsyncMock()
    send = AsyncMock()
    middleware = jwt_auth.JWTAuthMiddleware(inner)
    payload = _ws_payload()
    middleware.get_user_from_ticket = AsyncMock(
        return_value=(user, None, payload["session_id"], payload["query"])
    )
    with patch.object(jwt_auth, "SessionGuard") as guard:
        guard.return_value.run = AsyncMock()
        asyncio.run(
            middleware(
                {"query_string": b"ticket=FAKE-ticket", "path": payload["path"]},
                AsyncMock(),
                send,
            )
        )
        guard.assert_called_once_with(payload["session_id"], user.id)
        forwarded = guard.return_value.run.call_args.args[1]
    assert forwarded["query_string"] == b"workspace_id=FAKE-workspace"
    assert forwarded["user"] is user
    assert "ticket" not in forwarded["query_string"].decode()
    send.assert_not_called()


@pytest.mark.django_db
def test_ticket_mint_cache_failure_is_categorical_and_no_store(user):
    from accounts.views import handoff_exchange as views

    with (
        patch.object(views, "decode_token", return_value=(user, "FAKE-access", None)),
        patch.object(
            views,
            "decrypt_message",
            return_value={
                "user_id": str(user.pk),
                "id": _ws_payload()["session_id"],
            },
        ),
        patch.object(views, "issue", side_effect=RuntimeError("FAKE-private-detail")),
    ):
        response = APIClient().post(
            reverse("handoff-ws-ticket"),
            {"path": "/ws/connect/"},
            format="json",
            HTTP_ORIGIN=APP_ORIGIN,
            HTTP_AUTHORIZATION="Bearer FAKE-access",
        )
    assert response.status_code == 400
    assert "FAKE-private-detail" not in str(response.data)
    _assert_never_cached(response)
