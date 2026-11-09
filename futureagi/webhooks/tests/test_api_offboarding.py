"""WT-18b (G1 offboarding) through FA's real member-removal routes, each called with the Owner's own API key: a removed,
deactivated, downgraded or deleted member's enabled endpoints turn off with ``creator_removed`` and an audit entry
naming the member; an admin who turns one on becomes its owner. Manage Team's remove from organization deletes
the user row (``Endpoint.owner`` is SET_NULL), and WT-18b also runs on direct ``save()`` and ``update()`` calls.

Which mechanism each route reaches (``webhooks.api_offboarding``):

| FA route | What it does to the rows | Reached by |
|---|---|---|
| ``DELETE /accounts/organization/members/remove/`` | org membership ``save()`` | membership ``post_save`` |
| ``POST /accounts/organization/members/role/`` (workspace revoked) | org membership ``save()`` + ws ``update()`` | membership ``post_save`` |
| ``POST /accounts/organization/members/role/`` (ws level) | ws membership ``update_or_create`` | membership ``post_save`` |
| ``POST /accounts/user/deactivate/``, no other org | memberships ``update()``, ``User.is_active`` ``save()`` | ``User`` ``post_save`` |
| ``POST /accounts/user/deactivate/``, in another org too | memberships ``update()`` only | the sweep, ``reconcile_org`` |
| ``DELETE /accounts/team/users/<id>/`` (no ``?workspace_id``) | ``User.delete()`` | ``User`` ``pre_delete`` |
"""

from __future__ import annotations

import pytest

from accounts.models.audit_log import AuditLog
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.constants.levels import Level
from webhooks.api_offboarding import reconcile_org
from webhooks.models import Endpoint, Policy
from webhooks.tests.api_support import EP, client_for, person, workspace_in
from webhooks.tests.factories import make_endpoint, use_test_keys

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


def _create(client, name="hook") -> str:
    r = client.post(
        EP,
        {
            "name": name,
            "url": "https://hooks.acme.ai/x",
            "subscriptions": [{"event_type": "prompt.*"}],
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    return r.json()["result"]["endpoint"]["id"]


def _state(ep_id):
    ep = Endpoint.objects.get(id=ep_id)
    return ep.status, ep.disabled_reason


def _off_entry(ep_id):
    from webhooks.audit import target_uuid

    return AuditLog.objects.get(
        scope="webhooks",
        action="webhook.endpoint.disabled",
        target_id=target_uuid(ep_id),
        metadata__reason="creator_removed",
    )


def _member(organization, workspace, label, ws_level=Level.WORKSPACE_MEMBER):
    return person(organization, workspace, Level.MEMBER, ws_level, label)


def test_wt18b_member_remove_route_turns_off_and_an_admin_becomes_owner(
    organization, workspace, user, keys
):
    Policy.objects.create(organization=organization, members_can_manage=True)
    member = _member(organization, workspace, "asha")
    ep_id = _create(client_for(member, organization, workspace), "asha-hook")
    kept = _create(client_for(user, organization, workspace), "owners-hook")
    r = client_for(user, organization, workspace).delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(member.id)},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert _state(ep_id) == ("disabled_manual", "creator_removed")
    assert _state(kept) == ("enabled", "")
    assert _off_entry(ep_id).metadata["member_id"] == str(member.id)
    admin = person(organization, workspace, Level.ADMIN, None, "admin")
    r = client_for(admin, organization, workspace).post(
        f"{EP}{ep_id}/enable/", {}, format="json"
    )
    assert r.status_code == 200, r.content
    ep = Endpoint.objects.get(id=ep_id)
    assert (ep.status, ep.owner_id) == ("enabled", admin.id)
    assert (
        AuditLog.objects.filter(
            scope="webhooks", action="webhook.endpoint.owner_changed"
        ).count()
        == 1
    )


def test_wt18b_role_route_revoking_the_workspace_turns_off(
    organization, workspace, user, keys
):
    ws_b = workspace_in(organization, "B", user)
    lead = _member(organization, workspace, "lead", Level.WORKSPACE_ADMIN)
    ep_id = _create(client_for(lead, organization, workspace), "lead-hook")
    r = client_for(user, organization, workspace).post(
        "/accounts/organization/members/role/",
        {
            "user_id": str(lead.id),
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(ws_b.id), "level": Level.WORKSPACE_MEMBER}
            ],
        },
        format="json",
    )
    assert r.status_code == 200, r.content
    assert _state(ep_id) == ("disabled_manual", "creator_removed")


