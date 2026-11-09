"""READINESS lane 3.22 on Postgres: deleting a workspace or an organization writes an outbox
row in the deleting transaction on every path, the relay sends ``workspace.deleted`` or
``org.deleted`` in a body of its own, keeps asking while EE answers ``pending``, and records
EE's receipt in ``audit_log`` (workspace) and ``OmegaPurgeReceipt`` (both). Deactivating a user
ends their Future AGI sessions on every path. Two tenants throughout. Needs the test Postgres
(the triggers are Postgres-only).
"""

import json
import time

import pytest
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import pre_delete
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import decode_token, generate_encrypted_message
from accounts.host_events import relay
from accounts.host_events.transport import SendResult
from accounts.lifecycle.interpret import EVENT_ORG_DELETED, EVENT_WORKSPACE_DELETED
from accounts.lifecycle.receipts import ACTION_WORKSPACE_PURGED, RECEIPT_SCHEMA
from accounts.models.audit_log import AuditLog
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.host_event_outbox import HostEventOutbox, HostEventStatus
from accounts.models.omega_purge_receipt import OmegaPurgeReceipt
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from accounts.tests.host_events_support import ReadyConfig, make_tenants

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(user, organization, workspace):
    return make_tenants(user, organization, workspace)


class PurgingEE:
    """Stands in for transport.send_batch: answers each lifecycle event ``pending`` for its
    first ``pending_rounds`` deliveries, then ``applied`` with a receipt for that exact event.
    """

    def __init__(self, pending_rounds=1):
        self.pending_rounds = pending_rounds
        self.bodies = []
        self.asked = {}

    def send(self, config, body, event_ids):
        document = json.loads(body)
        self.bodies.append(document["events"])
        outcomes, receipts, delivered = {}, {}, set()
        for event in document["events"]:
            if event["type"] not in (EVENT_WORKSPACE_DELETED, EVENT_ORG_DELETED):
                outcomes[event["id"]] = "applied"
                delivered.add(event["id"])
                continue
            asked = self.asked[event["id"]] = self.asked.get(event["id"], 0) + 1
            if asked <= self.pending_rounds:
                continue
            delivered.add(event["id"])
            outcomes[event["id"]] = "applied"
            receipts[event["id"]] = receipt_for(event)
        missing = set(event_ids) - delivered
        return SendResult(
            frozenset(delivered),
            "ee_purge_pending" if missing else "",
            int(time.time() * 1000),
            outcomes,
            receipts,
        )


def receipt_for(event):
    return {
        "schemaVersion": RECEIPT_SCHEMA,
        "purgeId": "purge_test_0001",
        "eventId": event["id"],
        "kind": event["type"],
        "hostOrgId": event["organizationId"],
        "hostWorkspaceId": event["workspaceId"],
        "requestedAtMs": event["occurredAtMs"],
        "acceptedAtMs": event["occurredAtMs"] + 10,
        "completedAtMs": event["occurredAtMs"] + 900,
        "tenants": 1,
        "bindingsClosed": 2,
        "stores": {
            "agents": {"erased": 0, "retired": 2, "retained": ["kept by design"]},
            "sandboxes": {"erased": 3, "retired": 0, "retained": []},
            "deployments": {"erased": 1, "retired": 0, "retained": []},
        },
        "audit": [{"entryId": "wa_0123456789abcdef_0", "hash": "c" * 64}],
    }


def _drain(ee):
    # Every row is due now: the test's clock does not wait out the backoff.
    HostEventOutbox.objects.filter(status=HostEventStatus.PENDING).update(
        next_attempt_at=timezone.now(), lease_until=None
    )
    return relay.drain(config=ReadyConfig(), send=ee.send, purge_old=False)


def _lifecycle_rows():
    return list(
        HostEventOutbox.objects.filter(
            source__in=("workspace", "organization")
        ).order_by("id")
    )


