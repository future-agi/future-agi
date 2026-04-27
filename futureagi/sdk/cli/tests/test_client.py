"""Tests for ``sdk.cli.client`` — HTTP client for FutureAGI Simulate API.

Uses ``httpx.MockTransport`` to mock all HTTP interactions.
"""

from __future__ import annotations

import json

import httpx
import pytest

from sdk.cli.client import (
    APIError,
    AuthConfig,
    AuthError,
    CLIError,
    SimulateClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AUTH = AuthConfig(api_key="test-key", secret_key="test-secret")
BASE_URL = "https://test.futureagi.com"


def _make_client(handler: httpx.MockTransport) -> SimulateClient:
    """Create a ``SimulateClient`` with a mocked transport."""
    client = SimulateClient(base_url=BASE_URL, auth=AUTH)
    # Replace the internal httpx.Client with one using MockTransport.
    client._client = httpx.Client(
        transport=handler,
        headers={
            "X-Api-Key": AUTH.api_key,
            "X-Secret-Key": AUTH.secret_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return client


# ---------------------------------------------------------------------------
# start_execution
# ---------------------------------------------------------------------------


class TestStartExecution:
    """Tests for ``SimulateClient.start_execution``."""

    def test_happy_path(self) -> None:
        """Verify a successful execution start returns correct metadata."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/execute/" in str(request.url)
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "execution_id": "exec-123",
                    "run_test_id": "test-456",
                    "status": "started",
                    "total_scenarios": 3,
                    "total_calls": 9,
                },
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.start_execution(
            test_id="test-456",
            scenario_ids=["s1", "s2", "s3"],
            simulator_id="sim-1",
        )

        assert result.execution_id == "exec-123"
        assert result.run_test_id == "test-456"
        assert result.status == "started"
        assert result.total_scenarios == 3
        assert result.total_calls == 9
        client.close()

    def test_auth_failure_raises_auth_error(self) -> None:
        """Verify 401 response raises ``AuthError``."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Invalid credentials"})

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(AuthError, match="Authentication failed"):
            client.start_execution(test_id="test-456")
        client.close()

    def test_403_raises_auth_error(self) -> None:
        """Verify 403 response raises ``AuthError``."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "Forbidden"})

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(AuthError, match="Authentication failed"):
            client.start_execution(test_id="test-456")
        client.close()

    def test_server_error_retries_then_fails(self) -> None:
        """Verify 500 errors are retried once, then raise ``APIError``."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, text="Internal Server Error")

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(APIError, match="API error"):
            client.start_execution(test_id="test-456")
        # Should have been called twice (original + 1 retry).
        assert call_count == 2
        client.close()

    def test_network_error_raises_cli_error(self) -> None:
        """Verify network errors raise ``CLIError``."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(CLIError, match="Network error"):
            client.start_execution(test_id="test-456")
        client.close()


# ---------------------------------------------------------------------------
# poll_status
# ---------------------------------------------------------------------------


class TestPollStatus:
    """Tests for ``SimulateClient.poll_status``."""

    def test_returns_running_status(self) -> None:
        """Verify a running status is returned correctly."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/status/" in str(request.url)
            return httpx.Response(
                200,
                json={"status": "running", "progress": 0.6},
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.poll_status("test-456")

        assert result.status == "running"
        assert result.progress == 0.6
        assert not result.is_terminal
        client.close()

    def test_completed_is_terminal(self) -> None:
        """Verify 'completed' is recognized as a terminal status."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status": "completed", "progress": 1.0},
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.poll_status("test-456")

        assert result.is_terminal
        client.close()

    def test_failed_is_terminal(self) -> None:
        """Verify 'failed' is recognized as a terminal status."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status": "failed", "progress": 0.3},
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.poll_status("test-456")

        assert result.is_terminal
        client.close()


# ---------------------------------------------------------------------------
# get_eval_summary
# ---------------------------------------------------------------------------


class TestGetEvalSummary:
    """Tests for ``SimulateClient.get_eval_summary``."""

    def test_computes_aggregate_pass_rate(self) -> None:
        """Verify aggregate pass rate is computed from individual evals."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "execution_id=exec-1" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "evals": [
                        {"name": "Accuracy", "passed": 8, "total": 10},
                        {"name": "Relevance", "passed": 9, "total": 10},
                    ],
                },
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.get_eval_summary("test-456", "exec-1")

        assert result.execution_id == "exec-1"
        assert len(result.evals) == 2
        # 17 passed out of 20 total = 0.85
        assert result.aggregate_pass_rate == pytest.approx(0.85)
        client.close()

    def test_empty_evals_returns_zero(self) -> None:
        """Verify empty evals list returns 0.0 pass rate."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"evals": []})

        client = _make_client(httpx.MockTransport(handler))
        result = client.get_eval_summary("test-456", "exec-1")

        assert result.aggregate_pass_rate == 0.0
        client.close()

    def test_handles_pass_rate_field(self) -> None:
        """Verify pass_rate field is used when passed/total are absent."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "evals": [
                        {"name": "Eval1", "pass_rate": 0.9, "total": 0},
                    ],
                },
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.get_eval_summary("test-456", "exec-1")

        assert result.aggregate_pass_rate == pytest.approx(0.9)
        client.close()


# ---------------------------------------------------------------------------
# wait_for_completion
# ---------------------------------------------------------------------------


class TestWaitForCompletion:
    """Tests for ``SimulateClient.wait_for_completion``."""

    def test_returns_on_completion(self) -> None:
        """Verify polling stops and returns when status is 'completed'."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(
                    200, json={"status": "running", "progress": 0.5}
                )
            return httpx.Response(
                200, json={"status": "completed", "progress": 1.0}
            )

        client = _make_client(httpx.MockTransport(handler))
        result = client.wait_for_completion(
            "test-456", poll_interval=0, timeout=10
        )

        assert result.status == "completed"
        assert call_count == 3
        client.close()

    def test_timeout_raises_cli_error(self) -> None:
        """Verify timeout raises ``CLIError``."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"status": "running", "progress": 0.1}
            )

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(CLIError, match="Timed out"):
            client.wait_for_completion(
                "test-456", poll_interval=0, timeout=0
            )
        client.close()
