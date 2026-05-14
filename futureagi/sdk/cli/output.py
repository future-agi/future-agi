"""Output formatters for fi-simulate CLI.

Supports three output formats:
- ``text``: Human-readable table summary (default).
- ``json``: Stable JSON schema for machine parsing.
- ``github``: Markdown suitable for ``$GITHUB_STEP_SUMMARY``.
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from typing import Any


class BaseFormatter(ABC):
    """Abstract base class for output formatters."""

    @abstractmethod
    def format_execution_started(
        self,
        execution_id: str,
        run_test_id: str,
        total_scenarios: int,
    ) -> str:
        """Format the execution-started message."""

    @abstractmethod
    def format_polling(self, status: str, progress: float) -> str:
        """Format a polling status update."""

    @abstractmethod
    def format_summary(
        self,
        execution_id: str,
        status: str,
        evals: list[dict[str, Any]],
        aggregate_pass_rate: float,
        threshold: float,
        passed: bool,
    ) -> str:
        """Format the final evaluation summary."""

    @abstractmethod
    def format_timeout(
        self, execution_id: str, timeout: int, last_status: str
    ) -> str:
        """Format a timeout message."""

    @abstractmethod
    def format_error(self, message: str) -> str:
        """Format an error message."""


class TextFormatter(BaseFormatter):
    """Human-readable text output formatter."""

    def format_execution_started(
        self,
        execution_id: str,
        run_test_id: str,
        total_scenarios: int,
    ) -> str:
        """Format execution-started as a readable banner."""
        lines = [
            "┌─────────────────────────────────────────────┐",
            "│         fi-simulate • Execution Started      │",
            "├─────────────────────────────────────────────┤",
            f"│  Run Test ID:   {run_test_id[:36]:<28}│",
            f"│  Execution ID:  {execution_id[:36]:<28}│",
            f"│  Scenarios:     {total_scenarios:<28}│",
            "└─────────────────────────────────────────────┘",
        ]
        return "\n".join(lines)

    def format_polling(self, status: str, progress: float) -> str:
        """Format polling as a progress indicator."""
        bar_width = 20
        filled = int(bar_width * progress)
        bar = "█" * filled + "░" * (bar_width - filled)
        return f"  ⏳ [{bar}] {progress:.0%} — {status}"

    def format_summary(
        self,
        execution_id: str,
        status: str,
        evals: list[dict[str, Any]],
        aggregate_pass_rate: float,
        threshold: float,
        passed: bool,
    ) -> str:
        """Format the final summary as a table."""
        icon = "✅" if passed else "❌"
        verdict = "PASSED" if passed else "FAILED"

        lines = [
            "",
            f"  {icon} Result: {verdict}",
            f"  Aggregate pass rate: {aggregate_pass_rate:.1%}"
            f" (threshold: {threshold:.1%})",
            f"  Status: {status}",
            f"  Execution ID: {execution_id}",
            "",
        ]

        if evals:
            lines.append("  Per-eval breakdown:")
            lines.append(
                f"  {'Eval':<30} {'Passed':>8} {'Total':>8} {'Rate':>8}"
            )
            lines.append(f"  {'─' * 56}")

            for eval_item in evals:
                name = str(
                    eval_item.get("name", eval_item.get("eval_name", "—"))
                )[:30]
                passed_count = eval_item.get(
                    "passed", eval_item.get("pass_count", 0)
                )
                total = eval_item.get(
                    "total", eval_item.get("total_count", 0)
                )
                rate = (
                    f"{passed_count / total:.1%}" if total > 0 else "N/A"
                )
                lines.append(
                    f"  {name:<30} {passed_count:>8} {total:>8} {rate:>8}"
                )

        return "\n".join(lines)

    def format_timeout(
        self, execution_id: str, timeout: int, last_status: str
    ) -> str:
        """Format timeout as a warning message."""
        return (
            f"\n  ⏰ TIMEOUT after {timeout}s\n"
            f"  Last status: {last_status}\n"
            f"  Execution ID: {execution_id}\n"
            f"  The execution may still be running on the server."
        )

    def format_error(self, message: str) -> str:
        """Format error as a highlighted message."""
        return f"\n  ❌ Error: {message}"


class JsonFormatter(BaseFormatter):
    """JSON output formatter for machine parsing."""

    def format_execution_started(
        self,
        execution_id: str,
        run_test_id: str,
        total_scenarios: int,
    ) -> str:
        """Format execution-started as JSON."""
        return json.dumps(
            {
                "event": "execution_started",
                "execution_id": execution_id,
                "run_test_id": run_test_id,
                "total_scenarios": total_scenarios,
            },
            indent=2,
        )

    def format_polling(self, status: str, progress: float) -> str:
        """Format polling as JSON (suppressed — only final output matters)."""
        return ""

    def format_summary(
        self,
        execution_id: str,
        status: str,
        evals: list[dict[str, Any]],
        aggregate_pass_rate: float,
        threshold: float,
        passed: bool,
    ) -> str:
        """Format summary as a stable JSON schema."""
        return json.dumps(
            {
                "event": "summary",
                "execution_id": execution_id,
                "status": status,
                "passed": passed,
                "aggregate_pass_rate": round(aggregate_pass_rate, 4),
                "threshold": threshold,
                "evals": evals,
            },
            indent=2,
        )

    def format_timeout(
        self, execution_id: str, timeout: int, last_status: str
    ) -> str:
        """Format timeout as JSON."""
        return json.dumps(
            {
                "event": "timeout",
                "execution_id": execution_id,
                "timeout_seconds": timeout,
                "last_status": last_status,
            },
            indent=2,
        )

    def format_error(self, message: str) -> str:
        """Format error as JSON."""
        return json.dumps(
            {"event": "error", "message": message},
            indent=2,
        )


class GithubFormatter(BaseFormatter):
    """GitHub Actions step summary markdown formatter."""

    def format_execution_started(
        self,
        execution_id: str,
        run_test_id: str,
        total_scenarios: int,
    ) -> str:
        """Format execution-started as GitHub markdown."""
        return (
            f"### 🚀 Simulation Started\n\n"
            f"| Field | Value |\n"
            f"|---|---|\n"
            f"| Run Test ID | `{run_test_id}` |\n"
            f"| Execution ID | `{execution_id}` |\n"
            f"| Scenarios | {total_scenarios} |\n"
        )

    def format_polling(self, status: str, progress: float) -> str:
        """Format polling (suppressed for GitHub output)."""
        return ""

    def format_summary(
        self,
        execution_id: str,
        status: str,
        evals: list[dict[str, Any]],
        aggregate_pass_rate: float,
        threshold: float,
        passed: bool,
    ) -> str:
        """Format summary as GitHub-flavored markdown."""
        icon = "✅" if passed else "❌"
        verdict = "PASSED" if passed else "FAILED"

        lines = [
            f"### {icon} Simulation {verdict}\n",
            f"**Aggregate pass rate:** {aggregate_pass_rate:.1%}"
            f" (threshold: {threshold:.1%})\n",
            f"**Status:** {status}  ",
            f"**Execution ID:** `{execution_id}`\n",
        ]

        if evals:
            lines.append("#### Per-eval breakdown\n")
            lines.append("| Eval | Passed | Total | Rate |")
            lines.append("|---|---|---|---|")

            for eval_item in evals:
                name = str(
                    eval_item.get("name", eval_item.get("eval_name", "—"))
                )
                passed_count = eval_item.get(
                    "passed", eval_item.get("pass_count", 0)
                )
                total = eval_item.get(
                    "total", eval_item.get("total_count", 0)
                )
                rate = (
                    f"{passed_count / total:.1%}" if total > 0 else "N/A"
                )
                lines.append(
                    f"| {name} | {passed_count} | {total} | {rate} |"
                )

        return "\n".join(lines)

    def format_timeout(
        self, execution_id: str, timeout: int, last_status: str
    ) -> str:
        """Format timeout as GitHub markdown warning."""
        return (
            f"### ⏰ Simulation Timed Out\n\n"
            f"> **Warning:** Execution did not complete within"
            f" {timeout}s.\n\n"
            f"| Field | Value |\n"
            f"|---|---|\n"
            f"| Execution ID | `{execution_id}` |\n"
            f"| Timeout | {timeout}s |\n"
            f"| Last Status | {last_status} |\n"
        )

    def format_error(self, message: str) -> str:
        """Format error as GitHub markdown."""
        return f"### ❌ Error\n\n> {message}\n"


def get_formatter(output_format: str) -> BaseFormatter:
    """Return the appropriate formatter for the given output format.

    Args:
        output_format: One of ``text``, ``json``, or ``github``.

    Returns:
        A ``BaseFormatter`` instance.

    Raises:
        ValueError: If the output format is not recognized.
    """
    formatters: dict[str, type[BaseFormatter]] = {
        "text": TextFormatter,
        "json": JsonFormatter,
        "github": GithubFormatter,
    }

    formatter_cls = formatters.get(output_format.lower())
    if formatter_cls is None:
        raise ValueError(
            f"Unknown output format: {output_format!r}. "
            f"Choose from: {', '.join(formatters)}"
        )

    return formatter_cls()


def emit(message: str, file: Any = None) -> None:
    """Print a message to the given file (default: stdout).

    Args:
        message: The message to print.
        file: Output file object (defaults to ``sys.stdout``).
    """
    if not message:
        return
    print(message, file=file or sys.stdout, flush=True)
