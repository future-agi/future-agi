"""Tests for devflow_tick.py: red cases fail on the wrong rule, green on the right one."""

import json
import pathlib
import sys
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

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


DEV_TIP = "d" * 40
SHARD = "backend / pytest shard 3"


def group(run_id, pr, head, base=DEV_TIP, created="2026-11-26T11:00:00Z", conclusion="failure"):
    """One backend merge-queue run of PR `pr`, built on `base`."""
    return {"id": run_id, "head_sha": head, "head_branch": f"gh-readonly-queue/dev/pr-{pr}-{base}",
            "path": ".github/workflows/backend-queue-ci.yml", "created_at": created,
            "updated_at": created, "status": "completed", "conclusion": conclusion}


def shard_failed(at="2026-11-26T11:30:00Z"):
    return [{"name": SHARD, "status": "completed", "conclusion": "failure", "completed_at": at}]


class Cascade(unittest.TestCase):
    """Queue groups are cumulative: count a failure once, on the entry that caused it."""

    def trips(self, runs, jobs, dev=()):
        return t.detect_breaker(t.independent(t.queue_failures(runs, jobs), runs, jobs) + list(dev), NOW)

    def test_one_front_defect_with_two_entries_behind_does_not_trip(self):
        # Red before: three PRs failed the shard, so the breaker tripped.
        runs = [group(1, 10, "1" * 40), group(2, 11, "2" * 40, base="1" * 40), group(3, 12, "3" * 40, base="2" * 40)]
        jobs = {r["id"]: shard_failed() for r in runs}
        self.assertEqual(len(t.detect_breaker(t.queue_failures(runs, jobs), NOW)), 1)
        self.assertEqual(self.trips(runs, jobs), [])
        self.assertEqual([f["pr"] for f in t.independent(t.queue_failures(runs, jobs), runs, jobs)], [10])

    def test_a_rebuild_without_the_front_entry_that_passed_drops_the_failure(self):
        # The front entry failed another job; its follower failed the shard,
        # then passed it once rebuilt on dev.
        runs = [
            group(1, 10, "1" * 40),
            group(2, 11, "2" * 40, base="1" * 40),
            group(3, 11, "4" * 40, created="2026-11-26T11:40:00Z", conclusion="success"),
        ]
        jobs = {1: [{"name": "other", "status": "completed", "conclusion": "failure", "completed_at": "2026-11-26T11:20:00Z"}],
                2: shard_failed()}
        self.assertEqual([f["pr"] for f in t.independent(t.queue_failures(runs, jobs), runs, jobs)], [10])

    def test_the_same_shard_on_three_independent_groups_trips(self):
        # Sequential evictions: each PR fails the shard at the front of its own group.
        runs = [group(1, 10, "1" * 40), group(2, 11, "2" * 40, created="2026-11-26T11:10:00Z"),
                group(3, 12, "3" * 40, created="2026-11-26T11:20:00Z")]
        jobs = {r["id"]: shard_failed() for r in runs}
        self.assertEqual(self.trips(runs, jobs), [{"job": SHARD, "prs": [10, 11, 12]}])

    def test_the_dev_push_run_trips_by_itself(self):
        dev_run = {"id": 50, "path": ".github/workflows/backend-ci.yml", "conclusion": "failure",
                   "created_at": "2026-11-26T11:00:00Z", "updated_at": "2026-11-26T11:30:00Z"}
        dev = t.queue_failures([dev_run], {50: [{"name": "pytest shard 3", "status": "completed",
                                                 "conclusion": "failure", "completed_at": "2026-11-26T11:30:00Z"},
                                                {"name": "Backend Tests Pass", "status": "completed",
                                                 "conclusion": "failure", "completed_at": "2026-11-26T11:31:00Z"}]},
                               "backend / ", dev=True)
        self.assertEqual([f["job"] for f in dev], [SHARD])
        self.assertEqual(self.trips([], {}, dev), [{"job": SHARD, "prs": [], "dev": True}])
        # A cascade alone still does not trip, and the dev run joins it.
        runs = [group(1, 10, "1" * 40), group(2, 11, "2" * 40, base="1" * 40)]
        jobs = {r["id"]: shard_failed() for r in runs}
        self.assertEqual(self.trips(runs, jobs, dev), [{"job": SHARD, "prs": [10], "dev": True}])
        self.assertIn("the newest dev push run", t.breaker_line(self.trips(runs, jobs, dev)[0]))

    def test_only_the_newest_finished_dev_push_run_counts(self):
        older = {"id": 1, "conclusion": "failure", "created_at": "2026-11-26T10:00:00Z", "updated_at": "2026-11-26T10:30:00Z", "path": "b"}
        newer = dict(older, id=2, conclusion="success", created_at="2026-11-26T11:00:00Z")
        cancelled = dict(older, id=3, conclusion="cancelled", created_at="2026-11-26T11:30:00Z")
        api = FakeApi({"/repos/o/r/actions/workflows/": ("workflow_runs", [older, newer, cancelled], None)}, size=10)
        runs, failures = t.collect_dev(api)
        self.assertEqual(([r["id"] for r in runs], failures), ([2, 2], []))


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


