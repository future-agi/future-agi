"""Endpoint health, notices and the backlog cap on Postgres (DESIGN.md sections 7.7 and 17: WT-12 and the R8 cap).
The ops alerts are ``test_ops_alerts``.

WT-12 runs the real dispatcher loop against a real receiver with FI_WEBHOOKS_TIME_SCALE semantics (the scale is handed
to the store, the dispatcher and the health pass, as the activity does when the guard allows it). The emails go
through ``email_helper`` to Django's test mail sink.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail
from django.db import close_old_connections, transaction

from webhooks import health_pass
from webhooks.core.fanout import BACKLOG_CAP
from webhooks.models import (
    Delivery,
    Endpoint,
    EndpointState,
    Secret,
)
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.store_settle import disable_endpoint
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Receiver, real_dispatcher


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def now():
    return datetime.now(UTC)


def pub(org, ws, keys_):
    with transaction.atomic():
        for k in keys_:
            publish(
                type="prompt.label.changed",
                organization_id=org.id,
                workspace_id=ws.id,
                data=label_event_data(),
                source_key=k,
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )


def auto_disabled_entries(ep_id):
    """The ``webhook.endpoint.auto_disabled`` audit entries of one endpoint (section 9.2)."""
    from accounts.models.audit_log import AuditLog
    from webhooks.audit import target_uuid

    return list(
        AuditLog.objects.filter(
            scope="webhooks",
            action="webhook.endpoint.auto_disabled",
            target_id=target_uuid(ep_id),
        )
    )


def _loop(d, stop, seconds):
    try:
        d.run(stop, run_for_s=seconds)
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
def test_wt12_auto_disable_after_24h_scaled_with_failing_and_off_emails(
    organization, workspace, user, keys, receiver
):
    """Scale 0.0001 (24 h = 8.64 s, 10 min = 60 ms). E1 is down for "24 h": a Failing email after 3 attempts over
    "10 min", then turned off ("failing") with an Off email, its backlog paused. E2 is down for "6 h" (2.16 s), then
    answers: never turned off, its delivery succeeds, its health is reset."""
    scale = 0.0001
    e1 = make_endpoint(
        organization, workspace, url=receiver.url("/fail"), owner=user, name="down-24h"
    )
    e2 = make_endpoint(
        organization,
        workspace,
        url=receiver.url("/fail-for/2160"),
        owner=user,
        name="down-6h",
    )
    pub(organization, workspace, ["k1", "k2"])
    receiver.t0 = time.monotonic()
    d = real_dispatcher(receiver, scale=scale)
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(d, stop, 14.0), daemon=True)
    t.start()
    while t.is_alive():
        health_pass.run(now(), scale)
        time.sleep(0.2)
    t.join()
    health_pass.run(now(), scale)

    e1.refresh_from_db()
    e2.refresh_from_db()
    st1 = EndpointState.objects.get(endpoint=e1)
    st2 = EndpointState.objects.get(endpoint=e2)

    def state(ep, st):
        return {
            "status": ep.status,
            "disabled_reason": ep.disabled_reason,
            "disabled_at": ep.disabled_at,
            "consecutive_failed_attempts": st.consecutive_failed_attempts,
            "failing_since": st.failing_since,
            "last_success_at": st.last_success_at,
            "deliveries": evidence.by_status(Delivery.objects.filter(endpoint=ep)),
        }

    evidence.write(
        "WT-12",
        scale=scale,
        scaled={"24 h": "8.64 s", "6 h": "2.16 s", "10 min": "60 ms"},
        db={"down_24h": state(e1, st1), "down_6h": state(e2, st2)},
        emails=[{"subject": m.subject, "recipients": len(m.to)} for m in mail.outbox],
        receiver=evidence.receiver_lines(receiver, limit=15),
        audit=[
            {"actor_id": a.actor_id, "metadata": a.metadata}
            for a in auto_disabled_entries(e1.id)
        ],
    )
    assert (e1.status, e1.disabled_reason) == ("disabled_auto", "failing")
    # The audit entry (section 9.2): one per turn-off, no actor, the rule that fired, last status, failing since.
    (entry,) = auto_disabled_entries(e1.id)
    assert entry.actor_id is None and entry.metadata["actor"] == "system"
    assert entry.metadata["reason"] == "failing"
    assert entry.metadata["failing_since"] == st1.failing_since.isoformat()
    assert entry.metadata["last_outcome"] == st1.last_outcome
    assert auto_disabled_entries(e2.id) == []
    assert st1.consecutive_failed_attempts >= 10
    assert (e1.disabled_at - st1.failing_since).total_seconds() >= 8.64
    assert set(
        Delivery.objects.filter(endpoint=e1).values_list("status", flat=True)
    ) <= {
        "paused",
        "failed",
    }
    assert e2.status == "enabled"
    assert st2.consecutive_failed_attempts == 0 and st2.last_success_at is not None
    assert set(
        Delivery.objects.filter(endpoint=e2).values_list("status", flat=True)
    ) == {"succeeded"}

    failing = [m for m in mail.outbox if m.subject == "Webhook failing: down-24h"]
    off = [m for m in mail.outbox if m.subject == "Webhook turned off: down-24h"]
    assert 1 <= len(failing) <= 2, [
        m.subject for m in mail.outbox
    ]  # at most once per "24 h"
    assert len(off) == 1
    assert off[0].to == [user.email]
    assert "24 hours" in off[0].body
    assert not [m for m in mail.outbox if m.subject == "Webhook turned off: down-6h"]
    for m in mail.outbox:  # never a receiver body or an address
        assert "127.0.0.1" not in m.body and '{"ok":true}' not in m.body
        assert "93.184.215.14" not in m.body


@pytest.mark.django_db(transaction=True)
def test_the_24h_rule_turns_off_an_endpoint_no_attempt_settles_on(
    organization, workspace, keys
):
    ep = make_endpoint(organization, workspace)
    pub(organization, workspace, ["k1"])
    OrmStore().fan_out(int(time.time() * 1000), 500)
    t = now()
    EndpointState.objects.filter(endpoint=ep).update(
        consecutive_failed_attempts=10,
        failing_since=t - timedelta(hours=25),
        last_failure_at=t - timedelta(hours=1),
        last_outcome="http_error",
    )
    assert health_pass.run(t)["turned_off_idle"] == 1
    ep.refresh_from_db()
    assert (ep.status, ep.disabled_reason) == ("disabled_auto", "failing")
    (entry,) = auto_disabled_entries(ep.id)
    assert (entry.actor_id, entry.metadata["reason"]) == (None, "failing")
    assert entry.metadata["last_outcome"] == "http_error"
    assert Delivery.objects.get(endpoint=ep).status == "paused"
    # Nine failures, or a first failure 23 h old, are not enough.
    other = make_endpoint(organization, workspace)
    EndpointState.objects.filter(endpoint=other).update(
        consecutive_failed_attempts=10, failing_since=t - timedelta(hours=23)
    )
    assert health_pass.disable_idle_failing(t) == 0


@pytest.mark.django_db(transaction=True)
def test_slow_lane_recheck_after_30_minutes(organization, workspace, keys):
    calm = make_endpoint(organization, workspace)
    still = make_endpoint(organization, workspace)
    t = now()
    ms = int(t.timestamp() * 1000)
    EndpointState.objects.filter(endpoint=calm).update(
        lane="slow", slow_until=t - timedelta(seconds=1), recent_timeouts_at=[]
    )
    EndpointState.objects.filter(endpoint=still).update(
        lane="slow",
        slow_until=t - timedelta(seconds=1),
        recent_timeouts_at=[ms - 60_000, ms - 30_000, ms - 1000],
    )
    assert health_pass.recheck_slow_lane(t) == 1
    c = EndpointState.objects.get(endpoint=calm)
    s = EndpointState.objects.get(endpoint=still)
    assert (c.lane, c.slow_until) == ("normal", None)
    assert s.lane == "slow" and s.slow_until > t + timedelta(minutes=29)


@pytest.mark.django_db(transaction=True)
def test_backlog_cap_holds_at_claim_time_and_after_a_turn_off(
    organization, workspace, keys
):
    """R8: 10,000 paused per endpoint; past it, deliveries are skipped with ``backlog_cap`` and keep their numbers."""
    off = make_endpoint(organization, workspace)
    on = make_endpoint(organization, workspace)
    pub(organization, workspace, ["k1", "k2", "k3"])
    store = OrmStore()
    store.fan_out(int(time.time() * 1000), 500)
    # Claim time: the endpoint went off after fan-out; one place left under the cap.
    Endpoint.objects.filter(id=off.id).update(status="disabled_manual")
    EndpointState.objects.filter(endpoint=off).update(paused_count=BACKLOG_CAP - 1)
    EndpointState.objects.filter(endpoint=on).update(paused_count=BACKLOG_CAP - 2)
    Endpoint.objects.filter(id=on.id).update(rate_limit_per_s=1, max_in_flight=1)
    store.claim_due(int(time.time() * 1000), "normal", 48, 16)
    got = sorted(
        Delivery.objects.filter(endpoint=off).values_list(
            "seq", "status", "skip_reason"
        )
    )
    assert got == [
        (1, "paused", ""),
        (2, "skipped", "backlog_cap"),
        (3, "skipped", "backlog_cap"),
    ]
    assert EndpointState.objects.get(endpoint=off).paused_count == BACKLOG_CAP
    # After a turn-off: 2 places left for the 2 still-due deliveries of `on` (one is leased).
    Delivery.objects.filter(endpoint=on, status="sending").update(
        status="pending", lease_token=None
    )
    assert disable_endpoint(on.id, "failing", now()) == 2
    statuses = sorted(
        Delivery.objects.filter(endpoint=on).values_list("status", flat=True)
    )
    assert statuses == ["paused", "paused", "skipped"]
    assert EndpointState.objects.get(endpoint=on).paused_count == BACKLOG_CAP


@pytest.mark.django_db(transaction=True)
def test_previous_secrets_expire_and_lose_their_ciphertext(
    organization, workspace, keys
):
    ep = make_endpoint(organization, workspace)
    old = Secret.objects.get(endpoint=ep)
    Secret.objects.filter(id=old.id).update(
        state="previous", expires_at=now() - timedelta(seconds=1)
    )
    assert health_pass.expire_previous_secrets(now()) == 1
    old.refresh_from_db()
    assert (old.state, old.secret_encrypted) == ("revoked", None)
    assert old.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_a_retry_whose_endpoint_was_turned_off_in_flight_is_paused_at_its_settle(
    organization, workspace, keys
):
    from webhooks.core.attempt import AttemptRecord, next_action_for

    ep = make_endpoint(organization, workspace, rate_limit_per_s=100)
    gone = make_endpoint(organization, workspace, rate_limit_per_s=100)
    pub(organization, workspace, ["k1"])
    store = OrmStore()
    store.fan_out(int(time.time() * 1000), 500)
    claims = {
        c.endpoint.id: c
        for c in store.claim_due(int(time.time() * 1000), "normal", 48, 16)
    }
    # While the attempts are in flight, one endpoint is turned off and the other deleted.
    assert (
        disable_endpoint(ep.id, "manual", now()) == 0
    )  # its delivery is leased: pause_backlog skips it
    Endpoint.objects.filter(id=gone.id).update(deleted=True)
    for c in claims.values():
        t = int(time.time() * 1000)
        rec = AttemptRecord(
            n=c.attempt_n,
            trigger="auto",
            started_at_ms=t,
            settled_at_ms=t,
            outcome="http_error",
            response_status=503,
        )
        action = next_action_for(c, rec)
        assert action["action"] == "retry"
        assert store.settle(c, rec.__dict__, action)
    assert Delivery.objects.get(endpoint=ep).status == "paused"
    assert EndpointState.objects.get(endpoint=ep).paused_count == 1
    d = Delivery.objects.get(endpoint=gone)
    assert (d.status, d.final_outcome) == ("cancelled", "endpoint_deleted")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "gone", "response_status": 410},
        {"outcome": "", "policy_blocked": True},
    ],
    ids=["a 410 settle", "a policy-block settle"],
)
def test_backlog_cap_holds_when_a_settle_pauses_its_own_delivery(
    organization, workspace, keys, answer
):
    """R8 at the two settles that pause their own delivery and turn the endpoint off: at the cap, the delivery is
    skipped with ``backlog_cap`` and ``paused_count`` stays at 10,000; one place under the cap, it is paused."""
    from webhooks.core.attempt import AttemptRecord, next_action_for

    full = make_endpoint(organization, workspace, rate_limit_per_s=100)
    room = make_endpoint(organization, workspace, rate_limit_per_s=100)
    pub(organization, workspace, ["k1"])
    store = OrmStore()
    store.fan_out(int(time.time() * 1000), 500)
    EndpointState.objects.filter(endpoint=full).update(paused_count=BACKLOG_CAP)
    EndpointState.objects.filter(endpoint=room).update(paused_count=BACKLOG_CAP - 1)
    for c in store.claim_due(int(time.time() * 1000), "normal", 48, 16):
        t = int(time.time() * 1000)
        rec = AttemptRecord(
            n=c.attempt_n, trigger="auto", started_at_ms=t, settled_at_ms=t, **answer
        )
        assert store.settle(c, rec.__dict__, next_action_for(c, rec))
    for ep, status, reason, count in (
        (full, "skipped", "backlog_cap", BACKLOG_CAP),
        (room, "paused", "", BACKLOG_CAP),
    ):
        ep.refresh_from_db()
        assert ep.status in ("disabled_auto", "blocked_by_policy")
        d = Delivery.objects.get(endpoint=ep)
        assert (d.status, d.skip_reason) == (status, reason)
        st = EndpointState.objects.get(endpoint=ep)
        assert (st.paused_count, st.in_flight) == (count, 0)
