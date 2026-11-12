"""Review fixes, without Django: error reports and reprs that carry no secret (K2), a test answered 410, a URL outside
the policy, and the excerpt rules. Run from ``futureagi/``: ``python -m unittest webhooks.core.tests.test_core_redaction``.
"""

from __future__ import annotations

import unittest

from webhooks.core import dispatch
from webhooks.core.attempt import AttemptRecord, excerpt, inert, next_action_for
from webhooks.core.dispatch import Dispatcher
from webhooks.core.ports import PinnedPost, TransportResult
from webhooks.core.tests.fakes import (
    SECRET_A,
    FakeClock,
    FakeResolver,
    FakeTransport,
    claim,
    endpoint,
    make_store,
    publish,
    sender,
)
from webhooks.core.tests.test_core_dispatch_faults import (
    BODY_MARK,
    HEADER_VALUE,
    PUBLIC,
    QUERY_TOKEN,
    _Capture,
    _ok,
    _raw,
)


class TestDeliveryAndPolicyTests(unittest.TestCase):
    def test_a_test_answered_410_fails_and_never_disables(self):
        rec = AttemptRecord(
            n=1,
            trigger="test",
            started_at_ms=0,
            settled_at_ms=1,
            response_status=410,
            outcome="gone",
        )
        action = next_action_for(claim(is_test=True, seq=None, trigger="test"), rec)
        self.assertEqual(
            action,
            {
                "outcome": "gone",
                "action": "give_up",
                "delivery_status": "failed",
                "final_outcome": "test_not_retried",
            },
        )
        clock = FakeClock()
        st = make_store(clock)
        _raw(
            st,
            9,
            data={"action": "moved", "label": {"name": "x"}, "prompt": {"id": "p"}},
            test=True,
            test_endpoint_id="whep_A",
        )
        d = Dispatcher(st, _ok(clock, TransportResult(status=410)), clock)
        d.tick()
        d.drain()
        (dl,) = st.deliveries.values()
        self.assertEqual(
            (dl["status"], dl["final_outcome"], dl["seq"]),
            ("failed", "test_not_retried", None),
        )
        self.assertEqual(st.attempts[-1]["outcome"], "gone")
        self.assertEqual(
            (st.endpoints["whep_A"].config.status, st.endpoints["whep_A"].paused_count),
            ("enabled", 0),
        )

    def test_a_url_outside_the_policy_blocks_the_endpoint_and_pauses(self):
        clock = FakeClock()
        st = make_store(clock, allowed_hosts={"org_allowed_hosts": ["*.other.ai"]})
        for i in range(3):
            publish(st, i, clock)
        tr = FakeTransport(TransportResult(status=200))
        d = Dispatcher(st, sender(FakeResolver([PUBLIC]), tr, clock), clock)
        d.tick()
        d.drain()
        ep = st.endpoints["whep_A"]
        self.assertEqual(
            (ep.config.status, ep.disabled_reason), ("blocked_by_policy", "policy")
        )
        self.assertEqual(
            sorted(x["status"] for x in st.deliveries.values()), ["paused"] * 3
        )
        self.assertEqual(
            [x["attempts_count"] for x in st.deliveries.values()], [0, 0, 0]
        )
        self.assertEqual(
            (st.attempts, tr.requests, ep.paused_count, ep.in_flight), ([], [], 3, 0)
        )
        rec = AttemptRecord(
            n=1,
            trigger="test",
            started_at_ms=0,
            outcome="ssrf_blocked",
            policy_blocked=True,
        )
        self.assertEqual(
            next_action_for(claim(is_test=True), rec)["final_outcome"],
            "test_not_retried",
        )


class RedactionTests(unittest.TestCase):
    def _config(self):
        return endpoint(
            url=f"https://hooks.acme.ai/omega?token={QUERY_TOKEN}",
            custom_headers=(("Authorization", HEADER_VALUE),),
        )

    def _assert_clean(self, text):
        for needle in ("whsec_", QUERY_TOKEN, HEADER_VALUE, BODY_MARK, SECRET_A[6:20]):
            self.assertNotIn(needle, text)

    def test_reprs_carry_no_secret_url_header_or_body(self):
        c = claim(self._config(), body=BODY_MARK.encode())
        post = PinnedPost(
            scheme="https",
            host="hooks.acme.ai",
            port=443,
            target=f"/omega?token={QUERY_TOKEN}",
            addresses=(PUBLIC,),
            headers=(("Authorization", HEADER_VALUE),),
            body=BODY_MARK.encode(),
            deadline=1.0,
        )
        rec = AttemptRecord(
            n=1, trigger="auto", started_at_ms=0, signature_header="v1,c2lnbmF0dXJl"
        )
        for obj in (
            c,
            c.endpoint,
            post,
            rec,
            TransportResult(status=200, body=BODY_MARK.encode()),
        ):
            self._assert_clean(repr(obj))
        self.assertNotIn("c2lnbmF0dXJl", repr(rec))

    def test_a_crashed_send_is_reported_without_frame_variables(self):
        clock = FakeClock()
        st = make_store(clock)
        st.endpoints["whep_A"].config = self._config()
        publish(st, 1, clock)

        def explode(_request):
            raise RuntimeError("transport bug")

        cap = _Capture()
        dispatch.log.addHandler(cap)
        try:
            d = Dispatcher(
                st, sender(FakeResolver([PUBLIC]), FakeTransport(explode), clock), clock
            )
            d.tick()
            d.drain()
        finally:
            dispatch.log.removeHandler(cap)
        (rec,) = [r for r in cap.records if r.getMessage() == "webhooks send failed"]
        tb, frames = rec.exc_info[2], 0
        while tb is not None:
            frames += 1
            self._assert_clean(repr(dict(tb.tb_frame.f_locals)))
            tb = tb.tb_next
        self.assertGreaterEqual(
            frames, 3
        )  # the stack is kept; only the variables are gone


class TextTests(unittest.TestCase):
    def test_inert_drops_c1_controls_and_bidi_marks(self):
        self.assertEqual(inert("a\x85b‎c؜d‏e‮f\x9fg\tok\n"), "abcdefg\tok\n")

    def test_excerpt_is_cut_at_2_kb_of_utf8(self):
        out = excerpt(("é" * 3000).encode())
        self.assertLessEqual(len(out.encode("utf-8")), 2048)
        self.assertEqual(out, "é" * 1024)


if __name__ == "__main__":
    unittest.main()
