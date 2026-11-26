"""Tests for devflow_tick.py: red cases fail on the wrong rule, green on the right one."""

import json
import pathlib
import sys
import unittest
import urllib.error
import urllib.parse
from datetime import UTC, datetime, timedelta, timezone

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

    def dev_failures(self, run_id=50, at="2026-11-26T11:30:00Z", prior=False):
        dev_run = {"id": run_id, "path": ".github/workflows/backend-ci.yml", "conclusion": "failure",
                   "created_at": "2026-11-26T11:00:00Z", "updated_at": at}
        return t.queue_failures([dev_run], {run_id: [{"name": "pytest shard 3", "status": "completed",
                                                      "conclusion": "failure", "completed_at": at},
                                                     {"name": "Backend Tests Pass", "status": "completed",
                                                      "conclusion": "failure", "completed_at": at}]},
                                "backend / ", dev=True, prior=prior)

    def test_one_red_dev_push_run_alone_does_not_trip(self):
        # 6 of the last 40 dev push runs failed E2E and 5 Backend, mostly one
        # run at a time: a single red dev run is a flake until it repeats.
        dev = self.dev_failures()
        self.assertEqual([f["job"] for f in dev], [SHARD])
        self.assertEqual(self.trips([], {}, dev), [])
        self.assertEqual(self.trips([], {}, self.dev_failures(51, prior=True)), [], "the older run alone")

    def test_the_two_newest_dev_push_runs_red_on_the_same_job_trip(self):
        both = self.dev_failures() + self.dev_failures(49, "2026-11-26T11:10:00Z", prior=True)
        self.assertEqual(self.trips([], {}, both), [{"job": SHARD, "prs": [], "dev": 2}])
        self.assertIn("the two newest dev push runs", t.breaker_line(self.trips([], {}, both)[0]))
        # The run before the newest counts whatever its age; the newest must be recent.
        old_prior = self.dev_failures() + self.dev_failures(49, "2026-11-26T06:00:00Z", prior=True)
        self.assertEqual(self.trips([], {}, old_prior), [{"job": SHARD, "prs": [], "dev": 2}])
        stale = self.dev_failures(at="2026-11-26T09:00:00Z") + self.dev_failures(49, "2026-11-26T08:00:00Z", prior=True)
        self.assertEqual(self.trips([], {}, stale), [])

    def test_one_red_dev_run_and_one_independent_queue_failure_trip(self):
        dev = self.dev_failures()
        # A cascade counts once: two groups, one independent failure (#10).
        runs = [group(1, 10, "1" * 40), group(2, 11, "2" * 40, base="1" * 40)]
        jobs = {r["id"]: shard_failed() for r in runs}
        self.assertEqual(self.trips(runs, jobs, dev), [{"job": SHARD, "prs": [10], "dev": 1}])
        self.assertIn("the newest dev push run", t.breaker_line(self.trips(runs, jobs, dev)[0]))
        self.assertEqual(self.trips(runs, jobs), [], "the queue failure alone")

    def test_the_two_newest_finished_dev_push_runs_count(self):
        def dev_run(i, conclusion, created):
            return {"id": i, "conclusion": conclusion, "created_at": created, "updated_at": created, "path": "b"}

        shard = [{"name": "pytest shard 3", "status": "completed", "conclusion": "failure",
                  "completed_at": "2026-11-26T11:00:00Z"}]
        older = dev_run(1, "failure", "2026-11-26T10:00:00Z")
        newer = dev_run(2, "success", "2026-11-26T11:00:00Z")
        cancelled = dev_run(3, "cancelled", "2026-11-26T11:30:00Z")
        api = FakeApi({"/repos/o/r/actions/workflows/": ("workflow_runs", [older, newer, cancelled], None)}, size=10)
        runs, failures = t.collect_dev(api)
        self.assertEqual(([r["id"] for r in runs], failures), ([2, 2], []), "a green newest run clears it")
        newest = dev_run(4, "failure", "2026-11-26T11:40:00Z")
        lists = {"/repos/o/r/actions/workflows/": ("workflow_runs", [older, newer, newest, cancelled], None),
                 "/repos/o/r/actions/runs/4/jobs": ("jobs", shard, None)}
        runs, failures = t.collect_dev(FakeApi(lists, size=10))
        self.assertEqual({(f["run"], bool(f.get("prior"))) for f in failures}, {(4, False)}, "run 2 before it passed")
        newer["conclusion"] = "failure"
        lists["/repos/o/r/actions/runs/2/jobs"] = ("jobs", shard, None)
        runs, failures = t.collect_dev(FakeApi(lists, size=10))
        self.assertEqual([r["id"] for r in runs], [4, 4])
        self.assertEqual({(f["run"], bool(f.get("prior"))) for f in failures}, {(4, False), (2, True)})


    def test_a_page_of_cancelled_runs_does_not_hide_the_finished_ones(self):
        # Red before: one page of 20 was read, so 20 cancelled runs hid both.
        def dev_run(i, conclusion, minute):
            at = f"2026-11-26T11:{minute:02d}:00Z"
            return {"id": i, "conclusion": conclusion, "created_at": at, "updated_at": at, "path": "b"}

        cancelled = [dev_run(100 + i, "cancelled", 59 - i) for i in range(20)]
        finished = [dev_run(2, "success", 30), dev_run(1, "success", 20)]
        listing = {"/repos/o/r/actions/workflows/": ("workflow_runs", cancelled + finished, None)}
        runs, failures = t.collect_dev(FakeApi(listing, size=20))
        self.assertEqual(([r["id"] for r in runs], failures), ([2, 2], []))
        # Bounded: runs past DEV_PUSH_PAGES pages are not read.
        buried = cancelled * t.DEV_PUSH_PAGES + finished
        api = FakeApi({"/repos/o/r/actions/workflows/": ("workflow_runs", buried, None)}, size=20)
        self.assertEqual(t.collect_dev(api), ([], []))
        self.assertEqual(len(api.calls), 2 * t.DEV_PUSH_PAGES)


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

    def test_tight_mode_enters_above_two_percent_and_leaves_below_one_and_a_half(self):
        for rate, now_tight, want in (
            (0.021, False, True), (0.02, False, False), (0.015, False, False),
            (0.02, True, True), (0.015, True, True), (0.0149, True, False), (0.0, True, False),
        ):
            with self.subTest(rate=rate, now_tight=now_tight):
                self.assertEqual(t.tight_wanted(rate, now_tight), want)
        two = [commit(930 + i, f"fix: z (#{2020 + i})", "fixes #1100") for i in range(2)]
        self.assertFalse(t.escape_rate(self.history(two))["tight"], "2% does not enter")
        self.assertTrue(t.escape_rate(self.history(two), tight_now=True)["tight"], "2% does not leave")
        self.assertFalse(t.escape_rate(self.history(two[:1]), tight_now=True)["tight"], "1% leaves")

    def test_issue_numbers_are_not_prs(self):
        out = t.escape_rate(self.history([commit(903, "fix: y (#2002)", "Fixes #5")]))
        self.assertEqual(out["count"], 0)

    def test_self_reference_ignored(self):
        out = t.escape_rate(self.history([commit(912, "fix: c (#2005)", "fixes #2005")]))
        self.assertEqual(out["count"], 0)


