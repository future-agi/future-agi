"""The plan may replace the checkout's fallback only with verified dev timings."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import fetch_saved_timings as timings

ROOT = Path(__file__).resolve().parents[2]
API = "repos/owner/repo/actions"
RUNS = f"{API}/workflows/backend-ci.yml/runs?branch=dev&event=push&status=success&per_page=10"
LISTING = f"{API}/runs/2/artifacts?name=test-durations-saved"
DOWNLOAD = f"{API}/artifacts/17/zip"
FALLBACK = b'{"suite::old": 2}\n'
SAVED = b'{"suite::one": 0, "suite::two": 0.0123}\n'


def archive(data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(".test_durations", data)
    return buf.getvalue()


def run_row(day, **overrides):
    return {
        "id": day,
        "created_at": f"2026-11-{day:02d}T00:00:00Z",
        "event": "push",
        "head_branch": "dev",
        "conclusion": "success",
        **overrides,
    }


def checkout_fallback(root):
    path = root / "futureagi/.test_durations"
    path.parent.mkdir()
    path.write_bytes(FALLBACK)
    for args in (
        ["init", "-q"],
        ["add", "--", "futureagi/.test_durations"],
        [
            "-c",
            "user.name=ci",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "fallback",
        ],
    ):
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
    return path


class FetchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = checkout_fallback(Path(tmp.name))
        self.summary = Path(tmp.name) / "summary"
        self.log = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.log))
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY": "owner/repo",
                    "GITHUB_STEP_SUMMARY": str(self.summary),
                },
            )
        )

    def responses(self, data=SAVED, digest=None):
        raw = archive(data)
        saved = {
            "id": 17,
            "name": "test-durations-saved",
            "expired": False,
            "size_in_bytes": len(raw),
            "digest": digest or "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
        return {
            RUNS: json.dumps(
                {
                    "workflow_runs": [
                        run_row(3, head_branch="main"),
                        run_row(4, event="pull_request"),
                        run_row(1),
                        run_row(5, conclusion="failure"),
                        run_row(2),
                    ]
                }
            ).encode(),
            LISTING: json.dumps({"artifacts": [saved]}).encode(),
            DOWNLOAD: raw,
        }

    def test_only_the_newest_successful_dev_push_supplies_the_plan(self):
        replies = self.responses()
        with mock.patch.object(timings, "api", side_effect=replies.__getitem__) as api:
            timings.main(self.path)
        self.assertEqual(
            api.call_args_list, [mock.call(url) for url in (RUNS, LISTING, DOWNLOAD)]
        )
        self.assertEqual(self.path.read_bytes(), SAVED)
        self.assertNotIn("::warning", self.log.getvalue())
        summary = self.summary.read_text()
        self.assertIn("dev run `2`", summary)
        self.assertIn(
            "sha256:" + hashlib.sha256(replies[DOWNLOAD]).hexdigest(), summary
        )

    def assert_fallback(self, reason):
        self.assertEqual(self.path.read_bytes(), FALLBACK)
        self.assertIn(f"::warning title=Timings::{reason}", self.log.getvalue())
        self.assertIn("committed fallback", self.summary.read_text())

    def test_digest_mismatch_leaves_the_fallback(self):
        replies = self.responses(digest="sha256:" + "0" * 64)
        with mock.patch.object(timings, "api", side_effect=replies.__getitem__):
            timings.main(self.path)
        self.assert_fallback("artifact digest mismatch")

    def test_non_timings_json_is_rejected_even_when_the_digest_matches(self):
        for data in (b"[1]", b'{"suite::one": true}', b'{"suite::one": "slow"}'):
            with self.subTest(data=data):
                replies = self.responses(data)
                with mock.patch.object(timings, "api", side_effect=replies.__getitem__):
                    timings.main(self.path)
                self.assert_fallback("not a test-to-seconds object")

    def test_no_saved_artifact_in_latest_run_keeps_the_fallback(self):
        replies = {**self.responses(), LISTING: b'{"artifacts": []}'}
        with mock.patch.object(timings, "api", side_effect=replies.__getitem__) as api:
            timings.main(self.path)
        self.assertEqual(api.call_args_list, [mock.call(RUNS), mock.call(LISTING)])
        self.assert_fallback("no unique current test-durations-saved in dev run 2")

    def test_partial_cache_extraction_is_repaired_before_even_a_failed_api_call(self):
        self.path.write_bytes(b'{"partial')
        at_request = []

        def unavailable(_endpoint):
            at_request.append(self.path.read_bytes())
            raise subprocess.CalledProcessError(1, "gh api", stderr=b"API error body")

        with mock.patch.object(timings, "api", side_effect=unavailable):
            timings.main(self.path)
        self.assertEqual(at_request, [FALLBACK])
        self.assert_fallback("CalledProcessError")
        self.assertNotIn("API error body", self.log.getvalue())


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        self.plan = self.workflow.split("\n  plan:\n")[1].split("\n  test:\n")[0]
        self.steps = self.plan.split("\n      - ")

    def step(self, needle):
        return next(s for s in self.steps if needle in s)

    def test_timings_job_uploads_the_merged_dotfile(self):
        job = self.workflow.split("\n  timings:\n")[1].split("\n  collection:\n")[0]
        steps = job.split("\n      - ")
        upload = next(s for s in steps if "name: test-durations-saved\n" in s)
        for setting in (
            "uses: actions/upload-artifact@",
            "path: futureagi/.test_durations\n",
            "include-hidden-files: true\n",
            "retention-days: 14\n",
        ):
            self.assertIn(setting, upload)
        self.assertLess(job.index("merge_test_durations.py"), job.index(upload))

    def test_only_a_cache_miss_fetches_before_floors_and_plan_upload(self):
        restore = self.step("id: timing-cache\n")
        hit = self.step("if: steps.timing-cache.outputs.cache-matched-key != ''")
        fetch = self.step("fetch_saved_timings.py")
        self.assertIn("uses: actions/cache/restore@", restore)
        self.assertIn("$GITHUB_STEP_SUMMARY", hit)
        self.assertIn("${{ steps.timing-cache.outputs.cache-matched-key }}", hit)
        self.assertIn("if: steps.timing-cache.outputs.cache-matched-key == ''\n", fetch)
        self.assertIn("GH_TOKEN: ${{ github.token }}\n", fetch)
        order = [
            restore,
            fetch,
            "duration_floors.py apply",
            "name: test-durations-plan",
        ]
        positions = [self.plan.index(value) for value in order]
        self.assertEqual(positions, sorted(positions))

    def test_summary_and_fetch_failures_are_optional_but_the_plan_is_not(self):
        hit = self.step("if: steps.timing-cache.outputs.cache-matched-key != ''")
        miss = self.step("fetch_saved_timings.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = checkout_fallback(root)
            # A directory instead of a summary file forces both writes to fail.
            env = {**os.environ, "GITHUB_STEP_SUMMARY": tmp, "CACHE_KEY": "timings-key"}
            for step in (hit, miss):
                # Simulate the helper exiting nonzero after modifying the file.
                stub = "python3() { echo damaged > futureagi/.test_durations; return 1; }\n"
                script = step.split("        run: |\n", 1)[1]
                result = subprocess.run(
                    ["bash", "-e", "-c", stub + script],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    check=True,
                    text=True,
                )
                self.assertIn("::warning title=Timings::", result.stdout)
                self.assertEqual(path.read_bytes(), FALLBACK)
        for name in (
            "-m unittest discover",
            "duration_floors.py apply",
            "name: test-durations-plan",
        ):
            with self.subTest(step=name):
                required = self.step(name)
                for guard in ("continue-on-error:", "\n        if:", "||"):
                    self.assertNotIn(guard, required)


if __name__ == "__main__":
    unittest.main()
