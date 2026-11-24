"""Tests for ci_base_gate.py.

Recorded payloads (fixtures/ci_base_gate), trimmed to the fields the check
reads:

- pr-4335-58671faf: one coherent state of PR #4335. The live pull request
  (base dev, mergeable), the dev tip, GitHub's merge candidate (parents
  [dev tip, head]) and that head's Backend CI and E2E CI runs, which failed.
  The pull request's own base.sha is older than the tip, as recorded.
- pr-4335-e8e8b458: the same PR's earlier head, whose Backend CI and E2E CI
  succeeded.
- skipped-4332: a Backend CI run that an edit started with every job skipped.
- timeline-4022 (GitHub's automatic retarget), timeline-4293 (a manual
  retarget) and timeline-4332 (none).

No producer records receipts until this change is live, so every receipt
here is CONSTRUCTED inside a test, bound to the recorded state, and labelled
so. They are never fixture files and never evidence of a real proof.
"""

import copy
import hashlib
import io
import json
import pathlib
import re
import sys
import unittest
import urllib.error
import zipfile
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import ci_base_gate as gate  # noqa: E402

FIXTURES = SCRIPTS / "fixtures/ci_base_gate"
REPO = "future-agi/future-agi"
PR = "4335"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class State:
    """The recorded PR #4335 state, served the way the REST API serves it."""

    def __init__(self, timeline="timeline-4022.json"):
        snap = "pr-4335-58671faf/"
        self.pull = load(snap + "pull.json")
        self.branch = load(snap + "branch-dev.json")
        self.commit = load(snap + "commit-merge.json")
        self.timeline = load(timeline)
        self.head = self.pull["head"]["sha"]
        self.tip = self.branch["commit"]["sha"]
        self.tree = self.commit["commit"]["tree"]["sha"]
        self.runs = load(snap + "runs.json")["workflow_runs"]
        self.jobs = {
            run["id"]: load(f"{snap}jobs-{run['id']}.json")["jobs"]
            for run in self.runs
            if run["path"]
            in (".github/workflows/backend-ci.yml", ".github/workflows/e2e-ci.yml")
        }
        self.artifacts = {}
        self.blobs = {}
        self.pulls = []  # Later reads of the pull request, if they change.
        self.calls = []
        self.paged = False  # Serve artifact lists one item per page.

    def succeeded(self):
        """This head's producers, as the earlier head's real successful runs.

        Labelled transformation: e8e8b458's recorded runs and jobs, moved to
        the recorded head so they can stand for this head's runs.
        """
        old = "pr-4335-e8e8b458/"
        runs = load(old + "runs.json")["workflow_runs"]
        self.runs = [dict(run, head_sha=self.head) for run in runs]
        self.jobs = {
            run["id"]: load(f"{old}jobs-{run['id']}.json")["jobs"]
            for run in runs
            if (FIXTURES / f"{old}jobs-{run['id']}.json").exists()
        }
        return self

    def producer(self, kind):
        path = {"backend": "backend-ci.yml", "e2e": "e2e-ci.yml"}[kind]
        return next(
            run for run in self.runs if run["path"] == ".github/workflows/" + path
        )

    def proof_run(self):
        """A CONSTRUCTED Retarget proof run for this head, newer than the rest.

        Its jobs carry the callers' prefixes, as the queue's do ("backend /
        ..."): "retarget-backend / ..." and "retarget-e2e / ...".
        """
        run = {
            "id": max(run["id"] for run in self.runs) + 1,
            "name": "Retarget proof",
            "path": ".github/workflows/retarget-proof.yml",
            "event": "pull_request",
            "head_sha": self.head,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
        }
        self.runs.append(run)
        self.jobs[run["id"]] = [
            {
                "name": "retarget candidate is current",
                "conclusion": "success",
                "run_attempt": 1,
            },
            {
                "name": "retarget-backend / Backend Tests Pass",
                "conclusion": "success",
                "run_attempt": 1,
            },
            {
                "name": "retarget-e2e / E2E Tests Pass",
                "conclusion": "success",
                "run_attempt": 1,
            },
        ]
        return run

    def receipt(self, kind, /, conclusion="success", members=None, at=None, **changes):
        """Attach a CONSTRUCTED receipt (and its job) to a producer run.

        `at` is the run (default: this head's direct producer); `changes`
        override fields of the receipt body.
        """
        run = at or self.producer(kind)
        prefix = gate.PRODUCERS[kind][run["path"]]
        attempt = changes.pop("attempt", 1)
        name = prefix + gate.RECEIPT_JOB
        self.jobs[run["id"]] = [
            job for job in self.jobs.get(run["id"], []) if job["name"] != name
        ] + [
            {
                "name": name,
                "status": "completed",
                "conclusion": conclusion,
                "run_attempt": attempt,
            }
        ]
        body = {
            "schema": 1,
            "kind": kind,
            # A same-repo pull request's own Backend CI runs with the private
            # package; Retarget proof's and every E2E run do not.
            "scope": "internal"
            if kind == "backend" and run["path"].endswith("/backend-ci.yml")
            else "oss",
            "pr": int(PR),
            "head_sha": self.head,
            "base_ref": self.pull["base"]["ref"],
            "base_sha": self.tip,
            "merge_sha": self.pull["merge_commit_sha"],
            "merge_tree": self.tree,
            "run_id": run["id"],
            "run_attempt": attempt,
            **changes,
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for member in members or ["receipt.json"]:
                archive.writestr(member, json.dumps(body))
        blob = out.getvalue()
        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{run['id']}-{kind}/zip"
        self.blobs[url] = blob
        artifact = f"ci-candidate-{kind}-attempt-{attempt}"
        self.artifacts[run["id"]] = [
            item
            for item in self.artifacts.get(run["id"], [])
            if item["name"] != artifact
        ] + [
            {
                "name": artifact,
                "expired": False,
                "digest": "sha256:" + hashlib.sha256(blob).hexdigest(),
                "archive_download_url": url,
            }
        ]
        return run

    # --- the API -----------------------------------------------------------

    def json(self, path):
        self.calls.append(path)
        root = f"/repos/{REPO}"
        if path == f"{root}/pulls/{PR}":
            pull = self.pulls.pop(0) if self.pulls else self.pull
            return copy.deepcopy(pull), ""
        if path.startswith(f"{root}/branches/"):
            # One recorded base branch, whatever the pull request names.
            return copy.deepcopy(self.branch), ""
        if path.startswith(f"{root}/commits/"):
            return copy.deepcopy(self.commit), ""
        if path.startswith(f"{root}/issues/{PR}/timeline"):
            return copy.deepcopy(self.timeline), ""
        if path.startswith(f"{root}/actions/runs?head_sha="):
            return {"workflow_runs": copy.deepcopy(self.runs)}, ""
        match = re.fullmatch(
            rf"(?:https://api\.github\.com)?{root}/actions/runs/(\d+)/(jobs|artifacts)\?(.*)",
            path,
        )
        if match:
            run_id, what, query = int(match[1]), match[2], match[3]
            if what == "jobs":
                return {"jobs": copy.deepcopy(self.jobs.get(run_id, []))}, ""
            items = copy.deepcopy(self.artifacts.get(run_id, []))
            if not self.paged or len(items) < 2:
                return {"artifacts": items}, ""
            # One artifact per page, linked the way GitHub links pages.
            page = int(re.search(r"page=(\d+)", query)[1]) if "page=" in query else 1
            link = (
                f"<https://api.github.com{root}/actions/runs/{run_id}/artifacts"
                f'?per_page=100&page={page + 1}>; rel="next"'
                if page < len(items)
                else ""
            )
            return {"artifacts": items[page - 1 : page]}, link
        raise AssertionError(f"unexpected API path {path}")

    def download(self, url):
        return self.blobs[url]


def judge(state, base_changed=False, guarded="dev"):
    """The verdict, with the recorded PR's base (dev) as the guarded one."""
    sleeps = []
    with mock.patch.object(gate, "GUARDED_BASE", guarded):
        code, lines = gate.decide(
            state, repo=REPO, pr=PR, base_changed=base_changed, sleep=sleeps.append
        )
    return code, "\n".join(lines), sleeps


class RecordedStateTests(unittest.TestCase):
    def test_the_recorded_candidate_is_the_head_on_the_base_tip(self):
        state = State()
        self.assertEqual(
            [parent["sha"] for parent in state.commit["parents"]],
            [state.tip, state.head],
        )
        # The pull request's own base.sha lags: binding to it would be wrong.
        self.assertNotEqual(state.pull["base"]["sha"], state.tip)


class VerdictTests(unittest.TestCase):
    def test_never_retargeted_passes_without_reading_runs(self):
        state = State("timeline-4332.json")
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)
        self.assertIn("The base never changed", text)
        self.assertFalse(any("/actions/" in call for call in state.calls))

    def test_a_retarget_with_failed_producers_fails_closed(self):
        code, text, _ = judge(State())
        self.assertEqual(code, 1, text)
        self.assertIn("Backend Tests Pass is failure", text)
        self.assertIn("E2E Tests Pass is failure", text)

    def test_a_stale_retarget_is_rejected_then_fresh_proof_accepted(self):
        state = State().succeeded()
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("recorded no merge candidate", text)
        backend, e2e = state.receipt("backend"), state.receipt("e2e")
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)
        self.assertIn(
            f"Backend CI run {backend['id']} and E2E CI run {e2e['id']}", text
        )
        self.assertIn(
            "proved against the base tip at this run, not at merge time", text
        )

    def test_a_receipt_of_another_pull_request_on_the_same_head_is_rejected(self):
        state = State().succeeded()
        state.receipt("backend", pr=4334)
        state.receipt("e2e")
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("it tested pr 4334; now 4335", text)

    def test_a_receipt_from_an_older_base_tip_is_rejected(self):
        state = State().succeeded()
        # The recorded pull request's own, older base.sha.
        state.receipt("backend", base_sha=state.pull["base"]["sha"])
        state.receipt("e2e")
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("it tested base_sha", text)

    def test_a_receipt_of_another_tree_head_or_base_is_rejected(self):
        for change in (
            {"merge_tree": "0" * 40},
            {"head_sha": "1" * 40},
            {"base_ref": "main"},
            {"kind": "e2e"},
            {"schema": 2},
        ):
            with self.subTest(change=change):
                state = State().succeeded()
                state.receipt("backend", **change)
                state.receipt("e2e")
                code, text, _ = judge(state)
                self.assertEqual(code, 1, text)
                key = next(iter(change))
                self.assertIn(f"it tested {key} {change[key]!r}", text)

    def test_a_skipped_producer_is_rejected(self):
        state = State().succeeded()
        skipped = load("skipped-4332/run-45070969031.json")
        state.runs = [run for run in state.runs if run["path"] != skipped["path"]]
        state.runs.append(dict(skipped, head_sha=state.head))
        state.jobs[skipped["id"]] = load("skipped-4332/jobs-45070969031.json")["jobs"]
        state.receipt("e2e")
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("Backend Tests Pass is absent", text)

    def test_one_producer_alone_does_not_pass(self):
        state = State().succeeded()
        state.receipt("backend")
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("no successful e2e run", text)

    def test_a_failed_receipt_job_is_rejected(self):
        state = State().succeeded()
        state.receipt("backend", conclusion="failure")
        state.receipt("e2e")
        self.assertEqual(judge(state)[0], 1)

    def test_a_delayed_timeline_does_not_hide_the_retarget(self):
        state = State("timeline-4332.json")
        code, text, _ = judge(state, base_changed=True)
        self.assertEqual(code, 1, text)
        self.assertIn("The base changed", text)

    def test_a_manual_retarget_counts_too(self):
        self.assertEqual(judge(State("timeline-4293.json"))[0], 1)

    def test_an_uncomputed_merge_polls_then_fails_closed(self):
        state = State()
        state.pull["mergeable"] = None
        code, text, sleeps = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("has not computed a merge", text)
        self.assertEqual(sleeps, [gate.IDENTITY_SECONDS] * gate.IDENTITY_POLLS)

    def test_a_conflicting_pull_request_fails_closed(self):
        state = State()
        state.pull["mergeable"] = False
        self.assertEqual(judge(state)[0], 1)

    def test_a_candidate_on_an_older_tip_is_not_accepted(self):
        state = State()
        state.branch["commit"]["sha"] = "2" * 40  # The base moved on.
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("has not computed a merge", text)

    def test_a_change_during_evaluation_fails_closed(self):
        state = State().succeeded()
        state.receipt("backend")
        state.receipt("e2e")
        real = state.json
        reads = {"pull": 0}

        def json(path):
            if path.endswith(f"/pulls/{PR}"):
                reads["pull"] += 1
                if reads["pull"] == 2:
                    # Constructed: the base gained a commit while it was judged,
                    # and GitHub re-merged the head onto it (a coherent state).
                    newer = "5" * 40
                    state.branch["commit"]["sha"] = newer
                    state.commit["parents"][0]["sha"] = newer
            return real(path)

        state.json = json
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("moved while it was judged", text)

    def test_moving_onto_main_while_judged_is_judged_as_main(self):
        state = State().succeeded()
        real = state.json
        reads = {"pull": 0}

        def json(path):
            if path.endswith(f"/pulls/{PR}"):
                reads["pull"] += 1
                if reads["pull"] >= 2:
                    pull, link = real(path)
                    pull["base"]["ref"] = "main"
                    return pull, link
            return real(path)

        state.json = json
        # Guarded is main; the first read was dev, a later one main.
        code, text, _ = judge(state, guarded="main")
        self.assertEqual(code, 1, text)
        self.assertIn("::error", text)

    def test_an_uncomputed_merge_into_another_base_only_warns(self):
        state = State("timeline-4332.json")
        state.pull["mergeable"] = None
        code, text, _ = judge(state, guarded="main")
        self.assertEqual(code, 0, text)
        self.assertIn("::warning", text)

    def test_the_base_advancing_after_a_pass_makes_it_stale(self):
        state = State().succeeded()
        state.receipt("backend")
        state.receipt("e2e")
        self.assertEqual(judge(state)[0], 0)
        # Constructed: the base gained a commit and GitHub re-merged the head.
        newer = "4" * 40
        state.branch["commit"]["sha"] = newer
        state.commit["parents"][0]["sha"] = newer
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("it tested base_sha", text)

    def test_an_api_error_fails_closed(self):
        state = State()

        def broken(path):
            raise urllib.error.HTTPError(path, 502, "Bad Gateway", None, None)

        state.json = broken
        # Before the base is known, fail closed whatever it turns out to be.
        code, text, _ = judge(state, guarded="main")
        self.assertEqual(code, 1, text)
        self.assertIn("::error title=CI ran on this base::", text)

    def test_into_another_base_it_passes_and_says_what_main_requires(self):
        code, text, sleeps = judge(State(), guarded="main")
        self.assertEqual(code, 0, text)
        self.assertIn("Into dev this passes", text)
        self.assertIn("Into main it would fail", text)
        self.assertEqual(sleeps, [])

    def test_it_waits_for_this_head_s_running_producers(self):
        state = State().succeeded()
        running = [dict(run, status="in_progress") for run in state.runs]
        done = state.runs
        calls = {"n": 0}
        real = state.json

        def json(path):
            if "/actions/runs?head_sha=" in path:
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"workflow_runs": copy.deepcopy(running)}, ""
                state.receipt("backend")
                state.receipt("e2e")
                return {"workflow_runs": copy.deepcopy(done)}, ""
            return real(path)

        state.json = json
        code, text, sleeps = judge(state)
        self.assertEqual(code, 0, text)
        self.assertEqual(sleeps, [gate.EVIDENCE_SECONDS])

    def test_a_retarget_proof_run_proves_both_kinds(self):
        state = State().succeeded()  # Its direct runs recorded nothing.
        proof = state.proof_run()
        state.receipt("backend", at=proof)
        state.receipt("e2e", at=proof)
        state.paged = True  # Both receipts, one per page of the list.
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)
        self.assertIn(
            f"Backend CI run {proof['id']} and E2E CI run {proof['id']}", text
        )
        self.assertTrue(any("&page=2" in call for call in state.calls))

    def test_a_rejected_newer_run_does_not_hide_a_valid_older_one(self):
        state = State().succeeded()
        state.receipt("backend")
        state.receipt("e2e")
        proof = state.proof_run()  # Newer, but its suites failed.
        for job in state.jobs[proof["id"]]:
            job["conclusion"] = "failure"
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)
        self.assertTrue(
            any(f"/actions/runs/{proof['id']}/jobs?" in call for call in state.calls)
        )

    def test_producers_that_never_finish_fail_after_the_wait(self):
        state = State().succeeded()
        for run in state.runs:
            run["status"] = "in_progress"
        code, text, sleeps = judge(state)
        self.assertEqual(code, 1, text)
        self.assertEqual(sleeps, [gate.EVIDENCE_SECONDS] * gate.EVIDENCE_POLLS)

    def test_a_failed_poll_while_waiting_counts_as_pending(self):
        state = State().succeeded()
        real = state.json
        polls = {"n": 0}

        def json(path):
            if "/actions/runs?head_sha=" in path:
                polls["n"] += 1
                if polls["n"] == 1:
                    raise urllib.error.URLError("connection reset")
                state.receipt("backend")
                state.receipt("e2e")
            return real(path)

        state.json = json
        code, text, sleeps = judge(state)
        self.assertEqual(code, 0, text)
        self.assertEqual(sleeps, [gate.EVIDENCE_SECONDS])

    def test_the_verdict_says_when_the_backend_proof_ran_the_oss_scope(self):
        state = State().succeeded()
        state.receipt("backend")
        state.receipt("e2e")
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)
        self.assertNotIn("OSS scope", text)
        state.receipt("backend", scope="oss")
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)
        self.assertIn(gate.OSS_BACKEND, text)
        proof = state.proof_run()  # Retarget proof: always the OSS scope.
        state.receipt("backend", at=proof)
        state.receipt("e2e", at=proof)
        self.assertIn(gate.OSS_BACKEND, judge(state)[1])

    def test_an_unknown_or_missing_scope_is_rejected(self):
        for kind, scope in (("backend", "ee"), ("backend", None), ("e2e", "internal")):
            with self.subTest(kind=kind, scope=scope):
                state = State().succeeded()
                state.receipt(kind, scope=scope)
                state.receipt("e2e" if kind == "backend" else "backend")
                code, text, _ = judge(state)
                self.assertEqual(code, 1, text)
                self.assertIn(f"it recorded scope {scope!r}", text)


