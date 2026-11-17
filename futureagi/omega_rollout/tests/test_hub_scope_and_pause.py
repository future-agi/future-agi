"""Whose access a Hub request uses, and what a paused Hub still serves (READINESS 3.13, RC-02, RC-03,
RC-08's Hub leg).

Regressions for the A2 verifier's findings:
- A2: a user-publisher Hub token was decided by the legacy `user.organization` key, which removing a
  member leaves in place, so a person removed from admitted org A kept the dark Hub by token.
- A7: an org-level API key was decided for `key.workspace_id` (None), so a workspace deny did not
  apply to it on the Hub's public reads.
- A4: a paused Hub refused token revocation and maintainer takedowns, and a paused Hub could not be
  killed again to stop installs without first reopening submissions.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.urls import URLPattern, reverse

from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from omega_hub import urls as hub_urls
from omega_hub.models import Entry, Publisher
from omega_hub.services import tokens
from omega_hub.tests.helpers import CHECKER_TOKEN, make_person, token_client
from omega_rollout import ops
from omega_rollout import products as p
from omega_rollout.hub_gate import STOP_WORK
from omega_rollout.models import ProductRollout, RolloutAuditEvent
from omega_rollout.tests.conftest import admit, client_for, key_client
from omega_rollout.tests.test_hub_gate import SAMPLE
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
WRITES = ("post", "put", "patch", "delete")


@pytest.fixture(autouse=True)
def hub_settings(settings, tmp_path):
    settings.OMEGA_HUB_STORAGE = "filesystem"
    settings.OMEGA_HUB_STORAGE_DIR = str(tmp_path / "hub-store")
    settings.OMEGA_HUB_CHECKER_TOKEN = CHECKER_TOKEN
    settings.OMEGA_HUB_PUBLIC_API_URL = "http://testserver/omega-hub/v1"
    return settings


def refusal(response):
    if response.status_code not in (404, 503):
        return response.status_code, None
    try:
        return response.status_code, (response.json().get("error") or {}).get("code")
    except (ValueError, AttributeError):
        return response.status_code, None


def grant_maintainer(user):
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="omega_hub", codename="hub_maintainer"
        )
    )


def me_status(client):
    return refusal(client.get(reverse("omega_hub:me")))


def user_publisher_token(person, handle):
    publisher = Publisher.objects.create(
        handle=handle, kind="user", owner_user=person.user, display_name=handle
    )
    return tokens.create_token(person.user, publisher, "cli")


def remove_from(person, organization):
    """Exactly what MemberRemoveAPIView does: deactivate the memberships, keep user.organization."""
    OrganizationMembership.no_workspace_objects.filter(
        user=person.user, organization=organization
    ).update(is_active=False)
    WorkspaceMembership.no_workspace_objects.filter(
        user=person.user, workspace__organization=organization
    ).update(is_active=False)


def join(person, organization, level=Level.MEMBER):
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=person.user,
        organization=organization,
        defaults={
            "role": Level.LEVEL_TO_ORG_STRING[level],
            "level": level,
            "is_active": True,
        },
    )


def a2_only_member(tenants, name):
    """A member of org A whose only active workspace is A2 (denied)."""
    person = make_person(
        name, org_level=Level.MEMBER, organization=tenants.a.organization
    )
    WorkspaceMembership.no_workspace_objects.filter(user=person.user).update(
        is_active=False
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=person.user,
        workspace=tenants.a2,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=OrganizationMembership.no_workspace_objects.get(
            user=person.user, organization=tenants.a.organization
        ),
    )
    person.workspace = tenants.a2
    return person


class TestHubTokensFollowActiveMemberships:
    def test_a2_a_member_removed_from_the_admitted_org_loses_the_hub_by_token(
        self, tenants
    ):
        admit(tenants, p.HUB)
        person = make_person(
            "u-removed", org_level=Level.ADMIN, organization=tenants.a.organization
        )
        _, plaintext = user_publisher_token(person, "u-removed")
        assert me_status(token_client(plaintext))[0] == 200
        remove_from(person, tenants.a.organization)
        join(person, tenants.b.organization)
        person.user.refresh_from_db()
        assert (
            person.user.organization_id == tenants.a.organization.pk
        )  # legacy key kept
        assert me_status(client_for(person)) == (404, p.REASON_NOT_ADMITTED)
        assert me_status(token_client(plaintext)) == (404, p.REASON_NOT_ADMITTED)

    def test_a_token_is_admitted_through_an_active_membership_whatever_the_legacy_key_says(
        self, tenants
    ):
        admit(tenants, p.HUB)
        person = make_person("v-joined", organization=tenants.b.organization)
        join(person, tenants.a.organization, level=Level.ADMIN)
        person.user.refresh_from_db()
        assert person.user.organization_id == tenants.b.organization.pk
        _, plaintext = user_publisher_token(person, "v-joined")
        assert me_status(token_client(plaintext))[0] == 200

    def test_a_token_holder_whose_only_workspace_is_denied_is_refused(self, tenants):
        admit(tenants, p.HUB)  # A admitted, A2 denied
        person = a2_only_member(tenants, "w-a2")
        _, plaintext = user_publisher_token(person, "w-a2")
        assert me_status(client_for(person, tenants.a2)) == (404, p.REASON_NOT_ADMITTED)
        assert me_status(token_client(plaintext)) == (404, p.REASON_NOT_ADMITTED)

    def test_an_org_publisher_token_is_decided_only_in_the_publishers_org(
        self, tenants
    ):
        """The holder admins B (not admitted) and A (admitted). B's publisher token stays dark: an
        org publisher's token never borrows its holder's admission elsewhere. Their own user
        publisher's token is admitted through A."""
        admit(tenants, p.HUB)
        holder = make_person(
            "holder", org_level=Level.ADMIN, organization=tenants.b.organization
        )
        join(holder, tenants.a.organization, level=Level.ADMIN)
        publisher = Publisher.objects.create(
            handle="b-org-pub",
            kind="organization",
            owner_org=tenants.b.organization,
            display_name="B",
        )
        _, org_token = tokens.create_token(holder.user, publisher, "cli")
        _, user_token = user_publisher_token(holder, "holder-own")
        assert me_status(token_client(org_token)) == (404, p.REASON_NOT_ADMITTED)
        assert me_status(token_client(user_token))[0] == 200


