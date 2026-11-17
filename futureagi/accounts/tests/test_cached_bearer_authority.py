"""L1: replay a real cached bearer after platform authority is revoked.

The money view's auth, permissions and grant handler are real. Only the EE
ledger boundary and response formatter are replaced so this also runs in OSS.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.contrib import admin
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.admin import CustomUserAdmin
from accounts.authentication import (
    APIKeyAuthentication,
    decode_token,
    generate_encrypted_message,
)
from accounts.models import User
from accounts.models.auth_token import AuthToken, AuthTokenType
from agentcc.views import managed_credits

pytestmark = pytest.mark.django_db
AUTHORITY_FIELDS = ("is_staff", "is_superuser", "is_active")


@pytest.fixture(autouse=True)
def isolated_auth_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cached-bearer-authority-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def session(user):
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    row = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"id": str(row.pk), "user_id": str(user.pk)})
    decoded, _, _ = decode_token(bearer)
    key = f"access_token_{row.pk}"
    assert decoded.is_staff and decoded.is_superuser
    assert cache.get(key)["non_saml_v1"] is True
    return SimpleNamespace(user=user, row=row, bearer=bearer, key=key)


@pytest.fixture
def grant(monkeypatch, workspace):
    ledger = SimpleNamespace(
        grant=Mock(return_value=(SimpleNamespace(funded_debt_micros=0), True)),
        ManagedCreditError=type("LedgerError", (Exception,), {}),
    )
    monkeypatch.setattr(managed_credits, "_ledger", lambda: ledger)
    monkeypatch.setattr(
        managed_credits.ManagedCreditGrantView,
        "_written",
        lambda self, *args, **kwargs: Response({"written": True}, status=201),
    )

    def request(session):
        req = APIRequestFactory().post(
            "/agentcc/managed-credits/grant/",
            {"amount_usd": "1.00", "idempotency_key": "l1-grant", "note": "L1"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {session.bearer}",
            HTTP_X_WORKSPACE_ID=str(workspace.pk),
            HTTP_X_ORGANIZATION_ID=str(workspace.organization_id),
        )
        return managed_credits.ManagedCreditGrantView.as_view()(req)

    return request, ledger.grant


def revoke(session, field, method):
    if method == "update":
        User.objects.filter(pk=session.user.pk).update(**{field: False})
    elif method == "bulk_update":
        setattr(session.user, field, False)
        User.objects.bulk_update([session.user], [field])
    else:
        setattr(session.user, field, False)
        if method == "admin":
            CustomUserAdmin(User, admin.site).save_model(
                APIRequestFactory().post("/admin/accounts/user/"),
                session.user,
                SimpleNamespace(changed_data=[field]),
                change=True,
            )
        else:
            session.user.save(update_fields=[field])


@pytest.mark.parametrize("method", ["save", "admin", "update", "bulk_update"])
def test_revoked_staff_cannot_replay_cached_bearer_for_grant(session, grant, method):
    request, write = grant
    assert request(session).status_code == 201
    write.reset_mock()
    revoke(session, "is_staff", method)
    assert request(session).status_code == 403
    write.assert_not_called()


@pytest.mark.parametrize("method", ["save", "admin", "update", "bulk_update"])
def test_inactive_user_cannot_replay_cached_bearer_for_grant(session, grant, method):
    request, write = grant
    revoke(session, "is_active", method)
    assert request(session).status_code in (401, 403)
    write.assert_not_called()


class SuperuserPermission(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_superuser


class SuperuserProbe(APIView):
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [SuperuserPermission]

    def get(self, request):
        return Response({"allowed": True})


@pytest.mark.parametrize("method", ["save", "admin", "update", "bulk_update"])
def test_revoked_superuser_loses_permission_on_cached_replay(session, method):
    def request():
        return SuperuserProbe.as_view()(
            APIRequestFactory().get(
                "/accounts/authority-probe/",
                HTTP_AUTHORIZATION=f"Bearer {session.bearer}",
            )
        )

    assert request().status_code == 200
    revoke(session, "is_superuser", method)
    assert request().status_code == 403
    assert decode_token(session.bearer)[0].is_superuser is False


def test_unchanged_staff_can_still_grant(session, grant):
    request, write = grant
    assert request(session).status_code == 201
    assert request(session).status_code == 201
    assert write.call_count == 2
    assert write.call_args.kwargs["staff_authorized"] is True


@pytest.mark.parametrize("field", AUTHORITY_FIELDS)
@pytest.mark.parametrize("method", ["save", "admin"])
def test_authority_save_evicts_cached_sessions(session, field, method):
    revoke(session, field, method)
    assert cache.get(session.key) is None


@pytest.mark.parametrize("field", AUTHORITY_FIELDS)
def test_authority_change_evicts_again_after_commit(
    session, field, django_capture_on_commit_callbacks
):
    stale = cache.get(session.key)
    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            revoke(session, field, "save")
            # A concurrent request can refill from the pre-commit row.
            cache.set(session.key, stale)
    assert cache.get(session.key) is None


def test_partial_profile_save_does_not_evict_or_revoke_staff(session):
    session.user.is_staff = False  # Not among the fields actually persisted.
    session.user.name = "Profile edit"
    session.user.save(update_fields=["name"])
    assert cache.get(session.key) is not None
    assert decode_token(session.bearer)[0].is_staff is True


def test_unchanged_authority_save_keeps_cache(session):
    session.user.save(update_fields=list(AUTHORITY_FIELDS))
    assert cache.get(session.key) is not None


@pytest.mark.parametrize("field", ["is_staff", "is_superuser"])
def test_stale_refill_after_save_is_not_authority(session, field):
    stale = cache.get(session.key)
    revoke(session, field, "save")
    cache.set(session.key, stale)
    assert getattr(decode_token(session.bearer)[0], field) is False


class OrdinaryProbe(APIView):
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"cached": getattr(request.user, "l1_cache_marker", False)})


def test_non_staff_endpoint_keeps_cached_user(session, django_assert_num_queries):
    User.objects.filter(pk=session.user.pk).update(is_staff=False, is_superuser=False)
    cache.delete(session.key)
    decode_token(session.bearer)
    cached = cache.get(session.key)
    cached["user"].l1_cache_marker = True
    cache.set(session.key, cached)
    with django_assert_num_queries(1):
        assert decode_token(session.bearer)[0].l1_cache_marker is True
    response = OrdinaryProbe.as_view()(
        APIRequestFactory().get(
            "/accounts/ordinary-probe/",
            HTTP_AUTHORIZATION=f"Bearer {session.bearer}",
        )
    )
    assert response.status_code == 200
    assert response.data["cached"] is True


def test_revoked_authority_is_reloaded_from_primary(session):
    revoke(session, "is_staff", "update")
    # Reads sent through a replica router could retain the revoked authority.
    with patch("django.db.router.db_for_read", return_value="unavailable_replica"):
        assert decode_token(session.bearer)[0].is_staff is False


def test_liveness_database_failure_never_serves_cached_authority(session):
    with patch(
        "accounts.authentication._touch_non_saml_access_token",
        side_effect=DatabaseError("unavailable"),
    ):
        with pytest.raises(DatabaseError):
            decode_token(session.bearer)


def test_inactive_stale_cache_never_authenticates(session):
    stale = cache.get(session.key)
    revoke(session, "is_active", "update")
    cache.set(session.key, stale)
    with pytest.raises(AuthenticationFailed):
        decode_token(session.bearer)
