"""Thin HTTP client for the FutureAGI Simulate API.

Handles starting executions, polling status, and fetching eval summaries.
Uses ``httpx`` with sane timeouts and a single retry on transient 5xx errors.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Exit codes (shared with main.py)
# ---------------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_REGRESSION = 1
EXIT_TIMEOUT_OR_FAILURE = 2
EXIT_USAGE_ERROR = 3

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_BASE_URL = "https://app.futureagi.com"
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 60.0
MAX_RETRIES = 1
BACKOFF_MULTIPLIER = 1.5
MAX_POLL_INTERVAL_SECONDS = 30

# The backend wraps API responses in {"status": 0, "result": <payload>}.
# This is the key under which the actual data lives.
API_RESULT_ENVELOPE_KEY = "result"

# Terminal statuses returned by the /status/ endpoint.
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "error", "cancelling"}
)


class CLIError(Exception):
    """Base exception for CLI errors."""


class AuthError(CLIError):
    """Raised when authentication fails (401 / 403)."""


class APIError(CLIError):
    """Raised when the API returns an unexpected error."""


class PollTimeoutError(CLIError):
    """Raised when polling times out (distinct from network/auth errors)."""


@dataclass(frozen=True)
class AuthConfig:
    """Holds API authentication credentials."""

    api_key: str
    secret_key: str


@dataclass(frozen=True)
class ExecutionResult:
    """Result of starting a test execution."""

    execution_id: str
    run_test_id: str
    status: str
    total_scenarios: int = 0
    total_calls: int = 0


@dataclass(frozen=True)
class StatusResult:
    """Result of polling execution status."""

    status: str
    progress: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` if the execution has reached a terminal state."""
        return self.status.lower() in TERMINAL_STATUSES


