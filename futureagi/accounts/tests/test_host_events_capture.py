"""READINESS 1.2a on Postgres: every way Future AGI removes access writes an outbox row in the
writer's transaction, the relay turns it into exactly the event EE needs, and nothing leaks between
tenants. Needs the test Postgres (the capture triggers are Postgres-only).

The real endpoints are driven where one exists (member removal, user deactivation); the bulk and
``_base_manager`` paths that fire no Django signal are driven exactly as the product code writes them.
"""

import pytest
from django.db import transaction
from rest_framework import status

from accounts.host_events import relay
from accounts.host_events.interpret import (
    EVENT_MEMBERSHIP_REMOVED,
    EVENT_ROLE_CHANGED,
    EVENT_USER_DEACTIVATED,
)
from accounts.models.host_event_outbox import HostEventOutbox, HostEventStatus
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.member_role_service import update_member_role
from accounts.tests.host_events_support import (
    ReadyConfig,
    RecordingEE,
    make_tenants,
    person,
)
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(user, organization, workspace):
    return make_tenants(user, organization, workspace)


def _drained():
    ee = RecordingEE()
    stats = relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)
    return ee.events, stats


def test_member_removal_endpoint_sends_one_org_removal_for_tenant_a_only(
    auth_client, tenants
):
    response = auth_client.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(tenants["alice"].id)},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    # The view deactivates the org membership with save() and both workspace rows with QuerySet.update().
    rows = list(HostEventOutbox.objects.order_by("id"))
    assert {row.source for row in rows} == {
        "organization_membership",
        "workspace_membership",
    }
    assert all(str(row.user_id) == str(tenants["alice"].id) for row in rows)
    events, stats = _drained()
    assert events == [
        {
            "id": events[0]["id"],
            "type": EVENT_MEMBERSHIP_REMOVED,
            "subject": str(tenants["alice"].id),
            "organizationId": str(tenants["a"].id),
            "workspaceId": None,
            "role": None,
            "occurredAtMs": events[0]["occurredAtMs"],
        }
    ]
    assert stats["delivered"] == 1 and stats["skipped"] == len(rows) - 1
    assert not HostEventOutbox.objects.filter(status=HostEventStatus.PENDING).exists()
    delivered = HostEventOutbox.objects.get(status=HostEventStatus.DELIVERED)
    assert delivered.sent_at is not None and delivered.ee_received_at_ms is not None
    # Tenant B is untouched.
    assert OrganizationMembership.no_workspace_objects.get(
        user=tenants["bob"]
    ).is_active


def test_workspace_revoke_through_the_role_service_removes_that_workspace_only(
    user, tenants
):
    # The role service revokes workspaces left out of workspace_access with one QuerySet.update().
    update_member_role(
        organization=tenants["a"],
        actor=user,
        target_user_id=tenants["alice"].id,
        org_level=Level.MEMBER,
        workspace_access=[
            {"workspace_id": tenants["a_main"].id, "level": Level.WORKSPACE_MEMBER}
        ],
        workspace_access_provided=True,
    )
    events, _ = _drained()
    removals = [event for event in events if event["type"] == EVENT_MEMBERSHIP_REMOVED]
    assert [(event["organizationId"], event["workspaceId"]) for event in removals] == [
        (str(tenants["a"].id), str(tenants["a_side"].id))
    ]


def test_admin_demoted_to_member_is_a_role_change(user, tenants):
    carol = person(tenants["a"], "carol@futureagi.com", Level.ADMIN, [])
    HostEventOutbox.objects.all().delete()
    update_member_role(
        organization=tenants["a"],
        actor=user,
        target_user_id=carol.id,
        org_level=Level.MEMBER,
        workspace_access=[
            {"workspace_id": tenants["a_main"].id, "level": Level.WORKSPACE_MEMBER}
        ],
        workspace_access_provided=True,
    )
    events, _ = _drained()
    assert [(event["type"], event["role"]) for event in events] == [
        (EVENT_ROLE_CHANGED, "member")
    ]


def test_bulk_and_base_manager_paths_are_captured(tenants):
    # rbac_views invite cancel: _base_manager soft delete; workspace_management: plain bulk deactivate.
    OrganizationMembership._base_manager.filter(
        user=tenants["bob"], organization=tenants["b"]
    ).update(is_active=False, deleted=True)
    WorkspaceMembership.no_workspace_objects.filter(
        user=tenants["alice"], workspace=tenants["a_side"]
    ).update(is_active=False)
    events, _ = _drained()
    assert sorted(
        (event["subject"], event["workspaceId"]) for event in events
    ) == sorted(
        [
            (str(tenants["bob"].id), None),
            (str(tenants["alice"].id), str(tenants["a_side"].id)),
        ]
    )


def test_user_deactivation_endpoint_names_the_users_organizations(auth_client, tenants):
    response = auth_client.post(
        "/accounts/user/deactivate/",
        {"user_id": str(tenants["alice"].id)},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    events, _ = _drained()
    deactivations = [
        event for event in events if event["type"] == EVENT_USER_DEACTIVATED
    ]
    assert [event["organizations"] for event in deactivations] == [
        [str(tenants["a"].id)]
    ]
    assert all(event["subject"] == str(tenants["alice"].id) for event in events)


def test_hard_delete_cascades_are_captured(tenants):
    tenants["bob"].delete()
    events, _ = _drained()
    assert [(event["type"], event["organizationId"]) for event in events] == [
        (EVENT_MEMBERSHIP_REMOVED, str(tenants["b"].id))
    ]


def test_a_rolled_back_removal_leaves_no_row(tenants):
    with pytest.raises(RuntimeError), transaction.atomic():
        OrganizationMembership.no_workspace_objects.filter(
            user=tenants["alice"]
        ).update(is_active=False)
        assert HostEventOutbox.objects.count() == 1
        raise RuntimeError("roll back")
    assert HostEventOutbox.objects.count() == 0


def test_unrelated_saves_and_grants_write_nothing_or_send_nothing(tenants):
    membership = OrganizationMembership.no_workspace_objects.get(user=tenants["alice"])
    membership.save(update_fields=["updated_at"])
    tenants["alice"].name = "Alice Renamed"
    tenants["alice"].save()
    assert HostEventOutbox.objects.count() == 0
    # Re-activating a membership is captured but grants access, so nothing is sent.
    OrganizationMembership.no_workspace_objects.filter(user=tenants["alice"]).update(
        is_active=False
    )
    _drained()
    OrganizationMembership.no_workspace_objects.filter(user=tenants["alice"]).update(
        is_active=True
    )
    events, stats = _drained()
    assert events == [] and stats["skipped"] == 1
