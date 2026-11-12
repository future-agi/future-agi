"""Incremental endpoint health on the state row (``webhooks.core.health``) against the whole-history rules
(``schedule.endpoint_health``) and the conformance vectors (DESIGN.md section 7.7). Standard library only."""

from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

from webhooks.core import health, schedule

CONF = Path(__file__).resolve().parents[1] / "conformance"
T0 = 1_792_663_200_000
OUTCOMES = (
    "success",
    "http_error",
    "rejected",
    "timeout",
    "timeout",
    "dns_error",
    "connect_error",
    "tls_error",
    "ssrf_blocked",
    "ssrf_blocked",
    "redirect_refused",
    "gone",
    "rate_limited",
    "worker_lost",
    "superseded",
)


def folded(attempts, scale=1.0):
    st = health.HealthState()
    for a in attempts:
        st = health.fold(st, a, scale)
    return st


class HealthVectorTests(unittest.TestCase):
    def test_every_endpoint_health_vector(self):
        cases = json.loads((CONF / "schedule.json").read_text())["endpoint_health"]
        self.assertGreaterEqual(len(cases), 17)
        for case in cases:
            with self.subTest(case["id"]):
                got = health.decide(folded(case["attempts"]), case["now_ms"])
                self.assertEqual(got, case["expected"])
                self.assertEqual(
                    got, schedule.endpoint_health(case["attempts"], case["now_ms"])
                )


class HealthEquivalenceTests(unittest.TestCase):
    def _history(self, rng, n):
        t, out = T0, []
        for _ in range(n):
            t += rng.choice((1, 500, 30_000, 120_000, 600_000, 3_600_000, 7_200_000))
            a = {"outcome": rng.choice(OUTCOMES), "at_ms": t}
            if rng.random() < 0.8:
                a["duration_ms"] = rng.choice((40, 900, 4_999, 5_001, 9_000, 15_000))
            if rng.random() < 0.05:
                a["test"] = True
            out.append(a)
        return out

    def test_random_histories_decide_as_the_whole_history_does(self):
        rng = random.Random(20261022)
        for i in range(3000):
            hist = self._history(rng, rng.randint(1, 60))
            scale = rng.choice((1.0, 1.0, 0.001, 0.0001))
            st = health.HealthState()
            for j, a in enumerate(hist):
                st = health.fold(st, a, scale)
                if rng.random() < 0.3 or j == len(hist) - 1:
                    for later in (0, 1, 600_000, 86_400_000):
                        now = a["at_ms"] + later
                        with self.subTest(i=i, j=j, later=later):
                            self.assertEqual(
                                health.decide(st, now, scale),
                                schedule.endpoint_health(hist[: j + 1], now, scale),
                            )

    def test_the_state_stays_bounded(self):
        st = folded(
            [
                {"outcome": "timeout", "at_ms": T0 + i, "duration_ms": 15_000}
                for i in range(500)
            ]
        )
        self.assertEqual(len(st.latencies_ms), 20)
        self.assertLessEqual(len(st.timeouts_ms), 500)
        st = health.fold(st, {"outcome": "timeout", "at_ms": T0 + 3_600_000}, 1.0)
        self.assertEqual(st.timeouts_ms, (T0 + 3_600_000,))


class HealthRuleTests(unittest.TestCase):
    def test_24h_rule_and_its_six_hour_counterpart(self):
        hour = 3_600_000
        down24 = [
            {"outcome": "http_error", "at_ms": T0 + i * (25 * hour // 10)}
            for i in range(11)
        ]
        self.assertEqual(
            health.decide(folded(down24), down24[-1]["at_ms"])["disable_endpoint"],
            "failing",
        )
        down6 = [
            {"outcome": "http_error", "at_ms": T0 + i * (6 * hour // 10)}
            for i in range(11)
        ]
        self.assertIsNone(
            health.decide(folded(down6), down6[-1]["at_ms"])["disable_endpoint"]
        )
        # Nine failures over two days are not enough; the tenth turns it off.
        nine = down24[:9]
        self.assertIsNone(
            health.decide(folded(nine), T0 + 48 * hour)["disable_endpoint"]
        )

    def test_failing_notice_at_most_once_per_24h(self):
        day = 86_400_000
        self.assertFalse(health.failing_notice_due(False, None, T0))
        self.assertTrue(health.failing_notice_due(True, None, T0))
        self.assertFalse(health.failing_notice_due(True, T0, T0 + day - 1))
        self.assertTrue(health.failing_notice_due(True, T0, T0 + day))
        self.assertFalse(health.failing_notice_due(True, T0, T0 + 8639, scale=0.0001))
        self.assertTrue(health.failing_notice_due(True, T0, T0 + 8640, scale=0.0001))

    def test_lane_after(self):
        slow = {"slow_lane": True, "slow_until_ms": T0 + 1_800_000}
        calm = {"slow_lane": False, "slow_until_ms": None}
        self.assertEqual(
            health.lane_after("normal", None, slow, T0), ("slow", T0 + 1_800_000)
        )
        # Never shortened by a later decision.
        self.assertEqual(
            health.lane_after("slow", T0 + 2_000_000, slow, T0),
            ("slow", T0 + 2_000_000),
        )
        # Stays slow until its 30 minutes are up, then the re-check sends it back.
        self.assertEqual(
            health.lane_after("slow", T0 + 10, calm, T0), ("slow", T0 + 10)
        )
        self.assertEqual(
            health.lane_after("slow", T0 + 10, calm, T0 + 10), ("normal", None)
        )


if __name__ == "__main__":
    unittest.main()
