"""Test doubles for the FA -> Omega EE event channel (READINESS lane 1.2a). Not collected by pytest."""

import json
import time

from accounts.host_events.config import READY, EventChannelConfig
from accounts.host_events.transport import SendResult
from accounts.models.host_event_outbox import HostEventOutbox
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import set_workspace_context


def ReadyConfig(
    url="https://ee.test/host/events",
):  # noqa: N802 - reads like a constant at call sites
    return EventChannelConfig(
        state=READY,
        issuer="https://futureagi.test",
        url=url,
        key_id="fa-1",
        secret=b"t" * 40,
    )


class RecordingEE:
    """Stands in for transport.send_batch: records every event it is sent.

    ``fail`` is a list of error codes consumed one per call (``"ee_unreachable"``...);
    while it is non-empty the call delivers nothing.
    """

    def __init__(self, fail=None, refuse_ids=()):
        self.events = []
        self.bodies = []
        self.calls = 0
        self.fail = list(fail or [])
        self.refuse_ids = set(refuse_ids)

    def send(self, config, body, event_ids):
        self.calls += 1
        self.bodies.append(body)
        if self.fail:
            return SendResult(frozenset(), self.fail.pop(0))
        document = json.loads(body)
        accepted = [
            event for event in document["events"] if event["id"] not in self.refuse_ids
        ]
        self.events.extend(accepted)
        missing = set(event_ids) - {event["id"] for event in accepted}
        return SendResult(
            frozenset(event["id"] for event in accepted),
            "ee_event_refused" if missing else "",
            int(time.time() * 1000),
        )


def person(organization, email, org_level, workspaces):
    person = User.objects.create_user(
        email=email,
        password="disposable-pass-1",
        name=email,
        organization=organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person,
        organization=organization,
        role="Member",
        level=org_level,
        is_active=True,
    )
    for workspace, level in workspaces:
        WorkspaceMembership.no_workspace_objects.create(
            workspace=workspace,
            user=person,
            role="Workspace Member",
            level=level,
            is_active=True,
            organization_membership=membership,
        )
    return person


def make_tenants(user, organization, workspace):
    """Tenant A (the conftest owner's org, two workspaces) and tenant B, each with a member."""
    side = Workspace.objects.create(
        name="A side",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    alice = person(
        organization,
        "alice@futureagi.com",
        Level.MEMBER,
        [(workspace, Level.WORKSPACE_MEMBER), (side, Level.WORKSPACE_MEMBER)],
    )
    other_org = Organization.objects.create(name="Tenant B")
    b_main = Workspace.objects.create(
        name="B main",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    bob = person(
        other_org, "bob@futureagi.com", Level.MEMBER, [(b_main, Level.WORKSPACE_MEMBER)]
    )
    set_workspace_context(workspace=workspace, organization=organization, user=user)
    HostEventOutbox.objects.all().delete()
    return {
        "a": organization,
        "a_main": workspace,
        "a_side": side,
        "alice": alice,
        "b": other_org,
        "b_main": b_main,
        "bob": bob,
    }
