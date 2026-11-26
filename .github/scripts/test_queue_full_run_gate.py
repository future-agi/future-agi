"""Tests for queue_full_run_gate.py. Payloads are constructed, trimmed to the
fields the gate reads; the merge-commit shape matches the queue branch
gh-readonly-queue/dev/pr-4454-* (a chain of "Merge pull request #N" commits,
each with the member head as second parent)."""

import hashlib
import io
import json
import pathlib
import sys
import unittest
import zipfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import queue_full_run_gate as gate  # noqa: E402

REPO = "future-agi/future-agi"
BASE = "b" * 40


def sha(n):
    return f"{n:040x}"


class Api:
    def __init__(self):
        self.commits, self.pulls, self.runs = {}, {}, {}
        self.jobs, self.artifacts, self.blobs = {}, {}, {}

    def member(self, pr, parent, *, fork=False):
        """Add one queue merge commit for pr onto parent; return its sha."""
        head, merge = sha(pr), sha(pr + 100000)
        self.commits[merge] = {
            "parents": [{"sha": parent}, {"sha": head}],
            "commit": {"message": f"Merge pull request #{pr} from future-agi/x\n\ntitle"},
        }
        owner = "someone/future-agi" if fork else REPO
        self.pulls[pr] = {"head": {"sha": head, "repo": {"full_name": owner}}}
        self.runs.setdefault(head, [])
        return merge

    def run(self, number, run_id, *, scope="internal", receipt=True, aggregate="success", path=gate.PRODUCER, **override):
        head = sha(number)
        self.runs[head].append({"id": run_id, "head_sha": head, "event": "pull_request", "path": path})
        jobs = [{"name": "Backend Tests Pass", "run_attempt": 1, "conclusion": aggregate, "started_at": "a", "completed_at": "b"}]
        if receipt:
            jobs.append({"name": gate.RECEIPT_JOB, "run_attempt": 1, "conclusion": "success", "started_at": "a", "completed_at": "b"})
            body = {"schema": 1, "kind": "backend", "scope": scope, "pr": number, "head_sha": head, "run_id": run_id, "run_attempt": 1}
            body.update(override)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("receipt.json", json.dumps(body))
            blob, url = buf.getvalue(), f"https://api.github.com/blob/{run_id}"
            self.blobs[url] = blob
            self.artifacts[run_id] = [{
                "name": "ci-candidate-backend-attempt-1", "expired": False,
                "digest": "sha256:" + hashlib.sha256(blob).hexdigest(), "archive_download_url": url,
            }]
        self.jobs[run_id] = jobs

    def json(self, path):
        root = f"/repos/{REPO}"
        if path.startswith(f"{root}/compare/dev..."):
            # dev's tip is BASE: every queue merge above it is unlanded.
            self.compared = path
            return {"merge_base_commit": {"sha": BASE}}, ""
        if path.startswith(f"{root}/commits/"):
            return self.commits[path.rsplit("/", 1)[1]], ""
        if path.startswith(f"{root}/pulls/"):
            return self.pulls[int(path.rsplit("/", 1)[1])], ""
        if path.startswith(f"{root}/actions/runs?head_sha="):
            head = path.split("head_sha=")[1].split("&")[0]
            return {"workflow_runs": self.runs.get(head, [])}, ""
        run_id, what = path.split("/actions/runs/")[1].split("?")[0].split("/")
        if what == "jobs":
            return {"jobs": self.jobs.get(int(run_id), [])}, ""
        return {"artifacts": self.artifacts.get(int(run_id), [])}, ""

    def download(self, url):
        return self.blobs[url]


def judge(api, head):
    code, lines = gate.decide(api, REPO, "dev", head)
    return code, "\n".join(lines)


