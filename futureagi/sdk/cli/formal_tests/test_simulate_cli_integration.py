"""
End-to-end integration probes for the fi-simulate polling state machine.

DISTINCTION from the Z3 and Hypothesis suites:
  - Z3 proofs verify that the *model* (state encoding) is internally consistent.
  - Hypothesis tests verify individual *methods* in isolation with property checks.
  - These integration probes run SimulatePoller.run() all the way through with a
    scripted fake HTTP backend, then check ALL TLA+ invariants simultaneously on
    the final PollState.

This catches emergent bugs that individually-correct methods can produce when they
interact: wrong phase transition ordering, execution_id set too late, summary fetched
before terminal status, etc.

Each scenario corresponds to a path in the SimulateCLI.tla state graph.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, call, patch

import pytest
import requests

pytestmark = pytest.mark.integration

try:
    from sdk.cli.poll import (
        Phase,
        PollState,
        SimulatePoller,
        TERMINAL_PHASES,
        TERMINAL_RUN_STATUSES,
    )
    from sdk.cli.main import _headless_output
except ImportError:
    pytest.skip(
        "sdk.cli.poll not importable — run with PYTHONPATH=futureagi", allow_module_level=True
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resp(status_code=200, data=None, error=False):
    """Build a mock response."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data if data is not None else {}
    if error or status_code >= 400:
        exc = requests.HTTPError(response=m)
        m.raise_for_status.side_effect = exc
    else:
        m.raise_for_status.return_value = None
    return m


def _poller(poll_interval_s=0.001, max_polls=10, timeout_s=999, threshold=80):
    return SimulatePoller(
        base_url="http://stub",
        api_key="test-key",
        poll_interval_s=poll_interval_s,
        max_polls=max_polls,
        timeout_s=timeout_s,
        threshold=threshold,
    )


def _assert_invariants(state: PollState, poller: SimulatePoller) -> None:
    """Check all TLA+ safety invariants on a final state simultaneously."""
    # TypeInvariant
    assert isinstance(state.phase, Phase), f"phase not a Phase: {state.phase}"
    assert state.exit_code in (-1, 0, 1), f"exit_code out of range: {state.exit_code}"
    assert state.polls_done >= 0
    assert state.elapsed_s >= 0

    # NeverPollBeforeStart — if we ever polled, execution_id must be set
    if state.polls_done > 0:
        assert state.execution_id is not None, (
            "NeverPollBeforeStart violated: polls_done > 0 but execution_id is None"
        )

    # SummaryOnlyAfterTerminal — pass_rate set only when run_status was terminal
    if state.pass_rate is not None:
        assert state.run_status in TERMINAL_RUN_STATUSES, (
            f"SummaryOnlyAfterTerminal violated: pass_rate set but run_status={state.run_status}"
        )

    # TimeoutBounded
    assert state.elapsed_s <= poller.timeout_s + poller.poll_interval_s, (
        f"TimeoutBounded violated: elapsed_s={state.elapsed_s} > timeout_s={poller.timeout_s}"
    )

    # ExitCodeOnlyWhenTerminal
    if state.exit_code != -1:
        assert state.is_terminal, (
            f"ExitCodeOnlyWhenTerminal violated: exit_code={state.exit_code} in non-terminal phase {state.phase}"
        )

    # TerminalIsStable — final state must be terminal (run() contract)
    assert state.is_terminal, f"run() returned non-terminal phase: {state.phase}"