def test_the_workspace_delete_endpoint_purges_in_ee_and_audits_the_receipt(
    auth_client, tenants
):
    response = auth_client.delete(f"/accounts/workspaces/{tenants['a_side'].id}/")
    assert response.status_code == status.HTTP_200_OK, response.json()
    rows = _lifecycle_rows()
    assert [(row.source, row.workspace_id) for row in rows] == [
        ("workspace", tenants["a_side"].id)
    ]

    ee = PurgingEE(pending_rounds=1)
    first = _drain(ee)
    # EE is still purging: the row stays pending, quietly, and nothing is audited yet.
    rows[0].refresh_from_db()
    assert rows[0].status == HostEventStatus.PENDING
    assert rows[0].last_error == "ee_purge_pending"
    assert first["retrying"] >= 1
    assert not OmegaPurgeReceipt.objects.exists()
    _drain(ee)
    rows[0].refresh_from_db()
    assert rows[0].status == HostEventStatus.DELIVERED

    # The lifecycle event travelled in a body of its own; the members' removals in another.
    lifecycle_bodies = [
        b for b in ee.bodies if any(e["type"] == EVENT_WORKSPACE_DELETED for e in b)
    ]
    assert all(len(body) == 1 for body in lifecycle_bodies)
    sent = lifecycle_bodies[-1][0]
    assert sent == {
        "id": str(rows[0].event_id),
        "occurredAtMs": sent["occurredAtMs"],
        "type": EVENT_WORKSPACE_DELETED,
        "subject": None,
        "organizationId": str(tenants["a"].id),
        "workspaceId": str(tenants["a_side"].id),
        "role": None,
    }
    receipt = OmegaPurgeReceipt.objects.get()
    assert (receipt.kind, receipt.outcome, receipt.workspace_id) == (
        EVENT_WORKSPACE_DELETED,
        "purged",
        tenants["a_side"].id,
    )
    assert receipt.receipt["stores"]["sandboxes"]["erased"] == 3
    audit = AuditLog.objects.get(action=ACTION_WORKSPACE_PURGED)
    assert audit.organization_id == tenants["a"].id
    assert audit.target_id == tenants["a_side"].id
    assert audit.actor_id is None
    assert audit.changes["stores"]["agents"] == {
        "erased": 0,
        "retired": 2,
        "retained": ["kept by design"],
    }
    assert audit.metadata["purgeId"] == "purge_test_0001"
    # Delivered once; asking again changes nothing.
    _drain(ee)
    assert OmegaPurgeReceipt.objects.count() == 1
    assert AuditLog.objects.filter(action=ACTION_WORKSPACE_PURGED).count() == 1
    # Tenant B was never named.
    assert all(
        e["organizationId"] != str(tenants["b"].id) for b in ee.bodies for e in b
    )


def test_every_soft_delete_path_is_captured_and_nothing_else_is(tenants):
    side = tenants["a_side"]
    # A bulk update of the deleted flag (no signal) is captured like the endpoint's soft delete.
    Workspace.no_workspace_objects.filter(id=side.id).update(deleted=True)
    assert [row.workspace_id for row in _lifecycle_rows()] == [side.id]
    HostEventOutbox.objects.all().delete()
    # Renaming a live workspace, or changing an already deleted one (even re-activating it),
    # writes nothing: EE already has its deletion.
    Workspace.no_workspace_objects.filter(id=tenants["a_main"].id).update(
        name="renamed"
    )
    Workspace.all_objects.filter(id=side.id).update(deleted=False, is_active=False)
    Workspace.all_objects.filter(id=side.id).update(is_active=True)
    assert _lifecycle_rows() == []
    # Hard-deleting a live workspace (an admin, a cascade) is captured too.
    Workspace.all_objects.filter(id=side.id).delete()
    assert [(row.operation, row.workspace_id) for row in _lifecycle_rows()] == [
        ("DELETE", side.id)
    ]


def test_a_rolled_back_deletion_leaves_no_row(tenants):
    with pytest.raises(RuntimeError), transaction.atomic():
        Workspace.no_workspace_objects.filter(id=tenants["a_side"].id).update(
            is_active=False
        )
        raise RuntimeError("abort")
    assert _lifecycle_rows() == []


def test_deleting_an_organization_sends_one_org_deletion_and_keeps_its_receipt(tenants):
    b = tenants["b"]
    Organization.objects.filter(id=b.id).delete()
    sources = sorted({row.source for row in HostEventOutbox.objects.all()})
    assert "organization" in sources and "workspace" in sources
    ee = PurgingEE(pending_rounds=0)
    _drain(ee)
    lifecycle = [
        e
        for body in ee.bodies
        for e in body
        if e["type"] in (EVENT_ORG_DELETED, EVENT_WORKSPACE_DELETED)
    ]
    # Django's cascade deleted B's workspace first; the org deletion covers it.
    assert [(e["type"], e["organizationId"]) for e in lifecycle] == [
        (EVENT_ORG_DELETED, str(b.id))
    ]
    receipt = OmegaPurgeReceipt.objects.get()
    assert (receipt.kind, receipt.organization_id, receipt.workspace_id) == (
        EVENT_ORG_DELETED,
        b.id,
        None,
    )
    # Its audit_log went with the organization; the receipt row outlives it.
    assert not AuditLog.objects.filter(organization_id=b.id).exists()
    assert all(
        row.status != HostEventStatus.PENDING for row in HostEventOutbox.objects.all()
    )