def merge(n, pr, title, body=""):
    """A merge-method landing as dev has them: the title is the body's first line."""
    return commit(n, f"Merge pull request #{pr} from future-agi/branch-{pr}", f"{title}\n\n{body}".rstrip())


WRITERS = {"reviewer"}


def verdict_review(engine=None, login="reviewer", at="2026-11-26T10:00:00Z", association="MEMBER"):
    verdict = {"schema": "devflow-review/v1", "verdict": "pass", "findings": []}
    if engine is not None:
        verdict["engine"] = engine
    body = f"{t.REVIEW_MARKER}\n<details>\n\n```json\n{json.dumps(verdict)}\n```\n</details>"
    return {"body": body, "author": {"login": login}, "authorAssociation": association, "submittedAt": at}


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
            verdict_review("spoofed", login="outsider", at="2026-11-26T11:00:00Z"),
        ]
        self.assertEqual(t.review_engine(reviews, WRITERS), "claude-code/claude-opus-5-5")
        self.assertEqual(t.review_engine([verdict_review()], WRITERS), "unknown")
        self.assertIsNone(t.review_engine([{"body": "LGTM", "author": {"login": "reviewer"}}], WRITERS))

    def test_trust_is_write_permission_not_the_association(self):
        # A private org member is CONTRIBUTOR to GITHUB_TOKEN; MEMBER without write is no reviewer.
        private = verdict_review("opus", login="Pat", association="CONTRIBUTOR")
        self.assertEqual(t.review_engine([private], {"pat"}), "opus")
        self.assertIsNone(t.review_engine([verdict_review("opus", login="pat")], set()))

        class Perm(t.Api):
            def __init__(self):
                super().__init__("o/r", "token")
                self.asked = []

            def request(self, method, url, body=None, token=None):
                self.asked.append(url)
                return {"pat": {"permission": "write", "role_name": "write"},
                        "rex": {"permission": "read", "role_name": "read"},
                        "max": {"permission": "write", "role_name": "maintain"}}[url.split("/")[-2]], ""

        api = Perm()
        self.assertEqual(api.writers({"pat", "rex", "max"}), {"pat", "max"})
        self.assertEqual(api.writers({"pat"}), {"pat"})
        self.assertEqual(len(api.asked), 3, "one read per login per tick")

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


