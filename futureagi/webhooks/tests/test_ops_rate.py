"""WT-20 and WT-20b (DESIGN.md sections 7.7, 7.8 and 17): rate, in-flight and fairness across two dispatcher loops,
and hung endpoints moving to the slow lane, against a real receiver over the real transport and real Postgres.

Each "replica" is a full dispatcher loop with its own store and its own database connections, in its own thread;
what they share is what two pods share: the database (the endpoint state rows, under their row locks).
"""

from __future__ import annotations

import logging
import threading
import time

import pytest
from django.db import close_old_connections, transaction

from accounts.models import Organization
from webhooks.core import ratelimit
from webhooks.core.signing import verify
from webhooks.models import Attempt, Delivery, EndpointState, Event
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Receiver, real_dispatcher

SECRET = "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def _publish(org_id, ws_id, keys_):
    with transaction.atomic():
        for k in keys_:
            publish(
                type="prompt.label.changed",
                organization_id=org_id,
                workspace_id=ws_id,
                data=label_event_data(),
                source_key=k,
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )


def _fan_out_all():
    store = OrmStore()
    while store.fan_out(int(time.time() * 1000), 500):
        pass
    assert not Event.objects.filter(fanout_state="pending").exists()


class CountingStore(OrmStore):
    def __init__(self, counts, name, **kw):
        super().__init__(**kw)
        self.counts, self.name = counts, name

    def claim_due(self, *a, **kw):
        got = super().claim_due(*a, **kw)
        self.counts[self.name] = self.counts.get(self.name, 0) + len(got)
        return got


def _loop(d, stop, seconds):
    try:
        d.run(stop, run_for_s=seconds)
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
def test_wt20_rate_in_flight_and_fairness_on_two_replicas(
    organization, workspace, keys, receiver
):
    """Org A publishes 5,000 events to two endpoints: R at 10/s (a 100 ms receiver), and C at 100/s with 4 in flight
    (a 500 ms receiver, so the in-flight limit binds). Org B publishes one event while both replicas work through org
    A's backlog. Receiver-measured: R gets at most 11 requests in any 1 s window and keeps close to 10/s; C never has
    more than 4 at once (and reaches 4); org B's first attempt comes within 3 s of its commit."""
    ep_a = make_endpoint(
        organization,
        workspace,
        url=receiver.url("/sleep/100"),
        secret=SECRET,
        rate_limit_per_s=10,
        max_in_flight=4,
    )
    ep_c = make_endpoint(
        organization,
        workspace,
        url=receiver.url("/sleep/500"),
        secret=SECRET,
        rate_limit_per_s=100,
        max_in_flight=4,
    )
    org_b = Organization.objects.create(name="Org B")
    ep_b = make_endpoint(org_b, None, url=receiver.url("/ok"), secret=SECRET)
    _publish(organization.id, workspace.id, [f"a{i}" for i in range(5000)])
    _fan_out_all()
    assert Delivery.objects.filter(endpoint=ep_a, status="pending").count() == 5000
    assert Delivery.objects.filter(endpoint=ep_c, status="pending").count() == 5000

    counts: dict[str, int] = {}
    replicas = []
    for name in ("replica-1", "replica-2"):
        d = real_dispatcher(receiver)
        d.store = CountingStore(counts, name)
        replicas.append(d)
    stop = threading.Event()
    threads = [
        threading.Thread(target=_loop, args=(d, stop, 14.0), daemon=True)
        for d in replicas
    ]
    for t in threads:
        t.start()
    time.sleep(5.0)
    _publish(org_b.id, None, ["b1"])
    b_committed = time.monotonic()
    for t in threads:
        t.join(90)
    assert not any(t.is_alive() for t in threads)

    a_arrivals = receiver.arrivals("/sleep/100")
    c_arrivals = receiver.arrivals("/sleep/500")
    b_arrivals = receiver.arrivals("/ok")
    worst = ratelimit.max_in_window([t * 1000 for t in a_arrivals])
    a_span = a_arrivals[-1] - a_arrivals[0] if len(a_arrivals) > 1 else 0.0
    evidence.write(
        "WT-20",
        replicas="two dispatcher loops, own stores and DB connections, sharing Postgres",
        leased_by_replica=counts,
        rate_endpoint={
            "rate_limit_per_s": 10,
            "requests": len(a_arrivals),
            "max_in_any_1s_window": worst,
            "measured_per_s": round(len(a_arrivals) / a_span, 2) if a_span else None,
        },
        in_flight_endpoint={
            "max_in_flight": 4,
            "requests": len(c_arrivals),
            "max_concurrent_at_receiver": receiver.max_active_by_path.get("/sleep/500"),
        },
        org_b_commit_to_first_attempt_s=(
            round(b_arrivals[0] - b_committed, 3) if b_arrivals else None
        ),
        receiver=evidence.receiver_lines(receiver, limit=25),
        db={
            "org_a_rate_endpoint_deliveries": evidence.by_status(
                Delivery.objects.filter(endpoint=ep_a)
            ),
            "org_a_in_flight_endpoint_deliveries": evidence.by_status(
                Delivery.objects.filter(endpoint=ep_c)
            ),
            "org_b_attempts": evidence.by_status(
                Attempt.objects.filter(delivery__endpoint=ep_b), "outcome"
            ),
        },
    )
    # Both replicas leased org A deliveries; the shared state row kept the endpoint's limits across them.
    assert counts.get("replica-1", 0) > 0 and counts.get("replica-2", 0) > 0, counts
    assert len(a_arrivals) >= 80, len(a_arrivals)
    assert worst <= 11, worst
    assert receiver.max_active_by_path["/sleep/100"] <= 4
    assert receiver.max_active_by_path["/sleep/500"] == 4, receiver.max_active_by_path
    assert len(c_arrivals) >= 40, len(c_arrivals)
    span = a_arrivals[-1] - a_arrivals[0]
    assert len(a_arrivals) / span >= 8.0, (
        len(a_arrivals),
        span,
    )  # the rate is reachable, not only capped
    assert b_arrivals, "org B was never sent"
    assert b_arrivals[0] - b_committed < 3.0, b_arrivals[0] - b_committed
    # The deliveries really went out signed (one verified here; WT-01 covers signing in full).
    req = next(r for r in receiver.requests if r["path"] == "/ok")
    assert verify(SECRET, req["headers"], req["body"], int(time.time())) == "ok"
    for ep in (ep_a, ep_c):
        assert EndpointState.objects.get(endpoint=ep).in_flight == 0
    assert (
        Attempt.objects.filter(delivery__endpoint=ep_b, outcome="success").count() == 1
    )


