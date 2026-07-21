"""
HTTP client for the fi-simulate CLI.

Handles start / poll / eval-summary calls against the FutureAGI API.
"""

import time
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any


# Terminal states that stop polling
TERMINAL_STATES = {"completed", "failed", "cancelled", "error"}


class SimulateClientError(Exception):
    """Raised on auth / network / usage errors (exit code 3)."""


class SimulateTimeoutError(Exception):
    """Raised when polling exceeds --timeout (exit code 2)."""


class FiSimulateClient:
    """Thin HTTP client wrapping the three Simulate REST endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        secret_key: str | None = None,
        request_timeout: int = 30,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._secret_key = secret_key
        self._req_timeout = request_timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if self._secret_key:
            return {
                "X-Api-Key": self._api_key,
                "X-Secret-Key": self._secret_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)

        try:
            with urllib.request.urlopen(req, timeout=self._req_timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            if exc.code in (401, 403):
                raise SimulateClientError(
                    f"Authentication failed ({exc.code}): {body_text}"
                ) from exc
            if exc.code == 404:
                raise SimulateClientError(
                    f"Resource not found (404): check --test-id. {body_text}"
                ) from exc
            raise SimulateClientError(
                f"HTTP {exc.code}: {body_text}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SimulateClientError(
                f"Network error reaching {self._base}: {exc.reason}"
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_execution(
        self,
        run_test_id: str,
        scenario_ids: list[str] | None = None,
        simulator_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /simulate/run-tests/{run_test_id}/execute/"""
        payload: dict[str, Any] = {}
        if scenario_ids:
            payload["scenario_ids"] = scenario_ids
        if simulator_id:
            payload["simulator_id"] = simulator_id

        return self._request(
            "POST",
            f"/simulate/run-tests/{run_test_id}/execute/",
            body=payload,
        )

    def get_status(self, run_test_id: str) -> dict[str, Any]:
        """GET /simulate/run-tests/{run_test_id}/status/"""
        return self._request("GET", f"/simulate/run-tests/{run_test_id}/status/")

    def get_eval_summary(
        self, run_test_id: str, execution_id: str
    ) -> list[dict[str, Any]]:
        """GET /simulate/run-tests/{run_test_id}/eval-summary/?execution_id={id}"""
        result = self._request(
            "GET",
            f"/simulate/run-tests/{run_test_id}/eval-summary/",
            params={"execution_id": execution_id},
        )
        # Response may be wrapped in {"result": [...]} or be a bare list
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            inner = result.get("result", result.get("data", result))
            if isinstance(inner, list):
                return inner
        return []

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    def poll_until_terminal(
        self,
        run_test_id: str,
        timeout: int = 1800,
        poll_interval: int = 5,
    ) -> dict[str, Any]:
        """
        Poll GET /status/ until the execution reaches a terminal state or
        the timeout (in seconds) is exceeded.

        Returns the final status dict.
        Raises SimulateTimeoutError on timeout.
        """
        deadline = time.monotonic() + timeout
        retries = 0
        max_retries = 3

        while True:
            try:
                status = self.get_status(run_test_id)
                retries = 0  # reset on success
            except SimulateClientError as exc:
                # Retry transient errors up to max_retries
                retries += 1
                if retries > max_retries:
                    raise
                wait = min(poll_interval * retries, 30)
                time.sleep(wait)
                continue

            raw_status = str(status.get("status", "")).lower()

            if raw_status in TERMINAL_STATES:
                return status

            if time.monotonic() >= deadline:
                raise SimulateTimeoutError(
                    f"Timed out after {timeout}s. "
                    f"execution_id={status.get('execution_id', 'unknown')}"
                )

            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Aggregate pass rate
    # ------------------------------------------------------------------

    @staticmethod
    def compute_pass_rate(eval_summary: list[dict[str, Any]]) -> float:
        """
        Compute aggregate pass rate from the eval-summary response.

        Each item may contain 'pass_rate', 'score', 'pass_count'/'total_count',
        or 'passed'/'total'. Falls back to 0.0 if no numeric data is found.
        """
        if not eval_summary:
            return 0.0

        scores: list[float] = []
        for item in eval_summary:
            if "pass_rate" in item and item["pass_rate"] is not None:
                scores.append(float(item["pass_rate"]))
            elif "score" in item and item["score"] is not None:
                scores.append(float(item["score"]))
            elif "pass_count" in item and "total_count" in item:
                total = int(item["total_count"])
                if total > 0:
                    scores.append(int(item["pass_count"]) / total)
            elif "passed" in item and "total" in item:
                total = int(item["total"])
                if total > 0:
                    scores.append(int(item["passed"]) / total)

        return sum(scores) / len(scores) if scores else 0.0
