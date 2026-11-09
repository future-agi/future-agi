"""The dispatcher's side of the shared send rate (DESIGN.md section 7.8): a sender waits for the start its claim
reserved, a lost lease ends that wait, and the loop passes every ``BUSY_POLL_S`` while senders are in flight or the
last pass found work, so a rate-limited endpoint keeps its rate and a backlog is not left for an idle second (and
every second when idle). Standard library only."""

from __future__ import annotations

import threading
import time
import unittest

from webhooks.core import dispatch
from webhooks.core.dispatch import Dispatcher
from webhooks.core.ports import AbortHandle, TransportResult
from webhooks.core.tests.fakes import (
    FakeClock,
    FakeResolver,
    FakeTransport,
    claim,
    make_store,
    publish,
    sender,
)


class RecordingQueue:
    def __init__(self, stop):
        self.waits, self.stop = [], stop

    def kick(self):
        pass

    def wait(self, timeout_s):
        self.waits.append(timeout_s)
        if len(self.waits) >= 6:
            self.stop.set()
        time.sleep(min(timeout_s, 0.05))
        return False


class StartDelayTests(unittest.TestCase):
    def _dispatcher(self, transport):
        clock = FakeClock()
        return Dispatcher(
            make_store(clock),
            sender(FakeResolver(["93.184.215.14"]), transport, clock),
            clock,
        )

    def test_the_sender_waits_for_its_reserved_start(self):
        d = self._dispatcher(FakeTransport(TransportResult(status=200)))
        t0 = time.monotonic()
        d._wait_for_start(claim(start_delay_ms=200), AbortHandle())
        self.assertGreaterEqual(time.monotonic() - t0, 0.2)
        t0 = time.monotonic()
        d._wait_for_start(claim(start_delay_ms=0), None)
        self.assertLess(time.monotonic() - t0, 0.05)

    def test_a_lost_lease_ends_the_wait(self):
        d = self._dispatcher(FakeTransport(TransportResult(status=200)))
        abort = AbortHandle()
        threading.Timer(0.1, abort.abort).start()
        t0 = time.monotonic()
        d._wait_for_start(claim(start_delay_ms=5000), abort)
        self.assertLess(time.monotonic() - t0, 1.0)


class BusyPollTests(unittest.TestCase):
    def test_short_waits_while_a_send_is_in_flight_then_the_idle_poll(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        release = threading.Event()

        def slow(_req):
            release.wait(5)
            return TransportResult(status=200, connected_ip="93.184.215.14")

        stop = threading.Event()
        q = RecordingQueue(stop)
        d = Dispatcher(
            st,
            sender(FakeResolver(["93.184.215.14"]), FakeTransport(slow), clock),
            clock,
            q,
        )
        runner = threading.Thread(target=d.run, args=(stop,))
        runner.start()
        deadline = time.monotonic() + 5
        while len(q.waits) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        release.set()
        runner.join(10)
        self.assertFalse(runner.is_alive())
        self.assertEqual(q.waits[0], dispatch.BUSY_POLL_S)
        self.assertEqual(q.waits[-1], dispatch.POLL_S)
        self.assertEqual(dispatch.BUSY_POLL_S, 0.25)


class FoundWorkTests(unittest.TestCase):
    def test_a_pass_that_found_work_polls_soon_even_when_its_sends_are_done(self):
        clock = FakeClock()
        st = make_store(clock)
        d = Dispatcher(
            st,
            sender(
                FakeResolver(["93.184.215.14"]),
                FakeTransport(
                    TransportResult(status=200, connected_ip="93.184.215.14")
                ),
                clock,
            ),
            clock,
        )
        self.assertEqual(d._next_wait_s(), dispatch.POLL_S)  # nothing yet
        publish(st, 1, clock)
        out = d.tick()
        self.assertEqual((out["fanned_out"], out["started"]), (1, 1))
        d.drain(5)  # the send is done: nothing in flight
        self.assertFalse(d._has_inflight())
        self.assertEqual(
            d._next_wait_s(), dispatch.BUSY_POLL_S
        )  # there may be more due
        out = d.tick()
        self.assertEqual(sum(out.values()), 0)
        self.assertEqual(d._next_wait_s(), dispatch.POLL_S)  # idle again


if __name__ == "__main__":
    unittest.main()
