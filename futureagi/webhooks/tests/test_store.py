"""The Postgres store and the dispatcher on it (DESIGN.md sections 7 and 13.1; R2, R3, R6, R8).

Needs the FA test database. The resolver and transport are the core's fakes, so no request leaves the test; the real
transport is proven against local receivers in ``test_transport``.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.db.models.functions import Now

from webhooks.core.attempt import Sender
from webhooks.core.dispatch import Dispatcher
from webhooks.core.ipclass import Profile
from webhooks.core.ports import TransportResult
from webhooks.core.signing import verify
from webhooks.core.tests.fakes import FakeResolver, FakeTransport
from webhooks.models import Attempt, Delivery, Endpoint, EndpointState, Event, Payload
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys

PUBLIC = "93.184.215.14"


def _dt_ms(ms):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, UTC)


class Clock:
    def __init__(self):
        self.ms = int(time.time() * 1000)
        self.mono = 1000.0

    def now_ms(self):
        return self.ms

    def monotonic(self):
        return self.mono

    def advance(self, ms):
        self.ms += ms
        self.mono += ms / 1000


def psl(host):
    return "ai" if host.endswith(".ai") else None


def dispatcher(clock, *results, answers=(PUBLIC,)):
    tr = FakeTransport(*results)
    s = Sender(Profile(mode="hosted"), psl, FakeResolver(list(answers)), tr, clock)
    return Dispatcher(OrmStore(), s, clock, after_send=connection.close), tr


def pub(org, ws, n, **kw):
    with transaction.atomic():
        return publish(
            type="prompt.label.changed",
            organization_id=org.id,
            workspace_id=ws.id,
            data=label_event_data(**kw),
            source_key=f"k{n}",
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.mark.django_db(transaction=True)
def test_fan_out_sequences_variants_and_filters(organization, workspace, keys):
    a = make_endpoint(organization, workspace)
    b = make_endpoint(
        organization,
        workspace,
        event_types=("prompt.label.changed",),
        filters={"label_names": ["Production"]},
        include_people_names=False,
    )
    org_wide = make_endpoint(organization, None)
    c = make_endpoint(organization, workspace, status="disabled_manual")
    for i in range(3):
        pub(organization, workspace, i, label="Production" if i != 1 else "Staging")
    assert OrmStore().fan_out(int(time.time() * 1000), 500) == 3
    seqs = {
        ep.id: list(
            Delivery.objects.filter(endpoint=ep)
            .order_by("seq")
            .values_list("seq", "status")
        )
        for ep in (a, b, org_wide, c)
    }
    assert seqs[a.id] == [(1, "pending"), (2, "pending"), (3, "pending")]
    assert seqs[b.id] == [
        (1, "pending"),
        (2, "pending"),
    ]  # the Staging move is filtered out
    assert seqs[org_wide.id] == [(1, "pending"), (2, "pending"), (3, "pending")]
    assert seqs[c.id] == [(1, "paused"), (2, "paused"), (3, "paused")]
    assert EndpointState.objects.get(endpoint=c).paused_count == 3
    # One body per variant that a delivery sends (DESIGN 4.6, 7.5): names on (a, org-wide, c) for all 3 events,
    # names off (b) only for the 2 events b matched. The Staging move gets no names-off body: nobody would be sent it.
    events = {e.id: e.source_key for e in Event.objects.all()}
    assert sorted(
        (events[p.event_id], p.include_people_names) for p in Payload.objects.all()
    ) == [("k0", False), ("k0", True), ("k1", True), ("k2", False), ("k2", True)]
    shared = {
        (dl.event_id, dl.endpoint_id): dl.payload_id for dl in Delivery.objects.all()
    }
    for ev_id, key in events.items():
        names_on = shared[(ev_id, a.id)]
        assert shared[(ev_id, org_wide.id)] == shared[(ev_id, c.id)] == names_on
        if key != "k1":
            assert shared[(ev_id, b.id)] != names_on
    assert set(Event.objects.values_list("fanout_state", flat=True)) == {"done"}


@pytest.mark.django_db(transaction=True)
def test_send_sign_settle(organization, workspace, keys):
    ep = make_endpoint(
        organization,
        workspace,
        secret="whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
    )
    pub(organization, workspace, 1)
    clock = Clock()
    d, tr = dispatcher(clock, TransportResult(status=204, connected_ip=PUBLIC))
    d.tick()
    d.drain()
    dl = Delivery.objects.get(endpoint=ep)
    assert (dl.status, dl.attempts_count, dl.lease_token) == ("succeeded", 1, None)
    req = tr.requests[0]
    assert (
        verify(
            "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
            dict(req.headers),
            req.body,
            clock.ms // 1000,
        )
        == "ok"
    )
    assert req.body == bytes(dl.payload.body)
    att = Attempt.objects.get(delivery=dl)
    assert (att.outcome, att.resolved_ip, att.n) == ("success", PUBLIC, 1)
    assert dl.request_headers["webhook-signature"].startswith("v1,…")
    assert EndpointState.objects.get(endpoint=ep).in_flight == 0


@pytest.mark.django_db(transaction=True)
def test_retry_then_worker_lost_then_superseded(organization, workspace, keys):
    ep = make_endpoint(organization, workspace)
    pub(organization, workspace, 1)
    clock = Clock()
    store = OrmStore()
    store.fan_out(clock.ms, 10)
    (first,) = store.claim_due(clock.ms, "normal", 48, 12)
    # The first sender stalls past its lease; leases run on the database clock.
    clock.advance(2 * (15_000 + 5_000) + 1)
    Delivery.objects.filter(id=first.delivery_id).update(
        lease_until=Now() - timedelta(seconds=1)
    )
    d, _tr = dispatcher(clock, TransportResult(status=200))
    assert d.tick()["reaped"] == 1
    d.drain()
    dl = Delivery.objects.get(endpoint=ep)
    assert dl.status == "retrying"
    assert list(
        Attempt.objects.filter(delivery=dl).values_list("outcome", flat=True)
    ) == ["worker_lost"]
    assert not store.renew(first.delivery_id, first.lease_token, clock.ms + 1000)
    clock.ms = int(dl.next_attempt_at.timestamp() * 1000)
    d.tick()
    d.drain()
    dl.refresh_from_db()
    assert dl.status == "succeeded"
    # The stalled sender wakes with its old token: only its own row, the delivery stays succeeded.
    stale = Sender(
        Profile(mode="hosted"),
        psl,
        FakeResolver([PUBLIC]),
        FakeTransport(TransportResult(status=500)),
        clock,
    )
    rec = stale.attempt(first)
    from webhooks.core.attempt import next_action_for

    assert store.settle(first, rec.__dict__, next_action_for(first, rec)) is False
    last = Attempt.objects.get(delivery=dl, outcome="superseded")
    assert last.observed_outcome == "http_error"
    dl.refresh_from_db()
    assert dl.status == "succeeded"


@pytest.mark.django_db(transaction=True)
def test_two_loops_never_claim_the_same_delivery(organization, workspace, keys):
    make_endpoint(organization, workspace, max_in_flight=16, rate_limit_per_s=100)
    for i in range(20):
        pub(organization, workspace, i)
    now = int(time.time() * 1000)
    OrmStore().fan_out(now, 500)
    got: list[list[str]] = [[], []]

    def run(i):
        try:
            got[i] = [
                c.delivery_id for c in OrmStore().claim_due(now, "normal", 48, 48)
            ]
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not set(got[0]) & set(got[1])
    assert len(got[0]) + len(got[1]) <= 16  # max_in_flight on the shared state row


@pytest.mark.django_db(transaction=True)
def test_token_bucket_and_per_org_share(organization, workspace, keys):
    """The shared send rate (WH-1b: one reserved start per 1 / rate on the state row, on the database clock)."""
    ep = make_endpoint(organization, workspace, rate_limit_per_s=1, max_in_flight=16)
    for i in range(5):
        pub(organization, workspace, i)
    store = OrmStore()
    now = int(time.time() * 1000)
    store.fan_out(now, 500)
    (first,) = store.claim_due(now, "normal", 48, 12)  # one start per second
    assert first.start_delay_ms == 0
    assert Delivery.objects.filter(status="pending").count() == 4
    # No start is free at the same instant: nothing is claimed, nothing is touched.
    assert store.claim_due(now, "normal", 48, 12) == []
    assert all(
        d.next_attempt_at <= _dt_ms(now)
        for d in Delivery.objects.filter(status="pending")
    )
    tat = EndpointState.objects.get(endpoint=ep).rate_tat
    assert tat is not None
    # A second later (on the database clock) the next start is free, 1 s after the first.
    time.sleep(1.05)
    (second,) = store.claim_due(int(time.time() * 1000), "normal", 48, 12)
    assert second.start_delay_ms == 0
    assert (
        EndpointState.objects.get(endpoint=ep).rate_tat - tat
    ).total_seconds() >= 1.0


@pytest.mark.django_db(transaction=True)
def test_org_share_counts_attempts_in_flight(organization, workspace, keys):
    make_endpoint(organization, workspace, rate_limit_per_s=100, max_in_flight=16)
    for i in range(12):
        pub(organization, workspace, i)
    store = OrmStore()
    now = int(time.time() * 1000)
    store.fan_out(now, 500)
    got = store.claim_due(now, "normal", 48, 8, {str(organization.id): 5})
    assert len(got) == 3


@pytest.mark.django_db(transaction=True)
def test_gone_disables_and_pauses_the_backlog(organization, workspace, keys):
    ep = make_endpoint(organization, workspace, rate_limit_per_s=1)
    for i in range(3):
        pub(organization, workspace, i)
    clock = Clock()
    d, _ = dispatcher(clock, TransportResult(status=410))
    d.tick()
    d.drain()
    ep.refresh_from_db()
    assert (ep.status, ep.disabled_reason) == ("disabled_auto", "gone_410")
    assert set(
        Delivery.objects.filter(endpoint=ep).values_list("status", flat=True)
    ) == {"paused"}
    assert EndpointState.objects.get(endpoint=ep).paused_count == 3


@pytest.mark.django_db(transaction=True)
def test_three_ssrf_blocked_in_a_row_disable_unsafe_address(
    organization, workspace, keys
):
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(3):
        pub(organization, workspace, i)
    clock = Clock()
    d, tr = dispatcher(clock, TransportResult(status=200), answers=("10.0.0.7",))
    d.tick()
    d.drain()
    ep.refresh_from_db()
    assert tr.requests == []
    assert (ep.status, ep.disabled_reason) == ("disabled_auto", "unsafe_address")
    for att in Attempt.objects.filter(delivery__endpoint=ep):
        assert "10.0.0.7" not in att.error_detail
        assert att.resolved_ip is None


@pytest.mark.django_db(transaction=True)
def test_run_targeting_needs_the_opt_in(organization, workspace, keys):
    opted = make_endpoint(
        organization, workspace, event_types=(), accepts_run_targeting=True
    )
    not_opted = make_endpoint(organization, workspace, event_types=())
    data = {
        "deployment": {
            "id": "dep-1",
            "environment": "production",
            "name": "a",
            "revision": 1,
        },
        "run": {"trigger": {"kind": "api"}, "status": "succeeded"},
    }
    for ep, key in ((opted, "t1"), (not_opted, "t2")):
        with transaction.atomic():
            publish(
                type="deployment.run.completed",
                organization_id=organization.id,
                workspace_id=workspace.id,
                data=data,
                source_key=key,
                source_hash="h",
                producer="omega.deployments",
                occurred_at=T0,
                targeted_endpoint_id=ep.id,
            )
    OrmStore().fan_out(int(time.time() * 1000), 10)
    assert list(Delivery.objects.values_list("endpoint_id", "targeted")) == [
        (opted.id, True)
    ]
    assert Endpoint.objects.count() == 2
