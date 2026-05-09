"""
fi-simulate CLI entry point.

Usage:
  python -m sdk.cli.main --run-test-id <UUID> [options]

  Or via the installed console script:
  fi-simulate --run-test-id <UUID> [options]

Exit codes:
  0 — execution completed with pass_rate >= threshold
  1 — execution failed, was cancelled, timed out, or pass_rate < threshold
  2 — invalid arguments

Modes:
  Headed  (default when stdout is a TTY): rich spinner + summary table
  Headless (--json or non-TTY):           single JSON object written to stdout
"""

import argparse
import json
import os
import sys
from typing import Optional

from sdk.cli.poll import Phase, PollState, SimulatePoller

DEFAULT_BASE_URL = "https://app.futureagi.com"
DEFAULT_THRESHOLD = 80
DEFAULT_TIMEOUT_S = 300
DEFAULT_POLL_INTERVAL_S = 5
DEFAULT_MAX_POLLS = 60


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fi-simulate",
        description="Run a Future AGI simulation suite and report results.",
    )
    p.add_argument(
        "--run-test-id",
        required=True,
        metavar="UUID",
        help="UUID of the RunTest (simulation suite) to execute",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("FI_API_KEY", ""),
        metavar="KEY",
        help="Future AGI API key (defaults to FI_API_KEY env var)",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("FI_BASE_URL", DEFAULT_BASE_URL),
        metavar="URL",
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        metavar="PCT",
        help=f"Pass-rate %% threshold for exit 0 (default: {DEFAULT_THRESHOLD})",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        dest="timeout_s",
        metavar="SECONDS",
        help=f"Total timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_S,
        dest="poll_interval_s",
        metavar="SECONDS",
        help=f"Seconds between status polls (default: {DEFAULT_POLL_INTERVAL_S})",
    )
    p.add_argument(
        "--max-polls",
        type=int,
        default=DEFAULT_MAX_POLLS,
        metavar="N",
        help=f"Maximum poll attempts (default: {DEFAULT_MAX_POLLS})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Force machine-readable JSON output (auto-enabled on non-TTY stdout)",
    )
    return p


def _headless_output(state: PollState) -> None:
    """Write a single JSON object to stdout and flush."""
    payload = {
        "execution_id": state.execution_id,
        "status": state.run_status,
        "phase": state.phase.value,
        "pass_rate": state.pass_rate,
        "polls_done": state.polls_done,
        "elapsed_s": round(state.elapsed_s, 1),
        "exit_code": state.exit_code,
        "error": state.error,
    }
    print(json.dumps(payload))
    sys.stdout.flush()


def _run_headed(poller: SimulatePoller, run_test_id: str) -> PollState:
    """Drive the poller with a rich live spinner."""
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        # Fall back to headless if rich is not installed
        return poller.run(run_test_id)

    console = Console()
    _last_state: list[Optional[PollState]] = [None]

    def _on_progress(s: PollState) -> None:
        _last_state[0] = s

    poller.progress_cb = _on_progress

    with Live(console=console, refresh_per_second=4) as live:

        def _render(s: Optional[PollState]) -> Spinner:
            msg = f"Polling… attempt {s.polls_done if s else 0}"
            if s and s.run_status not in ("none", "pending"):
                msg = f"Status: {s.run_status} (attempt {s.polls_done})"
            return Spinner("dots", text=msg)

        import threading

        result_holder: list[Optional[PollState]] = [None]
        exc_holder: list[Optional[Exception]] = [None]

        def _worker() -> None:
            try:
                result_holder[0] = poller.run(run_test_id)
            except Exception as exc:  # noqa: BLE001
                exc_holder[0] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        while thread.is_alive():
            live.update(_render(_last_state[0]))
            thread.join(timeout=0.25)

        if exc_holder[0]:
            raise exc_holder[0]

        state = result_holder[0]

    # Print summary table
    if state and state.phase == Phase.DONE and state.summary:
        table = Table(title="Evaluation Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Pass Rate", justify="right")
        table.add_column("Score", justify="right")
        for item in state.summary:
            if isinstance(item, dict):
                name = item.get("name") or item.get("eval_name") or "—"
                pr = item.get("pass_rate")
                sc = item.get("score") or item.get("avg_score")
                table.add_row(
                    str(name),
                    f"{pr:.1f}%" if pr is not None else "—",
                    f"{sc:.2f}" if sc is not None else "—",
                )
        console.print(table)

    if state:
        status_color = "green" if state.exit_code == 0 else "red"
        pr_str = f"{state.pass_rate:.1f}%" if state.pass_rate is not None else "n/a"
        console.print(
            Text(
                f"\n{'✓' if state.exit_code == 0 else '✗'} "
                f"Phase: {state.phase.value} | "
                f"Pass rate: {pr_str} | "
                f"Polls: {state.polls_done} | "
                f"Elapsed: {state.elapsed_s:.0f}s",
                style=status_color,
            )
        )

    return state


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.api_key:
        parser.error("--api-key is required (or set FI_API_KEY environment variable)")

    headless = args.json_mode or not sys.stdout.isatty()

    poller = SimulatePoller(
        base_url=args.base_url,
        api_key=args.api_key,
        poll_interval_s=args.poll_interval_s,
        max_polls=args.max_polls,
        timeout_s=args.timeout_s,
        threshold=args.threshold,
    )

    try:
        if headless:
            state = poller.run(args.run_test_id)
        else:
            state = _run_headed(poller, args.run_test_id)
    except KeyboardInterrupt:
        if headless:
            print(json.dumps({"error": "interrupted", "exit_code": 1}))
        return 1

    if headless:
        _headless_output(state)

    return state.exit_code if state.exit_code != -1 else 1


if __name__ == "__main__":
    sys.exit(main())