def merge(n, pr, title, body=""):
    """A merge-method landing as dev has them: the title is the body's first line."""
    return commit(n, f"Merge pull request #{pr} from future-agi/branch-{pr}", f"{title}\n\n{body}".rstrip())


def verdict_review(engine=None, association="MEMBER", at="2026-11-26T10:00:00Z"):
    verdict = {"schema": "devflow-review/v1", "verdict": "pass", "findings": []}
    if engine is not None:
        verdict["engine"] = engine
    body = f"{t.REVIEW_MARKER}\n<details>\n\n```json\n{json.dumps(verdict)}\n```\n</details>"
    return {"body": body, "authorAssociation": association, "submittedAt": at}


class MergeLandings(unittest.TestCase):
    """dev lands by merge commit: "Merge pull request #N from ..." plus the title."""

    def history(self, extra):
        return extra + [merge(i, 1000 + i, f"feat: thing {i}") for i in range(300, 0, -1)]

    def test_a_revert_merge_is_counted_from_its_title(self):
        # Red before the fix: the subject starts "Merge pull request", never "Revert ".
        extra = [
            merge(901, 2000, 'Revert "feat: thing 100"'),
            merge(902, 2001, "revert: back out saved-task start until the durable retry lands"),
        ]
        out = t.escape_rate(self.history(extra))
        self.assertEqual(out["count"], 2)
        self.assertEqual([e["kind"] for e in out["escapes"]], ["revert", "revert"])

    def test_a_fix_title_naming_an_earlier_pr_is_a_fix_forward(self):
        out = t.escape_rate(self.history([merge(903, 2002, "fix(ui): keep the composer steady after #1150")]))
        self.assertEqual((out["count"], out["escapes"][0]["refs"]), (1, [1150]))

    def test_a_pr_that_never_landed_on_dev_is_not_an_escape(self):
        # "carry #2379 ... onto dev": #2379 is not a dev landing.
        out = t.escape_rate(self.history([merge(904, 2003, "fix(frontend): carry #2379 dependency updates onto dev")]))
        self.assertEqual(out["count"], 0)

    def test_a_feat_title_naming_a_pr_is_not_an_escape(self):
        out = t.escape_rate(self.history([merge(905, 2004, "feat: build on #1150")]))
        self.assertEqual(out["count"], 0)

    def test_planned_follow_ups_in_ci_or_chore_work_are_not_escapes(self):
        for title in ("ci(backend): record the shard services", "chore(frontend): tidy npm lock entries"):
            with self.subTest(title=title):
                extra = [merge(907, 2006, title)]
                out = t.escape_rate(self.history(extra), {2006: {"body": "Follow-up to #1150.", "engine": None}})
                self.assertEqual(out["count"], 0)

    def test_the_description_from_the_api_counts(self):
        # A merge commit carries only the title; "Follow-up to #N" is in the description.
        extra = [merge(906, 2005, "fix(gateway): tidy selection")]
        self.assertEqual(t.escape_rate(self.history(extra))["count"], 0)
        out = t.escape_rate(self.history(extra), {2005: {"body": "Follow-up to #1150.", "engine": None}})
        self.assertEqual((out["count"], out["escapes"][0]["refs"]), (1, [1150]))