class GateTests(unittest.TestCase):
    def test_a_member_with_a_passing_internal_receipt_passes(self):
        api = Api()
        head = api.member(4454, BASE)
        api.run(4454, 9001)
        code, text = judge(api, head)
        self.assertEqual(code, 0, text)
        self.assertIn("run 9001 ran the internal suite", text)

    def test_a_held_light_run_records_no_receipt_and_fails_the_group(self):
        api = Api()
        head = api.member(4454, BASE)
        api.run(4454, 9001, receipt=False)
        code, text = judge(api, head)
        self.assertEqual(code, 1)
        self.assertIn("no receipt", text)
        self.assertIn("ci:full", text)

    def test_a_light_run_re_run_with_ci_full_counts_on_its_second_attempt(self):
        # The train adds ci:full and re-runs the whole light run. Attempt 1 has
        # only "Backend Tests Deferred" and no receipt; attempt 2 is full.
        api = Api()
        head = api.member(4454, BASE)
        api.run(4454, 9001)
        receipt = api.artifacts[9001][0]
        receipt["name"] = "ci-candidate-backend-attempt-2"
        api.jobs[9001] = [
            {"name": "Backend Tests Deferred", "run_attempt": 1, "conclusion": "success", "started_at": "a", "completed_at": "b"},
            {"name": "Backend Tests Pass", "run_attempt": 2, "conclusion": "success", "started_at": "c", "completed_at": "d"},
            {"name": gate.RECEIPT_JOB, "run_attempt": 2, "conclusion": "success", "started_at": "c", "completed_at": "d"},
        ]
        code, text = judge(api, head)
        self.assertEqual(code, 0, text)
        # Attempt 1 alone (the light run) does not count.
        api.jobs[9001] = api.jobs[9001][:1]
        code, text = judge(api, head)
        self.assertEqual(code, 1)
        self.assertIn("Backend Tests Pass did not pass", text)

    def test_every_member_is_checked_not_only_the_group_head(self):
        # HEADGREEN: a green entry behind cannot carry a light one ahead of it.
        api = Api()
        first = api.member(4131, BASE)
        head = api.member(4454, first)
        api.run(4454, 9002)
        code, text = judge(api, head)
        self.assertEqual(code, 1)
        self.assertIn("#4131", text)
        api.run(4131, 9001)
        self.assertEqual(judge(api, head)[0], 0)

    def test_an_oss_scope_or_another_pull_requests_receipt_does_not_count(self):
        for override in ({"scope": "oss"}, {"pr": 1}, {"head_sha": sha(7)}):
            with self.subTest(override=override):
                api = Api()
                head = api.member(4454, BASE)
                api.run(4454, 9001, **override)
                self.assertEqual(judge(api, head)[0], 1)

    def test_a_failed_aggregate_or_another_workflow_does_not_count(self):
        for kwargs in ({"aggregate": "failure"}, {"path": ".github/workflows/retarget-proof.yml"}):
            with self.subTest(**kwargs):
                api = Api()
                head = api.member(4454, BASE)
                api.run(4454, 9001, **kwargs)
                self.assertEqual(judge(api, head)[0], 1)

    def test_the_newest_passing_run_is_enough(self):
        api = Api()
        head = api.member(4454, BASE)
        api.run(4454, 9001)
        api.run(4454, 9002, receipt=False)  # a later light re-run
        self.assertEqual(judge(api, head)[0], 0)

    def test_a_fork_member_is_exempt(self):
        api = Api()
        head = api.member(1615, BASE, fork=True)
        code, text = judge(api, head)
        self.assertEqual(code, 0)
        self.assertIn("fork: exempt", text)

    def test_a_commit_that_is_not_a_queue_merge_fails_closed(self):
        api = Api()
        head = api.member(4454, BASE)
        api.commits[head]["commit"]["message"] = "squashed"
        code, text = judge(api, head)
        self.assertEqual(code, 1)
        self.assertIn("not a merge-queue merge commit", text)

    def test_the_queue_workflow_runs_the_gate_only_in_light_mode(self):
        text = (SCRIPTS.parent / "workflows/backend-queue-ci.yml").read_text()
        self.assertIn("if: vars.CI_SCOPE_MODE == 'light'", text)
        self.assertIn("python3 -I .github/scripts/queue_full_run_gate.py", text)
        # The script comes from dev's tip, not from the unlanded group.
        # The script comes from the target branch: base_sha can be the entry
        # ahead, which is unlanded too.
        self.assertIn("ref: ${{ github.event.merge_group.base_ref }}", text)
        self.assertNotIn("ref: ${{ github.event.merge_group.base_sha }}", text)
        self.assertIn("BASE_REF: ${{ github.event.merge_group.base_ref }}", text)

    def test_the_walk_starts_at_the_merge_base_not_the_event_base(self):
        # HEADGREEN: the event's base_sha for #4454 is #4131's queue merge
        # (gh-readonly-queue/dev/pr-4454-d5d90489...), not a dev commit.
        api = Api()
        ahead = api.member(4131, BASE)
        head = api.member(4454, ahead)
        api.run(4454, 9002)
        code, text = judge(api, head)
        self.assertEqual(code, 1)
        self.assertIn("#4131", text)
        self.assertTrue(api.compared.endswith(f"/compare/dev...{head}"))


if __name__ == "__main__":
    unittest.main()
