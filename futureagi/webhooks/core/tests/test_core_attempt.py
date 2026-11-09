"""One attempt over the ports, without Django: URL rules again, resolve, vet, sign at send, classify (DESIGN.md
sections 5.2, 6.1, 6.2, 7.2 and 8.1). Run from ``futureagi/``: ``python -m unittest webhooks.core.tests.test_core_attempt``.
"""

from __future__ import annotations

import unittest

from webhooks.core import signing
from webhooks.core.attempt import Sender, excerpt, next_action_for
from webhooks.core.ipclass import Profile
from webhooks.core.ports import ResolveError, TransportResult
from webhooks.core.tests.fakes import (
    SECRET_A,
    SECRET_B,
    T0,
    FakeClock,
    FakeResolver,
    FakeTransport,
    claim,
    endpoint,
    psl,
    sender,
)


class AttemptTests(unittest.TestCase):
    def test_success_signs_at_send_and_pins_the_vetted_address(self):
        tr = FakeTransport(
            TransportResult(
                status=204, headers={"Server": "x\u202e"}, connected_ip="93.184.215.14"
            )
        )
        clock = FakeClock()
        rec = sender(FakeResolver(["93.184.215.14"]), tr, clock).attempt(claim())
        self.assertEqual(rec.outcome, "success")
        req = tr.requests[0]
        self.assertEqual(
            (req.host, req.port, req.target, req.addresses),
            ("hooks.acme.ai", 443, "/omega?team=7", ("93.184.215.14",)),
        )
        h = dict(req.headers)
        self.assertEqual(h["webhook-timestamp"], str(T0 // 1000))
        self.assertEqual(signing.verify(SECRET_A, h, req.body, T0 // 1000), "ok")
        self.assertEqual(h["X-FutureAGI-Sequence"], "7")
        self.assertEqual(rec.resolved_ip, "93.184.215.14")
        self.assertEqual(rec.response_headers, {"server": "x"})
        self.assertAlmostEqual(req.deadline, 1000.0 + 15.0)

    def test_custom_headers_are_sent_literally_and_logged_masked(self):
        tr = FakeTransport(TransportResult(status=200))
        ep = endpoint(
            custom_headers=(("Authorization", "Bearer abcdefghijklmnop${HOME}"),)
        )
        rec = sender(FakeResolver(["93.184.215.14"]), tr).attempt(claim(ep))
        self.assertEqual(
            dict(tr.requests[0].headers)["Authorization"],
            "Bearer abcdefghijklmnop${HOME}",
        )
        self.assertEqual(rec.request_headers["Authorization"], "••••OME}")
        self.assertTrue(rec.request_headers["webhook-signature"].startswith("v1,…"))
        self.assertNotIn(rec.signature_header, str(rec.request_headers))

    def test_overlap_sends_two_signatures_newest_first(self):
        tr = FakeTransport(TransportResult(status=200))
        rec = sender(FakeResolver(["93.184.215.14"]), tr).attempt(
            claim(endpoint(secrets_newest_first=(SECRET_A, SECRET_B)))
        )
        sig = dict(tr.requests[0].headers)["webhook-signature"]
        self.assertEqual(len(sig.split(" ")), 2)
        self.assertEqual(rec.signature_key_tails, [SECRET_A[-4:], SECRET_B[-4:]])
        for s in (SECRET_A, SECRET_B):
            self.assertEqual(
                signing.verify(
                    s, dict(tr.requests[0].headers), tr.requests[0].body, T0 // 1000
                ),
                "ok",
            )

    def test_rebinding_to_a_private_answer_never_connects_and_hides_the_address(self):
        tr = FakeTransport(TransportResult(status=200))
        rec = sender(FakeResolver(["93.184.215.14", "10.0.0.7"]), tr).attempt(claim())
        self.assertEqual(rec.outcome, "ssrf_blocked")
        self.assertEqual(tr.requests, [])
        self.assertNotIn("10.0.0.7", rec.error_detail)
        self.assertIn("10.0.0.7", rec.staff_detail)
        self.assertIsNone(rec.resolved_ip)

    def test_an_install_address_guard_runs_after_the_tables(self):
        tr = FakeTransport(TransportResult(status=200))
        s = Sender(
            Profile(mode="hosted"),
            psl,
            FakeResolver(["93.184.215.14"]),
            tr,
            FakeClock(),
            address_guard=lambda a: a != "93.184.215.14",
        )
        rec = s.attempt(claim())
        self.assertEqual((rec.outcome, tr.requests), ("ssrf_blocked", []))
        self.assertNotIn("93.184.215.14", rec.error_detail)

    def test_metadata_through_nat64_is_blocked(self):
        tr = FakeTransport(TransportResult(status=200))
        rec = sender(FakeResolver(["64:ff9b::a9fe:a9fe"]), tr).attempt(claim())
        self.assertEqual((rec.outcome, tr.requests), ("ssrf_blocked", []))

    def test_stored_url_that_no_longer_passes_is_blocked_before_any_lookup(self):
        res = FakeResolver(["93.184.215.14"])
        rec = sender(res, FakeTransport(TransportResult(status=200))).attempt(
            claim(
                endpoint(
                    url="https://hooks.acme.ai/x",
                    allowed_hosts={"org_allowed_hosts": ["*.other.ai"]},
                )
            )
        )
        self.assertEqual((rec.outcome, res.calls), ("ssrf_blocked", []))

    def test_dns_failure_and_budget(self):
        res = FakeResolver(ResolveError("NXDOMAIN"))
        rec = sender(res, FakeTransport(TransportResult(status=200))).attempt(claim())
        self.assertEqual(rec.outcome, "dns_error")
        self.assertEqual(res.calls[0], ("hooks.acme.ai", 2.0))
        rec = sender(
            FakeResolver([]), FakeTransport(TransportResult(status=200))
        ).attempt(claim())
        self.assertEqual(rec.outcome, "dns_error")

    def test_answers_classify_as_the_schedule_says(self):
        cases = [
            (500, "http_error"),
            (400, "rejected"),
            (413, "rejected"),
            (410, "gone"),
            (429, "rate_limited"),
            (301, "redirect_refused"),
        ]
        for status, outcome in cases:
            with self.subTest(status=status):
                tr = FakeTransport(
                    TransportResult(
                        status=status, headers={"Location": "https://10.0.0.1/x"}
                    )
                )
                rec = sender(FakeResolver(["93.184.215.14"]), tr).attempt(claim())
                self.assertEqual(rec.outcome, outcome)
                if status == 301:
                    self.assertEqual(
                        rec.error_detail,
                        "Redirected to another address; update the URL.",
                    )

    def test_transport_errors_and_excerpt(self):
        rec = sender(
            FakeResolver(["93.184.215.14"]),
            FakeTransport(TransportResult(error="timeout")),
        ).attempt(claim())
        self.assertEqual(rec.outcome, "timeout")
        body = ("ok sk-proj-abcdefghijkl \x1b[31m" + "y" * 5000).encode()
        text = excerpt(body)
        self.assertLessEqual(len(text), 2048)
        self.assertNotIn("sk-proj", text)
        self.assertNotIn("\x1b", text)

    def test_test_delivery_carries_the_test_header_and_is_never_retried(self):
        tr = FakeTransport(TransportResult(status=500))
        c = claim(
            is_test=True, seq=None, event_id="evt_test_01JBF3Q7Z8X4N6R2T5V9W0YB1C"
        )
        rec = sender(FakeResolver(["93.184.215.14"]), tr).attempt(c)
        h = dict(tr.requests[0].headers)
        self.assertEqual(h["X-FutureAGI-Test"], "1")
        self.assertNotIn("X-FutureAGI-Sequence", h)
        self.assertEqual(next_action_for(c, rec)["action"], "give_up")


if __name__ == "__main__":
    unittest.main()
