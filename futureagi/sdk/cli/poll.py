"""
Polling state machine for fi-simulate CLI.

Mirrors the phases defined in docs/tla/SimulateCLI.tla:
  init → authenticating → starting → polling → summarizing → done/failed/timed_out

Invariants maintained at every step (correspond to TLA+ safety properties):
  NeverPollBeforeStart     — execution_id is set before any poll attempt
  SummaryOnlyAfterTerminal — summary only fetched when run_status is terminal
  TimeoutBounded           — elapsed_s never exceeds timeout_s
  TerminalIsStable         — once terminal, phase never transitions again
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import requests


class Phase(str, Enum):
    INIT = "init"
    AUTHENTICATING = "authenticating"
    STARTING = "starting"
    POLLING = "polling"
    SUMMARIZING = "summarizing"
    DONE = "done"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


TERMINAL_PHASES = {Phase.DONE, Phase.FAILED, Phase.TIMED_OUT}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class PollState:
    phase: Phase = Phase.INIT
    execution_id: Optional[str] = None
    run_status: str = "none"
    polls_done: int = 0
    elapsed_s: float = 0.0
    pass_rate: Optional[float] = None
    exit_code: int = -1
    error: Optional[str] = None
    summary: list = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES


class PollError(Exception):
    """Raised when the polling state machine hits an unrecoverable error."""


class SimulatePoller:
    """
    HTTP client that drives the fi-simulate polling state machine.

    Parameters
    ----------
    base_url:
        Backend API base URL, e.g. "https://app.futureagi.com".
    api_key:
        Future AGI API key (passed as Authorization header).
    poll_interval_s:
        Seconds between poll attempts. Must satisfy
        max_polls * poll_interval_s <= timeout_s (per TLA+ ASSUME).
    max_polls:
        Maximum poll attempts before timing out.
    timeout_s:
        Total wall-clock budget. Enforces TimeoutBounded invariant.
    threshold:
        Pass-rate percentage (0-100) below which exit_code is set to 1.
    progress_cb:
        Optional callback invoked after each poll with the current PollState.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        poll_interval_s: float = 5.0,
        max_polls: int = 60,
        timeout_s: float = 300.0,
        threshold: int = 80,
        progress_cb: Optional[Callable[[PollState], None]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.poll_interval_s = poll_interval_s
        self.max_polls = max_polls
        self.timeout_s = timeout_s
        self.threshold = threshold
        self.progress_cb = progress_cb
        self._session = requests.Session()
        # api_key is "<X-Api-Key>:<X-Secret-Key>" — the two tokens the backend
        # OrgApiKey model requires; split on first colon only.
        if ":" not in api_key:
            raise ValueError(
                "FI_API_KEY must be '<api_key>:<secret_key>' "
                "(copy both values from Settings → API Keys in the platform)"
            )
        _ak, _sk = api_key.split(":", 1)
        self._session.headers.update({"X-Api-Key": _ak, "X-Secret-Key": _sk})

    # ------------------------------------------------------------------
    # Individual actions (mirror TLA+ action names)
    # ------------------------------------------------------------------

    def _authenticate(self, state: PollState) -> None:
        """Validate that the API key is accepted by the backend."""
        assert state.phase == Phase.INIT
        state.phase = Phase.AUTHENTICATING
        try:
            resp = self._session.get(
                f"{self.base_url}/api/simulate/run-tests/",
                params={"limit": 1},
                timeout=10,
            )
            if resp.status_code == 401:
                state.phase = Phase.FAILED
                state.exit_code = 1
                state.error = "Authentication failed: invalid API key"
                return
            resp.raise_for_status()
        except requests.RequestException as exc:
            state.phase = Phase.FAILED
            state.exit_code = 1
            state.error = f"Authentication error: {exc}"
            return
        state.phase = Phase.STARTING

    def _start_execution(self, state: PollState, run_test_id: str) -> None:
        """POST to the execute endpoint; record the execution_id."""
        assert state.phase == Phase.STARTING
        assert state.execution_id is None, "NeverPollBeforeStart: id already set"
        try:
            resp = self._session.post(
                f"{self.base_url}/api/simulate/run-tests/{run_test_id}/execute/",
                json={},
                timeout=30,
            )
            if resp.status_code == 404:
                state.phase = Phase.FAILED
                state.exit_code = 1
                state.error = f"Run test not found: {run_test_id}"
                return
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            state.phase = Phase.FAILED
            state.exit_code = 1
            state.error = f"Failed to start execution: {exc}"
            return
        execution_id = data.get("execution_id")
        if not execution_id:
            state.phase = Phase.FAILED
            state.exit_code = 1
            state.error = "Backend returned no execution_id — cannot poll"
            return
        state.execution_id = execution_id
        state.run_status = "pending"
        state.phase = Phase.POLLING

    def _poll(self, state: PollState, run_test_id: str, wall_start: float) -> None:
        """Poll status once; advance phase if terminal."""
        state.elapsed_s = time.monotonic() - wall_start

        # TimeoutBounded invariant — check before sleeping
        if state.elapsed_s + self.poll_interval_s > self.timeout_s:
            state.phase = Phase.TIMED_OUT
            state.exit_code = 1
            return
        # Exhausted poll budget
        if state.polls_done >= self.max_polls:
            state.phase = Phase.TIMED_OUT
            state.exit_code = 1
            return

        time.sleep(self.poll_interval_s)
        state.elapsed_s = time.monotonic() - wall_start

        try:
            resp = self._session.get(
                f"{self.base_url}/api/simulate/run-tests/{run_test_id}/status/",
                params={"execution_id": state.execution_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            # Transient error — count the poll, stay in polling phase
            state.polls_done += 1
            state.error = f"Poll error (attempt {state.polls_done}): {exc}"
            return

        state.polls_done += 1
        state.elapsed_s = time.monotonic() - wall_start
        state.run_status = data.get("status", state.run_status)

        if self.progress_cb:
            self.progress_cb(state)

        if state.run_status in TERMINAL_RUN_STATUSES:
            state.phase = Phase.SUMMARIZING

    def _fetch_summary(self, state: PollState, run_test_id: str) -> None:
        """Fetch eval summary; set pass_rate and final exit_code."""
        # SummaryOnlyAfterTerminal invariant
        assert state.run_status in TERMINAL_RUN_STATUSES, (
            f"SummaryOnlyAfterTerminal violated: status={state.run_status}"
        )
        if state.run_status in {"failed", "cancelled"}:
            state.phase = Phase.FAILED
            state.exit_code = 1
            return

        try:
            resp = self._session.get(
                f"{self.base_url}/api/simulate/run-tests/{run_test_id}/eval-summary/",
                params={"execution_id": state.execution_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            state.phase = Phase.FAILED
            state.exit_code = 1
            state.error = f"Failed to fetch summary: {exc}"
            return

        state.summary = data if isinstance(data, list) else []
        if state.summary:
            scores = [
                item.get("pass_rate") if item.get("pass_rate") is not None
                else item.get("score", 0)
                for item in state.summary
                if isinstance(item, dict)
            ]
            state.pass_rate = sum(scores) / len(scores) if scores else 0.0
        else:
            state.pass_rate = 0.0

        state.exit_code = 0 if state.pass_rate >= self.threshold else 1
        state.phase = Phase.DONE

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, run_test_id: str) -> PollState:
        """Drive the state machine to a terminal phase. Returns the final state."""
        state = PollState()
        wall_start = time.monotonic()

        # Phase: init → authenticating → starting
        self._authenticate(state)
        if state.is_terminal:
            return state

        # Phase: starting → polling
        self._start_execution(state, run_test_id)
        if state.is_terminal:
            return state

        # Phase: polling (loop)
        while state.phase == Phase.POLLING:
            self._poll(state, run_test_id, wall_start)

        # Phase: summarizing → done/failed
        if state.phase == Phase.SUMMARIZING:
            self._fetch_summary(state, run_test_id)

        state.elapsed_s = time.monotonic() - wall_start

        # TerminalIsStable: assert we are now in a terminal phase
        assert state.is_terminal, f"Expected terminal phase, got {state.phase}"
        return state
