"""Bearer-cache format regressions using only newly generated test credentials.

PYTEST_DONT_REWRITE: never include a bearer or cache payload in assertion output.
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.authentication import (
    APIKeyAuthentication,
    decode_token,
    generate_encrypted_message,
)
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)

pytestmark = pytest.mark.django_db


class CacheProbe(APIView):
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {"cached_marker": getattr(request.user, "digest_cache_marker", False)}
        )


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "auth-cache-no-raw-bearer-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def session(user):
    row = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"id": str(row.pk), "user_id": str(user.pk)})
    return SimpleNamespace(
        user=user, row=row, bearer=bearer, key=f"access_token_{row.pk}"
    )


def request(session):
    return CacheProbe.as_view()(
        APIRequestFactory().get(
            "/accounts/cache-digest-probe/",
            HTTP_AUTHORIZATION=f"Bearer {session.bearer}",
        )
    )


def assert_digest_only(session):
    payload = cache.get(session.key)
    assert payload is not None, "authenticated request must populate cache"
    assert not any(value == session.bearer for value in payload.values()), (
        "cache must not contain a raw bearer in any field"
    )
    assert "token" not in payload, "legacy bearer field must not be retained"
    expected = hashlib.sha256(session.bearer.encode("utf-8")).hexdigest()
    assert payload.get("token_digest") == expected, "cache must contain SHA-256 digest"
    assert payload.get("non_saml_v1") is True
    assert payload["user"].pk == session.user.pk


@pytest.mark.parametrize("warm", [False, True])
def test_authenticated_request_caches_only_digest(session, warm):
    if warm:
        assert request(session).status_code == 200
        payload = cache.get(session.key)
        payload["user"].digest_cache_marker = True
        cache.set(session.key, payload)
    with patch("accounts.authentication.cache.set", wraps=cache.set) as write:
        response = request(session)
    assert response.status_code == 200
    assert response.data["cached_marker"] is warm
    assert_digest_only(session)
    assert write.call_count == 1
    assert write.call_args.kwargs["timeout"] == (
        AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60
    )
    session.row.refresh_from_db()
    assert session.row.last_used_at is not None


def test_legacy_raw_bearer_entry_is_reloaded_and_rewritten(session):
    session.user.digest_cache_marker = True
    cache.set(
        session.key,
        {"token": session.bearer, "user": session.user, "non_saml_v1": True},
    )
    response = request(session)
    assert response.status_code == 200
    assert response.data["cached_marker"] is False, "legacy cache must not authenticate"
    assert_digest_only(session)


def mismatched_entry(session):
    other = generate_encrypted_message(
        {"id": str(session.row.pk), "user_id": str(session.user.pk)}
    )
    assert other != session.bearer, "synthetic encryptions must be distinct"
    session.user.digest_cache_marker = True
    return {
        "token_digest": hashlib.sha256(other.encode("utf-8")).hexdigest(),
        "user": session.user,
        "non_saml_v1": True,
    }


def test_other_bearer_digest_with_same_id_is_not_a_cache_hit(session):
    cache.set(session.key, mismatched_entry(session))
    response = request(session)
    assert response.status_code == 200
    assert response.data["cached_marker"] is False, "mismatched digest must miss cache"
    assert_digest_only(session)


def test_other_bearer_digest_cannot_replace_missing_authoritative_row(session):
    payload = mismatched_entry(session)
    session.row.delete()
    cache.set(session.key, payload)
    assert request(session).status_code in (401, 403)
    assert cache.get(session.key) is None


def test_digest_cache_hit_still_evicts_on_user_revocation(session):
    assert request(session).status_code == 200
    assert_digest_only(session)
    session.user.is_active = False
    session.user.save(update_fields=["is_active"])
    assert cache.get(session.key) is None
    assert request(session).status_code in (401, 403)


def test_no_touch_never_reads_or_rewrites_legacy_cache(session):
    cache.set(
        session.key,
        {"token": session.bearer, "user": session.user, "non_saml_v1": True},
    )
    before = session.row.last_used_at
    with (
        patch("accounts.authentication.cache.get") as read,
        patch("accounts.authentication.cache.set") as write,
        patch("accounts.authentication.cache.delete") as delete,
    ):
        decoded, _, credential = decode_token(session.bearer, touch=False)
    assert decoded.pk == session.user.pk
    assert credential is None
    assert read.call_count == write.call_count == delete.call_count == 0
    session.row.refresh_from_db()
    assert session.row.last_used_at == before
