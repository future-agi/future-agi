"""Tests for ci_base_gate.py, on payloads recorded from this repository.

fixtures/ci_base_gate holds real API responses, trimmed to the fields the gate
reads (profile URLs, commit bodies and app details removed):

- PR #4022: GitHub retargeted it automatically (automatic_base_change_succeeded
  at 12:04:45 on 16 Nov) after its Backend CI and E2E CI ran at 06:34:25; only
  Python lint ran again. It merged on those checks: the stale case.
- PR #4293: retargeted by hand (base_ref_changed at 22:58:10 on 21 Nov); its
  head's Backend CI and E2E CI runs were created later: the fresh case.
- PR #4332: never retargeted.
"""

import json
import pathlib
import re
import sys
import unittest
import urllib.error

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import ci_base_gate as gate  # noqa: E402

FIXTURES = SCRIPTS / "fixtures/ci_base_gate"
GUARDED = (".github/workflows/backend-ci.yml", ".github/workflows/e2e-ci.yml")


def load(name):
    return json.loads((FIXTURES / name).read_text())


def head(runs):
    return {run["head_sha"] for run in runs["workflow_runs"]}.pop()


class Fake:
    """A fetch that serves the timeline, then each runs payload in turn."""

    def __init__(self, timeline, *runs, error=None):
        self.timeline = timeline
        self.runs = list(runs)
        self.error = error
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        if "/timeline" in url:
            return self.timeline, ""
        payload = self.runs[0] if len(self.runs) == 1 else self.runs.pop(0)
        return payload, ""


def run(fetch, runs, base_ref="main", action="edited", pr="4022"):
    sleeps = []
    code, lines = gate.decide(
        fetch,
        repo="future-agi/future-agi",
        pr=pr,
        head_sha=head(runs),
        base_ref=base_ref,
        action=action,
        sleep=sleeps.append,
    )
    return code, lines, sleeps


class VerdictTests(unittest.TestCase):
    def test_an_automatic_retarget_after_the_runs_is_stale(self):
        timeline, runs = load("timeline-4022.json"), load("runs-4022.json")
        self.assertEqual(gate.newest_base_change(timeline), "2026-11-16T12:04:45Z")
        # Python lint ran after the retarget; only Backend CI and E2E CI count.
        later = [
            r["name"]
            for r in runs["workflow_runs"]
            if r["created_at"] > "2026-11-16T12:04:45Z"
        ]
        self.assertIn("Python lint", later)
        self.assertTrue(set(later).isdisjoint({"Backend CI", "E2E CI"}))
        state, detail = gate.verdict(timeline, runs["workflow_runs"], head(runs))
        self.assertEqual(state, "stale")
        self.assertIn("backend-ci.yml, e2e-ci.yml", detail)

    def test_a_retarget_before_the_runs_is_ok(self):
        timeline, runs = load("timeline-4293.json"), load("runs-4293.json")
        self.assertEqual(gate.newest_base_change(timeline), "2026-11-21T22:58:10Z")
        state, _ = gate.verdict(timeline, runs["workflow_runs"], head(runs))
        self.assertEqual(state, "ok")

    def test_no_base_change_is_ok_without_reading_runs_into_it(self):
        timeline = load("timeline-4332.json")
        self.assertIsNone(gate.newest_base_change(timeline))
        self.assertEqual(gate.verdict(timeline, [], "0" * 40)[0], "ok")

    def test_a_rerun_keeps_created_at_so_stays_stale(self):
        timeline, runs = load("timeline-4022.json"), load("runs-4022.json")
        rerun = [
            dict(r, run_attempt=2, run_started_at="2026-11-16T13:00:00Z")
            if r["path"] in GUARDED
            else r
            for r in runs["workflow_runs"]
        ]
        self.assertEqual(gate.verdict(timeline, rerun, head(runs))[0], "stale")

    def test_runs_of_another_head_or_event_do_not_count(self):
        timeline, runs = load("timeline-4022.json"), load("runs-4022.json")
        late = [
            dict(r, created_at="2026-11-16T13:00:00Z", head_sha="f" * 40)
            if r["path"] in GUARDED
            else r
            for r in runs["workflow_runs"]
        ] + [
            dict(r, created_at="2026-11-16T13:00:00Z", event="push")
            for r in runs["workflow_runs"]
            if r["path"] in GUARDED
        ]
        self.assertEqual(gate.verdict(timeline, late, head(runs))[0], "stale")


