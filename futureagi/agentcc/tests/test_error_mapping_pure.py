"""Dependency-free contracts. Run this file directly with Python through slot-run.

The Django adapter and actual exception classes are exercised by the separate
view suites; these tests execute the real shared status/copy/logging functions.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "views" / "_errors.py"
SPEC = importlib.util.spec_from_file_location("agentcc_error_mapping_pure", SOURCE)
errors = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = errors
SPEC.loader.exec_module(errors)

SENTINEL = "db.internal:5432 SELECT token FROM keys https://host.invalid/?key=sentinel"


class ErrorMappingTests(unittest.TestCase):
    def setUp(self):
        for target in (
            "socket.create_connection",
            "socket.getaddrinfo",
            "socket.socket.connect",
            "socket.socket.connect_ex",
        ):
            patcher = patch(target, side_effect=AssertionError("Network is forbidden"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_gateway_5xx_are_retryable_server_errors(self):
        for status in (500, 502, 503, 504, 599, None):
            with self.subTest(status=status):
                error = errors.upstream_error(status, service="gateway")
                self.assertEqual(error.status, 503)
                self.assertEqual(error.code, "gateway_unavailable")
                self.assertIn("temporarily unavailable", error.message)
                self.assertIn("retry", error.message)

    def test_gateway_4xx_keep_the_caller_status_and_action(self):
        for status, action in (
            (400, "settings"),
            (401, "administrator"),
            (403, "administrator"),
            (404, "Refresh"),
            (409, "Refresh"),
            (422, "settings"),
            (429, "retry"),
        ):
            with self.subTest(status=status):
                error = errors.upstream_error(status, service="gateway")
                self.assertEqual(error.status, status)
                self.assertIn(action, error.message)
                self.assertTrue(error.code.startswith("gateway_"))

    def test_upstream_status_is_not_copied_from_untrusted_values(self):
        for status in (SENTINEL, True, 200, 302, 600):
            with self.subTest(status=status):
                error = errors.upstream_error(status, service="gateway")
                self.assertEqual(error.status, 503)
                self.assertNotIn(SENTINEL, error.message)

    def test_provider_auth_failure_names_the_remedy(self):
        error = errors.upstream_error(401, service="provider")
        self.assertEqual(error.status, 400)
        self.assertEqual(error.code, "provider_authentication_failed")
        self.assertIn("rotate", error.message)

    def test_unexpected_logs_full_exception_with_stable_reference(self):
        logger = Mock()
        try:
            raise RuntimeError(SENTINEL)
        except RuntimeError as exc:
            error = errors.unexpected_error(
                exc, logger=logger, event="api_key_list_error"
            )
            args = logger.exception.call_args
            self.assertIs(args.kwargs["exc_info"][1], exc)
            self.assertIn(SENTINEL, str(args.kwargs["exc_info"][1]))
        self.assertEqual(error.status, 500)
        self.assertEqual(error.code, "agentcc_internal_error")
        self.assertEqual(
            error.message, "Something went wrong. Reference: agentcc_internal_error"
        )
        self.assertNotIn(SENTINEL, error.message)

    def test_secret_bearing_boundary_keeps_prior_type_only_logging(self):
        logger = Mock()
        error = errors.unexpected_error(
            RuntimeError(SENTINEL),
            logger=logger,
            event="fetch_models_error",
            log_detail=False,
        )
        logger.exception.assert_not_called()
        self.assertEqual(logger.warning.call_args.kwargs["error_type"], "RuntimeError")
        self.assertNotIn(SENTINEL, repr(logger.mock_calls))
        self.assertNotIn(SENTINEL, error.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
