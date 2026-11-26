"""Tests for devflow_tick.py: red cases fail on the wrong rule, green on the right one."""

import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import devflow_tick as t  # noqa: E402

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def fail(job, pr, at="2026-11-26T11:30:00Z"):
    return {"job": job, "pr": pr, "at": at}


def commit(n, subject, body=""):
    return {"sha": f"{n:040d}", "subject": subject, "body": body}


class Breaker(unittest.TestCase):
    def test_three_different_prs_trip(self):
        out = t.detect_breaker([fail("backend", 1), fail("backend", 2), fail("backend", 3)], NOW)
        self.assertEqual(out, [{"job": "backend", "prs": [1, 2, 3]}])

    def test_same_pr_three_times_does_not_trip(self):
        self.assertEqual(t.detect_breaker([fail("backend", 1)] * 3, NOW), [])

    def test_two_prs_do_not_trip(self):
        self.assertEqual(t.detect_breaker([fail("backend", 1), fail("backend", 2)], NOW), [])

    def test_old_failures_outside_window_ignored(self):
        old = "2026-11-26T09:00:00Z"
        self.assertEqual(t.detect_breaker([fail("e2e", n, old) for n in (1, 2, 3)], NOW), [])

    def test_different_jobs_do_not_add_up(self):
        self.assertEqual(t.detect_breaker([fail("a", 1), fail("b", 2), fail("c", 3)], NOW), [])

    def test_pr_number_from_queue_branch(self):
        self.assertEqual(t.pr_number({"head_branch": "gh-readonly-queue/dev/pr-4412-abc"}), 4412)
        self.assertIsNone(t.pr_number({"head_branch": "feat/x"}))


class Escape(unittest.TestCase):
    def history(self, extra):
        base = [commit(i, f"feat: thing (#{1000 + i})") for i in range(300, 0, -1)]
        return extra + base

    def test_revert_and_fix_forward_counted(self):
        extra = [
            commit(901, 'Revert "feat: thing (#1100)" (#2000)'),
            commit(902, "fix: patch (#2001)", "Follow-up to #1150"),
        ]
        out = t.escape_rate(self.history(extra))
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["total"], 100)
        self.assertEqual(out["rate"], 0.02)
        self.assertFalse(out["tight"])

    def test_three_escapes_is_above_two_percent(self):
        extra = [commit(920 + i, f"fix: z (#{2010 + i})", "fixes #1100") for i in range(3)]
        self.assertTrue(t.escape_rate(self.history(extra))["tight"])

    def test_issue_numbers_are_not_prs(self):
        out = t.escape_rate(self.history([commit(903, "fix: y (#2002)", "Fixes #5")]))
        self.assertEqual(out["count"], 0)

    def test_self_reference_ignored(self):
        out = t.escape_rate(self.history([commit(912, "fix: c (#2005)", "fixes #2005")]))
        self.assertEqual(out["count"], 0)


class Stats(unittest.TestCase):
    def runs(self):
        def mk(i, c):
            return {"id": i, "conclusion": c, "created_at": "2026-11-26T11:00:00Z", "updated_at": "2026-11-26T11:10:00Z"}

        return [mk(1, "success"), mk(2, "failure"), mk(3, "failure")]

    def test_red_classes_and_waits(self):
        jobs = {
            2: [{"name": "Build image", "conclusion": "failure", "created_at": "2026-11-26T11:00:00Z", "started_at": "2026-11-26T11:00:30Z"}],
            3: [{"name": "backend", "conclusion": "failure", "created_at": "2026-11-26T11:00:00Z", "started_at": "2026-11-26T11:02:00Z"}],
        }
        s = t.queue_stats(self.runs(), jobs, NOW)
        self.assertEqual(s["red"], {"infra": 1, "other": 1})
        self.assertAlmostEqual(s["red_rate"], 2 / 3)
        self.assertEqual(s["queue_p90_s"], 600)
        self.assertEqual(s["runner_p95_s"], 120)

    def test_mixed_failed_jobs_count_as_other(self):
        jobs = {2: [{"name": "Build image", "conclusion": "failure"}, {"name": "unit tests", "conclusion": "failure"}]}
        self.assertEqual(t.queue_stats(self.runs()[:2], jobs, NOW)["red"], {"infra": 0, "other": 1})

    def test_empty_is_na_not_crash(self):
        s = t.queue_stats([], {}, NOW)
        self.assertIsNone(s["queue_p90_s"])
        self.assertEqual(t.fmt_dur(None), "n/a")

    def test_dirty_share(self):
        self.assertEqual(t.dirty_share(["CONFLICTING", "MERGEABLE", "MERGEABLE", "UNKNOWN"]), 0.25)
        self.assertIsNone(t.dirty_share([]))


class Digest(unittest.TestCase):
    def test_render_contains_every_field_and_breaker(self):
        stats = t.queue_stats([], {}, NOW)
        esc = {"count": 3, "total": 100, "rate": 0.03, "tight": True}
        text = t.render_digest(17, stats, esc, 0.1, [{"job": "backend", "prs": [1, 2, 3]}])
        for needle in ("landings in the last hour: 17", "queue red rate", "p90", "p95", "3.0% (3)", "tight approval", "10.0%", "`backend`"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