class Engines(unittest.TestCase):
    def test_engine_of_the_newest_trusted_review(self):
        reviews = [
            verdict_review("codex/gpt-5.5", at="2026-11-26T09:00:00Z"),
            verdict_review("claude-code/claude-opus-5-5", at="2026-11-26T10:00:00Z"),
            verdict_review("spoofed", association="NONE", at="2026-11-26T11:00:00Z"),
        ]
        self.assertEqual(t.review_engine(reviews), "claude-code/claude-opus-5-5")
        self.assertEqual(t.review_engine([verdict_review()]), "unknown")
        self.assertIsNone(t.review_engine([{"body": "LGTM", "authorAssociation": "MEMBER"}]))

    def test_escape_rate_per_engine_blames_the_escaped_prs_reviewer(self):
        commits = [merge(1, 12, "fix: x", "fixes #10"), merge(2, 11, "feat: b"), merge(3, 10, "feat: a")]
        details = {10: {"engine": "codex/gpt-5.5"}, 11: {"engine": "claude-code/x"}, 12: {"engine": "claude-code/x"}}
        out = t.escape_rate(commits, details)["by_engine"]
        self.assertEqual(out["codex/gpt-5.5"], {"count": 1, "total": 1, "rate": 1.0})
        self.assertEqual(out["claude-code/x"], {"count": 0, "total": 2, "rate": 0.0})

    def test_no_reviews_no_engine_lines(self):
        commits = [merge(1, 12, "fix: x", "fixes #10"), merge(3, 10, "feat: a")]
        esc = t.escape_rate(commits, {10: {"engine": None}})
        self.assertEqual(esc["by_engine"], {})
        text = t.render_digest(0, t.queue_stats([], {}, NOW), esc, None, [])
        self.assertNotIn("reviewed by", text)
        esc = t.escape_rate(commits, {10: {"engine": "codex/gpt-5.5"}})
        text = t.render_digest(0, t.queue_stats([], {}, NOW), esc, None, [])
        self.assertIn("reviewed by `codex/gpt-5.5`: 100.0% (1 of 1)", text)


class FakeApi(t.Api):
    """Serves list endpoints in pages of `size`, linked the way GitHub links them."""

    def __init__(self, lists, size=2):
        super().__init__("o/r", "token")
        self.lists, self.size, self.calls = lists, size, []

    def request(self, method, url, body=None, token=None):
        self.calls.append(url)
        path, _, query = url.removeprefix("https://api.github.com").partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        page = int(params.get("page", 1))
        for prefix, (key, items, total) in self.lists.items():
            if path.startswith(prefix):
                chunk = items[(page - 1) * self.size: page * self.size]
                link = ""
                if page * self.size < len(items):
                    nxt = urllib.parse.urlencode(dict(params, page=page + 1))
                    link = f'<https://api.github.com{path}?{nxt}>; rel="next"'
                return ({key: chunk, "total_count": total if total is not None else len(items)} if key else chunk), link
        raise AssertionError(url)


