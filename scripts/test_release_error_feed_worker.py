import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("worker_release", Path(__file__).with_name("release-error-feed-worker.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
SHA = "a" * 40
VERSION = "v1.2.3"
RECEIPT = {"schema_version": 1, "version": VERSION, "source_sha": SHA,
           "image": worker.IMAGE + ":" + VERSION, "digest": "sha256:" + "b" * 64}
RELEASE = {"tag_name": "platform/" + VERSION, "draft": False,
           "prerelease": False, "body": json.dumps(RECEIPT)}


class ReleaseTests(unittest.TestCase):
    def release(self, **kwargs):
        return worker.release_worker(VERSION, retry_only=kwargs.get("retry_only", False),
                                     source_ref="reviewed-branch", request_id="123-1")

    def test_retry_reuses_receipt_without_dispatch(self):
        with patch.object(worker, "api", return_value=RELEASE) as api:
            self.assertEqual(self.release(retry_only=True), RECEIPT)
            self.assertEqual(api.call_count, 1)

    def test_retry_without_receipt_fails_without_building(self):
        with patch.object(worker, "api", return_value=None) as api:
            with self.assertRaisesRegex(RuntimeError, "No verified"):
                self.release(retry_only=True)
            self.assertEqual(api.call_count, 1)

    def test_dispatch_waits_for_matching_run_not_unrelated_success(self):
        completed = {"id": 2, "display_title": "worker-release v1.2.3 123-1",
                     "status": "completed", "conclusion": "success", "html_url": "run"}
        unrelated = {**completed, "display_title": "worker-release v1.2.3 999-1"}
        with patch.object(worker, "api", side_effect=[None, {"sha": SHA}, None,
                          {"workflow_runs": [unrelated]}, {"workflow_runs": [completed]}, RELEASE]) as api, \
                patch.object(worker.time, "sleep") as sleep:
            self.assertEqual(self.release(), RECEIPT)
            payload = api.call_args_list[2].args[1]["client_payload"]
            self.assertEqual(payload["source_sha"], SHA)
            self.assertEqual(payload["request_id"], "123-1")
            sleep.assert_called_once_with(15)

    def test_failed_or_cancelled_run_never_passes(self):
        for conclusion in ["failure", "cancelled", "timed_out"]:
            with self.subTest(conclusion=conclusion), patch.object(worker, "api", side_effect=[
                None, {"sha": SHA}, None, {"workflow_runs": [{"id": 1,
                "display_title": "worker-release v1.2.3 123-1", "status": "completed",
                "conclusion": conclusion, "html_url": "run"}]}]):
                with self.assertRaises(RuntimeError):
                    self.release()

    def test_timeout_blocks_bump(self):
        with patch.object(worker, "api", side_effect=[None, {"sha": SHA}, None]), \
                patch.object(worker.time, "monotonic", side_effect=[0, 2401]):
            with self.assertRaises(TimeoutError):
                self.release()

    def test_hostile_version_and_missing_source_rejected(self):
        with patch.object(worker, "api", return_value=None) as api:
            for version in ["", "v1.2.3\ninjected=x", "v1.2.3-dev", "$(echo bad)"]:
                with self.assertRaises(ValueError):
                    worker.release_worker(version, retry_only=False, source_ref="main", request_id="1-1")
            api.assert_not_called()
            with self.assertRaises(ValueError):
                worker.release_worker(VERSION, retry_only=False, source_ref="", request_id="1-1")
            self.assertEqual(api.call_count, 1)

    def test_receipt_must_match_version_source_image_and_digest(self):
        for key, value in [("version", "v9.0.0"), ("source_sha", "c" * 40),
                           ("image", "other/image:v1.2.3"), ("digest", "unknown")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker.validate_receipt({**RELEASE, "body": json.dumps({**RECEIPT, key: value})}, VERSION, SHA)
        for field in ["draft", "prerelease"]:
            with self.assertRaises(ValueError):
                worker.validate_receipt({**RELEASE, field: True}, VERSION)

    def test_only_explicit_404_means_absent(self):
        for status in [403, 429, 500]:
            with patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", f"(HTTP {status})")):
                with self.assertRaises(RuntimeError):
                    worker.api("releases/tags/test", missing_ok=True)
        with patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "(HTTP 404)")):
            self.assertIsNone(worker.api("releases/tags/test", missing_ok=True))


if __name__ == "__main__":
    unittest.main()