class AttemptTests(unittest.TestCase):
    """Re-run failed jobs: run 45140763390 (E2E CI, three attempts), recorded."""

    JOBS = load("rerun-45140763390/jobs.json")["jobs"]

    def test_a_job_copied_into_later_attempts_belongs_to_its_first(self):
        latest, attempt = gate.producing_attempt(self.JOBS, "e2e (distributed, 1/6)")
        self.assertEqual((latest["run_attempt"], attempt), (3, 1))

    def test_a_re_run_job_belongs_to_the_attempt_that_ran_it(self):
        latest, attempt = gate.producing_attempt(self.JOBS, "e2e (distributed, 2/6)")
        self.assertEqual((latest["conclusion"], attempt), ("success", 3))
        latest, attempt = gate.producing_attempt(self.JOBS, "E2E Tests Pass")
        self.assertEqual((latest["conclusion"], attempt), ("success", 3))

    def test_absent_or_inconsistent_copies_prove_nothing(self):
        self.assertEqual(gate.producing_attempt(self.JOBS, "no such job"), (None, None))
        copied = [
            dict(job, run_attempt=4)
            for job in self.JOBS
            if job["name"] == "e2e (distributed, 1/6)" and job["run_attempt"] == 3
        ]
        gap = [
            job
            for job in self.JOBS
            if not (job["name"] == "e2e (distributed, 1/6)" and job["run_attempt"] == 2)
        ] + copied
        with self.assertRaises(ValueError):
            gate.producing_attempt(gap, "e2e (distributed, 1/6)")

    def test_the_receipt_is_read_from_the_attempt_that_wrote_it(self):
        state = State().succeeded()
        run = state.receipt("backend", attempt=1)
        state.receipt("e2e")
        # Re-run failed jobs: attempt 2 copies the receipt job unchanged.
        jobs = state.jobs[run["id"]]
        recorded = next(job for job in jobs if job["name"] == gate.RECEIPT_JOB)
        jobs.append(dict(recorded, run_attempt=2))
        for job in [job for job in jobs if job["name"] == "Backend Tests Pass"]:
            jobs.append(dict(job, run_attempt=2))
        code, text, _ = judge(state)
        self.assertEqual(code, 0, text)