class Paging(unittest.TestCase):
    def run_(self, i, conclusion="failure", status="completed", created="2026-11-26T11:30:00Z"):
        return {"id": i, "status": status, "conclusion": conclusion, "created_at": created,
                "updated_at": created, "head_branch": f"gh-readonly-queue/dev/pr-{i}-abc", "path": ".github/workflows/e2e-ci.yml"}

    def test_every_run_and_every_job_page_is_read(self):
        # Red before the fix: one page of runs and one page of jobs, runs[:60].
        runs = [self.run_(i) for i in range(1, 6)]
        jobs = [{"name": f"j{k}", "status": "completed", "conclusion": "success"} for k in range(4)]
        jobs.append({"name": "backend", "status": "completed", "conclusion": "failure", "completed_at": "2026-11-26T11:40:00Z"})
        lists = {"/repos/o/r/actions/workflows/": ("workflow_runs", runs, None)}
        lists.update({f"/repos/o/r/actions/runs/{r['id']}/jobs": ("jobs", jobs, None) for r in runs})
        api = FakeApi(lists)
        got, failures, by_run = t.collect_queue(api, NOW, breaker_only=True)
        self.assertEqual(len(got), 10)  # 5 runs from each of the two queue workflows
        self.assertEqual({len(v) for v in by_run.values()}, {5})
        self.assertEqual(t.detect_breaker(failures, NOW), [{"job": "backend", "prs": [1, 2, 3, 4, 5]}])

    def test_a_full_listing_is_split_by_created_time(self):
        # The fake always says the listing is full: the window halves down to
        # two minutes, then each part is read.
        api = FakeApi({"/repos/o/r/actions/workflows/": ("workflow_runs", [], t.LISTING_CAP)})
        t.list_runs(api, "e2e-ci.yml", NOW - timedelta(minutes=8), NOW)
        windows = {c.split("created=")[1].split("&")[0] for c in api.calls}
        self.assertGreater(len(windows), 2)

    def test_landings_are_paged(self):
        api = FakeApi({"/repos/o/r/commits": (None, [{"sha": str(i)} for i in range(5)], None)})
        self.assertEqual(len(api.get_all("/commits?sha=dev&per_page=100")), 5)


class Liveness(unittest.TestCase):
    def test_a_running_queue_build_counts_its_finished_failed_jobs(self):
        # The queue's breaker job runs inside a run that is not done yet.
        run = {"id": 7, "status": "in_progress", "conclusion": None, "created_at": "2026-11-26T11:50:00Z",
               "updated_at": "2026-11-26T11:55:00Z", "head_branch": "gh-readonly-queue/dev/pr-9-x", "path": "p"}
        self.assertTrue(t.wants_jobs(run, NOW, breaker_only=True))
        jobs = {7: [
            {"name": "backend", "status": "completed", "conclusion": "failure", "completed_at": "2026-11-26T11:55:00Z"},
            {"name": "e2e", "status": "in_progress", "conclusion": None},
        ]}
        self.assertEqual([f["job"] for f in t.queue_failures([run], jobs)], ["backend"])

    def test_the_breaker_job_runs_in_the_queue_workflow_from_dev(self):
        text = (pathlib.Path(__file__).parents[1] / "workflows/backend-queue-ci.yml").read_text()
        breaker = text.split("\n  breaker:\n", 1)[1]
        self.assertIn("devflow_tick.py --breaker-only", breaker)
        self.assertIn("ref: ${{ github.event.merge_group.base_ref }}", breaker)
        self.assertIn("- continue-on-error: true", breaker)
        self.assertNotIn("breaker", text.split("\n  backend-tests:\n", 1)[1].split("\n  breaker:\n")[0])

    def test_per_pr_verdicts_and_aggregates_are_not_base_signals(self):
        # Three light PRs enqueued without ci:full fail the gate and the
        # aggregate; that is not a red base and must not pause the train.
        runs = [{"id": i, "updated_at": "2026-11-26T11:40:00Z", "head_branch": f"gh-readonly-queue/dev/pr-{i}-x", "path": "p"} for i in (1, 2, 3)]
        jobs = {i: [
            {"name": name, "status": "completed", "conclusion": "failure", "completed_at": "2026-11-26T11:40:00Z"}
            for name in ("Members ran the internal suite", "Backend Tests Pass", "E2E Tests Pass", "Base-health breaker")
        ] for i in (1, 2, 3)}
        self.assertEqual(t.detect_breaker(t.queue_failures(runs, jobs), NOW), [])
        jobs[1].append({"name": "backend / pytest shard 3", "status": "completed", "conclusion": "failure", "completed_at": "2026-11-26T11:40:00Z"})
        self.assertEqual([f["job"] for f in t.queue_failures(runs, jobs)], ["backend / pytest shard 3"])

    def test_a_trip_closes_after_the_job_stops_and_a_later_build_passes(self):
        issue = {"number": 5, "title": "Base looks red: backend", "created_at": "2026-11-26T10:00:00Z"}
        bad = [{"job": "backend", "pr": 1, "at": "2026-11-26T10:30:00Z", "workflow": "wf"}]
        green_after = {"id": 9, "conclusion": "success", "path": "wf", "created_at": "2026-11-26T11:00:00Z", "updated_at": "2026-11-26T11:20:00Z"}
        green_before = dict(green_after, created_at="2026-11-26T10:10:00Z")
        other_wf = dict(green_after, path="other")
        self.assertEqual(t.resolved_trips([issue], [], bad, [green_after]), [{"number": 5, "job": "backend", "run": 9}])
        self.assertEqual(t.resolved_trips([issue], [], bad, [green_before, other_wf]), [])
        self.assertEqual(t.resolved_trips([issue], [{"job": "backend", "prs": [1, 2, 3]}], bad, [green_after]), [])
        # An idle queue closes nothing.
        self.assertEqual(t.resolved_trips([issue], [], [], []), [])


