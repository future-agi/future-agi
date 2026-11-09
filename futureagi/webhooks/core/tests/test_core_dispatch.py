"""The dispatcher over the in-process store, without Django: fan-out, sequences, leases with fencing tokens,
``worker_lost``, ``superseded``, retries, a lost renewal closing the socket; and the deadline watchdog on a real
socket pair (DESIGN.md sections 7 and 13.1). Run from ``futureagi/``:
``python -m unittest webhooks.core.tests.test_core_dispatch``.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest

from webhooks.core.attempt import next_action_for
from webhooks.core.dispatch import Dispatcher, per_org_share
from webhooks.core.memstore import MemQueue
from webhooks.core.ports import TransportResult
from webhooks.core.tests.fakes import (
    ORG,
    FakeClock,
    FakeResolver,
    FakeTransport,
    make_store,
    publish,
    sender,
)
from webhooks.core.watchdog import Watchdog


class DispatchTests(unittest.TestCase):
    def test_fan_out_sends_and_settles(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        publish(st, 2, clock)
        self.assertEqual(
            publish(st, 1, clock), "evt_01JBF3Q7Z8X4N6R2T5V9W0YB01"
        )  # same source key: no new event
        with self.assertRaises(ValueError):
            st.publish(
                organization_id=ORG,
                source_key="k1",
                source_hash="other",
                event={"id": "x"},
            )
        tr = FakeTransport(TransportResult(status=200, connected_ip="93.184.215.14"))
        d = Dispatcher(
            st, sender(FakeResolver(["93.184.215.14"]), tr, clock), clock, MemQueue()
        )
        out = d.tick()
        d.drain()
        self.assertEqual((out["fanned_out"], out["started"]), (2, 2))
        dels = sorted(st.deliveries.values(), key=lambda x: x["seq"])
        self.assertEqual(
            [(x["endpoint_id"], x["seq"], x["status"]) for x in dels],
            [("whep_A", 1, "succeeded"), ("whep_A", 2, "succeeded")],
        )
        self.assertEqual(len(st.payloads), 2)
        self.assertEqual(st.endpoints["whep_A"].in_flight, 0)

    def test_paused_endpoint_takes_sequences_and_does_not_send(self):
        clock = FakeClock()
        st = make_store(clock, status="disabled_manual")
        publish(st, 1, clock)
        tr = FakeTransport(TransportResult(status=200))
        Dispatcher(st, sender(FakeResolver(["93.184.215.14"]), tr, clock), clock).tick()
        (dl,) = st.deliveries.values()
        self.assertEqual((dl["seq"], dl["status"], tr.requests), (1, "paused", []))

    def test_retry_then_success_on_the_standard_schedule(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        tr = FakeTransport(TransportResult(status=503), TransportResult(status=200))
        d = Dispatcher(st, sender(FakeResolver(["93.184.215.14"]), tr, clock), clock)
        d.tick()
        d.drain()
        (dl,) = st.deliveries.values()
        self.assertEqual((dl["status"], dl["retry_class"]), ("retrying", "standard"))
        self.assertEqual(d.tick()["started"], 0)  # not due yet
        clock.advance(dl["next_attempt_at_ms"] - clock.ms)
        d.tick()
        d.drain()
        self.assertEqual((dl["status"], dl["attempts_count"]), ("succeeded", 2))
        self.assertEqual([a["n"] for a in st.attempts], [1, 2])

    def test_expired_lease_is_worker_lost_and_a_stale_settle_is_superseded(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        st.fan_out(clock.ms, 10)
        (first,) = st.claim_due(clock.ms, "normal", 4, 8)
        self.assertEqual(first.lease_until_ms, clock.ms + 2 * (15_000 + 5_000))
        clock.advance(40_001)  # the first sender stalled past its lease
        tr = FakeTransport(TransportResult(status=200))
        d = Dispatcher(st, sender(FakeResolver(["93.184.215.14"]), tr, clock), clock)
        out = d.tick()
        d.drain()
        self.assertEqual(out["reaped"], 1)
        (dl,) = st.deliveries.values()
        self.assertEqual(st.attempts[0]["outcome"], "worker_lost")
        self.assertEqual(dl["status"], "retrying")
        self.assertFalse(st.renew(first.delivery_id, first.lease_token, clock.ms + 1))
        clock.advance(dl["next_attempt_at_ms"] - clock.ms)
        d.tick()
        d.drain()
        self.assertEqual(dl["status"], "succeeded")
        # The stalled sender wakes up with its old token: only its own attempt row is written.
        stale = sender(
            FakeResolver(["93.184.215.14"]),
            FakeTransport(TransportResult(status=500)),
            clock,
        )
        rec = stale.attempt(first)
        self.assertFalse(st.settle(first, rec.__dict__, next_action_for(first, rec)))
        self.assertEqual(
            (st.attempts[-1]["outcome"], st.attempts[-1]["observed_outcome"]),
            ("superseded", "http_error"),
        )
        self.assertEqual(dl["status"], "succeeded")

    def test_a_lost_renewal_closes_the_socket_and_the_settle_is_superseded(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        bound = threading.Event()

        def hang_until_aborted(request):
            done = threading.Event()
            request.abort.bind(done.set)
            bound.set()
            done.wait(5)
            return TransportResult(
                error="aborted" if request.abort.aborted else "timeout"
            )

        d = Dispatcher(
            st,
            sender(
                FakeResolver(["93.184.215.14"]),
                FakeTransport(hang_until_aborted),
                clock,
            ),
            clock,
        )
        d.tick()
        self.assertTrue(bound.wait(5))
        (dl,) = st.deliveries.values()
        dl["lease_token"] = "taken-by-another-worker"
        self.assertEqual(d.renew_leases(), 1)
        d.drain()
        self.assertEqual(
            (st.attempts[-1]["outcome"], st.attempts[-1]["observed_outcome"]),
            ("superseded", "worker_lost"),
        )
        self.assertEqual(dl["status"], "sending")  # untouched: the other worker owns it

    def test_gone_disables_the_endpoint(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        d = Dispatcher(
            st,
            sender(
                FakeResolver(["93.184.215.14"]),
                FakeTransport(TransportResult(status=410)),
                clock,
            ),
            clock,
        )
        d.tick()
        d.drain()
        (dl,) = st.deliveries.values()
        self.assertEqual(
            (dl["status"], st.endpoints["whep_A"].config.status),
            ("paused", "disabled_auto"),
        )

    def test_in_flight_and_per_org_caps(self):
        clock = FakeClock()
        st = make_store(clock)
        st.endpoints["whep_A"].max_in_flight = 2
        for i in range(5):
            publish(st, i, clock)
        st.fan_out(clock.ms, 10)
        self.assertEqual(
            len(st.claim_due(clock.ms, "normal", 48, per_org_share(48))), 2
        )
        self.assertEqual(per_org_share(16), 8)
        self.assertEqual(per_org_share(96), 24)

    def test_org_share_counts_attempts_already_in_flight(self):
        clock = FakeClock()
        st = make_store(clock)
        st.endpoints["whep_A"].max_in_flight = 16
        for i in range(12):
            publish(st, i, clock)
        st.fan_out(clock.ms, 50)
        first = st.claim_due(clock.ms, "normal", 48, 8, {ORG: 5})
        self.assertEqual(len(first), 3)  # 8 per org, 5 already in flight
        self.assertEqual(st.claim_due(clock.ms, "normal", 48, 8, {ORG: 8}), [])

    def test_run_loop_stops(self):
        clock = FakeClock()
        st = make_store(clock)
        stop = threading.Event()
        d = Dispatcher(
            st,
            sender(FakeResolver([]), FakeTransport(TransportResult(status=200)), clock),
            clock,
            MemQueue(),
        )
        th = threading.Thread(target=d.run, args=(stop,))
        th.start()
        stop.set()
        d.queue.kick()
        th.join(timeout=5)
        self.assertFalse(th.is_alive())


class WatchdogTests(unittest.TestCase):
    def test_deadline_shuts_a_trickling_socket(self):
        wd = Watchdog()
        a, b = socket.socketpair()
        try:
            t0 = time.monotonic()
            wd.register(t0 + 0.3, lambda: a.shutdown(socket.SHUT_RDWR))
            b.sendall(b"x")
            self.assertEqual(a.recv(1), b"x")
            a.settimeout(5)
            self.assertEqual(a.recv(1), b"")  # blocked until the watchdog shut it down
            self.assertLess(time.monotonic() - t0, 2.0)
        finally:
            a.close()
            b.close()
            wd.stop()

    def test_cancel_before_the_deadline(self):
        wd = Watchdog()
        fired = []
        tok = wd.register(time.monotonic() + 0.2, lambda: fired.append(1))
        self.assertTrue(wd.cancel(tok))
        time.sleep(0.35)
        self.assertEqual(fired, [])
        tok2 = wd.register(time.monotonic(), lambda: fired.append(2))
        time.sleep(0.2)
        self.assertEqual(fired, [2])
        self.assertTrue(wd.fired(tok2))
        self.assertFalse(wd.cancel(tok2))
        wd.stop()


if __name__ == "__main__":
    unittest.main()
