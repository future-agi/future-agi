"""Real-bearer invitation no-write controls; guarded integrated execution only."""

import hashlib
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.authentication import (
    decode_token,
    decrypt_message,
    generate_encrypted_message,
)
from accounts.models import User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.organization_invite import InviteStatus, OrganizationInvite

from . import test_invitation_expiry as expiry

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
# Reuse the exact snapshot and fixtures, including isolated real LocMemCache.
isolated_auth_cache = expiry.isolated_auth_cache
pending_invitation = expiry.pending_invitation


def _cache_state(user, bearer, state):
    payload = decrypt_message(bearer)
    row = AuthToken.objects.get(pk=payload["id"], user=user)
    # Make any auth touch observable, independent of clock resolution.
    AuthToken.objects.filter(pk=row.pk).update(
        last_used_at=timezone.now() - timedelta(seconds=1)
    )
    key = f"access_token_{row.pk}"
    cache.delete(key)
    if state != "cold":
        value = {"token": bearer, "user": user}
        if state == "warm":
            value["non_saml_v1"] = True
        cache.set(key, value, timeout=120)
    row.refresh_from_db()
    return row


def _bearer(user, state):
    User.objects.filter(pk=user.pk).update(is_active=True)
    user.refresh_from_db()
    row = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now(),
    )
    bearer = generate_encrypted_message({"user_id": str(user.pk), "id": str(row.pk)})
    return bearer, _cache_state(user, bearer, state)


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "refusal", ["deadline", "cancelled", "expired", "used", "missing"]
)
def test_bearer_invitation_refusal_is_read_only(
    pending_invitation, cache_state, method, refusal, monkeypatch
):
    user, invite, _ = pending_invitation
    bearer, _ = _bearer(user, cache_state)
    url = expiry._url(user)
    if refusal == "deadline":
        expiry._expire(invite, monkeypatch)
    elif refusal == "missing":
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            target_email="other@example.test"
        )
    else:
        state = {
            "cancelled": InviteStatus.CANCELLED,
            "expired": InviteStatus.EXPIRED,
            "used": InviteStatus.ACCEPTED,
        }[refusal]
        OrganizationInvite.objects.filter(pk=invite.pk).update(status=state)
    expiry._refused_without_writes(user, method, url=url, bearer=bearer)


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_replay_with_returned_access_bearer_is_read_only(
    pending_invitation, cache_state, method
):
    user, invite, _ = pending_invitation
    url = expiry._url(user)
    accepted = expiry._request("POST", url)
    assert accepted.status_code == 200
    user.refresh_from_db()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    bearer = accepted.data["access"]
    _cache_state(user, bearer, cache_state)
    expiry._refused_without_writes(user, method, url=url, bearer=bearer)


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
def test_valid_preview_with_bearer_is_read_only(pending_invitation, cache_state):
    user, _, _ = pending_invitation
    bearer, _ = _bearer(user, cache_state)
    url = expiry._url(user)
    before = expiry._snapshot()
    for _ in range(2):
        response = expiry._request("GET", url, bearer=bearer)
        expiry._assert_unchanged(before)
        assert response.status_code == 200
        assert response.data["valid"] is True
        assert response.data["email"] == user.email


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
def test_valid_accept_with_same_user_bearer(pending_invitation, cache_state):
    user, invite, workspace = pending_invitation
    bearer, old_access = _bearer(user, cache_state)
    url = expiry._url(user)
    before_access = AuthToken.objects.filter(pk=old_access.pk).values().get()
    before_password = user.password
    response = expiry._request("POST", url, bearer=bearer)
    assert response.status_code == 200
    user.refresh_from_db()
    invite.refresh_from_db()
    assert user.is_active
    assert user.password == before_password
    assert user.check_password("Original-Password123!")
    assert invite.status == InviteStatus.ACCEPTED
    assert user.config["selected_organization_id"] == str(invite.organization_id)
    assert user.workspace_memberships.filter(
        workspace=workspace, is_active=True
    ).exists()
    unchanged = (
        AuthToken.objects.filter(pk=old_access.pk).values().get() == before_access
    )
    assert unchanged, "Invitation authentication touched the existing bearer"
    for kind in ("access", "refresh"):
        payload = decrypt_message(response.data[kind])
        row = AuthToken.objects.get(pk=payload["id"], user=user, auth_type=kind)
        assert row.is_active
        if kind == "access":
            access_entry = cache.get(f"{kind}_token_{row.pk}")
            assert set(access_entry) == {"token_digest", "user"}
            assert access_entry["token_digest"] == hashlib.sha256(
                response.data[kind].encode("utf-8")
            ).hexdigest()
        else:
            assert cache.get(f"{kind}_token_{row.pk}")["token"] == response.data[kind]


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_different_user_bearer_still_refused_without_writes(
    pending_invitation, cache_state, method
):
    target, _, _ = pending_invitation
    other = User.objects.create_user(
        email="different-invite-user@example.test",
        password="Different-User-Password123!",
        name="Different invite user",
        is_active=True,
    )
    bearer, _ = _bearer(other, cache_state)
    url = expiry._url(target)
    before = expiry._snapshot()
    response = expiry._request(method, url, bearer=bearer)
    expiry._assert_unchanged(before)
    assert response.status_code == 403
    assert response.data["code"] == "authenticated_user_mismatch"
    assert "access" not in response.data and "refresh" not in response.data


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "invalid", ["revoked", "idle-expired", "refresh", "wrong-user"]
)
def test_invalid_bearer_remains_rejected_without_writes(
    pending_invitation, cache_state, method, invalid
):
    user, _, _ = pending_invitation
    bearer, row = _bearer(user, cache_state)
    if invalid == "revoked":
        AuthToken.objects.filter(pk=row.pk).update(is_active=False)
    elif invalid == "idle-expired":
        AuthToken.objects.filter(pk=row.pk).update(
            last_used_at=timezone.now()
            - timedelta(minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES, seconds=1)
        )
    elif invalid == "refresh":
        AuthToken.objects.filter(pk=row.pk).update(
            auth_type=AuthTokenType.REFRESH.value
        )
    else:
        other = User.objects.create_user(
            email="wrong-bearer-binding@example.test", is_active=True
        )
        AuthToken.objects.filter(pk=row.pk).update(user=other)
    url = expiry._url(user)
    before = expiry._snapshot()
    response = expiry._request(method, url, bearer=bearer)
    expiry._assert_unchanged(before)
    assert response.status_code == 401
    assert "access" not in response.data and "refresh" not in response.data


@pytest.mark.parametrize("cache_state", ["cold", "legacy", "warm"])
def test_ordinary_bearer_authentication_still_touches(pending_invitation, cache_state):
    user, _, _ = pending_invitation
    bearer, row = _bearer(user, cache_state)
    before = row.last_used_at
    # Real normal decoder control: the no-touch mode must be opt-in, not global.
    authenticated, token, scope = decode_token(bearer)
    row.refresh_from_db()
    assert authenticated.pk == user.pk and token == bearer and scope is None
    assert row.last_used_at > before
    assert cache.get(f"access_token_{row.pk}")["non_saml_v1"] is True