@pytest.mark.django_db(transaction=True)
def test_wt20b_hung_endpoints_do_not_hold_other_orgs_and_go_to_the_slow_lane(
    organization, workspace, keys, receiver
):
    """16 endpoints of org A hang past their 5 s deadline. Org B's first attempt is within 3 s; within the run every
    hung endpoint is on the slow lane (3 timeouts in 10 minutes)."""
    hung = [
        make_endpoint(
            organization,
            workspace,
            url=receiver.url(f"/hang/{i}"),
            timeout_ms=5000,
            rate_limit_per_s=100,
        )
        for i in range(16)
    ]
    org_b = Organization.objects.create(name="Org B")
    make_endpoint(org_b, None, url=receiver.url("/ok"))
    _publish(organization.id, workspace.id, [f"a{i}" for i in range(4)])
    _fan_out_all()
    d = real_dispatcher(receiver)
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(d, stop, 32.0), daemon=True)
    t.start()
    time.sleep(1.5)
    _publish(org_b.id, None, ["b1"])
    b_committed = time.monotonic()
    t.join(120)
    assert not t.is_alive()
    b = receiver.arrivals("/ok")
    states = {s.endpoint_id: s for s in EndpointState.objects.filter(endpoint__in=hung)}
    slow = [s for s in states.values() if s.lane == "slow"]
    evidence.write(
        "WT-20b",
        hung_endpoints=len(hung),
        timeout_ms=5000,
        org_b_commit_to_first_attempt_s=round(b[0] - b_committed, 3) if b else None,
        on_slow_lane=len(slow),
        db={
            "hung_endpoint_states": [
                {
                    "endpoint": s.endpoint_id,
                    "lane": s.lane,
                    "slow_until": s.slow_until,
                    "timeouts_in_window": len(s.recent_timeouts_at),
                }
                for s in sorted(states.values(), key=lambda s: s.endpoint_id)
            ],
            "hung_attempts": evidence.by_status(
                Attempt.objects.filter(delivery__endpoint__in=hung), "outcome"
            ),
        },
        receiver=evidence.receiver_lines(receiver, "/ok"),
    )
    assert b and b[0] - b_committed < 3.0, (b, b_committed)
    assert len(slow) == 16, [
        (s.endpoint_id, s.lane, s.recent_timeouts_at) for s in states.values()
    ]
    for s in slow:
        assert len(s.recent_timeouts_at) >= 3
        assert s.slow_until is not None
    assert (
        Attempt.objects.filter(delivery__endpoint__in=hung, outcome="timeout").count()
        >= 48
    )