class ReceiptIntegrityTests(unittest.TestCase):
    def test_a_digest_mismatch_is_rejected(self):
        state = State().succeeded()
        run = state.receipt("backend")
        state.receipt("e2e")
        state.artifacts[run["id"]][0]["digest"] = "sha256:" + "0" * 64
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("does not match its digest", text)

    def test_exactly_one_artifact_with_exactly_one_member(self):
        state = State().succeeded()
        run = state.receipt("backend")
        state.receipt("e2e")
        state.artifacts[run["id"]] *= 2
        self.assertIn("expected one artifact", judge(state)[1])
        state = State().succeeded()
        state.receipt("backend", members=["receipt.json", "other.json"])
        state.receipt("e2e")
        self.assertIn("holds", judge(state)[1])

    def test_an_expired_artifact_does_not_count(self):
        state = State().succeeded()
        run = state.receipt("backend")
        state.receipt("e2e")
        state.artifacts[run["id"]][0]["expired"] = True
        self.assertEqual(judge(state)[0], 1)

    def test_a_receipt_that_is_not_an_object_is_rejected(self):
        state = State().succeeded()
        run = state.receipt("backend")
        state.receipt("e2e")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("receipt.json", "[1, 2]")
        url = state.artifacts[run["id"]][0]["archive_download_url"]
        state.blobs[url] = out.getvalue()
        state.artifacts[run["id"]][0]["digest"] = (
            "sha256:" + hashlib.sha256(state.blobs[url]).hexdigest()
        )
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)
        self.assertIn("holds no receipt object", text)

    def test_reads_retry_transient_failures_only(self):
        pauses = []
        tries = iter(
            [
                urllib.error.HTTPError("u", 502, "Bad Gateway", None, None),
                urllib.error.URLError("reset"),
                "ok",
            ]
        )

        def read():
            value = next(tries)
            if isinstance(value, Exception):
                raise value
            return value

        self.assertEqual(gate.with_retries(read, pauses.append), "ok")
        self.assertEqual(pauses, [1, 2])
        with self.assertRaises(urllib.error.HTTPError):
            gate.with_retries(
                lambda: (_ for _ in ()).throw(
                    urllib.error.HTTPError("u", 404, "Not Found", None, None)
                ),
                pauses.append,
            )
        self.assertEqual(pauses, [1, 2])

    def test_the_download_opener_follows_no_redirect(self):
        github = gate.GitHub("secret-token")
        self.assertTrue(
            any(isinstance(h, gate._NoRedirect) for h in github.bare.handlers)
        )
        self.assertIsNone(
            gate._NoRedirect().redirect_request(None, None, 302, "", {}, "x")
        )

    def test_a_download_never_forwards_the_token(self):
        github = gate.GitHub("secret-token")
        redirect = urllib.error.HTTPError(
            "https://api.github.com/x",
            302,
            "Found",
            {"Location": "https://storage.example/blob?sig=1"},
            None,
        )
        seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opened(request, timeout):
            seen.append(("api", request.get_header("Authorization")))
            raise redirect

        def fetched(url, timeout):
            seen.append(("storage", url))
            return Response(b"zip")

        with (
            mock.patch.object(github.bare, "open", opened),
            mock.patch.object(gate.urllib.request, "urlopen", fetched),
        ):
            self.assertEqual(github.download("https://api.github.com/x"), b"zip")
        self.assertEqual(
            seen,
            [
                ("api", "Bearer secret-token"),
                ("storage", "https://storage.example/blob?sig=1"),
            ],
        )


