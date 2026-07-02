"""
Output formatters for fi-simulate CLI.

Supports three modes:
  text   — human-readable summary (default)
  json   — stable machine-parseable JSON schema
  github — Markdown suitable for $GITHUB_STEP_SUMMARY
"""

import json
import sys
from typing import Any


def _pass_rate_bar(rate: float, width: int = 20) -> str:
    filled = round(rate * width)
    return "█" * filled + "░" * (width - filled)


def print_text(
    *,
    run_test_id: str,
    execution_id: str,
    status: str,
    pass_rate: float,
    threshold: float,
    eval_summary: list[dict[str, Any]],
    total_calls: int = 0,
    completed_calls: int = 0,
    failed_calls: int = 0,
) -> None:
    gate = "PASS ✓" if pass_rate >= threshold else "FAIL ✗"
    bar = _pass_rate_bar(pass_rate)

    lines = [
        "",
        "fi-simulate — Run Summary",
        "─" * 50,
        f"  Test ID      : {run_test_id}",
        f"  Execution ID : {execution_id}",
        f"  Status       : {status}",
        f"  Calls        : {completed_calls} completed / {total_calls} total"
        + (f" ({failed_calls} failed)" if failed_calls else ""),
        "",
        f"  Pass rate    : {pass_rate:.1%}  [{bar}]",
        f"  Threshold    : {threshold:.1%}",
        f"  Gate         : {gate}",
    ]

    if eval_summary:
        lines += ["", "  Eval breakdown:"]
        for item in eval_summary:
            name = item.get("name") or item.get("eval_name") or item.get("template_name") or "—"
            rate = None
            if "pass_rate" in item and item["pass_rate"] is not None:
                rate = float(item["pass_rate"])
            elif "score" in item and item["score"] is not None:
                rate = float(item["score"])
            elif "pass_count" in item and "total_count" in item and int(item["total_count"]) > 0:
                rate = int(item["pass_count"]) / int(item["total_count"])
            rate_str = f"{rate:.1%}" if rate is not None else "n/a"
            lines.append(f"    • {name}: {rate_str}")

    lines += ["─" * 50, ""]
    print("\n".join(lines))


def print_json(
    *,
    run_test_id: str,
    execution_id: str,
    status: str,
    pass_rate: float,
    threshold: float,
    passed: bool,
    eval_summary: list[dict[str, Any]],
    total_calls: int = 0,
    completed_calls: int = 0,
    failed_calls: int = 0,
) -> None:
    payload = {
        "run_test_id": run_test_id,
        "execution_id": execution_id,
        "status": status,
        "pass_rate": round(pass_rate, 6),
        "threshold": threshold,
        "passed": passed,
        "calls": {
            "total": total_calls,
            "completed": completed_calls,
            "failed": failed_calls,
        },
        "eval_summary": eval_summary,
    }
    print(json.dumps(payload, indent=2))


def write_github_summary(
    *,
    run_test_id: str,
    execution_id: str,
    status: str,
    pass_rate: float,
    threshold: float,
    passed: bool,
    eval_summary: list[dict[str, Any]],
    total_calls: int = 0,
    completed_calls: int = 0,
    failed_calls: int = 0,
    summary_file: str | None = None,
) -> None:
    """Write a Markdown summary to $GITHUB_STEP_SUMMARY (or stdout if not set)."""
    import os
    gate_icon = "✅" if passed else "❌"
    rows = [
        "## fi-simulate Results\n",
        f"| | |",
        f"|---|---|",
        f"| **Test ID** | `{run_test_id}` |",
        f"| **Execution ID** | `{execution_id}` |",
        f"| **Status** | {status} |",
        f"| **Pass rate** | {pass_rate:.1%} |",
        f"| **Threshold** | {threshold:.1%} |",
        f"| **Gate** | {gate_icon} {'PASS' if passed else 'FAIL'} |",
        f"| **Calls** | {completed_calls}/{total_calls} completed"
        + (f", {failed_calls} failed" if failed_calls else "") + " |",
    ]

    if eval_summary:
        rows += [
            "",
            "### Eval breakdown",
            "",
            "| Eval | Pass rate |",
            "|---|---|",
        ]
        for item in eval_summary:
            name = item.get("name") or item.get("eval_name") or item.get("template_name") or "—"
            rate = None
            if "pass_rate" in item and item["pass_rate"] is not None:
                rate = float(item["pass_rate"])
            elif "score" in item and item["score"] is not None:
                rate = float(item["score"])
            elif "pass_count" in item and "total_count" in item and int(item.get("total_count", 0)) > 0:
                rate = int(item["pass_count"]) / int(item["total_count"])
            rate_str = f"{rate:.1%}" if rate is not None else "n/a"
            rows.append(f"| {name} | {rate_str} |")

    md = "\n".join(rows) + "\n"

    target = summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(md)
    else:
        print(md)