class Stats(unittest.TestCase):
    def runs(self):
        def mk(i, c):
            return {"id": i, "conclusion": c, "created_at": "2026-11-26T11:00:00Z", "updated_at": "2026-11-26T11:10:00Z"}

        return [mk(1, "success"), mk(2, "failure"), mk(3, "failure")]

    def test_red_classes_and_waits(self):
        jobs = {
            2: [{"name": "backend / pytest shard 10", "conclusion": "failure", "created_at": "2026-11-26T11:00:00Z", "started_at": "2026-11-26T11:00:30Z",
                 "steps": [{"name": "Run ./.github/actions/backend-test-packages", "conclusion": "failure"}]}],
            3: [{"name": "backend / pytest shard 11", "conclusion": "failure", "created_at": "2026-11-26T11:00:00Z", "started_at": "2026-11-26T11:02:00Z",
                 "steps": [{"name": "Run tests", "conclusion": "failure"}]}],
        }
        s = t.queue_stats(self.runs(), jobs, NOW)
        self.assertEqual(s["red"], {"infra": 1, "other": 1})
        self.assertAlmostEqual(s["red_rate"], 2 / 3)
        self.assertEqual(s["queue_p90_s"], 600)
        self.assertEqual(s["runner_p95_s"], 120)

    def test_mixed_failed_jobs_count_as_other(self):
        jobs = {2: [
            {"name": "a", "conclusion": "failure", "steps": [{"name": "Install packages", "conclusion": "failure"}]},
            {"name": "b", "conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]},
        ]}
        self.assertEqual(t.queue_stats(self.runs()[:2], jobs, NOW)["red"], {"infra": 0, "other": 1})

    def test_job_name_alone_is_not_infra(self):
        jobs = {2: [{"name": "Build image setup", "conclusion": "failure"}]}
        self.assertEqual(t.queue_stats(self.runs()[:2], jobs, NOW)["red"], {"infra": 0, "other": 1})

    def test_known_infra_steps(self):
        for step in ("Start test services", "Prefetch vendor images", "Run ./.github/actions/backend-test-packages/environment",
                     "Install uv", "Set up job", "Backend terminal inventory qualification"):
            job = {"conclusion": "failure", "steps": [{"name": step, "conclusion": "failure"}]}
            self.assertTrue(t.is_infra_job(job), step)
        for step in ("Run tests", "Run pytest shard", "Run e2e spec"):
            job = {"conclusion": "failure", "steps": [{"name": step, "conclusion": "failure"}]}
            self.assertFalse(t.is_infra_job(job), step)

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
