"""The merge steps of wave 2 (WH-1b operations + WH-2 API + XP-06 prompt events on one tree), each as a real test on
Postgres. The steps are the two work streams' merge gates:

- G1's offboarding sweep runs in every ``webhooks-health`` pass: WT-18b end to end through FA's real route whose
  removal fires no signal (deactivating a person who is in another org too): turned off, audit entry, the admins'
  "Creator removed" email in the same pass and only once, an admin turns it on and owns it, and the next pass leaves
  it on. The "only once" needs the notice to record the turn-off's own time, which the sweep stamps after the pass
  began (``notices._off_stamp``, found here).
- Every turn-off the system makes writes its audit entry with no actor (section 9.2; WT-12's last clause): 410, three
  unsafe addresses, a send the policy refused. A test delivery answered 410 writes none.
- A redelivered attempt's own row says ``redeliver`` and names who asked (section 4), from WH-2's ledger.
- ``webhooks_api_action`` has its purge rules: retention after a day for writes, tests and bulk redeliveries; a
  ``redeliver`` row goes with its delivery; org deletion.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail

from accounts.models.audit_log import AuditLog
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.constants.levels import Level
from webhooks import health_pass, lifecycle, notices, retention
from webhooks.audit import target_uuid
from webhooks.core.ports import TransportResult
from webhooks.models import (
    ApiAction,
    Attempt,
    Delivery,
    Endpoint,
    EndpointState,
    Event,
    Policy,
)
from webhooks.store import OrmStore
from webhooks.tests.api_support import (
    EP,
    Receiver,
    Worker,
    client_for,
    make,
    person,
    statuses,
    wait_for,
)
from webhooks.tests.api_support import pub as api_pub
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.test_store import Clock, dispatcher, pub

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


def entries(action: str, ep_id: str) -> list[AuditLog]:
    return list(
        AuditLog.objects.filter(
            scope="webhooks", action=action, target_id=target_uuid(ep_id)
        )
    )


def test_wt18b_end_to_end_the_health_pass_sweeps_offboards_and_emails_in_one_pass(
    organization, workspace, user, env
):
    """FA's ``POST /accounts/user/deactivate/`` for a person who is in another org too deactivates their memberships
    with ``update()``: no signal. One health pass turns their endpoint off (``creator_removed``), writes the audit
    entry naming them and sends the admins' email (never to them); an admin turns it on and owns it; the next pass
    changes nothing and sends nothing."""
    Policy.objects.create(organization=organization, members_can_manage=True)
    member = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "asha"
    )
    other = Organization.objects.create(name="Other org")
    OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=other,
        role=Level.LEVEL_TO_ORG_STRING[Level.MEMBER],
        level=Level.MEMBER,
        is_active=True,
    )
    r = client_for(member, organization, workspace).post(
        EP,
        {
            "name": "asha-hook",
            "url": "https://hooks.acme.ai/asha",
            "subscriptions": [{"event_type": "prompt.*"}],
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    ep_id = r.json()["result"]["endpoint"]["id"]
    kept = make_endpoint(organization, workspace, name="owners-hook", owner=user)
    r = client_for(user, organization, workspace).post(
        "/accounts/user/deactivate/", {"user_id": str(member.id)}, format="json"
    )
    assert r.status_code == 200, r.content
    assert User.objects.get(id=member.id).is_active is True
    assert Endpoint.objects.get(id=ep_id).status == "enabled"  # no signal fired
    mail.outbox.clear()

    out = health_pass.run(datetime.now(UTC))

    assert out["offboarding_turned_off"] == 1 and "failed_steps" not in out
    ep = Endpoint.objects.get(id=ep_id)
    assert (ep.status, ep.disabled_reason) == ("disabled_manual", "creator_removed")
    assert Endpoint.objects.get(id=kept.id).status == "enabled"
    (off,) = entries("webhook.endpoint.disabled", ep_id)
    assert off.metadata["reason"] == "creator_removed"
    assert off.metadata["member_id"] == str(member.id)
    assert out["creator_removed_notices"] == 1
    (sent,) = [m for m in mail.outbox if m.subject.startswith("Webhooks turned off:")]
    assert sent.subject == "Webhooks turned off: Asha left Test Workspace"
    assert user.email in sent.to and member.email not in sent.to
    assert "asha-hook" in sent.body
    # Once: the next pass, with the endpoint still off, neither turns it off again nor sends the email again.
    mail.outbox.clear()
    second = health_pass.run(datetime.now(UTC))
    assert (second["offboarding_turned_off"], second["creator_removed_notices"]) == (
        0,
        0,
    )
    assert mail.outbox == []

    admin = person(organization, workspace, Level.ADMIN, None, "admin")
    r = client_for(admin, organization, workspace).post(
        f"{EP}{ep_id}/enable/", {}, format="json"
    )
    assert r.status_code == 200, r.content
    ep = Endpoint.objects.get(id=ep_id)
    assert (ep.status, ep.owner_id) == ("enabled", admin.id)
    assert len(entries("webhook.endpoint.owner_changed", ep_id)) == 1
    mail.outbox.clear()
    again = health_pass.run(datetime.now(UTC))
    assert again["offboarding_turned_off"] == 0
    assert again["creator_removed_notices"] == 0 and mail.outbox == []
    assert Endpoint.objects.get(id=ep_id).status == "enabled"


def test_a_turn_off_stamped_after_the_pass_began_is_noticed_once(
    organization, workspace, user, keys
):
    """The sweep stamps its own time, later than the pass's ``now`` (and a dispatcher may settle a 410 while the
    pass runs). The notice records the turn-off's time, not the earlier ``now``, so the next pass does not send it
    again (found by the end-to-end WT-18b case above at the merge)."""
    ep = make_endpoint(organization, workspace, owner=user, name="late")
    t = datetime.now(UTC)
    off_at = t + timedelta(seconds=5)
    Endpoint.objects.filter(id=ep.id).update(
        status="disabled_auto", disabled_reason="gone_410", disabled_at=off_at
    )
    assert notices.send_turned_off(t) == 1
    assert EndpointState.objects.get(endpoint=ep).last_disabled_notice_at == off_at
    assert notices.send_turned_off(t + timedelta(seconds=60)) == 0
    assert [m.subject for m in mail.outbox].count("Webhook turned off: late") == 1


def test_every_turn_off_the_system_makes_writes_one_audit_entry_with_no_actor(
    organization, workspace, keys
):
    """410 (``gone_410``, last status 410) and three unsafe addresses (``unsafe_address``) write
    ``webhook.endpoint.auto_disabled``; a send the policy refused writes ``webhook.endpoint.blocked_by_policy``. Each
    exactly once (later settles find the endpoint off already), with no actor, in the settle's transaction. A test
    delivery answered 410 turns nothing off and writes nothing."""
    gone = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(3):
        pub(organization, workspace, i)
    d, _ = dispatcher(Clock(), TransportResult(status=410))
    d.tick()
    d.drain()
    gone.refresh_from_db()
    assert (gone.status, gone.disabled_reason) == ("disabled_auto", "gone_410")
    (entry,) = entries("webhook.endpoint.auto_disabled", gone.id)
    assert entry.actor_id is None and entry.organization_id == organization.id
    assert (entry.metadata["reason"], entry.metadata["last_status"]) == (
        "gone_410",
        410,
    )
    assert entry.metadata["actor"] == "system" and entry.metadata["target"] == gone.id

    Endpoint.objects.filter(id=gone.id).update(deleted=True)
    unsafe = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(3, 6):
        pub(organization, workspace, i)
    d, tr = dispatcher(Clock(), TransportResult(status=200), answers=("10.0.0.7",))
    d.tick()
    d.drain()
    unsafe.refresh_from_db()
    assert (unsafe.status, unsafe.disabled_reason) == (
        "disabled_auto",
        "unsafe_address",
    )
    (entry,) = entries("webhook.endpoint.auto_disabled", unsafe.id)
    assert (entry.actor_id, entry.metadata["reason"]) == (None, "unsafe_address")
    assert entry.metadata["last_outcome"] == "ssrf_blocked"
    assert "10.0.0.7" not in str(entry.metadata) and tr.requests == []

    Endpoint.objects.filter(id=unsafe.id).update(deleted=True)
    blocked = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(6, 9):
        pub(organization, workspace, i)
    Policy.objects.create(organization=organization, allowed_hosts=["*.other.ai"])
    d, _ = dispatcher(Clock(), TransportResult(status=200))
    d.tick()
    d.drain()
    blocked.refresh_from_db()
    assert (blocked.status, blocked.disabled_reason) == ("blocked_by_policy", "policy")
    (entry,) = entries("webhook.endpoint.blocked_by_policy", blocked.id)
    assert entry.actor_id is None and entry.metadata["reason"] == "policy"
    assert entry.changes == {
        "status": {"before": "enabled", "after": "blocked_by_policy"}
    }
    assert entries("webhook.endpoint.auto_disabled", blocked.id) == []

    Policy.objects.filter(organization=organization).delete()
    tested = make_endpoint(organization, workspace)
    Event.objects.create(
        id="evt_test_01JBF3Q7Z8X4N6R2T5V9W0YB1D",
        organization=organization,
        workspace=workspace,
        type="prompt.label.changed",
        occurred_at=T0,
        producer="webhooks.test",
        source_key="test-merge-1",
        source_hash="h",
        data=label_event_data(),
        is_test=True,
        test_endpoint=tested,
    )
    d, _ = dispatcher(Clock(), TransportResult(status=410))
    d.tick()
    d.drain()
    tested.refresh_from_db()
    assert tested.status == "enabled"
    assert (
        AuditLog.objects.filter(
            scope="webhooks", target_id=target_uuid(tested.id)
        ).count()
        == 0
    )


def test_a_redelivered_attempt_row_says_redeliver_and_names_who_asked(
    env, owner, organization, workspace, user
):
    """Section 4: ``trigger`` is ``auto``, ``redeliver`` or ``test``, with ``actor_id`` for a redelivery. The worker
    writes the attempt row; the claim reads WH-2's ledger for the attempt number and the settle names the person."""
    rcv = Receiver(answers=[(413, b"too large"), (200, b"ok")])
    try:
        ep_id, _secret = make(
            owner, rcv.url, subscriptions=[{"event_type": "prompt.label.changed"}]
        )
        api_pub(organization, workspace, "r1")
        with Worker() as wk:
            assert wait_for(lambda: statuses(ep_id) == ["failed"], 30)
            first = Delivery.objects.get(endpoint_id=ep_id)
            r = owner.post(f"/webhooks/deliveries/{first.id}/redeliver/")
            assert r.status_code == 202, r.content
            assert wait_for(lambda: statuses(ep_id) == ["succeeded"], 30)
        assert wk.errors == []
    finally:
        rcv.close()
    rows = list(
        Attempt.objects.filter(delivery_id=first.id)
        .order_by("n")
        .values_list("n", "trigger", "actor_id", "response_status")
    )
    assert rows == [(1, "auto", None, 413), (2, "redeliver", user.id, 200)]
    detail = owner.get(f"/webhooks/deliveries/{first.id}/").json()["result"]
    assert [(a["n"], a["trigger"], a["actor_id"]) for a in detail["attempts"]] == [
        (1, "auto", None),
        (2, "redeliver", str(user.id)),
    ]