def test_wt18b_role_route_lowering_the_workspace_level_turns_off(
    organization, workspace, user, keys
):
    lead = _member(organization, workspace, "lead2", Level.WORKSPACE_ADMIN)
    ep_id = _create(client_for(lead, organization, workspace), "lead2-hook")
    r = client_for(user, organization, workspace).post(
        "/accounts/organization/members/role/",
        {
            "user_id": str(lead.id),
            "ws_level": Level.WORKSPACE_MEMBER,
            "workspace_id": str(workspace.id),
        },
        format="json",
    )
    assert r.status_code == 200, r.content
    assert _state(ep_id) == ("disabled_manual", "creator_removed")


def test_wt18b_deactivate_route_turns_off_at_once(organization, workspace, user, keys):
    Policy.objects.create(organization=organization, members_can_manage=True)
    member = _member(organization, workspace, "leaver")
    ep_id = _create(client_for(member, organization, workspace), "leaver-hook")
    r = client_for(user, organization, workspace).post(
        "/accounts/user/deactivate/", {"user_id": str(member.id)}, format="json"
    )
    assert r.status_code == 200, r.content
    assert User.objects.get(id=member.id).is_active is False
    assert _state(ep_id) == ("disabled_manual", "creator_removed")
    assert _off_entry(ep_id).metadata["member_id"] == str(member.id)


def test_wt18b_deactivate_route_for_someone_in_another_org_is_caught_by_the_sweep(
    organization, workspace, user, keys
):
    """The account stays active (another org), and FA deactivates the memberships with ``update()``: no signal.
    ``reconcile_org`` catches it; the ``webhooks-health`` pass runs it (WH-1b, a merge gate)."""
    Policy.objects.create(organization=organization, members_can_manage=True)
    member = _member(organization, workspace, "twoorgs")
    other = Organization.objects.create(name="Other org")
    OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=other,
        role=Level.LEVEL_TO_ORG_STRING[Level.MEMBER],
        level=Level.MEMBER,
        is_active=True,
    )
    ep_id = _create(client_for(member, organization, workspace), "twoorgs-hook")
    r = client_for(user, organization, workspace).post(
        "/accounts/user/deactivate/", {"user_id": str(member.id)}, format="json"
    )
    assert r.status_code == 200, r.content
    assert User.objects.get(id=member.id).is_active is True
    assert reconcile_org(organization.id) == [ep_id]
    assert _state(ep_id) == ("disabled_manual", "creator_removed")
    assert reconcile_org(organization.id) == []


