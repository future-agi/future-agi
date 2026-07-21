"""
Unit tests for fi-simulate CLI.

Uses unittest.mock to patch urllib.request.urlopen so no real HTTP calls
are made. Tests cover:
  - happy path (pass rate >= threshold → exit 0)
  - regression (pass rate < threshold → exit 1)
  - timeout (poll exceeds limit → exit 2)
  - auth failure (HTTP 401 → exit 3)
  - network error → exit 3
  - --output json schema
  - --output github writes markdown
  - FI_API_KEY env var fallback
  - api_key:secret_key splitting
"""

import io
import json
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers to build mock HTTP responses
# ---------------------------------------------------------------------------

class _FakeHTTPResponse:
    def __init__(self, body: dict | list, status: int = 200):
        self._data = json.dumps(body).encode()
        self.status = status

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _mock_urlopen(*responses):
    """Return a side_effect list for urlopen returning each response in order."""
    return [_FakeHTTPResponse(r) for r in responses]


# ---------------------------------------------------------------------------
# Tests for FiSimulateClient
# ---------------------------------------------------------------------------

class TestFiSimulateClient(unittest.TestCase):

    def _client(self, **kwargs):
        from sdk.cli.client import FiSimulateClient
        return FiSimulateClient(
            base_url="https://test.example.com",
            api_key="testkey",
            **kwargs,
        )

    # --- start_execution --------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_start_execution_success(self, mock_urlopen):
        mock_urlopen.return_value = _FakeHTTPResponse(
            {"execution_id": "exec-1", "status": "started"}
        )
        client = self._client()
        resp = client.start_execution("run-1")
        self.assertEqual(resp["execution_id"], "exec-1")

    @patch("urllib.request.urlopen")
    def test_start_execution_with_scenarios(self, mock_urlopen):
        mock_urlopen.return_value = _FakeHTTPResponse(
            {"execution_id": "exec-2", "status": "started"}
        )
        client = self._client()
        resp = client.start_execution("run-1", scenario_ids=["s1", "s2"])
        self.assertEqual(resp["execution_id"], "exec-2")

    # --- get_status -------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_get_status(self, mock_urlopen):
        mock_urlopen.return_value = _FakeHTTPResponse(
            {"status": "completed", "execution_id": "exec-1", "total_calls": 10,
             "completed_calls": 10, "failed_calls": 0}
        )
        client = self._client()
        status = client.get_status("run-1")
        self.assertEqual(status["status"], "completed")

    # --- auth errors ------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_auth_failure_raises(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized",
            hdrs=None, fp=io.BytesIO(b"Unauthorized"),
        )
        from sdk.cli.client import FiSimulateClient, SimulateClientError
        client = FiSimulateClient(base_url="https://x.com", api_key="bad")
        with self.assertRaises(SimulateClientError) as ctx:
            client.get_status("run-1")
        self.assertIn("401", str(ctx.exception))

    # --- network error ----------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_network_error_raises(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        from sdk.cli.client import FiSimulateClient, SimulateClientError
        client = FiSimulateClient(base_url="https://x.com", api_key="k")
        with self.assertRaises(SimulateClientError):
            client.get_status("run-1")

    # --- compute_pass_rate ------------------------------------------------

    def test_compute_pass_rate_from_pass_rate_field(self):
        from sdk.cli.client import FiSimulateClient
        summary = [{"pass_rate": 0.9}, {"pass_rate": 0.8}]
        self.assertAlmostEqual(FiSimulateClient.compute_pass_rate(summary), 0.85)

    def test_compute_pass_rate_from_counts(self):
        from sdk.cli.client import FiSimulateClient
        summary = [{"pass_count": 8, "total_count": 10}]
        self.assertAlmostEqual(FiSimulateClient.compute_pass_rate(summary), 0.8)

    def test_compute_pass_rate_empty(self):
        from sdk.cli.client import FiSimulateClient
        self.assertEqual(FiSimulateClient.compute_pass_rate([]), 0.0)

    # --- poll_until_terminal ---------------------------------------------

    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_poll_until_terminal_completes(self, _sleep, mock_urlopen):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse({"status": "running", "execution_id": "e1"}),
            _FakeHTTPResponse({"status": "completed", "execution_id": "e1",
                               "total_calls": 5, "completed_calls": 5, "failed_calls": 0}),
        ]
        client = self._client()
        result = client.poll_until_terminal("run-1", timeout=60, poll_interval=1)
        self.assertEqual(result["status"], "completed")

    @patch("time.monotonic")
    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_poll_timeout_raises(self, _sleep, mock_urlopen, mock_mono):
        # monotonic returns values that expire immediately after first call
        mock_mono.side_effect = [0.0, 0.0, 9999.0]
        mock_urlopen.return_value = _FakeHTTPResponse(
            {"status": "running", "execution_id": "e1"}
        )
        from sdk.cli.client import FiSimulateClient, SimulateTimeoutError
        client = FiSimulateClient(base_url="https://x.com", api_key="k")
        with self.assertRaises(SimulateTimeoutError):
            client.poll_until_terminal("run-1", timeout=5, poll_interval=1)

    # --- api_key:secret_key split -----------------------------------------

    def test_secret_key_header(self):
        from sdk.cli.client import FiSimulateClient
        client = FiSimulateClient(
            base_url="https://x.com", api_key="mykey", secret_key="mysecret"
        )
        headers = client._headers()
        self.assertEqual(headers["X-Api-Key"], "mykey")
        self.assertEqual(headers["X-Secret-Key"], "mysecret")
        self.assertNotIn("Authorization", headers)

    def test_bearer_header(self):
        from sdk.cli.client import FiSimulateClient
        client = FiSimulateClient(base_url="https://x.com", api_key="mytoken")
        headers = client._headers()
        self.assertEqual(headers["Authorization"], "Bearer mytoken")
        self.assertNotIn("X-Api-Key", headers)