TRAIN_APP = "futureagi-devflow-train[bot]"


def rollup_issue(number, members, login=TRAIN_APP):
    """A rollup pull request as the issues listing returns it."""
    manifest = {"schema": "devflow-train/v1", "id": f"r{number}", "head_sha": f"{number:040x}",
                "members": [{"pr": n, "head_sha": f"{n:040x}"} for n in members]}
    return {"number": number, "pull_request": {}, "user": {"login": login, "type": "Bot"},
            "body": f"Batch train rollup.\n\n{t.TRAIN_MARKER}\n```json\n{json.dumps(manifest)}\n```"}


def rollup_run(run_id, number, wf="backend-ci.yml", conclusion="failure", branch=None):
    """The pull_request run of rollup #number on its built head."""
    at = "2026-11-26T11:30:00Z"
    return {"id": run_id, "head_sha": f"{number:040x}", "head_branch": branch or f"{t.TRAIN_BRANCH}r{number}",
            "path": f".github/workflows/{wf}", "created_at": at, "updated_at": at,
            "status": "completed", "conclusion": conclusion}


def pr_shard_failed():
    """A rollup PR run's jobs: backend-ci.yml names its shard without the queue's prefix."""
    return [{"name": "pytest shard 3", "status": "completed", "conclusion": "failure",
             "completed_at": "2026-11-26T11:30:00Z"},
            {"name": "Backend Tests Pass", "status": "completed", "conclusion": "failure",
             "completed_at": "2026-11-26T11:30:00Z"}]


