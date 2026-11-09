"""Configured invitation middleware must not mutate authentication state.

Run only in the guarded integrated runtime. The middleware observer calls the
real implementation; neither authentication nor the cache writer is stubbed.
"""

from types import ModuleType
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import path
from rest_framework.test import APIClient

from accounts import authentication
from accounts.models import User
from accounts.models.auth_token import AuthToken
from tfc.middleware import auth_monitoring

from . import test_invitation_bearer as bearer_tests
from . import test_invitation_expiry as expiry

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
isolated_auth_cache = expiry.isolated_auth_cache
pending_invitation = expiry.pending_invitation
COUNTER = "failed_auth_127.0.0.1"


@pytest.fixture
def middleware_calls(monkeypatch):
    calls = []
    original = auth_monitoring.AuthMonitoringMiddleware.__call__

    def observe(self, request):
        calls.append((request.path, request.method, request.user.is_authenticated))
        return original(self, request)

    monkeypatch.setattr(auth_monitoring.AuthMonitoringMiddleware, "__call__", observe)
    return calls


@pytest.mark.parametrize("signed_in", [False, True], ids=["signed-out", "bearer"])
@pytest.mark.parametrize("counter_state", ["absent", "populated"])
@pytest.mark.parametrize(
    "state,method,expected_status",
    [("deadline", "GET", 400), ("deadline", "POST", 400), ("valid", "GET", 200)],
)
def test_configured_middleware_invitation_is_read_only(
    pending_invitation,
    middleware_calls,
    signed_in,
    counter_state,
    state,
    method,
    expected_status,
):
    user, invite, _ = pending_invitation
    bearer = bearer_tests._bearer(user, "warm")[0] if signed_in else None
    if state == "deadline":
        expiry._backdate(invite)
    if counter_state == "populated":
        cache.set(COUNTER, 7, timeout=60)
    else:
        assert cache.get(COUNTER) is None
    url = expiry._url(user)
    before = expiry._snapshot()
    response = expiry._request(method, url, bearer=bearer)
    # Django still sees an anonymous session even with a real DRF bearer.
    assert middleware_calls == [(url, method, False)]
    expiry._assert_unchanged(before)
    assert response.status_code == expected_status
    assert "access" not in response.data and "refresh" not in response.data
    if state == "valid":
        assert response.data["valid"] is True


@pytest.mark.parametrize("denial", ["invalid-bearer", "different-user"])
def test_invitation_auth_failures_still_logged_without_cache_writes(
    pending_invitation,
    middleware_calls,
    denial,
):
    user, _, _ = pending_invitation
    if denial == "different-user":
        user = User.objects.create_user(
            email="monitoring-other@example.test",
            is_active=True,
        )
    bearer, row = bearer_tests._bearer(user, "cold")
    if denial == "invalid-bearer":
        AuthToken.objects.filter(pk=row.pk).update(is_active=False)
    url = expiry._url(pending_invitation[0])
    cache.set(COUNTER, 7, timeout=60)
    before = expiry._snapshot()
    with patch.object(auth_monitoring.logger, "warning") as warning:
        response = expiry._request("GET", url, bearer=bearer)
    assert middleware_calls == [(url, "GET", False)]
    expiry._assert_unchanged(before)
    assert response.status_code == (401 if denial == "invalid-bearer" else 403)
    assert "access" not in response.data and "refresh" not in response.data
    assert warning.call_count == 1


@pytest.mark.parametrize("status_code", [200, 401, 403])
@pytest.mark.parametrize(
    "url",
    [
        "/health/",
        "/accounts/accept-invitation-not-a-route/",
        "/accounts/accept-invitation/uid/token/extra/",
    ],
)
def test_unrelated_anonymous_requests_still_count_before_dispatch(url, status_code):
    request = RequestFactory().get(url)
    request.user = AnonymousUser()
    cache.set(COUNTER, 7, timeout=60)

    def downstream(request):
        assert cache.get(COUNTER) == 8
        return HttpResponse(status=status_code)

    with patch.object(auth_monitoring.logger, "warning") as warning:
        response = auth_monitoring.AuthMonitoringMiddleware(downstream)(request)
    assert response.status_code == status_code
    assert cache.get(COUNTER) == 8
    assert warning.call_count == (1 if status_code in (401, 403) else 0)


def test_same_path_and_name_for_another_view_is_not_exempt():
    def unrelated(request, **kwargs):
        return HttpResponse()

    request = RequestFactory().get("/accounts/accept-invitation/uid/token/")
    request.user = AnonymousUser()
    # Resolve this request's actual URLconf; neither the path nor name alone
    # authorizes the invitation-only no-write policy.
    request.urlconf = ModuleType("unrelated_invitation_named_route")
    request.urlconf.urlpatterns = [
        path(
            "accounts/accept-invitation/<uidb64>/<token>/",
            unrelated,
            name="accept_invitation_email",
        )
    ]
    response = auth_monitoring.AuthMonitoringMiddleware(unrelated)(request)
    assert response.status_code == 200
    assert cache.get(COUNTER) == 1


@pytest.mark.parametrize("method", ["PUT", "OPTIONS"])
def test_non_invitation_methods_still_count(pending_invitation, method):
    url = expiry._url(pending_invitation[0])
    request = RequestFactory().generic(method, url)
    request.user = AnonymousUser()
    response = auth_monitoring.AuthMonitoringMiddleware(lambda request: HttpResponse())(
        request
    )
    assert response.status_code == 200
    assert cache.get(COUNTER) == 1


@pytest.mark.parametrize(
    "url,cache_key,expected_code",
    [
        ("/accounts/login/", "blocked_ip_127.0.0.1", "LOGIN_IP_BLOCKED"),
        ("/accounts/token/", "blocked_ip_127.0.0.1", "LOGIN_IP_BLOCKED"),
        ("/accounts/signup/", "blocked_ip_127.0.0.1", "LOGIN_IP_BLOCKED"),
        (
            "/accounts/password-reset-initiate/",
            "rate_limit_127.0.0.1",
            "LOGIN_PASSWORD_RESET_RATE_LIMITED",
        ),
    ],
)
def test_configured_login_and_reset_blocks_are_unchanged(
    middleware_calls,
    monkeypatch,
    url,
    cache_key,
    expected_code,
):
    # Exercise cloud protection rather than the OSS deployment short circuit.
    monkeypatch.setattr(authentication, "is_oss", lambda: False)
    cache.set(cache_key, True, timeout=60)
    response = APIClient().post(url, {}, format="json")
    assert middleware_calls == [(url, "POST", False)]
    assert response.status_code == 403
    assert response.json()["result"]["error_code"] == expected_code
    assert cache.get(COUNTER) == 1
    assert cache.get(cache_key) is True
