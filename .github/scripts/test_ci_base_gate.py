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

    def receipt(self, kind, conclusion="success", members=None, **changes):
        """Attach a CONSTRUCTED receipt (and its job) to this head's producer."""
        run = self.producer(kind)
        attempt = changes.pop("attempt", 1)
        self.jobs[run["id"]] = [
            job for job in self.jobs[run["id"]] if job["name"] != gate.RECEIPT_JOB
        ] + [
            {
                "name": gate.RECEIPT_JOB,
                "status": "completed",
                "conclusion": conclusion,
                "run_attempt": attempt,
            }
        ]
        body = {
            "schema": 1,
            "kind": kind,
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
            for name in members or ["receipt.json"]:
                archive.writestr(name, json.dumps(body))
        blob = out.getvalue()
        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{run['id']}/zip"
        self.blobs[url] = blob
        self.artifacts[run["id"]] = [
            {
                "name": f"ci-candidate-{kind}-attempt-{attempt}",
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
        if path == f"{root}/branches/{self.pull['base']['ref']}":
            return copy.deepcopy(self.branch), ""
        if path.startswith(f"{root}/commits/"):
            return copy.deepcopy(self.commit), ""
        if path.startswith(f"{root}/issues/{PR}/timeline"):
            return copy.deepcopy(self.timeline), ""
        if path.startswith(f"{root}/actions/runs?head_sha="):
            return {"workflow_runs": copy.deepcopy(self.runs)}, ""
        match = re.fullmatch(rf"{root}/actions/runs/(\d+)/(jobs|artifacts)\?.*", path)
        if match:
            run_id, what = int(match[1]), match[2]
            if what == "jobs":
                return {"jobs": copy.deepcopy(self.jobs.get(run_id, []))}, ""
            return {"artifacts": copy.deepcopy(self.artifacts.get(run_id, []))}, ""
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
                self.assertEqual(judge(state)[0], 1)

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
        moved = copy.deepcopy(state.pull)
        moved["head"]["sha"] = "3" * 40
        state.pulls = [copy.deepcopy(state.pull), moved] + [moved] * gate.IDENTITY_POLLS
        code, text, _ = judge(state)
        self.assertEqual(code, 1, text)

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
        for kind, called in (("backend", "backend-ci.yml"), ("e2e", "e2e-ci.yml")):
            job = re.search(rf"(?ms)^  retarget-{kind}:\n(.*?)(?=^  \S|\Z)", text)[1]
            with self.subTest(kind=kind):
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
                # The required aggregate never waits for it.
                block = re.search(rf"(?ms)^  {aggregate}:\n(.*?)(?=^  \S|\Z)", text)[1]
                self.assertNotIn("candidate-receipt", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