class RollupRuns(unittest.TestCase):
    """Rollups go red on their own PR runs before the queue sees them, so the
    breaker counts those reds too: under the cascade rules, one bad member
    counts once however many rollups held it."""

    def failures(self, issues, runs):
        jobs = {r["id"]: pr_shard_failed() for r in runs}
        return t.rollup_failures(runs, jobs, t.rollup_members(issues, TRAIN_APP))

    def test_three_rollups_red_on_the_same_job_with_disjoint_members_trip(self):
        issues = [rollup_issue(80, [1, 2]), rollup_issue(81, [3]), rollup_issue(82, [4, 5])]
        fails = self.failures(issues, [rollup_run(1, 80), rollup_run(2, 81), rollup_run(3, 82)])
        self.assertEqual({f["job"] for f in fails}, {SHARD}, "named as the queue names it; no aggregate")
        self.assertEqual(t.detect_breaker(fails, NOW), [{"job": SHARD, "prs": [80, 81, 82]}])
        self.assertEqual(t.detect_breaker(fails[:2], NOW), [], "two are not enough")

    def test_one_bad_member_across_nested_rollups_does_not_trip(self):
        # #1 is bad: rollup 80 split into 81 and then 83; each went red.
        issues = [rollup_issue(80, [1, 2, 3, 4]), rollup_issue(81, [1, 2]), rollup_issue(83, [1])]
        fails = self.failures(issues, [rollup_run(1, 80), rollup_run(2, 81), rollup_run(3, 83)])
        self.assertEqual(len(fails), 3)
        self.assertEqual(t.detect_breaker(fails, NOW), [])
        # Its rollup red in the queue too: the same members, still one.
        queue = [dict(fail(SHARD, 81), members=[1, 2])]
        self.assertEqual(t.detect_breaker(fails + queue, NOW), [])

    def test_two_queue_reds_and_one_rollup_red_trip(self):
        # The chaos run: dev was red, only 2 queue builds failed, and the
        # rollups went red on their own PR runs first.
        queue = [fail(SHARD, 10), fail(SHARD, 11)]
        fails = self.failures([rollup_issue(81, [3])], [rollup_run(1, 81)])
        self.assertEqual(t.detect_breaker(queue, NOW), [])
        self.assertEqual(t.detect_breaker(queue + fails, NOW), [{"job": SHARD, "prs": [10, 11, 81]}])
        # A member that the queue also tested alone is the same signal.
        alone = [fail(SHARD, 10), fail(SHARD, 3)]
        self.assertEqual(t.detect_breaker(alone + fails, NOW), [])

    def test_a_red_dev_push_run_and_one_rollup_red_trip(self):
        dev = Cascade().dev_failures()
        fails = self.failures([rollup_issue(81, [3])], [rollup_run(1, 81)])
        self.assertEqual(t.detect_breaker(dev + fails, NOW), [{"job": SHARD, "prs": [81], "dev": 1}])

    def test_only_the_train_apps_own_rollups_count(self):
        runs = [rollup_run(1, 80), rollup_run(2, 81), rollup_run(3, 82)]
        for name, issues, rs in (
            ("another author", [rollup_issue(n, [n - 79], login="someone") for n in (80, 81, 82)], runs),
            ("an issue, not a pull request",
             [{k: v for k, v in rollup_issue(n, [n - 79]).items() if k != "pull_request"} for n in (80, 81, 82)], runs),
            ("another branch", [rollup_issue(n, [n - 79]) for n in (80, 81, 82)],
             [rollup_run(i, n, branch="feat/x") for i, n in ((1, 80), (2, 81), (3, 82))]),
            ("no manifest", [{**rollup_issue(n, [n - 79]), "body": "chore(train)"} for n in (80, 81, 82)], runs),
        ):
            with self.subTest(name):
                self.assertEqual(self.failures(issues, rs), [])

    def test_collect_rollups_reads_the_window_and_fails_open(self):
        issues = [rollup_issue(80, [1]), rollup_issue(81, [2]), rollup_issue(82, [3])]
        lists = {
            "/repos/o/r/issues": (None, issues, None),
            "/repos/o/r/actions/workflows/backend-ci.yml/runs": (
                "workflow_runs", [rollup_run(1, 80), rollup_run(2, 81), rollup_run(3, 82),
                                  rollup_run(4, 99), rollup_run(5, 82, conclusion="success")], None),
            "/repos/o/r/actions/workflows/e2e-ci.yml/runs": ("workflow_runs", [], None),
        }
        lists.update({f"/repos/o/r/actions/runs/{i}/jobs": ("jobs", pr_shard_failed(), None) for i in (1, 2, 3)})
        api = FakeApi(lists, size=100)
        fails, members = t.collect_rollups(api, NOW, TRAIN_APP)
        self.assertEqual(sorted(f["pr"] for f in fails), [80, 81, 82])
        self.assertEqual(members, {80: [1], 81: [2], 82: [3]})
        listing = next(c for c in api.calls if "/issues" in c)
        self.assertIn("labels=devflow%3Arollup", listing)
        self.assertIn("state=all", listing, "a red rollup is closed within a tick")
        self.assertFalse(any("/runs/4/" in c or "/runs/5/" in c for c in api.calls), "not a rollup; green")
        from unittest import mock

        class Down(FakeApi):
            def request(self, method, url, body=None, token=None):
                raise urllib.error.URLError("down")

        # The queue's breaker step must still file its own trips.
        with mock.patch("builtins.print"):
            self.assertEqual(t.collect_rollups(Down({}), NOW, TRAIN_APP), ([], {}))

    def test_run_breaker_adds_rollup_reds_and_gives_queued_rollups_their_members(self):
        from unittest import mock

        rollup = self.failures([rollup_issue(81, [3])], [rollup_run(1, 81)])
        for queue, want in (
            ([fail(SHARD, 10), fail(SHARD, 11)], [{"job": SHARD, "prs": [10, 11, 81]}]),
            # #81 red in the queue too: the same members, so one signal.
            ([fail(SHARD, 10), fail(SHARD, 81)], []),
        ):
            with (
                self.subTest(queue=[f["pr"] for f in queue]),
                mock.patch.object(t, "collect_dev", return_value=([], [])),
                mock.patch.object(t, "collect_rollups", return_value=(rollup, {81: [3]})),
                mock.patch.object(t, "open_breaker_issues", return_value=[]),
            ):
                tripped, _ = t.run_breaker(None, NOW, [], queue, {}, dry=True)
                self.assertEqual(tripped, want)

    def test_the_breaker_reads_rollups_and_the_train_login_everywhere(self):
        root = pathlib.Path(__file__).parents[1] / "workflows"
        digest = (root / "pr-digest.yml").read_text().split("\n  tick:\n", 1)[1].split("\n  tick-tests:\n", 1)[0]
        queue = (root / "backend-queue-ci.yml").read_text().split("\n  breaker:\n", 1)[1]
        for name, text, steps in (("pr-digest", digest, 2), ("backend-queue-ci", queue, 1)):
            with self.subTest(workflow=name):
                self.assertEqual(text.count("DEVFLOW_TRAIN_LOGIN: ${{ vars.DEVFLOW_TRAIN_LOGIN }}"), steps)
                self.assertIn("pull-requests: read", text, "the issues listing returns the rollups")

    def test_the_rollup_shape_matches_the_train(self):
        import devflow_train as train

        self.assertEqual(t.TRAIN_MARKER, train.MANIFEST_MARKER)
        self.assertEqual(t.TRAIN_BRANCH, train.BRANCH_PREFIX)
        self.assertEqual(t.TRAIN_ROLLUP_LABEL, train.ROLLUP_LABEL)
        self.assertEqual(t.bot(t.TRAIN_LOGIN), train.bot_login(train.Config().train_login).lower())
        built = train.new_manifest(f"{9:040x}", [{"number": 3, "head_sha": f"{3:040x}", "branch": "b", "title": "t"}])
        built["head_sha"] = f"{81:040x}"
        issue = {"number": 81, "pull_request": {}, "user": {"login": TRAIN_APP}, "body": train.render_manifest(built)}
        self.assertEqual(t.rollup_members([issue], TRAIN_APP),
                         {f"{81:040x}": {"pr": 81, "branch": train.BRANCH_PREFIX + built["id"], "members": [3]}})


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
        # Red before: a job-level group made breakers from two groups wait on each other.
        self.assertNotIn("\n    concurrency:", breaker)
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