# ------------------------------------------------------------------
# Scenario 1: Happy path — poll twice, complete, pass rate above threshold
# ------------------------------------------------------------------
class TestHappyPath:
    def test_run_to_done_with_high_pass_rate(self):
        """
        Path: init → authenticating → starting → polling(×2) → summarizing → done
        Final exit_code: 0 (pass_rate 92 >= threshold 80)
        """
        poller = _poller(threshold=80)

        get_responses = [
            _resp(data=[{"id": "rt-1", "name": "Suite"}]),      # authenticate
            _resp(data={"execution_id": "exec-1", "status": "running"}),   # poll 1 (via status endpoint)
            _resp(data={"execution_id": "exec-1", "status": "completed"}), # poll 2
            _resp(data=[{"name": "accuracy", "pass_rate": 92.0}]),         # eval summary
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response

            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.DONE
        assert state.execution_id == "exec-1"
        assert state.run_status == "completed"
        assert state.exit_code == 0
        assert state.pass_rate == 92.0
        assert state.polls_done == 2

    def test_run_to_done_with_low_pass_rate(self):
        """Same path but pass_rate 45 < threshold 80 → exit_code 1."""
        poller = _poller(threshold=80)

        get_responses = [
            _resp(data=[]),                                                  # authenticate
            _resp(data={"execution_id": "exec-1", "status": "completed"}),  # poll 1 terminal
            _resp(data=[{"name": "accuracy", "pass_rate": 45.0}]),           # summary
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.DONE
        assert state.exit_code == 1
        assert state.pass_rate == 45.0


# ------------------------------------------------------------------
# Scenario 2: Authentication failure
# ------------------------------------------------------------------
class TestAuthFailure:
    def test_401_terminates_in_failed(self):
        """Path: init → authenticating → failed (401)"""
        poller = _poller()

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(status_code=401, error=True)
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.FAILED
        assert state.exit_code == 1
        assert state.polls_done == 0  # never polled
        assert state.execution_id is None  # NeverPollBeforeStart still holds


# ------------------------------------------------------------------
# Scenario 3: Start execution fails (404)
# ------------------------------------------------------------------
class TestStartFailure:
    def test_404_on_execute_terminates_in_failed(self):
        """Path: init → authenticating → starting → failed (run_test not found)"""
        poller = _poller()

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.return_value = _resp(data=[])    # authenticate ok
            session.post.return_value = _resp(status_code=404, error=True)
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.FAILED
        assert state.exit_code == 1
        assert state.polls_done == 0
        assert state.execution_id is None  # NeverPollBeforeStart holds even on failure


# ------------------------------------------------------------------
# Scenario 4: Timeout — max_polls exhausted before terminal status
# ------------------------------------------------------------------
class TestTimeout:
    def test_max_polls_exhausted_terminates_in_timed_out(self):
        """Path: init → … → polling (×max_polls) → timed_out"""
        max_polls = 3
        poller = _poller(max_polls=max_polls, timeout_s=9999)

        # Every poll returns "running" — never completes
        get_responses = [
            _resp(data=[]),  # authenticate
            _resp(data={"execution_id": "exec-1", "status": "running"}),  # poll 1
            _resp(data={"execution_id": "exec-1", "status": "running"}),  # poll 2
            _resp(data={"execution_id": "exec-1", "status": "running"}),  # poll 3
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.TIMED_OUT
        assert state.exit_code == 1
        assert state.execution_id == "exec-1"  # was set before polling
        assert state.pass_rate is None  # SummaryOnlyAfterTerminal: no summary on timeout

    def test_time_budget_exhausted_terminates_in_timed_out(self):
        """TimeoutBounded: when elapsed + interval > timeout_s, transition to timed_out."""
        # poll_interval_s=10, timeout_s=5 → first poll would exceed budget
        poller = _poller(poll_interval_s=10, timeout_s=5)

        get_responses = [_resp(data=[])]  # authenticate ok
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.TIMED_OUT
        assert state.polls_done == 0  # no polls were made; timeout detected pre-poll
        assert state.execution_id == "exec-1"


# ------------------------------------------------------------------
# Scenario 5: Execution failed/cancelled — SummarySkipped path
# ------------------------------------------------------------------
class TestSummarySkipped:
    @pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
    def test_failed_execution_skips_summary(self, terminal_status):
        """Path: … → polling → summarizing → failed (no summary fetch for failed/cancelled)"""
        poller = _poller()

        get_responses = [
            _resp(data=[]),  # authenticate
            _resp(data={"execution_id": "exec-1", "status": terminal_status}),  # poll terminal
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.FAILED
        assert state.exit_code == 1
        assert state.pass_rate is None  # no summary was fetched
        # SummaryOnlyAfterTerminal: no summary GET was made for non-completed runs
        eval_calls = [str(c) for c in session.get.call_args_list]
        assert not any("eval-summary" in c for c in eval_calls), (
            "eval-summary endpoint called for non-completed execution — "
            "SummaryOnlyAfterTerminal violated"
        )


# ------------------------------------------------------------------
# Scenario 6: Transient poll error — recovers and continues
# ------------------------------------------------------------------
class TestTransientPollError:
    def test_recovers_from_one_poll_error(self):
        """A single poll network error increments polls_done and retries next iteration."""
        poller = _poller(max_polls=5)

        poll_error = MagicMock()
        poll_error.side_effect = requests.ConnectionError("transient")

        get_responses = [
            _resp(data=[]),                                                  # authenticate
            poll_error,                                                      # poll 1: network error
            _resp(data={"execution_id": "exec-1", "status": "completed"}),  # poll 2: terminal
            _resp(data=[{"name": "m", "pass_rate": 88.0}]),                 # summary
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        assert state.phase == Phase.DONE
        assert state.polls_done == 2
        assert state.exit_code == 0


# ------------------------------------------------------------------
# Scenario 7: Progress callback is fired on every poll
# ------------------------------------------------------------------
class TestProgressCallback:
    def test_progress_cb_receives_state_on_every_poll(self):
        """progress_cb is called after each poll with the current state."""
        poller = _poller()
        seen_phases: list[Phase] = []

        def on_progress(s: PollState):
            seen_phases.append(s.phase)

        poller.progress_cb = on_progress

        get_responses = [
            _resp(data=[]),
            _resp(data={"execution_id": "exec-1", "status": "running"}),
            _resp(data={"execution_id": "exec-1", "status": "completed"}),
            _resp(data=[{"name": "m", "pass_rate": 90.0}]),
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        _assert_invariants(state, poller)
        # progress_cb fired for each non-terminal poll (running → completed triggers transition)
        assert len(seen_phases) >= 1
        # All phases seen during callback must have been in polling
        for p in seen_phases:
            assert p == Phase.POLLING


# ------------------------------------------------------------------
# Scenario 8: Headless output round-trips through JSON
# ------------------------------------------------------------------
class TestHeadlessRoundTrip:
    def test_json_output_contains_all_invariant_fields(self):
        """The final PollState serialises to JSON with all required fields."""
        poller = _poller()

        get_responses = [
            _resp(data=[]),
            _resp(data={"execution_id": "exec-1", "status": "completed"}),
            _resp(data=[{"name": "m", "pass_rate": 95.0}]),
        ]
        post_response = _resp(data={"execution_id": "exec-1", "status": "pending"})

        with patch("sdk.cli.poll.requests.Session") as MockSession:
            session = MockSession.return_value
            session.headers = {}
            session.get.side_effect = get_responses
            session.post.return_value = post_response
            poller._session = session
            state = poller.run("run-test-id")

        buf = StringIO()
        with patch("sys.stdout", buf):
            _headless_output(state)

        output = json.loads(buf.getvalue())
        required = {"execution_id", "status", "phase", "pass_rate", "polls_done", "elapsed_s", "exit_code", "error"}
        assert required.issubset(output.keys())
        assert output["exit_code"] == 0
        assert output["phase"] == "done"
        assert output["pass_rate"] == 95.0
