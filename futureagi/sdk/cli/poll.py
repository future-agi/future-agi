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
                f"{self.base_url}/simulate/run-tests/",
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
                f"{self.base_url}/simulate/run-tests/{run_test_id}/execute/",
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
            state.run_status = "timed_out"
            state.exit_code = 1
            return
        # Exhausted poll budget
        if state.polls_done >= self.max_polls:
            state.phase = Phase.TIMED_OUT
            state.run_status = "timed_out"
            state.exit_code = 1
            return

        time.sleep(self.poll_interval_s)
        state.elapsed_s = time.monotonic() - wall_start

        try:
            resp = self._session.get(
                f"{self.base_url}/simulate/run-tests/{run_test_id}/status/",
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
                f"{self.base_url}/simulate/run-tests/{run_test_id}/eval-summary/",
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
                else (item.get("score") or 0)  # or-0 handles explicit None values
                for item in state.summary
                if isinstance(item, dict)
            ]
            state.pass_rate = sum(scores) / len(scores) if scores else 0.0
        else:
            state.pass_rate = 0.0

        state.exit_code = 0 if (state.pass_rate or 0) >= self.threshold else 1
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

    def create_suite(
        self,
        name: str,
        agent_url: Optional[str] = None,
        description: str = "",
    ) -> dict:
        """
        Create a minimal runnable suite (idempotent by name).
        POST /simulate/run-tests/create-cli/
        Returns {"id": "<uuid>", "name": "<name>", "created": bool}.
        """
        resp = self._session.post(
            f"{self.base_url}/simulate/run-tests/create-cli/",
            json={"name": name, "agent_url": agent_url, "description": description},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_suites(self, search: str = "", limit: int = 20) -> list[dict]:
        """
        Return simulation suites visible to this API key.
        GET /simulate/run-tests/?search=<search>&limit=<limit>
        """
        resp = self._session.get(
            f"{self.base_url}/simulate/run-tests/",
            params={"search": search, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("results", data.get("run_tests", []))

    def fetch_status(self, run_test_id: str, execution_id: str) -> dict:
        """
        Fetch the current status of a specific execution (read-only).
        GET /simulate/run-tests/<id>/status/?execution_id=<eid>
        """
        resp = self._session.get(
            f"{self.base_url}/simulate/run-tests/{run_test_id}/status/",
            params={"execution_id": execution_id},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Pure helper functions (testable without HTTP)
# ---------------------------------------------------------------------------

def parse_run_arg(arg: str) -> tuple[bool, str]:
    """
    Detect whether the CLI argument is a UUID or a name query.
    Returns (is_uuid, original_value).
    """
    import re
    UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    return bool(UUID_RE.match(arg.strip())), arg


def resolve_name(suites: list[dict], query: str) -> str:
    """
    Resolve a name query to a single run_test_id UUID.

    NameResolutionBeforeStart invariant: raises ValueError (not returns None)
    so the caller can never silently proceed with an unresolved UUID.

    Rules (from SimulateCLIHuman.tla):
      0 matches → ValueError("no suites match …")
      1 match   → return UUID
      >1 matches → ValueError("ambiguous: N suites match …")
    """
    q = query.lower().strip()
    matches = [s for s in suites if q in s.get("name", "").lower()]

    if len(matches) == 0:
        raise ValueError(
            f"no suites match {query!r} — run `fi-simulate list` to browse available suites"
        )
    if len(matches) > 1:
        names = ", ".join(repr(s["name"]) for s in matches)
        raise ValueError(
            f"ambiguous: {len(matches)} suites match {query!r}: {names}\n"
            "Use a more specific query or the UUID directly."
        )
    suite_id = matches[0].get("id")
    if not suite_id:
        raise ValueError(
            f"suite {matches[0].get('name', '?')!r} has no 'id' field in API response"
        )
    return suite_id


def format_suite_row(suite: dict, index: int) -> str:
    """Format a suite dict as a human-readable numbered list row."""
    name = suite.get("name", "—")
    n_scenarios = suite.get("scenario_count", "?")
    last_run = suite.get("last_run_at") or "never"
    last_pass = suite.get("last_pass_rate")
    pass_str = f"{last_pass:.0f}%" if last_pass is not None else "—"
    return f"  {index}. {name:<35} ({n_scenarios} scenarios, last: {last_run}, pass: {pass_str})"


def format_failures(metrics: list[dict], threshold: float) -> list[dict]:
    """
    Return only the metrics whose pass_rate is below the threshold.
    Used for the post-run failure drill-down.
    """
    return [
        m for m in metrics
        if isinstance(m, dict) and m.get("pass_rate") is not None
        and m["pass_rate"] < threshold
    ]