class Recorder(t.Api):
    """Answers every GET with an empty list; records every write."""

    def __init__(self, repo, token):
        super().__init__(repo, token)
        self.writes = []

    def request(self, method, url, body=None, token=None):
        if method != "GET":
            self.writes.append((method, url))
            return {}, ""
        return [], ""


class Heartbeat(unittest.TestCase):
    """DEVFLOW_HEARTBEAT=live is the only switch for the public base-red
    issues and the Slack posts; without it a tick writes its summary only."""

    def tick(self, argv, env):
        from unittest import mock

        at = t.iso(datetime.now(UTC) - timedelta(minutes=10))
        failures = [{"job": "backend / pytest shard 3", "pr": n, "at": at, "workflow": "w", "run": n} for n in (1, 2, 3)]
        api = {}

        def make(repo, token):
            api["x"] = Recorder(repo, token)
            return api["x"]

        with (
            mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t",
                                           "SLACK_WEBHOOK_URL": "https://hooks.example/x", **env}, clear=True),
            mock.patch.object(t, "Api", make),
            mock.patch.object(t, "collect_queue", return_value=([], failures, {})),
            mock.patch.object(t, "collect_dev", return_value=([], [])),
            mock.patch.object(t, "git_first_parent", return_value=[]),
            mock.patch.object(t, "pr_details", return_value={}),
            mock.patch.object(t, "open_pr_mergeables", return_value=[]),
            mock.patch.object(t, "collect_flaky", return_value=[]),
            mock.patch.object(t, "post_slack") as slack,
            mock.patch("sys.stdout"),
        ):
            self.assertEqual(t.main(argv), 0)
        return api["x"].writes, slack.call_count

    def test_without_the_heartbeat_a_trip_files_and_posts_nothing(self):
        for argv in (["--breaker-only"], []):
            for env in ({}, {"DEVFLOW_HEARTBEAT": "dry"}):
                with self.subTest(argv=argv, env=env):
                    self.assertEqual(self.tick(argv, env), ([], 0))

    def test_a_live_heartbeat_files_the_trip_and_posts(self):
        writes, posts = self.tick(["--breaker-only"], {"DEVFLOW_HEARTBEAT": "live"})
        self.assertIn(("POST", "/repos/o/r/issues"), writes)
        self.assertEqual(posts, 1)
        writes, posts = self.tick([], {"DEVFLOW_HEARTBEAT": "live"})
        self.assertIn(("POST", "/repos/o/r/issues"), writes)
        self.assertEqual(posts, 1, "the digest")

    def test_every_tick_step_passes_the_heartbeat(self):
        root = pathlib.Path(__file__).parents[1] / "workflows"
        digest = (root / "pr-digest.yml").read_text().split("\n  tick:\n", 1)[1].split("\n  tick-tests:\n", 1)[0]
        queue = (root / "backend-queue-ci.yml").read_text().split("\n  breaker:\n", 1)[1]
        for name, text, steps in (("pr-digest", digest, 2), ("backend-queue-ci", queue, 1)):
            with self.subTest(workflow=name):
                self.assertEqual(text.count("DEVFLOW_HEARTBEAT: ${{ vars.DEVFLOW_HEARTBEAT }}"), steps)
                self.assertEqual(text.count("python3 -I .github/scripts/devflow_tick.py"), steps)
        self.assertIn("APPROVAL_MODE_TIGHT: ${{ vars.APPROVAL_MODE_TIGHT }}", digest)
        # bash -eo pipefail: a failing tick is not hidden by its tee.
        self.assertIn("    defaults:\n      run:\n", digest)
        self.assertIn("        shell: bash\n", digest)


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


