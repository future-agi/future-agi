"""Invitation deadline safety; execute only in the guarded integration runtime.

Use real, freshly signed capabilities and exact unfiltered rows. Advance only the
invitation clock past its deadline, without expiring the signature or rotating the
offer generation. Snapshot values stay in memory; output names only the category.
"""

import hashlib
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.authentication import decrypt_message
from accounts.models import Organization, OrgApiKey, User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.invitation_links import (
    generation,
    invitation_matches,
    make_invitation_token,
    read_invitation_token,
)
from tfc.constants.levels import INVITE_VALIDITY_DAYS, Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
PASSWORD = "InviteDeadline-Valid-Password123!"


@pytest.fixture(autouse=True)
def isolated_auth_cache(settings, monkeypatch):
    from tfc.settings.settings import MIDDLEWARE

    # Exercise the configured public stack, not test.py's reduced middleware.
    # Keep every auth/permission/rate-limit middleware and the exact snapshots.
    settings.MIDDLEWARE = list(MIDDLEWARE)
    assert (
        "tfc.middleware.auth_monitoring.AuthMonitoringMiddleware" in settings.MIDDLEWARE
    )
    assert "accounts.authentication.AuthMonitoringMiddleware" in settings.MIDDLEWARE
    # Only external analytics are inert. The integrated socket guard still
    # covers startup, collection and every request; no auth writer is mocked.
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "")
    from analytics.posthog_util import posthog_tracker

    monkeypatch.setattr(posthog_tracker, "client", None)
    # Snapshot all keys and expiry metadata, including previously unknown keys.
    # No Redis/SMTP/provider is needed by these tests; the integrated guard is
    # still mandatory for collection, fixtures and the rest of Django startup.
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"invite-expiry-{uuid4()}",
            "TIMEOUT": None,
        }
    }
    yield
    cache.clear()


def _snapshot():
    models = {
        "users_password_activation_config": User,
        "invites": OrganizationInvite,
        "org_memberships_including_inactive_deleted": OrganizationMembership,
        "workspace_memberships_including_inactive_deleted": WorkspaceMembership,
        "invited_organizations": User.invited_organizations.through,
        "auth_tokens_including_inactive": AuthToken,
        "api_keys": OrgApiKey,
    }
    rows = {
        name: list(model._base_manager.order_by("pk").values())
        for name, model in models.items()
    }
    rows["cache_auth_values"] = deepcopy(dict(cache._cache))
    rows["cache_auth_expirations"] = deepcopy(cache._expire_info)
    return rows


def _assert_unchanged(before):
    after = _snapshot()
    for name in before:
        unchanged = after[name] == before[name]
        assert unchanged, f"Invitation request mutated {name}"


