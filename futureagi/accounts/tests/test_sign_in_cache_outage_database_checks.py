"""While the sign-in cache cannot be reached, the database decides each session."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import AuthenticationError as RedisAuthenticationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import BusyLoadingError, ResponseError, TimeoutError

from accounts.authentication import decode_token, generate_encrypted_message
from accounts.models import User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.services.auth_cache import cache_access_token


def wrapped(cause):
    error = ConnectionInterrupted(connection=None)
    error.__cause__ = cause
    return error


class _Captured:
    def set(self, key, value, timeout=None):
        self.value = value


def warm_entry(row, token, user):
    """What the sign-in cache holds for this session after a signed-in request."""
    captured = _Captured()
    cache_access_token(
        row.pk, token, user, timeout=60, non_saml=True, cache_backend=captured
    )
    return captured.value


@pytest.fixture
def access(user):
    row = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now() - timedelta(minutes=1),
    )
    token = generate_encrypted_message({"user_id": str(user.pk), "id": str(row.pk)})
    return row, token


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["session_inactive", "user_inactive"])
def test_outage_refuses_a_session_the_database_has_ended(
    api_client, user, access, change
):
    row, token = access
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("accounts.authentication.cache") as cache:
        cache.get.side_effect = wrapped(TimeoutError("cache timed out"))
        served = api_client.get("/accounts/user-info/")
        row.refresh_from_db()
        last_used = row.last_used_at
        if change == "session_inactive":
            AuthToken.objects.filter(pk=row.pk).update(is_active=False)
        else:
            User.objects.filter(pk=user.pk).update(is_active=False)
        refused = api_client.get("/accounts/user-info/")
    assert served.status_code == 200
    assert served.json()["email"] == user.email
    assert refused.status_code in (401, 403)
    assert cache.get.call_count == 2
    cache.set.assert_not_called()
    cache.delete.assert_not_called()
    row.refresh_from_db()
    assert row.last_used_at == last_used


@pytest.mark.django_db
def test_a_revoked_session_stays_refused_when_its_cached_entry_cannot_be_removed(
    api_client, user, access
):
    row, token = access
    entry = warm_entry(row, token, user)
    AuthToken.objects.filter(pk=row.pk).update(is_active=False)
    row.refresh_from_db()
    last_used = row.last_used_at
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("accounts.authentication.cache") as cache:
        cache.get.return_value = entry
        cache.delete.side_effect = wrapped(RedisConnectionError("cache unreachable"))
        first = api_client.get("/accounts/user-info/")
        second = api_client.get("/accounts/user-info/")
    assert first.status_code in (401, 403)
    assert second.status_code in (401, 403)
    assert cache.get.call_count == 2
    assert cache.delete.call_count == 2
    cache.set.assert_not_called()
    row.refresh_from_db()
    assert row.last_used_at == last_used


@pytest.mark.django_db
def test_cache_failure_without_a_transport_cause_still_answers_503(api_client, access):
    _, token = access
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("accounts.authentication.cache") as cache:
        cache.get.side_effect = ConnectionInterrupted(connection=None)
        response = api_client.get("/accounts/user-info/")
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    cache.set.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [
        wrapped(None),
        wrapped(ResponseError("cache refused")),
        RedisAuthenticationError("cache refused"),
        BusyLoadingError("cache loading"),
    ],
)
def test_decoding_raises_unknown_wrappers_and_connection_error_subtypes(access, error):
    _, token = access
    with patch("accounts.authentication.cache") as cache:
        cache.get.side_effect = error
        with pytest.raises(type(error)):
            decode_token(token)
        cache.set.assert_not_called()
        cache.delete.assert_not_called()


@pytest.mark.django_db
def test_cyclic_wrappers_end_when_a_token_is_decoded(access):
    _, token = access
    error = wrapped(None)
    error.__cause__ = error
    with patch("accounts.authentication.cache") as cache:
        cache.get.side_effect = error
        with pytest.raises(ConnectionInterrupted):
            decode_token(token)
        cache.set.assert_not_called()
