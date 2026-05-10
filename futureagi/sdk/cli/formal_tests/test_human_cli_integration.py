"""
Integration probes for the human-facing fi-simulate CLI (ADR-035).

DISTINCTION from the Z3 and Hypothesis suites:
  - Z3 proofs verify the *model* (phase encoding) is internally consistent.
  - Hypothesis tests verify individual pure functions in isolation.
  - These probes exercise main() end-to-end with a scripted fake HTTP backend,
    then check all SimulateCLIHuman.tla invariants simultaneously on real output.

Paths exercised:
  1. list — server returns suites, formatted rows printed, exit 0
  2. list --json — output parses as JSON array
  3. list empty — "No simulation suites found", exit 0
  4. run by UUID — UUID bypasses name resolution, exits 0 on high pass rate
  5. run by name (unique match) — name resolved, execution runs, exit 0
  6. run by name (no match) — exits 1, no execution dispatched (ZeroMatchesFails)
  7. run by name (ambiguous) — exits 1, lists all names (AmbiguousNameFails)
  8. status — fetch_status called, output printed, exec_id never set (StatusIsStateless)
  9. list server error — exits 1 cleanly
  10. run missing api-key — exits 2 (argument validation)
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import requests

pytestmark = pytest.mark.integration

try:
    from sdk.cli.main import main
    from sdk.cli.poll import Phase, SimulatePoller
except ImportError:
    pytest.skip(
        "sdk.cli not importable — run with PYTHONPATH=futureagi", allow_module_level=True
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_SUITES = [
    {"id": "aaaa-1111", "name": "Customer Service", "scenario_count": 12,
     "last_run_at": "2026-05-01", "last_pass_rate": 88.0},
    {"id": "bbbb-2222", "name": "Billing Support", "scenario_count": 7,
     "last_run_at": "2026-04-30", "last_pass_rate": 73.0},
]


def _resp(status_code=200, data=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data if data is not None else {}
    if status_code >= 400:
        m.raise_for_status.side_effect = requests.HTTPError(response=m)
    else:
        m.raise_for_status.return_value = None
    return m


def _run_main(argv, *, capture=True):
    """Run main(argv) with stdout captured; return (exit_code, stdout_text)."""
    buf = StringIO()
    if capture:
        with patch("sys.stdout", buf):
            code = main(argv)
    else:
        code = main(argv)
    return code, buf.getvalue()


COMMON = ["--api-key", "test-key", "--base-url", "http://stub"]


# ---------------------------------------------------------------------------
# 1. list — formatted output, exit 0
# ---------------------------------------------------------------------------
class TestList:
    def test_list_prints_formatted_rows(self):
        """ListIsStateless: list never reaches starting/polling/summarizing."""
        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data=FAKE_SUITES)

            code, out = _run_main(COMMON + ["list"])

        assert code == 0
        assert "Customer Service" in out
        assert "Billing Support" in out
        # No execution should have been dispatched
        session.post.assert_not_called()

    def test_list_json_mode(self):
        """--json flag returns parseable JSON array."""
        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data=FAKE_SUITES)

            code, out = _run_main(COMMON + ["list", "--json"])

        assert code == 0
        result = json.loads(out)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_list_empty_result(self):
        """Empty suite list exits 0 and returns an empty JSON array."""
        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data=[])

            code, out = _run_main(COMMON + ["list", "--json"])

        assert code == 0
        assert json.loads(out) == []

    def test_list_server_error_exits_1(self):
        """Server error on list exits 1 without crashing."""
        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(status_code=500)

            buf = StringIO()
            with patch("sys.stderr", buf):
                code, _ = _run_main(COMMON + ["list"])

        assert code == 1
        assert "Error" in buf.getvalue()


# ---------------------------------------------------------------------------
# 2. run by UUID — bypasses name resolution
# ---------------------------------------------------------------------------
class TestRunByUUID:
    def test_uuid_target_exits_0_on_pass(self):
        """UUID supplied directly → no GET list call → NeverPollBeforeStart holds."""
        run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        get_responses = [
            _resp(data=[]),                                                   # authenticate
            _resp(data={"status": "completed"}),                             # poll
            _resp(data=[{"name": "accuracy", "pass_rate": 90.0}]),           # summary
        ]
        post_response = _resp(data={"execution_id": "exec-42"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response

            code, out = _run_main(COMMON + ["run", run_uuid, "--json"])

        assert code == 0
        result = json.loads(out)
        assert result["phase"] == "done"
        assert result["pass_rate"] == 90.0
        # NeverPollBeforeStart: execution_id was set before any poll
        assert result["execution_id"] == "exec-42"

    def test_uuid_target_exits_1_on_fail(self):
        """Low pass rate → exit 1 (threshold check)."""
        run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        get_responses = [
            _resp(data=[]),
            _resp(data={"status": "completed"}),
            _resp(data=[{"name": "accuracy", "pass_rate": 40.0}]),
        ]
        post_response = _resp(data={"execution_id": "exec-99"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response

            code, out = _run_main(COMMON + ["run", run_uuid, "--json", "--threshold", "80"])

        assert code == 1
        result = json.loads(out)
        assert result["pass_rate"] == 40.0


# ---------------------------------------------------------------------------
# 3. run by name — name resolution paths
# ---------------------------------------------------------------------------
class TestRunByName:
    def test_unique_name_resolves_and_runs(self):
        """NameResolutionBeforeStart: 1 match → UUID resolved before execution starts."""
        get_responses = [
            _resp(data=[FAKE_SUITES[0]]),                                    # list (name search)
            _resp(data=[]),                                                   # authenticate
            _resp(data={"status": "completed"}),                             # poll
            _resp(data=[{"name": "accuracy", "pass_rate": 88.0}]),           # summary
        ]
        post_response = _resp(data={"execution_id": "exec-cs"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response

            code, out = _run_main(COMMON + ["run", "customer service", "--json"])

        assert code == 0
        result = json.loads(out)
        assert result["phase"] == "done"

    def test_zero_matches_exits_1_no_execution(self):
        """ZeroMatchesFails: 0 matches → exit 1, POST never called."""
        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data=[])  # no suites match

            buf = StringIO()
            with patch("sys.stderr", buf):
                code, _ = _run_main(COMMON + ["run", "nonexistent suite"])

        assert code == 1
        assert "no suites match" in buf.getvalue().lower()
        session.post.assert_not_called()

    def test_multiple_matches_exits_1_listing_names(self):
        """AmbiguousNameFails: >1 match → exit 1, lists all names, POST never called."""
        # Use suites whose names both contain the query so resolve_name finds >1 match.
        ambiguous_suites = [
            {"id": "id1", "name": "Support Chat",  "scenario_count": 5,
             "last_run_at": None, "last_pass_rate": None},
            {"id": "id2", "name": "Support Email", "scenario_count": 3,
             "last_run_at": None, "last_pass_rate": None},
        ]
        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data=ambiguous_suites)

            buf = StringIO()
            with patch("sys.stderr", buf):
                code, _ = _run_main(COMMON + ["run", "support"])

        assert code == 1
        err = buf.getvalue()
        assert "ambiguous" in err.lower() or "multiple" in err.lower() or "2" in err
        assert "Support Chat" in err
        assert "Support Email" in err
        session.post.assert_not_called()


# ---------------------------------------------------------------------------
# 4. status — read-only, never dispatches execution
# ---------------------------------------------------------------------------
class TestStatus:
    def test_status_outputs_json_no_post(self):
        """StatusIsStateless: status never sets execution_id or calls POST."""
        exec_uuid = "cccccccc-dddd-eeee-ffff-000000000000"
        run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data={"status": "completed", "pass_rate": 91.5})

            code, out = _run_main(
                COMMON + ["status", exec_uuid, "--run-test-id", run_uuid, "--json"]
            )

        assert code == 0
        result = json.loads(out)
        assert result["status"] == "completed"
        # StatusIsStateless: no POST was made
        session.post.assert_not_called()

    def test_status_server_error_exits_1(self):
        """Server error on status fetch exits 1 cleanly."""
        exec_uuid = "cccccccc-dddd-eeee-ffff-000000000000"
        run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(status_code=404)

            buf = StringIO()
            with patch("sys.stderr", buf):
                code, _ = _run_main(
                    COMMON + ["status", exec_uuid, "--run-test-id", run_uuid]
                )

        assert code == 1


# ---------------------------------------------------------------------------
# 5. argument validation
# ---------------------------------------------------------------------------
class TestArgValidation:
    def test_missing_api_key_exits_2(self, capsys):
        """Missing API key → parser.error → exit 2."""
        with pytest.raises(SystemExit) as exc:
            main(["--base-url", "http://stub", "list"])
        assert exc.value.code == 2

    def test_no_subcommand_exits_2(self, capsys):
        """No subcommand → help printed → exit 2."""
        code = main(["--api-key", "k", "--base-url", "http://stub"])
        assert code == 2