def _url(user):
    user.refresh_from_db()
    invite = OrganizationInvite.objects.get(
        organization=user.organization,
        target_email__iexact=user.email,
        status=InviteStatus.PENDING,
    )
    token = make_invitation_token(user, invite)
    payload = read_invitation_token(token, user.pk)
    assert invitation_matches(payload, invite, user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return f"/accounts/accept-invitation/{uid}/{token}/"


def _request(method, url, *, bearer=None):
    client = APIClient()  # No force_authenticate or inherited session.
    if bearer is not None:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
    if method == "GET":
        return client.get(url)
    return client.post(
        url,
        {"new_password": PASSWORD, "repeat_password": PASSWORD},
        format="json",
    )


def _refused_without_writes(user, method, url=None, *, bearer=None):
    url = url or _url(user)
    # Refuse the invitation state, not an invalid or expired signature.
    token = url.rstrip("/").split("/")[-1]
    assert read_invitation_token(token, user.pk)["user"] == str(user.pk)
    before = _snapshot()
    response = _request(method, url, bearer=bearer)
    _assert_unchanged(before)
    assert response.status_code == 400
    assert "access" not in response.data
    assert "refresh" not in response.data


def _seed_existing_auth(user):
    user.config = {"invitation_sentinel": "keep", "selected_organization_id": ""}
    user.save(update_fields=["config"])
    for kind in (AuthTokenType.ACCESS.value, AuthTokenType.REFRESH.value):
        token = AuthToken.objects.create(
            user=user, auth_type=kind, is_active=True, last_used_at=timezone.now()
        )
        cache.set(f"{kind}_token_{token.pk}", {"sentinel": kind}, timeout=None)


@pytest.fixture
def pending_invitation():
    org = Organization.objects.create(name="Deadline invitation org")
    user = User.objects.create_user(
        email="deadline-invite@example.test",
        name="Deadline invitee",
        password="Original-Password123!",
        organization=org,
        is_active=False,
    )
    workspace = Workspace.objects.create(
        name="Invited workspace", organization=org, created_by=user, is_default=True
    )
    membership = OrganizationMembership.objects.create(
        user=user,
        organization=org,
        level=Level.MEMBER,
        role=OrganizationRoles.MEMBER,
        is_active=False,
    )
    WorkspaceMembership.objects.create(
        user=user,
        workspace=workspace,
        organization_membership=membership,
        level=Level.WORKSPACE_MEMBER,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        is_active=False,
    )
    invite = OrganizationInvite.objects.create(
        organization=org,
        target_email=user.email,
        level=Level.MEMBER,
        workspace_access=[
            {"workspace_id": str(workspace.pk), "level": Level.WORKSPACE_MEMBER}
        ],
    )
    _seed_existing_auth(user)
    return user, invite, workspace


@pytest.fixture
def legacy_invitation(auth_client, workspace, monkeypatch):
    # Bind the REAL supported legacy producer, rather than inventing a bypass
    # for missing OrganizationInvite rows. The producer itself persists one.
    import accounts.views.workspace_management as wm

    monkeypatch.setattr(wm, "email_helper", lambda *args, **kwargs: None)
    monkeypatch.setattr(wm, "log_and_deduct_cost_for_resource_request", None)
    response = auth_client.post(
        "/accounts/team/users/",
        {
            "members": [
                {
                    "email": "legacy-deadline@example.test",
                    "name": "Legacy invitee",
                    "organization_role": OrganizationRoles.MEMBER,
                }
            ]
        },
        format="json",
    )
    assert response.status_code in (200, 201)
    user = User.objects.get(email="legacy-deadline@example.test")
    invite = OrganizationInvite.objects.get(
        organization=user.organization,
        target_email=user.email,
        status=InviteStatus.PENDING,
    )
    assert any(
        str(workspace.pk) == row["workspace_id"] for row in invite.workspace_access
    )
    assert user.is_active is False
    clear_workspace_context()
    _seed_existing_auth(user)
    return user, invite, workspace


def _expire(invite, monkeypatch):
    from accounts.models import organization_invite as invite_model

    # Backdating created_at would also rotate the signed generation, masking a
    # missing deadline check. Keep bearer/signature clocks and offer data intact.
    before_generation = generation(invite)
    deadline = invite.created_at + timedelta(days=INVITE_VALIDITY_DAYS, seconds=1)
    monkeypatch.setattr(invite_model, "timezone", SimpleNamespace(now=lambda: deadline))
    invite.refresh_from_db()
    assert generation(invite) == before_generation
    assert invite.status == InviteStatus.PENDING
    assert invite.is_expired is True
    assert invite.effective_status == InviteStatus.EXPIRED


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "active_user", [False, True], ids=["inactive-user", "active-user"]
)
def test_deadline_expired_pending_is_read_only(
    pending_invitation, method, active_user, monkeypatch
):
    user, invite, _ = pending_invitation
    User.objects.filter(pk=user.pk).update(is_active=active_user)
    url = _url(user)
    _expire(invite, monkeypatch)
    _refused_without_writes(user, method, url=url)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_cancelled_is_read_only(pending_invitation, method):
    user, invite, _ = pending_invitation
    url = _url(user)
    OrganizationInvite.objects.filter(pk=invite.pk).update(
        status=InviteStatus.CANCELLED
    )
    _refused_without_writes(user, method, url=url)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_explicit_expired_is_read_only(pending_invitation, method):
    user, invite, _ = pending_invitation
    url = _url(user)
    OrganizationInvite.objects.filter(pk=invite.pk).update(status=InviteStatus.EXPIRED)
    _refused_without_writes(user, method, url=url)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_used_invite_with_fresh_valid_token_is_read_only(pending_invitation, method):
    user, invite, _ = pending_invitation
    url = _url(user)
    OrganizationInvite.objects.filter(pk=invite.pk).update(status=InviteStatus.ACCEPTED)
    _refused_without_writes(user, method, url=url)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_missing_authoritative_invite_is_read_only(pending_invitation, method):
    user, invite, _ = pending_invitation
    url = _url(user)
    # A valid pending row for ANOTHER address is not authority for this user.
    OrganizationInvite.objects.filter(pk=invite.pk).update(
        target_email="other@example.test"
    )
    assert not OrganizationInvite.objects.filter(target_email=user.email).exists()
    _refused_without_writes(user, method, url=url)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_replay_is_read_only(pending_invitation, method):
    user, invite, _ = pending_invitation
    url = _url(user)
    password_reset_token = default_token_generator.make_token(user)
    assert default_token_generator.check_token(user, password_reset_token)
    assert _request("POST", url).status_code == 200
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    user.refresh_from_db()
    assert not default_token_generator.check_token(user, password_reset_token)
    payload = read_invitation_token(url.rstrip("/").split("/")[-1], user.pk)
    assert not invitation_matches(payload, invite, user)
    _refused_without_writes(user, method, url=url)


