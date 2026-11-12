"""Personal-key auth must not reseal rows during invitation preview/refusal."""

import pytest
from rest_framework.test import APIClient

from accounts.api_key_lookup import find_api_key
from accounts.models import OrgApiKey, User
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership

from . import test_invitation_expiry as expiry

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
isolated_auth_cache = expiry.isolated_auth_cache
pending_invitation = expiry.pending_invitation


def _personal_key(subject, stale_plaintext):
    user, invite, workspace = subject
    User.objects.filter(pk=user.pk).update(is_active=True)
    OrganizationMembership.objects.filter(
        user=user, organization=invite.organization
    ).update(is_active=True)
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(
        is_active=True
    )
    # Explicit personal-key fixture creation only, never an automatic/system key.
    key = OrgApiKey.objects.create(
        name="Invitation test personal key",
        user=user,
        organization=invite.organization,
        workspace=workspace,
        type="user",
    )
    secret = key.secret_key  # Issuing instance retains its one-time display value.
    if stale_plaintext:
        # Model the rolling-upgrade row the real lookup otherwise reseals.
        OrgApiKey.objects.filter(pk=key.pk).update(secret_key=secret)
    return key, secret


@pytest.mark.parametrize("stale_plaintext", [False, True], ids=["sealed", "stale"])
@pytest.mark.parametrize(
    "state,method,expected_status",
    [
        ("valid", "GET", 200),
        ("deadline", "GET", 400),
        ("deadline", "POST", 400),
        ("cancelled", "GET", 400),
        ("cancelled", "POST", 400),
    ],
)
def test_personal_key_invitation_is_read_only(
    pending_invitation, stale_plaintext, state, method, expected_status, monkeypatch
):
    user, invite, _ = pending_invitation
    key, secret = _personal_key(pending_invitation, stale_plaintext)
    url = expiry._url(user)
    if state == "deadline":
        expiry._expire(invite, monkeypatch)
    elif state == "cancelled":
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            status=InviteStatus.CANCELLED
        )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=secret)
    before = expiry._snapshot()
    if method == "GET":
        response = client.get(url)
    else:
        response = client.post(
            url,
            {"new_password": expiry.PASSWORD, "repeat_password": expiry.PASSWORD},
            format="json",
        )
    expiry._assert_unchanged(before)
    assert response.status_code == expected_status
    assert "access" not in response.data and "refresh" not in response.data


@pytest.mark.parametrize("stale_plaintext", [False, True], ids=["sealed", "stale"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_wrong_personal_key_secret_remains_refused(
    pending_invitation, stale_plaintext, method
):
    user, _, _ = pending_invitation
    key, secret = _personal_key(pending_invitation, stale_plaintext)
    url = expiry._url(user)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=secret + "wrong")
    before = expiry._snapshot()
    response = getattr(client, method.lower())(url)
    expiry._assert_unchanged(before)
    assert response.status_code == 401


def test_ordinary_key_lookup_still_reseals(pending_invitation):
    key, secret = _personal_key(pending_invitation, True)
    assert find_api_key(OrgApiKey.objects.all(), key.api_key, secret) is not None
    key.refresh_from_db()
    assert key.secret_key == ""
    assert key.secret_key_hash