@dataclass(frozen=True)
class EvalSummary:
    """Aggregated evaluation summary."""

    execution_id: str
    evals: list[dict[str, Any]] = field(default_factory=list)
    aggregate_pass_rate: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class SimulateClient:
    """HTTP client for the FutureAGI Simulate REST API.

    Args:
        base_url: API base URL (e.g. ``https://app.futureagi.com``).
        auth: Authentication credentials.
    """

    def __init__(self, base_url: str, auth: AuthConfig) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=DEFAULT_CONNECT_TIMEOUT,
                read=DEFAULT_READ_TIMEOUT,
                write=DEFAULT_READ_TIMEOUT,
                pool=DEFAULT_READ_TIMEOUT,
            ),
            headers={
                "X-Api-Key": auth.api_key,
                "X-Secret-Key": auth.secret_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_execution(
        self,
        test_id: str,
        scenario_ids: list[str] | None = None,
        simulator_id: str | None = None,
    ) -> ExecutionResult:
        """Start a test execution via ``POST /simulate/run-tests/{id}/execute/``.

        Args:
            test_id: UUID of the RunTest.
            scenario_ids: Optional list of scenario UUIDs to run.
            simulator_id: Optional simulator UUID.

        Returns:
            An ``ExecutionResult`` with the new execution's metadata.

        Raises:
            AuthError: If the API rejects authentication.
            APIError: On non-2xx responses.
        """
        url = f"{self._base_url}/simulate/run-tests/{test_id}/execute/"
        body: dict[str, Any] = {}
        if scenario_ids:
            body["scenario_ids"] = scenario_ids
        if simulator_id:
            body["simulator_id"] = simulator_id

        raw = self._request("POST", url, json_body=body)
        data = self._unwrap_envelope(raw)

        return ExecutionResult(
            execution_id=str(data.get("execution_id", "")),
            run_test_id=str(data.get("run_test_id", "")),
            status=str(data.get("status", "unknown")),
            total_scenarios=int(data.get("total_scenarios", 0)),
            total_calls=int(data.get("total_calls", 0)),
        )

    def poll_status(
        self, test_id: str, execution_id: str | None = None
    ) -> StatusResult:
        """Poll execution status via ``GET /simulate/run-tests/{id}/status/``.

        Args:
            test_id: UUID of the RunTest.
            execution_id: Optional UUID of a specific execution to query.

        Returns:
            A ``StatusResult`` with current execution progress.
        """
        url = f"{self._base_url}/simulate/run-tests/{test_id}/status/"
        params: dict[str, str] | None = None
        if execution_id:
            params = {"execution_id": execution_id}
        raw = self._request("GET", url, params=params)
        data = self._unwrap_envelope(raw)

        return StatusResult(
            status=str(data.get("status", "unknown")),
            progress=float(data.get("progress", 0.0)),
            raw=data,
        )

    def get_eval_summary(
        self, test_id: str, execution_id: str
    ) -> EvalSummary:
        """Fetch evaluation summary via ``GET /simulate/run-tests/{id}/eval-summary/``.

        Args:
            test_id: UUID of the RunTest.
            execution_id: UUID of the specific execution.

        Returns:
            An ``EvalSummary`` with per-eval results and aggregate pass rate.
        """
        url = f"{self._base_url}/simulate/run-tests/{test_id}/eval-summary/"
        params = {"execution_id": execution_id}
        raw = self._request("GET", url, params=params)
        data = self._unwrap_envelope(raw)

        evals = data.get("evals", data.get("results", []))
        aggregate_pass_rate = self._compute_aggregate_pass_rate(evals)

        return EvalSummary(
            execution_id=execution_id,
            evals=evals,
            aggregate_pass_rate=aggregate_pass_rate,
            raw=data,
        )

    def wait_for_completion(
        self,
        test_id: str,
        execution_id: str | None = None,
        poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> StatusResult:
        """Poll until the execution reaches a terminal state or times out.

        Uses exponential backoff: each poll increases the interval by
        ``BACKOFF_MULTIPLIER`` up to ``MAX_POLL_INTERVAL_SECONDS``.

        Args:
            test_id: UUID of the RunTest.
            execution_id: Optional UUID of the specific execution.
            poll_interval: Initial seconds between polls.
            timeout: Maximum seconds to wait.

        Returns:
            The final ``StatusResult``.

        Raises:
            PollTimeoutError: If the timeout is exceeded.
            AuthError: If authentication fails during polling.
            CLIError: On network errors during polling.
        """
        deadline = time.monotonic() + timeout
        current_interval = float(poll_interval)

        while True:
            result = self.poll_status(test_id, execution_id=execution_id)

            if result.is_terminal:
                return result

            if time.monotonic() >= deadline:
                raise PollTimeoutError(
                    f"Timed out after {timeout}s waiting for execution "
                    f"to complete. Last status: {result.status}"
                )

            time.sleep(current_interval)
            current_interval = min(
                current_interval * BACKOFF_MULTIPLIER,
                MAX_POLL_INTERVAL_SECONDS,
            )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with a single retry on transient 5xx errors.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full request URL.
            json_body: Optional JSON body for POST requests.
            params: Optional query parameters.

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            AuthError: On 401/403 responses.
            APIError: On other non-2xx responses.
            CLIError: On network errors.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                )

                if response.status_code in (401, 403):
                    raise AuthError(
                        f"Authentication failed ({response.status_code}). "
                        f"Check your API key and secret key."
                    )

                if response.status_code >= 500 and attempt < MAX_RETRIES:
                    last_error = APIError(
                        f"Server error ({response.status_code}): "
                        f"{response.text[:200]}"
                    )
                    time.sleep(1)
                    continue

                if response.status_code >= 400:
                    raise APIError(
                        f"API error ({response.status_code}): "
                        f"{response.text[:200]}"
                    )

                return response.json()

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt < MAX_RETRIES:
                    last_error = exc
                    time.sleep(1)
                    continue
                raise CLIError(
                    f"Network error: {exc}. "
                    f"Check your --base-url and network connection."
                ) from exc

        # Should not reach here, but handle defensively.
        if last_error is not None:
            raise CLIError(f"Request failed after retries: {last_error}")
        raise CLIError("Request failed unexpectedly")  # pragma: no cover

    @staticmethod
    def _unwrap_envelope(data: dict[str, Any]) -> dict[str, Any]:
        """Unwrap the FutureAGI API response envelope.

        The backend wraps all responses as::

            {"status": 0, "result": <actual_payload>}

        This method extracts the inner payload. If the response does
        not have the envelope structure, it returns the data as-is
        (graceful fallback for direct responses like execute/).

        Args:
            data: Raw parsed JSON response.

        Returns:
            The unwrapped payload dictionary.
        """
        if API_RESULT_ENVELOPE_KEY in data and isinstance(
            data[API_RESULT_ENVELOPE_KEY], dict
        ):
            return data[API_RESULT_ENVELOPE_KEY]
        return data

    @staticmethod
    def _compute_aggregate_pass_rate(
        evals: list[dict[str, Any]],
    ) -> float:
        """Compute the aggregate pass rate across all evaluations.

        Args:
            evals: List of per-eval result dictionaries.

        Returns:
            Aggregate pass rate as a float between 0.0 and 1.0.
        """
        if not evals:
            return 0.0

        total_passed = 0
        total_count = 0

        for eval_item in evals:
            passed = eval_item.get("passed", eval_item.get("pass_count", 0))
            total = eval_item.get("total", eval_item.get("total_count", 0))

            # Also handle pass_rate directly if provided.
            if total == 0 and "pass_rate" in eval_item:
                pass_rate = float(eval_item["pass_rate"])
                total_passed += int(pass_rate * 100)
                total_count += 100
            else:
                total_passed += int(passed)
                total_count += int(total)

        if total_count == 0:
            return 0.0

        return total_passed / total_count
