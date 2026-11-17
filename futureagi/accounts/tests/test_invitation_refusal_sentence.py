"""An invitation link that cannot be read answers one sentence for every cause.

The accept endpoint (GET preview and POST accept) refuses a link it cannot read:
an account id that does not decode, an account id that names no account, a
tampered or expired signature, a link minted for another account or by an
unknown producer, or an acceptance refused after the rows are locked. Each
answers the same 400 body, word for word, which tells the person what to do
next and never says which check failed. A refusal writes nothing. The
cancelled-or-expired answer for a link that was read but whose invitation is no
longer pending is a different sentence and stays as it is. A valid link is
still accepted.
"""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing
from django.core.cache import cache
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User
from accounts.models.auth_token import AuthToken
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services import invitation_links
from accounts.services.invitation_links import generation, make_invitation_token
from tfc.constants.levels import INVITE_VALIDITY_DAYS, Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.general_methods import GeneralMethods

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]

SENTENCE = (
    "This invitation link is invalid or has expired. "
    "Ask the person who invited you to send it again from Settings > Members."
)
CANCELLED = "This invitation has been cancelled or expired."
PASSWORD = "InviteRefusal-Valid-Password123!"


@pytest.fixture(autouse=True)
def isolated_auth_cache(settings, monkeypatch):
    # A private in-memory cache, so a refusal can be shown to leave every
    # cache key and expiry untouched. External analytics are inert.
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "")
    from analytics.posthog_util import posthog_tracker

    monkeypatch.setattr(posthog_tracker, "client", None)
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"invite-refusal-{uuid4()}",
            "TIMEOUT": None,
        }
    }
    yield
    cache.clear()


def _snapshot():
    models = {
        "users": User,
        "invites": OrganizationInvite,
        "org_memberships": OrganizationMembership,
        "workspace_memberships": WorkspaceMembership,
        "invited_organizations": User.invited_organizations.through,
        "auth_tokens": AuthToken,
        "api_keys": OrgApiKey,
    }
    rows = {
        name: list(model._base_manager.order_by("pk").values())
        for name, model in models.items()
    }
    rows["cache_values"] = deepcopy(dict(cache._cache))
    rows["cache_expirations"] = deepcopy(cache._expire_info)
    return rows


def _assert_unchanged(before):
    after = _snapshot()
    for name in before:
        assert after[name] == before[name], f"Invitation request mutated {name}"


def _uid(value):
    return urlsafe_base64_encode(force_bytes(value))


def _accept_url(uid, token):
    return f"/accounts/accept-invitation/{uid}/{token}/"


def _request(method, url):
    client = APIClient()  # Signed out: no bearer, no session.
    if method == "GET":
        return client.get(url)
    return client.post(
        url,
        {"new_password": PASSWORD, "repeat_password": PASSWORD},
        format="json",
    )


def _expected_body():
    return GeneralMethods().bad_request(SENTENCE).data