def test_a7_an_org_level_api_key_follows_the_workspace_deny(tenants):
    admit(tenants, p.HUB)
    person = a2_only_member(tenants, "k-a2")
    key = OrgApiKey.objects.create(
        organization=tenants.a.organization,
        type="user",
        user=person.user,
        workspace=None,
    )
    entries = reverse("omega_hub:entries")
    assert refusal(client_for(person, tenants.a2).get(entries)) == (
        404,
        p.REASON_NOT_ADMITTED,
    )
    assert refusal(key_client(key.api_key, key.secret_key).get(entries)) == (
        404,
        p.REASON_NOT_ADMITTED,
    )
    # An org-level key is decided for the workspace Future AGI resolves for the request, as a
    # session is: A's owner opens both workspaces, so the key reads in A's own and not in A2.
    owner_key = OrgApiKey.objects.create(
        organization=tenants.a.organization,
        type="user",
        user=tenants.a.user,
        workspace=None,
    )
    owner = key_client(owner_key.api_key, owner_key.secret_key)
    in_a = owner.get(entries, HTTP_X_WORKSPACE_ID=str(tenants.a.workspace.pk))
    assert in_a.status_code == 200, in_a.content
    in_a2 = owner.get(entries, HTTP_X_WORKSPACE_ID=str(tenants.a2.pk))
    assert refusal(in_a2) == (404, p.REASON_NOT_ADMITTED)


def hub_writes():
    """(method, url, view name, hub_action) for every write on every browser-facing Hub route."""
    out = []
    for pattern in hub_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        view = pattern.callback.view_class
        action = pattern.callback.view_initkwargs.get("hub_action", "")
        kwargs = {
            name: (
                "00000000-0000-4000-8000-000000000000"
                if type(conv).__name__ == "UUIDConverter"
                else SAMPLE.get(name, "x")
            )
            for name, conv in pattern.pattern.converters.items()
        }
        url = reverse(f"omega_hub:{pattern.name}", kwargs=kwargs)
        if url.startswith("/omega-hub/internal/"):
            continue
        out.extend(
            (method, url, view.__name__, action)
            for method in WRITES
            if hasattr(view, method)
        )
    return out


