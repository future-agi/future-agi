"""The ops alert rules of DESIGN.md section 13.3 (``webhooks.core.alerts``). Standard library only."""

from __future__ import annotations

import unittest

from webhooks.core import alerts

T0 = 1_792_663_200_000
MIN = 60_000


def step(snapshot, prior, now, scale=1.0):
    states, changes = alerts.evaluate(snapshot, prior, now, scale)
    return states, {(c.id, c.fired) for c in changes}


class AlertTests(unittest.TestCase):
    def test_wa1_fires_after_five_minutes_behind_and_clears(self):
        behind = {"oldest_due_seconds": 61, "due": 5, "last_beat_age_seconds": 1}
        s, ch = step(behind, {}, T0)
        self.assertEqual(ch, set())
        self.assertFalse(s["WA1"].active)
        s, ch = step(behind, s, T0 + 5 * MIN - 1)
        self.assertEqual(ch, set())
        s, ch = step(behind, s, T0 + 5 * MIN)
        self.assertEqual(ch, {("WA1", True)})
        s, ch = step(behind, s, T0 + 6 * MIN)
        self.assertEqual(ch, set())  # fires once
        s, ch = step(
            {"oldest_due_seconds": 3, "due": 1, "last_beat_age_seconds": 1},
            s,
            T0 + 7 * MIN,
        )
        self.assertEqual(ch, {("WA1", False)})
        # The clock restarts: a new 5 minutes are needed.
        s, ch = step(behind, s, T0 + 8 * MIN)
        self.assertEqual(ch, set())
        self.assertEqual(s["WA1"].since_ms, T0 + 8 * MIN)

    def test_wa2_only_while_something_is_due(self):
        _, ch = step({"due": 0, "last_beat_age_seconds": None}, {}, T0)
        self.assertEqual(ch, set())
        _, ch = step({"due": 3, "last_beat_age_seconds": 121}, {}, T0)
        self.assertEqual(ch, {("WA2", True)})
        _, ch = step({"due": 3, "last_beat_age_seconds": 119}, {}, T0)
        self.assertEqual(ch, set())
        _, ch = step({"due": 3, "last_beat_age_seconds": None}, {}, T0)
        self.assertEqual(ch, {("WA2", True)})

    def test_wa2_sees_one_loop_of_two_down_and_names_it(self):
        both = {"webhooks-dispatcher-1": 3.0, "webhooks-dispatcher-2": 4.0}
        _, ch = step({"due": 3, "loop_beat_age_seconds": both}, {}, T0)
        self.assertEqual(ch, set())
        one_down = {"webhooks-dispatcher-1": 0.0, "webhooks-dispatcher-2": 600.0}
        s, ch = step({"due": 3, "loop_beat_age_seconds": one_down}, {}, T0)
        self.assertEqual(ch, {("WA2", True)})
        self.assertEqual(
            s["WA2"].detail, {"due": 3, "stale_loops": ["webhooks-dispatcher-2"]}
        )
        never = {"webhooks-dispatcher-1": 1.0, "webhooks-dispatcher-2": None}
        s, ch = step({"due": 1, "loop_beat_age_seconds": never}, {}, T0)
        self.assertEqual(s["WA2"].detail["stale_loops"], ["webhooks-dispatcher-2"])
        # The per-loop map wins over the newest-beat summary.
        _, ch = step(
            {"due": 3, "loop_beat_age_seconds": one_down, "last_beat_age_seconds": 0.0},
            {},
            T0,
        )
        self.assertEqual(ch, {("WA2", True)})
        _, ch = step({"due": 0, "loop_beat_age_seconds": one_down}, {}, T0)
        self.assertEqual(ch, set())

    def test_wa3_wa4_wa5_wa8_wa9(self):
        s, _ = step({"fanout_lag_p95_seconds": 11}, {}, T0)
        _, ch = step({"fanout_lag_p95_seconds": 11}, s, T0 + 10 * MIN)
        self.assertIn(("WA3", True), ch)
        egress = {"attempts_5m": 100, "net_errors_5m": 51, "net_error_endpoints_5m": 20}
        self.assertIn(("WA4", True), step(egress, {}, T0)[1])
        self.assertNotIn(
            ("WA4", True), step({**egress, "net_error_endpoints_5m": 19}, {}, T0)[1]
        )
        self.assertNotIn(
            ("WA4", True), step({**egress, "net_errors_5m": 50}, {}, T0)[1]
        )
        s, ch = step({"ssrf_blocked_1m_by_org": {"o1": 21, "o2": 20}}, {}, T0)
        self.assertIn(("WA5", True), ch)
        self.assertEqual(s["WA5"].detail, {"organizations": ["o1"]})
        self.assertIn(("WA8", True), step({"superseded_10m": 1}, {}, T0)[1])
        self.assertIn(
            ("WA9", True), step({"orgs_over_attempt_quota": ["o9"]}, {}, T0)[1]
        )

    def test_scale_shrinks_windows_not_counts(self):
        behind = {"oldest_due_seconds": 0.7, "due": 1, "last_beat_age_seconds": 0.0}
        s, ch = step(behind, {}, T0, scale=0.01)
        self.assertEqual(ch, set())
        _, ch = step(behind, s, T0 + 3000, scale=0.01)
        self.assertEqual(ch, {("WA1", True)})
        _, ch = step({"ssrf_blocked_1m_by_org": {"o": 20}}, {}, T0, scale=0.01)
        self.assertEqual(ch, set())

    def test_backlog_alerts_hold_back_while_sending_is_off(self):
        off = {
            "sending_blocked": True,
            "due": 50,
            "oldest_due_seconds": 9999,
            "last_beat_age_seconds": None,
            "fanout_lag_p95_seconds": 99,
            "superseded_10m": 1,
        }
        s, ch = step(off, {}, T0)
        s, ch = step(off, s, T0 + 60 * MIN)
        self.assertEqual(ch, set())
        self.assertIn(("WA8", True), step(off, {}, T0)[1])  # the rest still apply

    def test_wa10_one_failed_fan_out_fires_at_once_and_clears_when_the_window_is_empty(
        self,
    ):
        s, ch = step({"fanout_failed_10m": 0}, {}, T0)
        self.assertEqual(ch, set())
        s, ch = step({"fanout_failed_10m": 1}, s, T0 + MIN)
        self.assertEqual(ch, {("WA10", True)})
        self.assertEqual(s["WA10"].detail, {"events": 1})
        s, ch = step({"fanout_failed_10m": 3}, s, T0 + 2 * MIN)
        self.assertEqual(ch, set())  # fires once while it holds
        s, ch = step({"fanout_failed_10m": 0}, s, T0 + 12 * MIN)
        self.assertEqual(ch, {("WA10", False)})
        # Sending deliberately off does not hold it back: the event is lost whatever the install's state.
        _, ch = step({"sending_blocked": True, "fanout_failed_10m": 1}, {}, T0)
        self.assertIn(("WA10", True), ch)

    def test_every_rule_has_a_design_id(self):
        self.assertEqual(
            [r.id for r in alerts.RULES],
            ["WA1", "WA2", "WA3", "WA4", "WA5", "WA8", "WA9", "WA10"],
        )


if __name__ == "__main__":
    unittest.main()
