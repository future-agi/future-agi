"""Tests for ``sdk.cli.output`` — output formatters."""

from __future__ import annotations

import json

import pytest

from sdk.cli.output import (
    GithubFormatter,
    JsonFormatter,
    TextFormatter,
    get_formatter,
)


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_EVALS = [
    {"name": "Accuracy", "passed": 8, "total": 10},
    {"name": "Relevance", "passed": 9, "total": 10},
]


# ---------------------------------------------------------------------------
# get_formatter
# ---------------------------------------------------------------------------


class TestGetFormatter:
    """Tests for ``get_formatter`` factory function."""

    def test_text(self) -> None:
        assert isinstance(get_formatter("text"), TextFormatter)

    def test_json(self) -> None:
        assert isinstance(get_formatter("json"), JsonFormatter)

    def test_github(self) -> None:
        assert isinstance(get_formatter("github"), GithubFormatter)

    def test_case_insensitive(self) -> None:
        assert isinstance(get_formatter("JSON"), JsonFormatter)

    def test_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown output format"):
            get_formatter("xml")


# ---------------------------------------------------------------------------
# TextFormatter
# ---------------------------------------------------------------------------


class TestTextFormatter:
    """Tests for ``TextFormatter``."""

    def setup_method(self) -> None:
        self.fmt = TextFormatter()

    def test_execution_started_contains_ids(self) -> None:
        output = self.fmt.format_execution_started("exec-1", "test-1", 5)
        assert "exec-1" in output
        assert "test-1" in output
        assert "5" in output

    def test_polling_contains_status(self) -> None:
        output = self.fmt.format_polling("running", 0.6)
        assert "running" in output
        assert "60%" in output

    def test_summary_pass(self) -> None:
        output = self.fmt.format_summary(
            "exec-1", "completed", SAMPLE_EVALS, 0.85, 0.8, passed=True
        )
        assert "PASSED" in output
        assert "85.0%" in output
        assert "Accuracy" in output

    def test_summary_fail(self) -> None:
        output = self.fmt.format_summary(
            "exec-1", "completed", SAMPLE_EVALS, 0.7, 0.8, passed=False
        )
        assert "FAILED" in output

    def test_timeout(self) -> None:
        output = self.fmt.format_timeout("exec-1", 1800, "running")
        assert "TIMEOUT" in output
        assert "1800" in output

    def test_error(self) -> None:
        output = self.fmt.format_error("something broke")
        assert "something broke" in output


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    """Tests for ``JsonFormatter``."""

    def setup_method(self) -> None:
        self.fmt = JsonFormatter()

    def test_execution_started_is_valid_json(self) -> None:
        output = self.fmt.format_execution_started("exec-1", "test-1", 5)
        data = json.loads(output)
        assert data["event"] == "execution_started"
        assert data["execution_id"] == "exec-1"

    def test_polling_returns_empty(self) -> None:
        """JSON formatter suppresses polling output."""
        output = self.fmt.format_polling("running", 0.5)
        assert output == ""

    def test_summary_schema(self) -> None:
        output = self.fmt.format_summary(
            "exec-1", "completed", SAMPLE_EVALS, 0.85, 0.8, passed=True
        )
        data = json.loads(output)
        assert data["event"] == "summary"
        assert data["passed"] is True
        assert data["aggregate_pass_rate"] == pytest.approx(0.85)
        assert data["threshold"] == 0.8
        assert len(data["evals"]) == 2

    def test_timeout_schema(self) -> None:
        output = self.fmt.format_timeout("exec-1", 1800, "running")
        data = json.loads(output)
        assert data["event"] == "timeout"
        assert data["timeout_seconds"] == 1800

    def test_error_schema(self) -> None:
        output = self.fmt.format_error("auth failed")
        data = json.loads(output)
        assert data["event"] == "error"
        assert data["message"] == "auth failed"


# ---------------------------------------------------------------------------
# GithubFormatter
# ---------------------------------------------------------------------------


class TestGithubFormatter:
    """Tests for ``GithubFormatter``."""

    def setup_method(self) -> None:
        self.fmt = GithubFormatter()

    def test_execution_started_is_markdown(self) -> None:
        output = self.fmt.format_execution_started("exec-1", "test-1", 5)
        assert "### 🚀 Simulation Started" in output
        assert "`exec-1`" in output

    def test_polling_returns_empty(self) -> None:
        """GitHub formatter suppresses polling output."""
        assert self.fmt.format_polling("running", 0.5) == ""

    def test_summary_pass_markdown(self) -> None:
        output = self.fmt.format_summary(
            "exec-1", "completed", SAMPLE_EVALS, 0.85, 0.8, passed=True
        )
        assert "✅" in output
        assert "PASSED" in output
        assert "| Accuracy |" in output

    def test_summary_fail_markdown(self) -> None:
        output = self.fmt.format_summary(
            "exec-1", "completed", SAMPLE_EVALS, 0.7, 0.8, passed=False
        )
        assert "❌" in output
        assert "FAILED" in output

    def test_timeout_markdown(self) -> None:
        output = self.fmt.format_timeout("exec-1", 1800, "running")
        assert "Timed Out" in output
        assert "`exec-1`" in output

    def test_error_markdown(self) -> None:
        output = self.fmt.format_error("something broke")
        assert "### ❌ Error" in output