@pytest.mark.django_db(transaction=True)
def test_wt10_retry_after_and_a_429_halves_the_rate_for_5_minutes(
    organization, workspace, keys, receiver, caplog
):
    """``429 Retry-After: 7``: that delivery's next attempt is at least 7 s later, and the endpoint's rate is halved
    (10/s to 5/s) for 5 minutes, measured on the receiver."""
    ep = make_endpoint(
        organization,
        workspace,
        url=receiver.url("/r429/1/7"),
        rate_limit_per_s=10,
        max_in_flight=4,
    )
    caplog.set_level(logging.INFO, logger="webhooks.metrics")
    # 30 deliveries: the backlog drains at the halved rate before the 429'd delivery is due again (deliveries are
    # claimed oldest-due first, so a larger backlog would be sent before its retry).
    _publish(organization.id, workspace.id, [f"a{i}" for i in range(30)])
    _fan_out_all()
    d = real_dispatcher(receiver)
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(d, stop, 12.0), daemon=True)
    t.start()
    t.join(60)
    reqs = [r for r in receiver.requests if r["path"] == "/r429/1/7"]
    first = reqs[0]
    first_id = first["headers"]["webhook-id"]
    again = [r for r in reqs[1:] if r["headers"]["webhook-id"] == first_id]
    others = [r for r in reqs[1:] if r["headers"]["webhook-id"] != first_id]
    after = [r["at"] * 1000 for r in others if r["at"] > first["at"] + 0.5]
    st = EndpointState.objects.get(endpoint=ep)
    evidence.write(
        "WT-10",
        retry_after_s=7,
        retry_gap_s=round(again[0]["at"] - first["at"], 3) if again else None,
        rate_limit_per_s=10,
        after_the_429={
            "requests": len(after),
            "max_in_any_1s_window": ratelimit.max_in_window(after) if after else None,
            "measured_per_s": (
                round((len(after) - 1) / ((after[-1] - after[0]) / 1000), 2)
                if len(after) > 1
                else None
            ),
        },
        db={
            "endpoint_state": {
                "rate_halved_until": st.rate_halved_until,
                "last_failure_at": st.last_failure_at,
                "last_status_code": st.last_status_code,
            },
            "attempts": evidence.by_status(
                Attempt.objects.filter(delivery__endpoint=ep), "outcome"
            ),
        },
        receiver=evidence.receiver_lines(receiver, limit=40),
    )
    assert len(others) == 29, len(others)
    assert again, "the 429'd delivery was retried within the run"
    assert again[0]["at"] - first["at"] >= 7.0, again[0]["at"] - first["at"]
    assert again[0]["headers"]["x-futureagi-attempt"] == "2"
    # Starts reserved before the 429 settled may still come at 10/s; from 0.5 s on, the halved rate holds.
    assert len(after) >= 20, len(after)
    assert ratelimit.max_in_window(after) <= 6, ratelimit.max_in_window(after)
    span = (after[-1] - after[0]) / 1000
    assert 4.0 <= (len(after) - 1) / span <= 6.0, (len(after), span)
    left = (st.rate_halved_until - st.last_failure_at).total_seconds()
    assert 299 <= left <= 301, left
    # The per-attempt metric lines (section 13.2): one per settled attempt, labelled outcome, status class and lane.
    lines = [
        r for r in caplog.records if getattr(r, "metric", None) == "webhooks.attempts"
    ]
    assert len(lines) == 31, len(lines)
    labels = {(r.outcome, r.status_class, r.lane) for r in lines}
    assert labels == {("rate_limited", "4xx", "normal"), ("success", "2xx", "normal")}
    assert all(isinstance(r.duration_ms, int) for r in lines)
    firsts = [
        r
        for r in caplog.records
        if getattr(r, "metric", None) == "webhooks.delivery.first_success_seconds"
    ]
    assert len(firsts) == 30 and all(r.type == "prompt.label.changed" for r in firsts)