# ---------------------------------------------------------------------------
# Tests for main.py (cmd_run)
# ---------------------------------------------------------------------------

class TestCmdRun(unittest.TestCase):

    def _run(self, extra_argv=None):
        """Call main() with a mocked sys.argv and return the exit code."""
        argv = ["fi-simulate", "run", "--test-id", "run-test-uuid", "--api-key", "testkey"]
        if extra_argv:
            argv += extra_argv
        with patch("sys.argv", argv):
            from sdk.cli.main import build_parser, cmd_run
            parser = build_parser()
            args = parser.parse_args(argv[1:])
            return cmd_run(args)

    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_happy_path_exit_0(self, _sleep, mock_urlopen):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse({"execution_id": "e1", "status": "started"}),
            _FakeHTTPResponse({"status": "completed", "execution_id": "e1",
                               "total_calls": 10, "completed_calls": 10, "failed_calls": 0}),
            _FakeHTTPResponse([{"pass_rate": 0.9}]),
        ]
        code = self._run()
        self.assertEqual(code, 0)

    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_regression_exit_1(self, _sleep, mock_urlopen):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse({"execution_id": "e1", "status": "started"}),
            _FakeHTTPResponse({"status": "completed", "execution_id": "e1",
                               "total_calls": 10, "completed_calls": 10, "failed_calls": 0}),
            _FakeHTTPResponse([{"pass_rate": 0.5}]),
        ]
        code = self._run(["--threshold", "0.8"])
        self.assertEqual(code, 1)

    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_execution_failed_exit_2(self, _sleep, mock_urlopen):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse({"execution_id": "e1", "status": "started"}),
            _FakeHTTPResponse({"status": "failed", "execution_id": "e1",
                               "error": "Something went wrong"}),
        ]
        code = self._run()
        self.assertEqual(code, 2)

    @patch("urllib.request.urlopen")
    def test_auth_error_exit_3(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized",
            hdrs=None, fp=io.BytesIO(b"Unauthorized"),
        )
        code = self._run()
        self.assertEqual(code, 3)

    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_output_json_valid(self, _sleep, mock_urlopen):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse({"execution_id": "e1", "status": "started"}),
            _FakeHTTPResponse({"status": "completed", "execution_id": "e1",
                               "total_calls": 5, "completed_calls": 5, "failed_calls": 0}),
            _FakeHTTPResponse([{"pass_rate": 0.9}]),
        ]
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self._run(["--output", "json"])
        output = json.loads(captured.getvalue())
        self.assertIn("pass_rate", output)
        self.assertIn("execution_id", output)
        self.assertTrue(output["passed"])

    @patch("os.environ.get")
    @patch("urllib.request.urlopen")
    @patch("time.sleep", return_value=None)
    def test_fi_api_key_env_var(self, _sleep, mock_urlopen, mock_env):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse({"execution_id": "e1", "status": "started"}),
            _FakeHTTPResponse({"status": "completed", "execution_id": "e1",
                               "total_calls": 2, "completed_calls": 2, "failed_calls": 0}),
            _FakeHTTPResponse([{"pass_rate": 1.0}]),
        ]
        mock_env.side_effect = lambda key, default="": (
            "envkey" if key == "FI_API_KEY" else default
        )
        argv = ["fi-simulate", "run", "--test-id", "run-uuid"]
        with patch("sys.argv", argv):
            from sdk.cli.main import build_parser, cmd_run
            parser = build_parser()
            args = parser.parse_args(argv[1:])
            # Override api_key to None so env fallback kicks in
            args.api_key = None
            code = cmd_run(args)
        self.assertEqual(code, 0)


# ---------------------------------------------------------------------------
# Tests for output.py
# ---------------------------------------------------------------------------

class TestOutput(unittest.TestCase):

    def _common(self):
        return dict(
            run_test_id="run-1",
            execution_id="exec-1",
            status="completed",
            pass_rate=0.9,
            threshold=0.8,
            eval_summary=[{"name": "accuracy", "pass_rate": 0.9}],
            total_calls=10,
            completed_calls=10,
            failed_calls=0,
        )

    def test_print_text_outputs_something(self):
        from sdk.cli.output import print_text
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_text(**self._common())
        self.assertIn("Pass rate", buf.getvalue())
        self.assertIn("PASS", buf.getvalue())

    def test_print_json_valid_schema(self):
        from sdk.cli.output import print_json
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_json(**self._common(), passed=True)
        obj = json.loads(buf.getvalue())
        for key in ("run_test_id", "execution_id", "pass_rate", "threshold", "passed"):
            self.assertIn(key, obj)

    def test_github_summary_writes_markdown(self):
        import tempfile
        from sdk.cli.output import write_github_summary
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as fh:
            tmp = fh.name
        write_github_summary(**self._common(), passed=True, summary_file=tmp)
        with open(tmp) as fh:
            content = fh.read()
        self.assertIn("fi-simulate Results", content)
        self.assertIn("Pass rate", content)
        self.assertIn("accuracy", content)


if __name__ == "__main__":
    unittest.main()