@pytest.fixture
def pending_invitation():
    org = Organization.objects.create(name="Refusal invitation org")
    user = User.objects.create_user(
        email="refusal-invite@example.test",
        name="Refusal invitee",
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
    return user, invite, workspace


def _uid_not_base64(user, invite, monkeypatch):
    return _accept_url("bad", make_invitation_token(user, invite))


def _uid_not_an_account_id(user, invite, monkeypatch):
    token = make_invitation_token(user, invite)
    return _accept_url(_uid("not-an-account-id"), token)


def _unknown_account(user, invite, monkeypatch):
    # A well-formed account id that names no account.
    token = make_invitation_token(user, invite)
    return _accept_url(_uid(uuid4()), token)


def _tampered_signature(user, invite, monkeypatch):
    token = make_invitation_token(user, invite)
    token = token[:-1] + ("A" if token[-1] != "A" else "B")
    return _accept_url(_uid(user.pk), token)


def _expired_signature(user, invite, monkeypatch):
    # Sign the link as if it was sent one day past the validity window ago.
    # Only the signature's own clock moves; the invitation row is still
    # pending and inside its deadline.
    signed_at = signing.time.time() - (INVITE_VALIDITY_DAYS + 1) * 86400
    with monkeypatch.context() as m:
        m.setattr(signing, "time", SimpleNamespace(time=lambda: signed_at))
        token = make_invitation_token(user, invite)
    return _accept_url(_uid(user.pk), token)


def _link_for_another_account(user, invite, monkeypatch):
    other = User.objects.create_user(
        email=f"refusal-other-{uuid4().hex[:8]}@example.test",
        name="Other invitee",
        password="Original-Password123!",
        organization=invite.organization,
        is_active=False,
    )
    return _accept_url(_uid(other.pk), make_invitation_token(user, invite))


def _unknown_delivery(user, invite, monkeypatch):
    token = signing.dumps(
        {
            "invite": str(invite.id),
            "organization": str(invite.organization_id),
            "user": str(user.pk),
            "email": user.email.casefold(),
            "generation": generation(invite),
            "delivery": "forwarded",
            "activation": True,
        },
        salt=invitation_links.SALT,
        compress=True,
    )
    return _accept_url(_uid(user.pk), token)


def _refused_after_the_lock(user, invite, monkeypatch):
    real_accept = OrganizationInvite.accept

    def accept_then_refuse(self, target):
        real_accept(self, target)
        raise ValueError("This invite is no longer pending.")

    monkeypatch.setattr(OrganizationInvite, "accept", accept_then_refuse)
    return _accept_url(_uid(user.pk), make_invitation_token(user, invite))


CAUSES = {
    "uid-not-base64": _uid_not_base64,
    "uid-not-an-account-id": _uid_not_an_account_id,
    "unknown-account": _unknown_account,
    "tampered-signature": _tampered_signature,
    "expired-signature": _expired_signature,
    "link-for-another-account": _link_for_another_account,
    "unknown-delivery": _unknown_delivery,
}
POST_ONLY_CAUSES = {"refused-after-the-lock": _refused_after_the_lock}

CASES = [(cause, method) for cause in CAUSES for method in ("GET", "POST")]
CASES += [(cause, "POST") for cause in POST_ONLY_CAUSES]
CASE_IDS = ["-".join(case) for case in CASES]


def _refuse(cause, method, pending_invitation, monkeypatch):
    user, invite, _ = pending_invitation
    make_url = {**CAUSES, **POST_ONLY_CAUSES}[cause]
    url = make_url(user, invite, monkeypatch)
    before = _snapshot()
    response = _request(method, url)
    _assert_unchanged(before)
    return response


@pytest.mark.parametrize(("cause", "method"), CASES, ids=CASE_IDS)
def test_each_cause_answers_the_one_sentence_and_writes_nothing(
    cause, method, pending_invitation, monkeypatch
):
    response = _refuse(cause, method, pending_invitation, monkeypatch)

    assert response.status_code == 400
    assert "access" not in response.data
    assert "refresh" not in response.data
    assert response.json() == _expected_body()
    assert response.json()["message"] == SENTENCE


def test_every_cause_answers_the_same_bytes(pending_invitation, monkeypatch):
    bodies = {}
    for cause, method in CASES:
        with monkeypatch.context() as m:
            response = _refuse(cause, method, pending_invitation, m)
        assert response.status_code == 400, (cause, method)
        bodies[(cause, method)] = (response["Content-Type"], response.content)

    distinct = set(bodies.values())
    assert len(distinct) == 1, distinct
    _, content = distinct.pop()
    assert json.loads(content) == _expected_body()
    assert SENTENCE.encode() in content


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_cancelled_invitation_keeps_its_own_sentence(pending_invitation, method):
    user, invite, _ = pending_invitation
    url = _accept_url(_uid(user.pk), make_invitation_token(user, invite))
    OrganizationInvite.objects.filter(pk=invite.pk).update(
        status=InviteStatus.CANCELLED
    )

    before = _snapshot()
    response = _request(method, url)
    _assert_unchanged(before)

    assert response.status_code == 400
    assert response.json()["message"] == CANCELLED


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_cancelled_after_the_lock_keeps_its_own_sentence(
    pending_invitation, method, monkeypatch
):
    import accounts.views.signup as signup

    user, invite, _ = pending_invitation
    url = _accept_url(_uid(user.pk), make_invitation_token(user, invite))
    real_matches = signup.invitation_matches
    calls = []

    def matches_until_locked(payload, row, person):
        # The first check reads the rows without a lock; the second re-reads
        # them under the lock and finds the invitation no longer usable.
        calls.append(row.pk if row is not None else None)
        return real_matches(payload, row, person) and len(calls) == 1

    monkeypatch.setattr(signup, "invitation_matches", matches_until_locked)

    before = _snapshot()
    response = _request(method, url)
    _assert_unchanged(before)

    assert len(calls) == 2
    assert response.status_code == 400
    assert response.json()["message"] == CANCELLED


def test_valid_invitation_is_still_accepted(pending_invitation):
    user, invite, workspace = pending_invitation
    url = _accept_url(_uid(user.pk), make_invitation_token(user, invite))

    preview = _request("GET", url)
    assert preview.status_code == 200
    assert preview.data["valid"] is True
    assert preview.data["email"] == user.email

    response = _request("POST", url)
    assert response.status_code == 200
    assert response.data["access"]
    assert response.data["refresh"]
    user.refresh_from_db()
    invite.refresh_from_db()
    assert user.is_active is True
    assert user.check_password(PASSWORD)
    assert invite.status == InviteStatus.ACCEPTED
    org_member = OrganizationMembership._base_manager.get(
        user=user, organization=invite.organization
    )
    ws_member = WorkspaceMembership._base_manager.get(user=user, workspace=workspace)
    assert org_member.is_active
    assert ws_member.is_active
