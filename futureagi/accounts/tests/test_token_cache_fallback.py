"""Optional access-token cache outages never replace authoritative DB checks."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import AuthenticationError as RedisAuthenticationError
from redis.exceptions import (
    BusyLoadingError,
    ConnectionError,
    ResponseError,
    TimeoutError,
)
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import (
    _cache_transport_category,
    custom_exception_handler,
    decode_token,
    generate_encrypted_message,
)
from accounts.models.auth_token import AuthToken, AuthTokenType


def wrapped(cause):
    error = ConnectionInterrupted(connection=None)
    error.__cause__ = cause
    return error


def transport_errors():
    return [
        ConnectionError("secret-url"),
        TimeoutError("secret-token"),
        wrapped(ConnectionError("secret-url")),
        wrapped(TimeoutError("secret-token")),
    ]


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
@pytest.mark.parametrize("error", transport_errors())
@pytest.mark.parametrize("operation", ["get", "fill", "refresh", "delete"])
def test_optional_cache_failure_preserves_valid_session(user, access, error, operation):
    row, token = access
    before = row.last_used_at
    with patch("accounts.authentication.cache") as cache:
        cache.get.return_value = (
            None
            if operation in ("get", "fill")
            else {"user": user, "token": token, "non_saml_v1": operation == "refresh"}
        )
        getattr(
            cache, "set" if operation in ("fill", "refresh") else operation
        ).side_effect = error
        authenticated, returned, credential = decode_token(token)
    assert authenticated.pk == user.pk
    assert authenticated.organization_id == user.organization_id
    assert returned == token
    assert credential is None
    row.refresh_from_db()
    assert row.last_used_at > before
    cache.get.assert_called_once()
    if operation in ("get", "delete"):
        cache.set.assert_not_called()
    if operation == "get":
        cache.delete.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    ["missing", "inactive", "expired", "wrong_user", "refresh", "inactive_user"],
)
@pytest.mark.parametrize("operation", ["get", "delete"])
def test_outage_cannot_authorize_invalid_database_state(user, access, state, operation):
    row, token = access
    if state == "missing":
        row.delete()
    elif state == "inactive_user":
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif state == "wrong_user":
        token = generate_encrypted_message({"user_id": str(uuid4()), "id": str(row.pk)})
    else:
        changes = {
            "inactive": {"is_active": False},
            "expired": {"last_used_at": timezone.now() - timedelta(days=2)},
            "refresh": {"auth_type": AuthTokenType.REFRESH.value},
        }[state]
        AuthToken.objects.filter(pk=row.pk).update(**changes)
    with patch("accounts.authentication.cache") as cache:
        # A legacy record must be reclassified even if eviction cannot complete.
        cache.get.return_value = {"user": user, "token": token}
        getattr(cache, operation).side_effect = TimeoutError("secret-token")
        with pytest.raises(AuthenticationFailed):
            decode_token(token)
        cache.set.assert_not_called()


@pytest.mark.django_db
def test_revoked_warm_token_is_not_revived_by_failed_eviction(user, access):
    row, token = access
    AuthToken.objects.filter(pk=row.pk).update(is_active=False)
    with patch("accounts.authentication.cache") as cache:
        cache.get.return_value = {"user": user, "token": token, "non_saml_v1": True}
        cache.delete.side_effect = TimeoutError("secret")
        with pytest.raises(AuthenticationFailed):
            decode_token(token)
        cache.set.assert_not_called()


@pytest.mark.django_db
def test_database_outage_during_fallback_still_returns_503(api_client, access):
    _, token = access
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with (
        patch("accounts.authentication.cache") as cache,
        patch(
            "accounts.authentication.AuthToken.objects.filter",
            side_effect=OperationalError("db-secret"),
        ),
    ):
        cache.get.side_effect = TimeoutError("cache-secret")
        response = api_client.get("/accounts/user-info/")
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert response.json()["detail"] == "Database temporarily unavailable."
    assert "secret" not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [
        ConnectionInterrupted(None),
        wrapped(ResponseError("secret")),
        RedisAuthenticationError("secret"),
        BusyLoadingError("secret"),
        ResponseError("secret"),
        RuntimeError("secret"),
    ],
)
def test_unknown_or_non_transport_cache_errors_never_authenticate(access, error):
    _, token = access
    with patch("accounts.authentication.cache") as cache:
        cache.get.side_effect = error
        with pytest.raises((type(error), AuthenticationFailed)):
            decode_token(token)
        cache.set.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        wrapped(None),
        wrapped(ResponseError("secret")),
        RedisAuthenticationError("secret"),
        BusyLoadingError("secret"),
    ],
)
def test_unknown_wrappers_and_connection_error_subclasses_are_not_transport(error):
    assert _cache_transport_category(error) is None


def test_cyclic_wrappers_are_bounded():
    error = wrapped(None)
    error.__cause__ = error
    assert _cache_transport_category(error) is None


@pytest.mark.parametrize("error", transport_errors())
def test_mandatory_cache_errors_keep_sanitized_503(error):
    response = custom_exception_handler(error, {})
    assert response.status_code == 503
    assert response.data["detail"] == "Cache temporarily unavailable."
    assert "secret" not in str(response.data)


def test_cache_503_logs_route_and_bounded_configuration_without_secrets(settings):
    settings.CACHES = {
        "default": {
            "OPTIONS": {
                "SOCKET_TIMEOUT": 1.0,
                "SOCKET_CONNECT_TIMEOUT": 0.5,
                "PASSWORD": "secret-password",
                "CONNECTION_POOL_KWARGS": {"retry_on_timeout": False},
            }
        }
    }
    request = SimpleNamespace(
        method="POST",
        resolver_match=SimpleNamespace(route="tracer/dashboard/metrics/"),
        headers={"Authorization": "Bearer secret-token"},
    )
    with patch("accounts.authentication.logger") as logger:
        try:
            raise TimeoutError("redis://secret-password")
        except TimeoutError as error:
            response = custom_exception_handler(error, {"request": request})
    assert response.status_code == 503
    fields = logger.warning.call_args.kwargs
    assert fields["method"] == "POST"
    assert fields["route_template"] == "tracer/dashboard/metrics/"
    assert fields["socket_timeout"] == 1.0
    assert fields["socket_connect_timeout"] == 0.5
    assert fields["retry_on_timeout"] is False
    assert (
        fields["source_function"]
        == "test_cache_503_logs_route_and_bounded_configuration_without_secrets"
    )
    assert "secret-password" not in str(logger.mock_calls)
    assert "secret-token" not in str(logger.mock_calls)


@pytest.mark.django_db
def test_fallback_logs_only_safe_operation_and_database_outcome(user, access):
    _, token = access
    with (
        patch("accounts.authentication.cache") as cache,
        patch("accounts.authentication.time.monotonic", side_effect=[10, 10.125]),
        patch("accounts.authentication.logger") as logger,
    ):
        cache.get.side_effect = wrapped(TimeoutError("redis://password/secret-token"))
        decode_token(token)
    event = logger.warning.call_args
    assert event.args == ("access_token_cache_unavailable",)
    assert (
        event.kwargs.items()
        >= {
            "operation": "get",
            "elapsed_ms": 125.0,
            "transport_category": "timeout",
            "transport_phase": "unknown",
            "fallback": "validate_database",
            "component": "access_token_cache",
        }.items()
    )
    logger.info.assert_called_once_with(
        "access_token_cache_fallback_result",
        component="access_token_cache",
        outcome="authenticated",
    )
    serialized = str(logger.mock_calls)
    for secret in (
        token,
        "password",
        "secret-token",
        "access_token_" + str(access[0].pk),
    ):
        assert secret not in serialized