class WorkflowTests(unittest.TestCase):
    WORKFLOWS = SCRIPTS.parent / "workflows"

    def text(self, name):
        return (self.WORKFLOWS / name).read_text()

    def test_the_verdict_only_reads_and_is_never_skipped(self):
        text = self.text("ci-base-gate.yml")
        permissions = re.search(r"(?ms)^permissions:\n(.*?)(?=^\S)", text)[1]
        self.assertEqual(
            sorted(re.findall(r"(?m)^  ([a-z-]+): (\w+)$", permissions)),
            [("actions", "read"), ("contents", "read"), ("pull-requests", "read")],
        )
        jobs = text.split("\njobs:\n", 1)[1]
        self.assertNotRegex(jobs, r"(?m)^    if:")
        self.assertRegex(jobs, r"(?m)^    name: CI ran on this base$")
        self.assertNotIn("secrets.", text)
        self.assertIn(
            "  pull_request:\n    types: [opened, synchronize, reopened, edited]", text
        )

    def test_retarget_proof_runs_only_on_a_base_change_and_matches_the_verdict(self):
        text = self.text("retarget-proof.yml")
        self.assertIn("  pull_request:\n    types: [edited]\n", text)
        permissions = re.search(r"(?ms)^permissions:\n(.*?)(?=^\S)", text)[1]
        self.assertEqual(
            sorted(re.findall(r"(?m)^  ([a-z-]+): (\w+)$", permissions)),
            [("actions", "read"), ("contents", "read"), ("packages", "read")],
        )
        self.assertNotIn("secrets", text.split("\njobs:\n", 1)[1])
        candidate = re.search(r"(?ms)^  candidate:\n(.*?)(?=^  \S|\Z)", text)[1]
        self.assertIn("    if: ${{ github.event.changes.base }}\n", candidate)
        self.assertIn('[[ $parents == "$tip $HEAD_SHA" ]]', candidate)
        # A fork's pull request gets no proof: the check fails, never passes.
        self.assertIn('if [[ $HEAD_REPO != "$GITHUB_REPOSITORY" ]]; then', candidate)
        self.assertIn("does not run for pull requests from forks", candidate)
        # No path forwards a secret: the callee declares none to receive.
        for called in ("backend-ci.yml", "e2e-ci.yml"):
            text_called = self.text(called)
            call = text_called.split("  workflow_call:\n", 1)[1].split(
                "\n  workflow_dispatch:", 1
            )[0]
            self.assertNotIn("secrets", call.split("\n  schedule:", 1)[0])
        for kind, called in (("backend", "backend-ci.yml"), ("e2e", "e2e-ci.yml")):
            job = re.search(rf"(?ms)^  retarget-{kind}:\n(.*?)(?=^  \S|\Z)", text)[1]
            with self.subTest(kind=kind):
                self.assertIn("    needs: candidate\n", job)
                self.assertIn(f"    name: retarget-{kind}\n", job)
                self.assertIn("    if: ${{ github.event.changes.base }}\n", job)
                self.assertIn(f"    uses: ./.github/workflows/{called}\n", job)
                self.assertIn(f'      job_prefix: "retarget-{kind} / "\n', job)
                self.assertEqual(
                    gate.PRODUCERS[kind][".github/workflows/retarget-proof.yml"],
                    f"retarget-{kind} / ",
                )

    def test_producers_record_receipts_outside_the_required_aggregate(self):
        for kind, name, aggregate, selected in (
            (
                "backend",
                "backend-ci.yml",
                "backend-tests",
                "needs.changes.outputs.backend == 'true'",
            ),
            ("e2e", "e2e-ci.yml", "e2e-tests", "needs.changes.outputs.any == 'true'"),
        ):
            text = self.text(name)
            job = re.search(r"(?ms)^  candidate-receipt:\n(.*?)(?=^  \S|\Z)", text)[1]
            with self.subTest(kind=kind):
                self.assertIn(f"    name: {gate.RECEIPT_JOB}\n", job)
                self.assertIn(f"    needs: [changes, {aggregate}]\n", job)
                self.assertIn(
                    f"    if: ${{{{ github.event_name == 'pull_request' && needs.{aggregate}.result == 'success' && {selected} }}}}\n",
                    job,
                )
                self.assertIn(
                    f"name: ci-candidate-{kind}-attempt-${{{{ github.run_attempt }}}}",
                    job,
                )
                self.assertIn('--arg scope "$SCOPE"', job)
                self.assertIn("scope: $scope", job)
                # Default (read) permissions; the e2e workflow pins its count.
                self.assertNotIn("    permissions:", job)
                # The required aggregate never waits for it.
                block = re.search(rf"(?ms)^  {aggregate}:\n(.*?)(?=^  \S|\Z)", text)[1]
                self.assertNotIn("candidate-receipt", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