class Flaky(unittest.TestCase):
    def test_a_fail_then_pass_pair_on_the_same_commit_is_flaky(self):
        # Run 7: shard 3 failed on attempt 1 and passed on the re-run (attempt 2).
        run = {"id": 7, "path": ".github/workflows/e2e-ci.yml", "run_attempt": 2, "html_url": "https://x/runs/7"}
        jobs = {7: [
            {"name": "e2e shard 3", "run_attempt": 1, "conclusion": "failure", "html_url": "https://x/job/1"},
            {"name": "e2e shard 3", "run_attempt": 2, "conclusion": "success", "html_url": "https://x/job/2"},
            {"name": "e2e shard 4", "run_attempt": 1, "conclusion": "failure", "html_url": "https://x/job/3"},
            {"name": "e2e shard 4", "run_attempt": 2, "conclusion": "failure", "html_url": "https://x/job/4"},
            {"name": "e2e shard 5", "run_attempt": 2, "conclusion": "success"},
        ]}
        flakes = t.rerun_flakes([run], jobs)
        self.assertEqual(flakes, [{"job": "e2e shard 3", "workflow": run["path"], "url": "https://x/job/1"}])
        top = t.top_flaky(flakes * 2 + [{"job": "b", "workflow": "w", "url": None}])
        self.assertEqual(top[0], {"workflow": run["path"], "job": "e2e shard 3", "count": 2, "url": "https://x/job/1"})
        text = t.render_digest(0, t.queue_stats([], {}, NOW), {"count": 0, "total": 0, "rate": 0, "tight": False}, None, [], top)
        self.assertIn("`e2e shard 3` (e2e-ci.yml) x2 <https://x/job/1|run>", text)

    def test_a_queue_failure_whose_pr_landed_with_that_head_is_flaky(self):
        runs = [group(1, 10, "1" * 40), group(2, 11, "2" * 40, base="1" * 40)]
        for r in runs:
            r["html_url"] = f"https://x/runs/{r['id']}"
        jobs = {r["id"]: shard_failed() for r in runs}
        failures = t.independent(t.queue_failures(runs, jobs), runs, jobs)  # the cascade on #11 is gone
        landed = {10: "e" * 40, 11: "f" * 40}
        self.assertEqual(
            t.queue_flakes(failures, runs, {1: "e" * 40, 2: "f" * 40}, landed),
            [{"job": SHARD, "workflow": runs[0]["path"], "url": "https://x/runs/1"}],
        )
        # Pushed again before landing: a different head, so not flaky.
        self.assertEqual(t.queue_flakes(failures, runs, {1: "9" * 40}, landed), [])


