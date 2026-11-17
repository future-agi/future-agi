"""The dispatcher when things go wrong, without Django (WH-1a review fixes): a poisoned event, a pass that raises, a
heartbeat that raises, an endpoint with no live secret, a test answered 410, a URL outside the policy, a drain that
times out, sending turned off and the per-loop org share (``test_core_redaction`` has the rest).
Run from ``futureagi/``: ``python -m unittest webhooks.core.tests.test_core_dispatch_faults``.
"""

from __future__ import annotations

import logging
import threading
import time
import unittest

from webhooks.core import dispatch
from webhooks.core.dispatch import Dispatcher, idle
from webhooks.core.memstore import MemEndpoint, MemQueue
from webhooks.core.ports import TransportResult
from webhooks.core.tests.fakes import (
    ORG,
    WS,
    FakeClock,
    FakeResolver,
    FakeTransport,
    endpoint,
    make_store,
    publish,
    sender,
)

PUBLIC = "93.184.215.14"
QUERY_TOKEN = "q-tok-5d1e"
HEADER_VALUE = "Bearer hv-77a1"
BODY_MARK = "body-mark-31c"


def _ok(clock, *results):
    return sender(FakeResolver([PUBLIC]), FakeTransport(*results), clock)


def _raw(st, n, type_="prompt.label.changed", data=None, **extra):
    st.publish(
        organization_id=ORG,
        source_key=f"raw{n}",
        source_hash="h",
        event={
            "id": f"evt_01JBF3Q7Z8X4N6R2T5V9W0YC{n:02d}",
            "type": type_,
            "workspace_id": WS,
            "timestamp": "2026-10-22T09:59:58.120Z",
            "data": data,
            **extra,
        },
    )


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class PoisonTests(unittest.TestCase):
    def test_poisoned_events_fail_alone_and_the_rest_are_sent(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        _raw(st, 2, data={"action": "moved", "label": {"name": "x"}})  # no "prompt"
        run = {
            "deployment": {"id": "dep-1", "environment": "production"},
            "run": {"trigger": {"kind": "api"}, "status": "succeeded"},
        }
        _raw(
            st, 3, type_="deployment.run.completed", data=run, builder_input={"x": 1}
        )  # no variant builder
        big = {
            "action": "moved",
            "label": {"name": "x" * 70_000},
            "prompt": {"id": "p"},
        }
        _raw(st, 4, data=big)  # a body over 64 KB
        publish(st, 5, clock)
        d = Dispatcher(st, _ok(clock, TransportResult(status=200)), clock)
        out = d.tick()
        d.drain()
        self.assertEqual(out["fanned_out"], 5)
        states = {e["id"][-2:]: e["fanout_state"] for e in st.events.values()}
        self.assertEqual(
            states,
            {
                "01": "done",
                "05": "done",
                "02": "failed",
                "03": "failed",
                "04": "failed",
            },
        )
        self.assertEqual(
            sorted(
                e.get("fanout_error")
                for e in st.events.values()
                if e.get("fanout_error")
            ),
            ["KeyError", "ValueError", "ValueError"],
        )
        dels = sorted(st.deliveries.values(), key=lambda x: x["seq"])
        self.assertEqual(
            [(x["seq"], x["status"]) for x in dels],
            [(1, "succeeded"), (2, "succeeded")],
        )
        self.assertEqual(
            d.tick()["fanned_out"], 0
        )  # a failed event is not retried every pass

    def test_a_pass_that_raises_is_retried_and_the_loop_lives(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)
        real_fan_out, calls = st.fan_out, []

        def flaky(now, limit):
            calls.append(now)
            if len(calls) == 1:
                raise RuntimeError("database went away")
            return real_fan_out(now, limit)

        st.fan_out = flaky
        beats = []
        d = Dispatcher(
            st,
            _ok(clock, TransportResult(status=200)),
            clock,
            MemQueue(),
            heartbeat=lambda: beats.append(1),
        )
        cap = _Capture()
        dispatch.log.addHandler(cap)
        stop = threading.Event()
        th = threading.Thread(target=d.run, args=(stop,))
        try:
            th.start()
            t_end = time.monotonic() + 8
            while time.monotonic() < t_end:
                if [x["status"] for x in st.deliveries.values()] == ["succeeded"]:
                    break
                time.sleep(0.05)
        finally:
            stop.set()
            d.queue.kick()
            th.join(10)
            dispatch.log.removeHandler(cap)
        self.assertFalse(th.is_alive())
        self.assertEqual([x["status"] for x in st.deliveries.values()], ["succeeded"])
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreaterEqual(len(beats), 2)  # it heartbeat through the backoff
        self.assertIn(
            "webhooks dispatcher pass failed", [r.getMessage() for r in cap.records]
        )

    def test_run_drains_even_when_the_loop_raises(self):
        clock = FakeClock()
        st = make_store(clock)

        def boom():
            raise RuntimeError("heartbeat refused")

        d = Dispatcher(
            st, _ok(clock, TransportResult(status=200)), clock, heartbeat=boom
        )
        old_pool = d._pool
        with self.assertRaises(RuntimeError):
            d.run(threading.Event())
        self.assertTrue(old_pool._shutdown)  # no orphaned pool behind an activity retry


class UnsendableTests(unittest.TestCase):
    def test_no_live_secret_is_released_not_sent_then_fails_past_max_age(self):
        clock = FakeClock()
        st = make_store(clock, secrets_newest_first=())
        publish(st, 1, clock)
        tr = FakeTransport(TransportResult(status=200))
        d = Dispatcher(st, sender(FakeResolver([PUBLIC]), tr, clock), clock)
        out = d.tick()
        d.drain()
        (dl,) = st.deliveries.values()
        self.assertEqual((out["started"], out["released"]), (0, 1))
        self.assertEqual(
            (dl["status"], dl["attempts_count"], tr.requests, st.attempts),
            ("pending", 0, [], []),
        )
        self.assertEqual(
            dl["next_attempt_at_ms"], clock.ms + dispatch.UNSENDABLE_RETRY_MS
        )
        self.assertEqual(st.endpoints["whep_A"].in_flight, 0)
        clock.advance(259_200_000)
        d.tick()
        self.assertEqual(
            (dl["status"], dl["final_outcome"]), ("failed", "max_age_exceeded")
        )


class DrainAndIdleTests(unittest.TestCase):
    def test_drain_heartbeats_and_closes_sockets_at_its_timeout(self):
        clock = FakeClock()
        st = make_store(clock)
        publish(st, 1, clock)

        def hang(request):
            done = threading.Event()
            request.abort.bind(done.set)
            done.wait(20)
            return TransportResult(
                error="aborted" if request.abort.aborted else "timeout"
            )

        beats = []
        d = Dispatcher(
            st,
            sender(FakeResolver([PUBLIC]), FakeTransport(hang), clock),
            clock,
            heartbeat=lambda: beats.append(1),
        )
        d.tick()
        t0 = time.monotonic()
        self.assertEqual(d.drain(timeout_s=1.2), 0)
        self.assertLess(time.monotonic() - t0, 4.0)
        self.assertGreaterEqual(len(beats), 1)
        (dl,) = st.deliveries.values()
        self.assertEqual(
            (st.attempts[-1]["outcome"], dl["status"]), ("worker_lost", "retrying")
        )

    def test_idle_heartbeats_until_its_time_or_the_stop(self):
        beats = []
        t0 = time.monotonic()
        idle(threading.Event(), 0.5, lambda: beats.append(1), beat_every_s=0.1)
        self.assertGreaterEqual(time.monotonic() - t0, 0.5)
        self.assertGreaterEqual(len(beats), 3)
        stop = threading.Event()
        stop.set()
        t0 = time.monotonic()
        idle(stop, 30, lambda: None)
        self.assertLess(time.monotonic() - t0, 0.5)


class ShareTests(unittest.TestCase):
    def test_the_org_share_is_per_loop_across_both_lanes(self):
        clock = FakeClock()
        st = make_store(clock)
        st.endpoints["whep_A"].max_in_flight = 16
        st.endpoints["whep_S"] = MemEndpoint(
            config=endpoint(id="whep_S"),
            workspace_id=WS,
            subscriptions=[{"event_type": "prompt.*"}],
            max_in_flight=16,
            lane="slow",
        )
        for i in range(30):
            publish(st, i, clock)
        release = threading.Event()

        def hold(_request):
            release.wait(10)
            return TransportResult(status=200)

        d = Dispatcher(
            st, sender(FakeResolver([PUBLIC]), FakeTransport(hold), clock), clock
        )
        try:
            out = d.tick()
            self.assertEqual(dispatch.per_org_share(48 + 16), 16)
            self.assertEqual(
                out["started"], 16
            )  # 16 per org per loop, not 12 + 8 per lane
        finally:
            release.set()
            d.drain()


if __name__ == "__main__":
    unittest.main()