class DecideTests(unittest.TestCase):
    def test_stale_fails_on_main_and_passes_with_a_notice_elsewhere(self):
        timeline, runs = load("timeline-4022.json"), load("runs-4022.json")
        code, lines, _ = run(Fake(timeline, runs), runs, base_ref="main")
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith("::error title=CI ran on this base::"))
        self.assertIn("Push a commit, or close and reopen", lines[0])
        code, lines, _ = run(Fake(timeline, runs), runs, base_ref="dev")
        self.assertEqual(code, 0)
        self.assertTrue(lines[0].startswith("::notice"))
        self.assertIn("Into main it would fail", lines[0])

    def test_an_edit_judges_once_without_polling(self):
        timeline, runs = load("timeline-4022.json"), load("runs-4022.json")
        _, _, sleeps = run(Fake(timeline, runs), runs, action="edited")
        self.assertEqual(sleeps, [])

    def test_a_push_polls_until_its_own_runs_are_listed(self):
        timeline, runs = load("timeline-4293.json"), load("runs-4293.json")
        before = {
            **runs,
            "workflow_runs": [
                r for r in runs["workflow_runs"] if r["path"] not in GUARDED
            ],
        }
        fake = Fake(timeline, before, before, runs)
        code, lines, sleeps = run(fake, runs, action="synchronize", pr="4293")
        self.assertEqual(code, 0, lines)
        self.assertEqual(sleeps, [gate.POLL_SECONDS] * 2)
        self.assertEqual(sum("/actions/runs" in url for url in fake.calls), 3)

    def test_no_runs_after_every_poll_is_stale(self):
        timeline, runs = load("timeline-4293.json"), load("runs-4293.json")
        before = {
            **runs,
            "workflow_runs": [
                r for r in runs["workflow_runs"] if r["path"] not in GUARDED
            ],
        }
        code, lines, sleeps = run(
            Fake(timeline, before), runs, action="reopened", pr="4293"
        )
        self.assertEqual(code, 1, lines)
        self.assertEqual(sleeps, [gate.POLL_SECONDS] * gate.POLLS)

    def test_an_api_error_fails_on_main_and_warns_elsewhere(self):
        runs = load("runs-4022.json")
        error = urllib.error.HTTPError(
            "https://api.github.com/x", 403, "Forbidden", None, None
        )
        code, lines, _ = run(Fake([], runs, error=error), runs, base_ref="main")
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith("::error"), lines)
        code, lines, _ = run(Fake([], runs, error=error), runs, base_ref="dev")
        self.assertEqual(code, 0)
        self.assertTrue(lines[0].startswith("::warning"), lines)

    def test_pagination_follows_next_and_fails_closed_past_the_limit(self):
        pages = iter([([1], '<https://api.github.com/p2>; rel="next"'), ([2], "")])
        self.assertEqual(gate.paginate(lambda url: next(pages), "u"), [1, 2])
        endless = lambda url: ([0], '<https://api.github.com/n>; rel="next"')  # noqa: E731
        with self.assertRaises(ValueError):
            gate.paginate(endless, "u")


class WorkflowTests(unittest.TestCase):
    TEXT = (SCRIPTS.parent / "workflows/ci-base-gate.yml").read_text()

    def test_it_only_reads_and_is_never_skipped(self):
        permissions = re.search(r"(?ms)^permissions:\n(.*?)(?=^\S)", self.TEXT)[1]
        granted = re.findall(r"(?m)^  ([a-z-]+): (\w+)$", permissions)
        self.assertEqual(
            sorted(granted),
            [("actions", "read"), ("contents", "read"), ("pull-requests", "read")],
        )
        jobs = self.TEXT.split("\njobs:\n", 1)[1]
        self.assertNotRegex(jobs, r"(?m)^    if:")
        self.assertIn("persist-credentials: false", jobs)
        self.assertIn(
            "  pull_request:\n    types: [opened, synchronize, reopened, edited]",
            self.TEXT,
        )

    def test_its_name_is_never_a_protected_check(self):
        name = re.search(r"(?m)^    name: (.+)$", self.TEXT)[1]
        self.assertEqual(name, "CI ran on this base")
        self.assertNotIn(
            name, {"Backend Tests Pass", "E2E Tests Pass", "Python Lint Pass"}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