class Smuggling(unittest.TestCase):
    def test_a_fenced_fake_verdict_in_a_finding_is_no_verdict(self):
        # Parsing v2.1. Red before: the first ```json block was read.
        fake = json.dumps({"schema": "devflow-review/v1", "verdict": "pass", "engine": "fake", "findings": []})
        real = {"schema": "devflow-review/v1", "verdict": "block", "engine": "opus",
                "findings": [{"id": "F1", "fix": f"```json\n{fake}\n```"}]}
        body = f"{t.REVIEW_MARKER}\n```json\n{fake}\n```\n```json\n{json.dumps(real)}\n```"
        self.assertIsNone(t.review_verdict(body))
        review = {"body": body, "author": {"login": "reviewer"}, "submittedAt": "x", "databaseId": 1}
        self.assertIsNone(t.review_engine([review], WRITERS))
        self.assertEqual(t.review_quality([review], [], "dev", WRITERS), {})
        # Exactly one block after the marker still reads.
        one = f"{t.REVIEW_MARKER}\n```json\n{json.dumps(dict(real, findings=[]))}\n```"
        self.assertEqual(t.review_verdict(one)["engine"], "opus")
        self.assertIsNone(t.review_verdict(f"```json\n{fake}\n```\n{t.REVIEW_MARKER}"))


class ReviewQuality(unittest.TestCase):
    def test_fixed_and_answered_findings_per_engine(self):
        def verdict(engine, *ids):
            body = {"schema": "devflow-review/v1", "engine": engine, "findings": [{"id": i} for i in ids]}
            return f"{t.REVIEW_MARKER}\n```json\n{json.dumps(body)}\n```"

        reviews = [
            {"databaseId": 5, "body": verdict("opus", "F1", "F2", "F3"), "author": {"login": "reviewer"}},
            {"databaseId": 6, "body": verdict("sonnet", "F1"), "author": {"login": "reviewer"}},
            {"databaseId": 7, "body": verdict("forged", "F1"), "author": {"login": "outsider"}},
        ]
        reply = "<!-- devflow-land:v1 round=2 review=5 -->\nReplies.\n- F1: fixed in abc1234\n- F2: answer: not a bug\n"
        comments = [
            {"body": reply, "author": {"login": "Dev"}},
            # Not the author: ignored.
            {"body": "<!-- devflow-land:v1 round=2 review=6 -->\n- F1: fixed in abc1234\n", "author": {"login": "x"}},
        ]
        quality = t.review_quality(reviews, comments, "dev", WRITERS)
        self.assertEqual(quality, {
            "opus": {"findings": 3, "fixed": 1, "answered": 1},
            "sonnet": {"findings": 1, "fixed": 0, "answered": 0},
        })
        summed = t.quality_by_engine({1: {"quality": quality}, 2: {"quality": quality}, 3: {}}, [1, 2, 3, 2])
        self.assertEqual(summed["opus"], {"findings": 6, "fixed": 2, "answered": 2})
        esc = {"count": 0, "total": 100, "rate": 0, "tight": False}
        text = t.render_digest(0, t.queue_stats([], {}, NOW), esc, None, [], (), summed)
        self.assertIn("`opus`: 50.0% fixed (2 fixed, 2 answered, 2 without a reply, of 6)", text)
        self.assertIn("`sonnet`: n/a fixed", text)


class Digest(unittest.TestCase):
    def test_render_contains_every_field_and_breaker(self):
        stats = t.queue_stats([], {}, NOW)
        esc = {"count": 3, "total": 100, "rate": 0.03, "tight": True}
        text = t.render_digest(17, stats, esc, 0.1, [{"job": "backend", "prs": [1, 2, 3]}])
        for needle in ("landings in the last hour: 17", "queue red rate", "p90", "p95", "3.0% (3)", "tight approval", "10.0%", "`backend`"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
