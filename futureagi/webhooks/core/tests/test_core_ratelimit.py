"""The shared per-endpoint send rate (``webhooks.core.ratelimit``; DESIGN.md section 7.8, WT-20). Standard library
only: two "replicas" claiming at independent, irregular moments against one ``tat`` (the state row)."""

from __future__ import annotations

import random
import unittest

from webhooks.core import ratelimit


def run(rate, passes_ms, max_in_flight=None):
    """Claims at the given moments, as many starts as ``room`` allows; returns the reserved starts."""
    tat, starts = None, []
    for now in passes_ms:
        for _ in range(ratelimit.room(tat, now, rate)):
            start, tat, wait = ratelimit.reserve(tat, now, rate)
            assert start is not None and wait == 0
            starts.append(start)
        start, _tat, wait = ratelimit.reserve(tat, now, rate)
        assert start is None and wait >= 1, "room() must equal what reserve() allows"
    return starts


class RateTests(unittest.TestCase):
    def test_two_replicas_never_exceed_rate_plus_one_in_any_second(self):
        rng = random.Random(7)
        for rate in (1, 3, 10, 37, 100):
            a = [i * 250 + rng.randint(0, 40) for i in range(240)]
            b = [i * 250 + 125 + rng.randint(0, 40) for i in range(240)]
            starts = run(rate, sorted(a + b))
            with self.subTest(rate=rate):
                gaps = [y - x for x, y in zip(starts, starts[1:], strict=False)]
                self.assertGreaterEqual(min(gaps), ratelimit.interval_ms(rate) - 1e-6)
                self.assertLessEqual(ratelimit.max_in_window(starts), rate + 1)
                # Passing every BURST_MS keeps the endpoint at its rate.
                span_s = (starts[-1] - starts[0]) / 1000
                self.assertGreaterEqual(len(starts) / span_s, rate * 0.97)

    def test_a_starved_bucket_waits_and_says_how_long(self):
        start, tat, wait = ratelimit.reserve(None, 0, 1)
        self.assertEqual((start, tat, wait), (0, 1000.0, 0))
        start, tat2, wait = ratelimit.reserve(tat, 0, 1)
        self.assertIsNone(start)
        self.assertEqual(tat2, tat)
        self.assertEqual(wait, 1000 - ratelimit.BURST_MS)
        self.assertEqual(ratelimit.room(tat, 0, 1), 0)
        self.assertEqual(ratelimit.room(tat, 1000, 1), 1)

    def test_halved_rate(self):
        self.assertEqual(ratelimit.effective_rate(10, True), 5)
        self.assertEqual(ratelimit.effective_rate(1, True), ratelimit.MIN_RATE)
        starts = run(ratelimit.effective_rate(10, True), range(0, 20_000, 250))
        self.assertLessEqual(ratelimit.max_in_window(starts), 6)

    def test_idle_time_is_not_banked(self):
        # After a long idle spell a claim gets one burst window's worth, never a backlog of starts.
        tat = 0.0
        self.assertEqual(ratelimit.room(tat, 60_000, 10), 3)

    def test_max_in_window(self):
        self.assertEqual(ratelimit.max_in_window([0, 100, 999, 1000, 1001]), 4)
        self.assertEqual(ratelimit.max_in_window([]), 0)


if __name__ == "__main__":
    unittest.main()