def test_ignored_is_recorded_as_ignored_and_a_bad_receipt_stays_pending(tenants):
    Workspace.no_workspace_objects.filter(id=tenants["a_side"].id).update(
        is_active=False
    )
    row = _lifecycle_rows()[0]

    def bad(config, body, event_ids):
        event = json.loads(body)["events"][0]
        wrong = {**receipt_for(event), "hostWorkspaceId": str(tenants["a_main"].id)}
        return SendResult(
            frozenset(event_ids),
            "",
            1,
            {event_ids[0]: "applied"},
            {event_ids[0]: wrong},
        )

    _drain(SimpleSend(bad))
    row.refresh_from_db()
    assert (row.status, row.last_error) == (
        HostEventStatus.PENDING,
        "ee_receipt_invalid",
    )
    assert not OmegaPurgeReceipt.objects.exists()

    def ignored(config, body, event_ids):
        return SendResult(frozenset(event_ids), "", 1, {event_ids[0]: "ignored"}, {})

    _drain(SimpleSend(ignored))
    row.refresh_from_db()
    assert row.status == HostEventStatus.DELIVERED
    receipt = OmegaPurgeReceipt.objects.get()
    assert (receipt.outcome, receipt.receipt, receipt.completed_at) == (
        "ignored",
        {},
        None,
    )
    assert (
        AuditLog.objects.get(action=ACTION_WORKSPACE_PURGED).changes["outcome"]
        == "ignored"
    )


class SimpleSend:
    def __init__(self, send):
        self.send = send


def test_a_pending_purge_is_its_own_backlog_and_alerts_at_45_minutes(tenants):
    Workspace.no_workspace_objects.filter(id=tenants["a_side"].id).update(
        is_active=False
    )
    HostEventOutbox.objects.update(
        captured_at=timezone.now() - timezone.timedelta(minutes=50)
    )
    assert relay.backlog_seconds() == 0
    assert relay.backlog_seconds(purges=True) > 45 * 60
    stats = relay.drain(
        config=ReadyConfig(), send=PurgingEE(pending_rounds=5).send, purge_old=False
    )
    assert stats["purge_backlog_seconds"] > relay.PURGE_ALERT_SECONDS


def _cached_session(person):
    tokens = {}
    for kind in (AuthTokenType.ACCESS.value, AuthTokenType.REFRESH.value):
        tokens[kind] = AuthToken.objects.create(
            user=person, auth_type=kind, is_active=True, last_used_at=timezone.now()
        )
    access = tokens[AuthTokenType.ACCESS.value]
    encrypted = generate_encrypted_message(
        {"user_id": str(person.id), "id": str(access.id)}
    )
    cache.set(
        f"access_token_{access.id}", {"token": encrypted, "user": person}, timeout=600
    )
    return encrypted, tokens


def test_deactivating_a_user_on_any_path_ends_their_sessions(tenants):
    alice, bob = tenants["alice"], tenants["bob"]
    alice_token, alice_rows = _cached_session(alice)
    bob_token, _ = _cached_session(bob)
    assert decode_token(alice_token)[0].id == alice.id
    # A bulk update fires no signal and clears no cache: the trigger and the token check still end it.
    User.objects.filter(id=alice.id).update(is_active=False)
    for row in alice_rows.values():
        row.refresh_from_db()
        assert row.is_active is False
    with pytest.raises(AuthenticationFailed):
        decode_token(alice_token)
    assert (
        cache.get(f"access_token_{alice_rows[AuthTokenType.ACCESS.value].id}") is None
    )
    # Tenant B's cached session is untouched and still stamps its use.
    before = AuthToken.objects.get(
        user=bob, auth_type=AuthTokenType.ACCESS.value
    ).last_used_at
    assert decode_token(bob_token)[0].id == bob.id
    assert (
        AuthToken.objects.get(
            user=bob, auth_type=AuthTokenType.ACCESS.value
        ).last_used_at
        >= before
    )
    # Re-activation does not revive an old session: the person signs in again.
    User.objects.filter(id=alice.id).update(is_active=True)
    assert not AuthToken.objects.filter(user=alice, is_active=True).exists()


@pytest.mark.django_db(transaction=True)
def test_a_failed_org_deletion_does_not_mute_later_member_audits(tenants):
    """The cascade raises after the organization's pre_delete marked it as being deleted, so its
    post_delete never runs; a later removal in that organization (another transaction) is audited.
    """
    b = tenants["b"]

    def fail(sender, instance, **kwargs):
        raise RuntimeError("the deletion fails mid-cascade")

    pre_delete.connect(fail, sender=Organization, dispatch_uid="lifecycle-test-fail")
    try:
        with pytest.raises(RuntimeError), transaction.atomic():
            Organization.objects.get(id=b.id).delete()
    finally:
        pre_delete.disconnect(sender=Organization, dispatch_uid="lifecycle-test-fail")
    assert Organization.objects.filter(id=b.id).exists()
    OrganizationMembership.no_workspace_objects.get(
        user=tenants["bob"], organization=b
    ).delete()
    assert AuditLog.objects.filter(
        organization_id=b.id, action="member.removed", target_id=tenants["bob"].id
    ).exists()