def test_the_api_ledger_has_its_purge_rules(organization, workspace, keys):
    """Retention deletes write, test and bulk-redelivery ledger rows after a day and keeps the newer ones; a
    ``redeliver`` row stays while its delivery does and goes with it, without counting as a delivery; org deletion
    purges the rest."""
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100)
    pub(organization, workspace, 1)
    OrmStore().fan_out(int(time.time() * 1000), 10)
    dl = Delivery.objects.get(endpoint=ep)
    now = datetime.now(UTC)

    def row(kind, age, **kw):
        a = ApiAction.objects.create(
            organization=organization, workspace=workspace, kind=kind, endpoint=ep, **kw
        )
        ApiAction.objects.filter(id=a.id).update(created_at=now - age)
        return a.id

    old = [row(k, timedelta(days=2)) for k in ("write", "test", "bulk_redeliver")]
    new = [row(k, timedelta(hours=1)) for k in ("write", "test", "bulk_redeliver")]
    redeliver = row("redeliver", timedelta(days=2), delivery=dl, attempt_n=2)

    assert retention.purge_api_ledger(now - timedelta(days=1)) == 3
    left = set(ApiAction.objects.values_list("id", flat=True))
    assert left == {*new, redeliver} and not left & set(old)
    out = retention.run(now)
    assert out["api_ledger"] == 0

    counts = retention.delete_events([dl.event_id])
    assert (counts["deliveries"], counts["events"]) == (1, 1)
    assert set(ApiAction.objects.values_list("id", flat=True)) == set(new)
    assert lifecycle.uncovered_tables() == []
    purged = lifecycle.purge_organization(organization.id)
    assert purged["webhooks_api_action"] == 3
    assert not ApiAction.objects.filter(organization=organization).exists()
