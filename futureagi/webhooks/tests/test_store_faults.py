"""The Postgres store when things go wrong (WH-1a review fixes; DESIGN.md sections 6.3, 7.7, 7.8 and 13.1).

Needs the FA test database. A poisoned event fails alone; a settle that turns an endpoint off cannot deadlock with a
claim holding its backlog (two connections); an endpoint at capacity does not hold back its org; claim-time pauses
count toward the backlog cap; a configuration that does not decrypt, an endpoint with no live secret and a URL
outside the policy are each handled without an attempt to the receiver; a test answered 410 never disables; leases
follow the database clock.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.db.models.functions import Now

from webhooks.core.attempt import AttemptRecord, next_action_for
from webhooks.core.ports import TransportResult
from webhooks.models import (
    Attempt,
    Delivery,
    Endpoint,
    EndpointState,
    Event,
    Policy,
    Secret,
)
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.test_store import Clock, dispatcher, pub

RUN = {
    "deployment": {
        "id": "dep-1",
        "environment": "production",
        "name": "a",
        "revision": 1,
    },
    "run": {"trigger": {"kind": "api"}, "status": "succeeded"},
}


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


def _raw_publish(org, ws, key, type_, data, **kw):
    with transaction.atomic():
        return publish(
            type=type_,
            organization_id=org.id,
            workspace_id=ws.id,
            data=data,
            source_key=key,
            source_hash="h",
            producer="test",
            occurred_at=T0,
            **kw,
        )


@pytest.mark.django_db(transaction=True)
def test_a_poisoned_event_fails_alone(organization, workspace, keys):
    ep = make_endpoint(
        organization, workspace, event_types=("prompt.*", "deployment.run.*")
    )
    pub(organization, workspace, 1)
    no_prompt = label_event_data()
    del no_prompt["prompt"]
    _raw_publish(organization, workspace, "bad-data", "prompt.label.changed", no_prompt)
    _raw_publish(
        organization,
        workspace,
        "too-big",
        "prompt.label.changed",
        label_event_data(label="x" * 70_000),
    )
    _raw_publish(
        organization,
        workspace,
        "no-builder",
        "deployment.run.completed",
        RUN,
        builder_input={"x": 1},
    )
    pub(organization, workspace, 5)
    assert OrmStore().fan_out(int(time.time() * 1000), 500) == 5
    states = dict(Event.objects.values_list("source_key", "fanout_state"))
    assert states == {
        "k1": "done",
        "k5": "done",
        "bad-data": "failed",
        "too-big": "failed",
        "no-builder": "failed",
    }
    errors = dict(
        Event.objects.filter(fanout_state="failed").values_list(
            "source_key", "fanout_error"
        )
    )
    assert errors["bad-data"].startswith("KeyError")
    assert errors["too-big"].startswith("ValueError") and errors[
        "no-builder"
    ].startswith("ValueError")
    assert list(
        Delivery.objects.filter(endpoint=ep)
        .order_by("seq")
        .values_list("seq", flat=True)
    ) == [1, 2]
    assert (
        EndpointState.objects.get(endpoint=ep).next_seq == 3
    )  # gap-free: the failed ones took no number
    assert (
        OrmStore().fan_out(int(time.time() * 1000), 500) == 0
    )  # not retried every pass


class _MatchFails(OrmStore):
    """Matching one event raises a database-level error, as a statement or lock timeout would."""

    def _match(self, ev):
        if ev.source_key == "db-error":
            with connection.cursor() as cur:
                cur.execute("SELECT 1 / 0")
        return super()._match(ev)


@pytest.mark.django_db(transaction=True)
def test_a_database_error_while_matching_fails_that_event_alone(
    organization, workspace, keys
):
    # Re-review should-fix 5: before, matching ran outside the event's savepoint, so this error aborted the batch
    # transaction ("current transaction is aborted") and no event of the batch was fanned out.
    ep = make_endpoint(organization, workspace)
    pub(organization, workspace, 1)
    _raw_publish(
        organization, workspace, "db-error", "prompt.label.changed", label_event_data()
    )
    pub(organization, workspace, 3)
    assert _MatchFails().fan_out(int(time.time() * 1000), 500) == 3
    states = dict(Event.objects.values_list("source_key", "fanout_state"))
    assert states == {"k1": "done", "db-error": "failed", "k3": "done"}
    assert Event.objects.get(source_key="db-error").fanout_error.startswith("DataError")
    assert list(
        Delivery.objects.filter(endpoint=ep)
        .order_by("seq")
        .values_list("seq", flat=True)
    ) == [1, 2]
    assert EndpointState.objects.get(endpoint=ep).next_seq == 3


@pytest.mark.django_db(transaction=True)
def test_a_410_settle_does_not_deadlock_with_a_claim_holding_the_backlog(
    organization, workspace, keys
):
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(4):
        pub(organization, workspace, i)
    store = OrmStore()
    now = int(time.time() * 1000)
    store.fan_out(now, 500)
    (first,) = store.claim_due(now, "normal", 1, 16)
    holding, go_on, errors, settled = threading.Event(), threading.Event(), [], []

    def claimer():  # what claim_due does: lock the due rows, then the state row
        try:
            with transaction.atomic():
                rows = list(
                    Delivery.objects.select_for_update().filter(
                        endpoint=ep, status="pending"
                    )
                )
                assert len(rows) == 3
                holding.set()
                go_on.wait(10)
                EndpointState.objects.select_for_update().get(endpoint=ep)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    def settler():
        try:
            rec = AttemptRecord(
                n=1,
                trigger="auto",
                started_at_ms=now,
                settled_at_ms=now,
                response_status=410,
                outcome="gone",
            )
            settled.append(
                store.settle(first, rec.__dict__, next_action_for(first, rec))
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    a = threading.Thread(target=claimer)
    a.start()
    assert holding.wait(10)
    b = threading.Thread(target=settler)
    t0 = time.monotonic()
    b.start()
    b.join(8)
    blocked = b.is_alive()
    go_on.set()
    a.join(15)
    b.join(15)
    assert not blocked, "the settle waited on rows the claim held"
    assert time.monotonic() - t0 < 8 and errors == [] and settled == [True]
    ep.refresh_from_db()
    assert (ep.status, ep.disabled_reason) == ("disabled_auto", "gone_410")
    assert Attempt.objects.get(delivery_id=first.delivery_id).outcome == "gone"
    # The rows the claim held were skipped by the pause; the next claim pauses them (the endpoint is off).
    assert store.claim_due(int(time.time() * 1000), "normal", 48, 16) == []
    assert set(
        Delivery.objects.filter(endpoint=ep).values_list("status", flat=True)
    ) == {"paused"}
    assert EndpointState.objects.get(endpoint=ep).paused_count == 4


@pytest.mark.django_db(transaction=True)
def test_an_endpoint_at_capacity_does_not_hold_back_its_org(
    organization, workspace, keys
):
    busy = make_endpoint(
        organization,
        workspace,
        event_types=("prompt.label.changed",),
        filters={"label_names": ["Production"]},
        max_in_flight=2,
        rate_limit_per_s=100,
    )
    other = make_endpoint(
        organization,
        workspace,
        event_types=("prompt.label.changed",),
        filters={"label_names": ["Staging"]},
        rate_limit_per_s=100,
    )
    for i in range(30):
        pub(organization, workspace, i, label="Production")
    pub(organization, workspace, 99, label="Staging")
    store = OrmStore()
    store.fan_out(int(time.time() * 1000), 500)
    EndpointState.objects.filter(endpoint=busy).update(in_flight=2)
    got = store.claim_due(int(time.time() * 1000), "normal", 48, 16)
    assert [c.endpoint.id for c in got] == [other.id]


@pytest.mark.django_db(transaction=True)
def test_claim_time_pauses_count_toward_the_backlog(organization, workspace, keys):
    ep = make_endpoint(organization, workspace)
    pub(organization, workspace, 1)
    pub(organization, workspace, 2)
    store = OrmStore()
    store.fan_out(int(time.time() * 1000), 500)
    Endpoint.objects.filter(id=ep.id).update(status="disabled_manual")
    assert store.claim_due(int(time.time() * 1000), "normal", 48, 16) == []
    assert (
        list(Delivery.objects.filter(endpoint=ep).values_list("status", flat=True))
        == ["paused"] * 2
    )
    assert EndpointState.objects.get(endpoint=ep).paused_count == 2


@pytest.mark.django_db(transaction=True)
def test_a_configuration_that_does_not_decrypt_is_released_alone(
    organization, workspace, keys
):
    bad = make_endpoint(organization, workspace)
    good = make_endpoint(organization, workspace)
    pub(organization, workspace, 1)
    store = OrmStore()
    now = int(time.time() * 1000)
    store.fan_out(now, 500)
    Endpoint.objects.filter(id=bad.id).update(
        url_encrypted=b"gAAAAAB-not-a-fernet-token"
    )
    got = store.claim_due(now, "normal", 48, 16)
    assert [c.endpoint.id for c in got] == [good.id]
    dl = Delivery.objects.get(endpoint=bad)
    assert (dl.status, dl.attempts_count, dl.lease_token) == ("pending", 0, None)
    assert (
        int(dl.next_attempt_at.timestamp() * 1000) >= now + 59_000
    )  # due again after a minute, not lost
    assert EndpointState.objects.get(endpoint=bad).in_flight == 0
    assert not Attempt.objects.filter(delivery=dl).exists()


@pytest.mark.django_db(transaction=True)
def test_no_live_secret_is_released_without_an_attempt(organization, workspace, keys):
    ep = make_endpoint(organization, workspace)
    Secret.objects.filter(endpoint=ep).update(state="revoked")
    pub(organization, workspace, 1)
    d, tr = dispatcher(Clock(), TransportResult(status=200))
    out = d.tick()
    d.drain()
    assert (out["started"], out["released"], tr.requests) == (0, 1, [])
    dl = Delivery.objects.get(endpoint=ep)
    assert (dl.status, dl.attempts_count) == ("pending", 0)
    assert EndpointState.objects.get(endpoint=ep).in_flight == 0


@pytest.mark.django_db(transaction=True)
def test_a_url_outside_the_policy_blocks_the_endpoint(organization, workspace, keys):
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(3):
        pub(organization, workspace, i)
    Policy.objects.create(organization=organization, allowed_hosts=["*.other.ai"])
    d, tr = dispatcher(Clock(), TransportResult(status=200))
    d.tick()
    d.drain()
    ep.refresh_from_db()
    assert (ep.status, ep.disabled_reason) == ("blocked_by_policy", "policy")
    assert set(
        Delivery.objects.filter(endpoint=ep).values_list("status", flat=True)
    ) == {"paused"}
    assert set(
        Delivery.objects.filter(endpoint=ep).values_list("attempts_count", flat=True)
    ) == {0}
    assert (tr.requests, Attempt.objects.count()) == ([], 0)
    st = EndpointState.objects.get(endpoint=ep)
    assert (st.paused_count, st.in_flight, st.consecutive_ssrf_blocked) == (3, 0, 0)


@pytest.mark.django_db(transaction=True)
def test_a_test_delivery_answered_410_never_disables(organization, workspace, keys):
    ep = make_endpoint(organization, workspace)
    Event.objects.create(
        id="evt_test_01JBF3Q7Z8X4N6R2T5V9W0YB1C",
        organization=organization,
        workspace=workspace,
        type="prompt.label.changed",
        occurred_at=T0,
        producer="webhooks.test",
        source_key="test-1",
        source_hash="h",
        data=label_event_data(),
        is_test=True,
        test_endpoint=ep,
    )
    d, _tr = dispatcher(Clock(), TransportResult(status=410))
    d.tick()
    d.drain()
    dl = Delivery.objects.get(endpoint=ep)
    assert (dl.status, dl.final_outcome, dl.seq) == ("failed", "test_not_retried", None)
    ep.refresh_from_db()
    assert ep.status == "enabled"
    assert EndpointState.objects.get(endpoint=ep).paused_count == 0


@pytest.mark.django_db(transaction=True)
def test_leases_follow_the_database_clock(organization, workspace, keys):
    make_endpoint(organization, workspace)
    pub(organization, workspace, 1)
    store = OrmStore()
    skewed = int(time.time() * 1000) + 3_600_000  # this worker's clock is an hour ahead
    store.fan_out(skewed, 500)
    (c,) = store.claim_due(skewed, "normal", 48, 16)
    assert abs(c.lease_until_ms - (time.time() * 1000 + 40_000)) < 10_000
    assert (
        store.reap_expired(skewed + 3_600_000) == []
    )  # a live lease is never reaped on a skewed clock
    Delivery.objects.filter(id=c.delivery_id).update(
        lease_until=Now() - timedelta(seconds=1)
    )
    (reaped,) = store.reap_expired(int(time.time() * 1000))
    assert reaped.delivery_id == c.delivery_id and reaped.lease_token != c.lease_token
    assert reaped.endpoint.secrets_newest_first == ()  # the reaper never decrypts
