"""Shards may restore only a dump that a successful dev push of this repo saved."""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import fetch_migrated_db as fetcher

NAME = "migrated-db-v3-" + "1" * 40
RECIPE = NAME + "-" + "2" * 16
API = "repos/owner/repo/actions"
LISTING = f"{API}/artifacts?name={NAME}&per_page=100"


def record(**members):
    files = {
        "dump.sql.gz": b"dump",
        "preseed": b"a" * 64 + b"\n",
        "recipe": RECIPE.encode() + b"\n",
        "checksums": b"sums\n",
        **members,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            if data is not None:
                archive.writestr(name, data)
    return buf.getvalue()


def run_row(run_id, **overrides):
    return {
        "id": run_id,
        "event": "push",
        "head_branch": "dev",
        "conclusion": "success",
        "path": ".github/workflows/backend-ci.yml",
        "repository": {"full_name": "owner/repo"},
        "head_repository": {"full_name": "owner/repo"},
        **overrides,
    }


def artifact(artifact_id, run_id, raw, day, **overrides):
    return {
        "id": artifact_id,
        "name": NAME,
        "expired": False,
        "created_at": f"2026-11-{day:02d}T00:00:00Z",
        "size_in_bytes": len(raw),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "workflow_run": {
            "id": run_id,
            "head_branch": "dev",
            "repository_id": 7,
            "head_repository_id": 7,
        },
        **overrides,
    }


class EligibleRunTest(unittest.TestCase):
    def test_only_a_successful_backend_ci_dev_push_of_this_repository(self):
        self.assertTrue(fetcher.eligible(run_row(1), "owner/repo"))
        for change in (
            {"event": "pull_request"},
            {"event": "workflow_dispatch"},
            {"head_branch": "main"},
            {"conclusion": "failure"},
            {"conclusion": None},
            {"path": ".github/workflows/other.yml"},
            {"head_repository": {"full_name": "fork/repo"}},
            {"head_repository": None},
            {"repository": {"full_name": "other/repo"}},
        ):
            self.assertFalse(
                fetcher.eligible(run_row(1, **change), "owner/repo"), change
            )


class UnpackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = Path(tmp.name) / "migrated-db"

    def unpack(self, raw, digest=None):
        digest = digest or "sha256:" + hashlib.sha256(raw).hexdigest()
        return fetcher.unpack(raw, digest, NAME, self.target)

    def test_a_complete_record_is_extracted_and_returns_its_recipe(self):
        self.assertEqual(self.unpack(record()), RECIPE)
        self.assertEqual(
            sorted(p.name for p in self.target.iterdir()), sorted(fetcher.RECORD)
        )

    def test_a_tampered_or_odd_record_is_refused(self):
        for raw, digest, reason in (
            (record(), "sha256:" + "0" * 64, "digest"),
            (record(extra=b"x"), None, "unexpected files"),
            (record(preseed=None), None, "unexpected files"),
            (record(**{"../recipe": b"x", "recipe": None}), None, "unexpected files"),
            (record(recipe=b"migrated-db-v3-other-1\n"), None, "other migrate inputs"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(fetcher.FetchError, reason):
                    fetcher.unpack(
                        raw,
                        digest or "sha256:" + hashlib.sha256(raw).hexdigest(),
                        NAME,
                        self.target.parent / reason.replace(" ", "-"),
                    )


class FetchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = Path(tmp.name) / "migrated-db"
        self.enterContext(mock.patch.dict("os.environ", GITHUB_REPOSITORY="owner/repo"))
        self.enterContext(
            mock.patch.object(
                fetcher.subprocess,
                "run",
                return_value=mock.Mock(stdout=NAME + "\n"),
            )
        )

    def serve(self, responses):
        calls = []

        def api(endpoint):
            calls.append(endpoint)
            reply = responses[endpoint]
            return reply if isinstance(reply, bytes) else json.dumps(reply).encode()

        self.enterContext(mock.patch.object(fetcher, "api", api))
        return calls

    def test_the_newest_eligible_dev_push_wins_over_newer_forks_and_failures(self):
        good, newer = record(), record(**{"dump.sql.gz": b"newer"})
        calls = self.serve(
            {
                LISTING: {
                    "artifacts": [
                        artifact(10, 100, good, 1),
                        artifact(13, 103, newer, 4, expired=True),
                        artifact(12, 102, newer, 3),
                        artifact(11, 101, newer, 2),
                    ]
                },
                f"{API}/runs/102": run_row(102, event="pull_request"),
                f"{API}/runs/101": run_row(101, conclusion="failure"),
                f"{API}/runs/100": run_row(100),
                f"{API}/artifacts/10/zip": good,
            }
        )
        recipe, source = fetcher.fetch(self.target)
        self.assertEqual(recipe, RECIPE)
        self.assertIn("dev run `100`", source)
        self.assertEqual((self.target / "dump.sql.gz").read_bytes(), b"dump")
        self.assertNotIn(f"{API}/runs/103", calls)
        self.assertNotIn(f"{API}/artifacts/12/zip", calls)

    def test_other_names_forks_and_other_branches_are_dropped_before_any_lookup(self):
        good = record()
        fork = {
            "id": 109,
            "head_branch": "dev",
            "repository_id": 7,
            "head_repository_id": 8,
        }
        branch = {
            "id": 108,
            "head_branch": "feature",
            "repository_id": 7,
            "head_repository_id": 7,
        }
        crowd = [
            artifact(20 + i, 200 + i, good, 10 + i, workflow_run=fork)
            for i in range(fetcher.CANDIDATES)
        ]
        calls = self.serve(
            {
                LISTING: {
                    "artifacts": [
                        *crowd,
                        artifact(19, 108, good, 9, workflow_run=branch),
                        artifact(18, 107, good, 8, name=NAME + "0"),
                        artifact(10, 100, good, 1),
                    ]
                },
                f"{API}/runs/100": run_row(100),
                f"{API}/artifacts/10/zip": good,
            }
        )
        self.assertEqual(fetcher.fetch(self.target)[0], RECIPE)
        self.assertEqual(calls, [LISTING, f"{API}/runs/100", f"{API}/artifacts/10/zip"])

    def test_only_the_newest_candidates_are_looked_up_and_size_is_capped(self):
        good = record()
        older = [
            artifact(30 + i, 300 + i, good, 20 - i) for i in range(fetcher.CANDIDATES)
        ]
        responses = {LISTING: {"artifacts": [*older, artifact(10, 100, good, 1)]}}
        responses.update(
            {
                f"{API}/runs/{300 + i}": run_row(300 + i, conclusion="failure")
                for i in range(fetcher.CANDIDATES)
            }
        )
        calls = self.serve(responses)
        with self.assertRaisesRegex(fetcher.FetchError, "no dump"):
            fetcher.fetch(self.target)
        self.assertNotIn(f"{API}/runs/100", calls)
        big = artifact(10, 100, good, 1, size_in_bytes=fetcher.MAX_BYTES + 1)
        self.serve({LISTING: {"artifacts": [big]}, f"{API}/runs/100": run_row(100)})
        with self.assertRaisesRegex(fetcher.FetchError, "too large"):
            fetcher.fetch(self.target)

    def test_no_eligible_dump_is_a_notice_and_never_a_failure_of_the_gate(self):
        self.serve(
            {
                LISTING: {"artifacts": [artifact(12, 102, record(), 3)]},
                f"{API}/runs/102": run_row(102, head_branch="feature"),
            }
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(fetcher.main(self.target), 1)
        self.assertIn("::notice title=Database dump::no dump", out.getvalue())
        self.assertFalse(self.target.exists())

    def test_an_api_error_does_not_leak_its_body(self):
        def api(endpoint):
            raise fetcher.subprocess.CalledProcessError(1, "gh", stderr=b"secret body")

        self.enterContext(mock.patch.object(fetcher, "api", api))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(fetcher.main(self.target), 1)
        self.assertIn("CalledProcessError", out.getvalue())
        self.assertNotIn("secret", out.getvalue())


if __name__ == "__main__":
    unittest.main()
