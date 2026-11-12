"""A claim racing an endpoint's turn-off, on two real connections (WH-1a re-review should-fix 1; DESIGN.md sections
7.7 and 7.8). Needs the FA test database.

The claim statement locks the due rows and reads the endpoint as enabled; the turn-off (a settle answered 410, or an
admin's ``disable_endpoint``) holds the endpoint's state row; the claim then waits on that row in ``_take_slot`` while
the turn-off commits. The claim must lease nothing: it reads the endpoint again once it has the row, and pauses the
rows it holds (the turn-off's own ``pause_backlog`` skipped them, SKIP LOCKED). Before the fix it leased up to
``max_in_flight`` of them to the endpoint that was now off.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.db import connection

from webhooks import store_settle
from webhooks.core.attempt import AttemptRecord, next_action_for
from webhooks.models import Attempt, Delivery, Endpoint, EndpointState
from webhooks.store import OrmStore
from webhooks.store_rows import dt_of
from webhooks.tests.factories import make_endpoint, use_test_keys
from webhooks.tests.test_store import pub


def _waits_on_a_lock(pids: list[int], timeout_s: float) -> bool:
    """True once the claimer's backend is waiting on a lock (``pg_stat_activity``, read on this thread's connection)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pids:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                    [pids[0]],
                )
                row = cur.fetchone()
            if row and row[0] == "Lock":
                return True
        time.sleep(0.05)
    return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("turn_off_by", ["settle_410", "admin_disable"])
def test_a_claim_waiting_on_the_state_row_leases_nothing_once_the_endpoint_is_off(
    turn_off_by, organization, workspace, monkeypatch
):
    use_test_keys(monkeypatch)
    ep = make_endpoint(organization, workspace, rate_limit_per_s=100)
    for i in range(4):
        pub(organization, workspace, i)
    store = OrmStore()
    now = int(time.time() * 1000)
    store.fan_out(now, 500)
    first = None
    if turn_off_by == "settle_410":
        (first,) = store.claim_due(
            now, "normal", 1, 16
        )  # the attempt that the receiver answers 410
    holding, go_on = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    claimed: list = []
    claimer_pid: list[int] = []
    real_turn_off = store_settle.turn_off

    def turn_off_once_the_claim_waits(*args):
        holding.set()  # this transaction holds the endpoint's state row now
        if not go_on.wait(20):
            raise AssertionError("the claim never waited on the state row")
        return real_turn_off(*args)

    monkeypatch.setattr(store_settle, "turn_off", turn_off_once_the_claim_waits)

    def turner():
        try:
            if turn_off_by == "settle_410":
                rec = AttemptRecord(
                    n=1,
                    trigger="auto",
                    started_at_ms=now,
                    settled_at_ms=now,
                    response_status=410,
                    outcome="gone",
                )
                assert store.settle(first, rec.__dict__, next_action_for(first, rec))
            else:
                store_settle.disable_endpoint(ep.id, "manual", dt_of(now))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    def claimer():
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                claimer_pid.append(cur.fetchone()[0])
            claimed.extend(store.claim_due(int(time.time() * 1000), "normal", 48, 16))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    t = threading.Thread(target=turner)
    t.start()
    assert holding.wait(15), errors
    c = threading.Thread(target=claimer)
    c.start()
    try:
        # The claim statement has run (it locked the due rows and read the endpoint) and the claim now waits on the
        # state row, while the endpoint is still enabled: nothing has committed the turn-off yet.
        waited = _waits_on_a_lock(claimer_pid, 15)
        status_while_waiting = Endpoint.objects.get(id=ep.id).status
    finally:
        go_on.set()
        t.join(20)
        c.join(20)
    assert not t.is_alive() and not c.is_alive() and errors == []
    assert waited, "the claim did not wait on the endpoint's state row"
    assert status_while_waiting == "enabled"
    assert (
        claimed == []
    )  # before the fix: 3 (settle) or 4 (admin) leases to an endpoint that was off
    ep.refresh_from_db()
    reason = "gone_410" if turn_off_by == "settle_410" else "manual"
    assert (ep.status, ep.disabled_reason) == ("disabled_auto", reason)
    assert set(
        Delivery.objects.filter(endpoint=ep).values_list("status", flat=True)
    ) == {"paused"}
    st = EndpointState.objects.get(endpoint=ep)
    assert (st.paused_count, st.in_flight) == (4, 0)
    outcomes = list(Attempt.objects.values_list("outcome", flat=True))
    assert outcomes == (["gone"] if turn_off_by == "settle_410" else [])
