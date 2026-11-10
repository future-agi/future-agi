"""Failure-reporting regressions with external systems replaced, not qualification."""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, suppress
from pathlib import Path
from types import ModuleType
from unittest import mock

from deploy.tests.helm_gitops import acceptance, browser


class GitOpsFailureTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix="gitops-failure-test-")
        self.addCleanup(temporary.cleanup)
        fixture = acceptance.Fixture("argo", "test", Path(temporary.name) / "evidence")

        def remove_work():
            with suppress(FileNotFoundError):
                shutil.rmtree(fixture.work)

        self.addCleanup(remove_work)
        return fixture

    def no_external_steps(self, fixture):
        fixture.artifacts = {"a": {"marker": "a"}}
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name in (
            "start",
            "select",
            "successful",
            "witnesses",
            "browser",
            "permissions",
            "pinned_digest_ignores_moved_tag",
            "missing_digest",
            "failed_bootstrap",
            "untrusted_source",
            "image_only_rollback",
        ):
            stack.enter_context(mock.patch.object(fixture, name))
        return stack

    def test_empty_exception_messages_cannot_publish_passed_summary(self):
        for error in (AssertionError(), TimeoutError(), RuntimeError("failed")):
            with self.subTest(error=type(error).__name__):
                fixture = self.fixture()
                with (
                    mock.patch.object(fixture, "start", side_effect=error),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertFalse(fixture.exercise())
                summary = json.loads((fixture.output / "summary.json").read_text())
                self.assertEqual(summary["status"], "failed")
                self.assertEqual(summary["error"], str(error))
                self.assertEqual(summary["checkpoints"], [])
                self.assertFalse(fixture.work.exists())

    def test_success_summary_matches_successful_return(self):
        fixture = self.fixture()
        self.no_external_steps(fixture)
        self.assertTrue(fixture.exercise())
        summary = json.loads((fixture.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "passed")
        self.assertIsNone(summary["error"])
        self.assertFalse(fixture.work.exists())

    def test_cleanup_failure_cannot_publish_passed_summary(self):
        fixture = self.fixture()
        self.no_external_steps(fixture)
        with mock.patch.object(fixture, "cleanup", side_effect=RuntimeError()):
            self.assertFalse(fixture.exercise())
        summary = json.loads((fixture.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertIn("cleanup:", summary["error"])

    def test_timeout_redacts_console_and_summary_not_only_status_projection(self):
        fixture = self.fixture()
        secret = "synthetic-private-value"
        status = {
            "sync": {
                "status": "OutOfSync",
                "revision": "requested-digest",
                "comparedTo": {
                    "source": {"helm": {"valuesObject": {"password": secret}}}
                },
            },
            "history": [{"source": {"helm": {"values": secret}}}],
            "conditions": [
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "Failed",
                    "message": secret,
                }
            ],
        }
        console = io.StringIO()

        def wait():
            return fixture.wait(
                "controller readiness",
                lambda: {"status": status},
                lambda _: False,
                seconds=1,
            )

        with (
            mock.patch.object(fixture, "start", side_effect=wait),
            mock.patch.object(acceptance.time, "monotonic", side_effect=[0, 0, 2]),
            mock.patch.object(acceptance.time, "sleep"),
            redirect_stdout(console),
        ):
            self.assertFalse(fixture.exercise())
        summary = json.loads((fixture.output / "summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        for output in (console.getvalue(), json.dumps(summary)):
            self.assertNotIn(secret, output)
            self.assertNotIn("valuesObject", output)
            self.assertNotIn("history", output)
            self.assertIn("OutOfSync", output)
            self.assertIn("requested-digest", output)

    def test_timeout_does_not_echo_non_mapping_status(self):
        fixture = self.fixture()
        for document in (
            "synthetic-private-value",
            {"status": "synthetic-private-value"},
            None,
        ):
            with (
                self.subTest(document=document),
                mock.patch.object(acceptance.time, "monotonic", side_effect=[0, 0, 2]),
                mock.patch.object(acceptance.time, "sleep"),
            ):
                with self.assertRaises(acceptance.Refused) as caught:
                    fixture.wait(
                        "controller readiness",
                        lambda document=document: document,
                        lambda _: False,
                        seconds=1,
                    )
                self.assertNotIn("synthetic-private-value", str(caught.exception))
                self.assertTrue(str(caught.exception).endswith("last status={}"))


class BrowserFailureTests(unittest.TestCase):
    def run_failure(self, screenshot_error):
        temporary = tempfile.TemporaryDirectory(prefix="gitops-browser-failure-")
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "browser.json"
        secret = "synthetic-private-value"
        page = mock.Mock()
        page.goto.side_effect = ValueError(secret)
        if screenshot_error:
            page.screenshot.side_effect = screenshot_error
        else:
            page.screenshot.side_effect = lambda *, path, **kwargs: Path(
                path
            ).write_bytes(b"synthetic image")
        browser_instance = mock.Mock()
        browser_instance.new_page.return_value = page
        manager = mock.MagicMock()
        manager.__enter__.return_value.chromium.launch.return_value = browser_instance
        # The CLI imports Playwright at its external boundary. Patch only that
        # dependency, scoped to this call; the failure handler itself is real.
        package = ModuleType("playwright")
        package.__path__ = []
        api = ModuleType("playwright.sync_api")
        api.expect = mock.Mock()
        api.sync_playwright = mock.Mock(return_value=manager)
        package.sync_api = api
        config = {
            "ui": "http://localhost:1",
            "api": "http://localhost:2",
            "output": str(output),
            "email": "review@example.test",
            "password": secret,
            "first_login": False,
        }
        with (
            mock.patch.dict(
                sys.modules, {"playwright": package, "playwright.sync_api": api}
            ),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(config))),
        ):
            self.assertEqual(browser.main(), 1)
        result = json.loads(output.read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "ValueError")
        self.assertEqual(result["stage"], "login form")
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(result["frames"])
        browser_instance.close.assert_called_once_with()
        self.assertEqual(page.screenshot.call_count, 1)
        self.assertEqual(
            page.screenshot.call_args.kwargs["mask"], [page.locator.return_value]
        )
        return result, output

    def test_screenshot_failure_is_recorded_without_replacing_original_failure(self):
        result, output = self.run_failure(OSError("synthetic-private-value"))
        self.assertFalse(output.with_suffix(".png").exists())
        self.assertIn("screenshot", result)
        self.assertEqual(result["screenshot"]["status"], "failed")
        self.assertEqual(result["screenshot"]["error_type"], "OSError")
        self.assertTrue(result["screenshot"]["frames"])
        self.assertLessEqual(len(result["screenshot"]["frames"]), 6)

    def test_captured_failure_screenshot_does_not_turn_failure_into_success(self):
        result, output = self.run_failure(None)
        self.assertTrue(output.with_suffix(".png").is_file())
        self.assertEqual(result["screenshot"], {"status": "captured"})

    def test_error_details_keep_only_bounded_stack_locations(self):
        def fail(depth):
            if depth:
                return fail(depth - 1)
            raise RuntimeError("synthetic-private-value")

        try:
            fail(10)
        except RuntimeError as error:
            detail = browser.error_details(error)
        self.assertEqual(detail["error_type"], "RuntimeError")
        self.assertEqual(len(detail["frames"]), 6)
        self.assertNotIn("synthetic-private-value", json.dumps(detail))
        for frame in detail["frames"]:
            self.assertEqual(set(frame), {"file", "function", "line"})
            self.assertEqual(frame["file"], Path(__file__).name)
            self.assertIsInstance(frame["line"], int)