class TestPausedHub:
    def test_a4_a_paused_hub_refuses_new_work_and_serves_every_stop_or_takedown(
        self, tenants, hub_settings
    ):
        admit(tenants, p.HUB)
        hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = str(tenants.a.organization.pk)
        grant_maintainer(tenants.a.user)
        ops.kill(p.HUB, actor=tenants.operator.user, reason="incident")
        client = client_for(tenants.a)
        served, refused, wrong = [], [], []
        for method, url, view, action in hub_writes():
            status, code = refusal(getattr(client, method)(url, {}, format="json"))
            stop = (view, method.upper(), action) in STOP_WORK
            paused = (status, code) == (503, p.REASON_PAUSED)
            (served if stop else refused).append(view)
            if stop == paused:
                wrong.append((method.upper(), url, view, status, code))
        assert wrong == []
        assert len(served) == len(STOP_WORK) and len(refused) > 20

    def test_a4_revoking_a_token_and_removing_an_entry_complete_while_paused(
        self, tenants, hub_settings
    ):
        admit(tenants, p.HUB)
        hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = str(tenants.a.organization.pk)
        grant_maintainer(tenants.a.user)
        publisher = Publisher.objects.create(
            handle="a-pub",
            kind="organization",
            owner_org=tenants.a.organization,
            display_name="A",
        )
        token, plaintext = tokens.create_token(tenants.a.user, publisher, "cli")
        entry = Entry.objects.create(publisher=publisher, name="bad", kind="agent")
        ops.kill(p.HUB, actor=tenants.operator.user, reason="incident")
        client = client_for(tenants.a)
        revoked = client.delete(
            reverse("omega_hub:token", kwargs={"token_id": token.pk})
        )
        assert revoked.status_code in (200, 204), revoked.content
        removed = client.post(
            reverse(
                "omega_hub:review-remove",
                kwargs={"publisher": "a-pub", "name": "bad"},
            ),
            {"category": "security issue", "note": "malicious"},
            format="json",
        )
        assert removed.status_code == 200, removed.content
        entry.refresh_from_db()
        assert entry.status == Entry.Status.REMOVED
        assert me_status(token_client(plaintext))[0] in (401, 403)  # the token is gone
        restored = client.post(
            reverse(
                "omega_hub:review-restore",
                kwargs={"publisher": "a-pub", "name": "bad"},
            ),
            {"note": "undo"},
            format="json",
        )
        assert refusal(restored) == (503, p.REASON_PAUSED)  # reopening is new work

    def test_a4_a_paused_hub_can_be_killed_again_to_stop_installs(self, tenants):
        operator = tenants.operator.user
        ops.kill(p.HUB, actor=operator, reason="incident")
        assert not ProductRollout.objects.get(product=p.HUB).installs_paused
        ops.kill(p.HUB, actor=operator, reason="stop installs", pause_installs=True)
        row = ProductRollout.objects.get(product=p.HUB)
        assert row.paused and row.installs_paused
        for pause_installs in (True, False):
            with pytest.raises(ops.RolloutChangeRefused, match="already paused"):
                ops.kill(
                    p.HUB, actor=operator, reason="again", pause_installs=pause_installs
                )
        # pause_installs means nothing outside the Hub: a paused Console is already paused.
        ops.kill(p.CONSOLE, actor=operator, reason="incident")
        with pytest.raises(ops.RolloutChangeRefused, match="already paused"):
            ops.kill(p.CONSOLE, actor=operator, reason="again", pause_installs=True)
        assert RolloutAuditEvent.objects.filter(action="killed").count() == 3
        ops.restore(p.HUB, actor=operator, reason="fixed")
        row.refresh_from_db()
        assert not row.paused and not row.installs_paused