def _assert_valid_acceptance(subject):
    user, invite, workspace = subject
    url = _url(user)
    before = _snapshot()
    for _ in range(2):
        response = _request("GET", url)
        assert response.status_code == 200
        assert response.data["valid"] is True
        assert response.data["email"] == user.email
        _assert_unchanged(before)
    response = _request("POST", url)
    assert response.status_code == 200
    user.refresh_from_db()
    invite.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert user.is_active is True
    assert invite.status == InviteStatus.ACCEPTED
    assert user.config["invitation_sentinel"] == "keep"
    assert user.config["selected_organization_id"] == str(invite.organization_id)
    assert user.config["currentOrganizationId"] == str(invite.organization_id)
    org_member = OrganizationMembership._base_manager.get(
        user=user, organization=invite.organization
    )
    ws_member = WorkspaceMembership._base_manager.get(user=user, workspace=workspace)
    assert org_member.is_active and not org_member.deleted
    assert org_member.level == invite.level
    assert ws_member.is_active and not ws_member.deleted
    assert ws_member.organization_membership_id == org_member.pk
    assert user.invited_organizations.filter(pk=invite.organization_id).exists()
    assert (
        AuthToken.objects.filter(
            user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
        ).count()
        == 1
    )
    for kind in ("access", "refresh"):
        payload = decrypt_message(response.data[kind])
        assert payload["user_id"] == str(user.pk)
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


def test_valid_invitation_preview_and_accept(pending_invitation):
    _assert_valid_acceptance(pending_invitation)


def test_legacy_team_invitation_preview_and_accept(legacy_invitation):
    _assert_valid_acceptance(legacy_invitation)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_legacy_team_deadline_expired_is_read_only(legacy_invitation, method, monkeypatch):
    user, invite, _ = legacy_invitation
    url = _url(user)
    _expire(invite, monkeypatch)
    _refused_without_writes(user, method, url=url)


def test_accept_failure_rolls_back_without_activation(pending_invitation, monkeypatch):
    user, _, _ = pending_invitation
    real_accept = OrganizationInvite.accept
    calls = []

    def fail_after_memberships(self, target):
        calls.append(self.pk)
        real_accept(self, target)
        raise ValueError("Acceptance failed after membership writes")

    monkeypatch.setattr(OrganizationInvite, "accept", fail_after_memberships)
    _refused_without_writes(user, "POST")
    assert len(calls) == 1