def test_wt18b_manage_team_remove_from_org_deletes_the_user_and_turns_off(
    organization, workspace, user, keys
):
    """The user row is deleted and ``Endpoint.owner`` becomes NULL. The endpoint is turned
    off before that, in the deletion's transaction; the entry has no actor (none may point at a deleted row)."""
    Policy.objects.create(organization=organization, members_can_manage=True)
    member = _member(organization, workspace, "teamgone")
    ep_id = _create(client_for(member, organization, workspace), "teamgone-hook")
    off_already = make_endpoint(
        organization, workspace, name="off", owner=member, status="disabled_auto"
    )
    r = client_for(user, organization, workspace).delete(
        f"/accounts/team/users/{member.id}/"
    )
    assert r.status_code == 200, r.content
    assert not User.objects.filter(id=member.id).exists()
    ep = Endpoint.objects.get(id=ep_id)
    assert (ep.status, ep.disabled_reason, ep.owner_id) == (
        "disabled_manual",
        "creator_removed",
        None,
    )
    entry = _off_entry(ep_id)
    assert entry.actor_id is None
    assert entry.metadata["member_id"] == str(member.id)
    assert entry.metadata["account"] == "deleted"
    assert entry.metadata["requested_by"] == str(user.id)
    assert reconcile_org(organization.id) == []
    # Turned on later, an endpoint whose owner is gone gets a new owner, whatever turned it off.
    admin = person(organization, workspace, Level.ADMIN, None, "admin2")
    ca = client_for(admin, organization, workspace)
    for eid in (ep_id, off_already.id):
        assert ca.post(f"{EP}{eid}/enable/", {}, format="json").status_code == 200
        ep = Endpoint.objects.get(id=eid)
        assert (ep.status, ep.owner_id) == ("enabled", admin.id)
    assert reconcile_org(organization.id) == []


def test_wt18b_the_sweep_covers_bulk_updates_and_ownerless_endpoints(
    organization, workspace, user, keys
):
    """``QuerySet.update()`` fires no signal, and an owner row deleted by a path that fires none leaves an enabled
    endpoint with no owner: the sweep turns both off. An endpoint whose owner may still manage it stays on."""
    from accounts.models.workspace import WorkspaceMembership

    lead = _member(organization, workspace, "lead3", Level.WORKSPACE_ADMIN)
    ep = make_endpoint(organization, workspace, name="leads", owner=lead)
    ownerless = make_endpoint(organization, workspace, name="ownerless", owner=None)
    kept = make_endpoint(organization, workspace, name="kept", owner=user)
    WorkspaceMembership.no_workspace_objects.filter(
        user=lead, workspace=workspace
    ).update(is_active=False)
    assert Endpoint.objects.get(id=ep.id).status == "enabled"
    assert sorted(reconcile_org(organization.id)) == sorted([ep.id, ownerless.id])
    for e in (ep, ownerless):
        assert _state(e.id) == ("disabled_manual", "creator_removed")
    assert _off_entry(ownerless.id).metadata["owner"] == "deleted"
    assert Endpoint.objects.get(id=kept.id).status == "enabled"
    assert reconcile_org(organization.id) == []


def test_an_account_deleted_with_its_organization_is_offboarded_elsewhere(
    organization, workspace, keys
):
    """Deleting an org deletes its people (``User.organization`` CASCADE). A person whose home org is deleted, and
    who is a member here, has their endpoints here turned off, and the deletion itself commits: the offboarding writes
    no row for the org being deleted (its endpoints go with it), which would fail the commit on its foreign key.

    The person's home membership is removed first. FA's own ``member.removed`` audit
    (``accounts/signals/membership_signals.py``, ``post_delete``) writes a row for the org of every membership
    deleted, so today an org that still has memberships cannot be deleted at all: the same foreign key fails its
    commit (a finding outside webhooks)."""
    from accounts.models.workspace import WorkspaceMembership

    home = Organization.objects.create(name="Home org")
    member = person(home, None, Level.OWNER, None, "homeowner")
    om = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role=Level.LEVEL_TO_ORG_STRING[Level.MEMBER],
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role=Level.LEVEL_TO_WS_ROLE[Level.WORKSPACE_ADMIN],
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
        organization_membership=om,
    )
    here = make_endpoint(organization, workspace, name="here", owner=member)
    at_home = make_endpoint(home, None, name="at-home", owner=member)
    OrganizationMembership.no_workspace_objects.filter(
        user=member, organization=home
    ).delete()
    Organization.objects.filter(id=home.id).delete()
    assert not User.objects.filter(id=member.id).exists()
    assert not Endpoint.objects.filter(id=at_home.id).exists()
    assert _state(here.id) == ("disabled_manual", "creator_removed")
    assert _off_entry(here.id).organization_id == organization.id
