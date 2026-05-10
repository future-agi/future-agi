"""
fi-simulate CLI entry point.

Subcommands:
  fi-simulate list [--search PATTERN] [--limit N] [--json]
  fi-simulate run <name-or-uuid> [options]
  fi-simulate status <execution-id> --run-test-id <UUID> [--json]

Exit codes:
  0 — success (list done, or run completed with pass_rate >= threshold)
  1 — failure (run failed / timed out / pass_rate < threshold, or name not resolved)
  2 — invalid arguments
"""

import argparse
import json
import os
import sys
from typing import Optional

from sdk.cli.poll import (
    Phase,
    PollState,
    SimulatePoller,
    format_failures,
    format_suite_row,
    parse_run_arg,
    resolve_name,
)

DEFAULT_BASE_URL = "https://app.futureagi.com"
DEFAULT_THRESHOLD = 80
DEFAULT_TIMEOUT_S = 300
DEFAULT_POLL_INTERVAL_S = 5
DEFAULT_MAX_POLLS = 60


def _add_common_args(p: argparse.ArgumentParser) -> None:
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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fi-simulate",
        description="Run and inspect Future AGI simulation suites.",
    )
    _add_common_args(p)

    sub = p.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")

    # list
    p_list = sub.add_parser("list", help="List available simulation suites")
    p_list.add_argument(
        "--search", default="", metavar="PATTERN",
        help="Filter suites by name (server-side substring match)",
    )
    p_list.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="Maximum results (default: 20)",
    )
    p_list.add_argument(
        "--json", action="store_true", dest="json_mode",
        help="Output as JSON array",
    )

    # run
    p_run = sub.add_parser(
        "run",
        help="Run a simulation suite by name or UUID",
    )
    p_run.add_argument(
        "target", metavar="NAME_OR_UUID",
        help="Suite name (substring search) or UUID",
    )
    p_run.add_argument(
        "--threshold", type=int, default=DEFAULT_THRESHOLD, metavar="PCT",
        help=f"Pass-rate %% for exit 0 (default: {DEFAULT_THRESHOLD})",
    )
    p_run.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_S, dest="timeout_s",
        metavar="SECONDS",
        help=f"Total timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    p_run.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_S,
        dest="poll_interval_s", metavar="SECONDS",
        help=f"Seconds between polls (default: {DEFAULT_POLL_INTERVAL_S})",
    )
    p_run.add_argument(
        "--max-polls", type=int, default=DEFAULT_MAX_POLLS, metavar="N",
        help=f"Maximum poll attempts (default: {DEFAULT_MAX_POLLS})",
    )
    p_run.add_argument(
        "--json", action="store_true", dest="json_mode",
        help="Force JSON output (auto-enabled on non-TTY stdout)",
    )

    # status
    p_status = sub.add_parser(
        "status",
        help="Show status of a specific execution (read-only)",
    )
    p_status.add_argument(
        "execution_id", metavar="EXECUTION_ID",
        help="UUID of the TestExecution to inspect",
    )
    p_status.add_argument(
        "--run-test-id", required=True, metavar="UUID",
        help="UUID of the RunTest (suite) that owns this execution",
    )
    p_status.add_argument(
        "--json", action="store_true", dest="json_mode",
        help="Output as JSON",
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


def _run_headed(poller: SimulatePoller, run_test_id: str, threshold: int) -> PollState:
    """Drive the poller with a rich live spinner; show failure drill-down after."""
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        # Fall back to headless if rich is not installed.
        # Emit JSON output so the caller sees results even on a TTY.
        state = poller.run(run_test_id)
        _headless_output(state)
        return state

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

        failures = format_failures(state.summary, threshold)
        if failures:
            console.print(Text("\nFailing metrics:", style="red bold"))
            for f in failures:
                name = f.get("name") or f.get("eval_name") or "—"
                pr = f.get("pass_rate")
                console.print(f"  • {name}: {pr:.1f}%" if pr is not None else f"  • {name}")

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


def _cmd_list(args, poller: SimulatePoller) -> int:
    try:
        suites = poller.list_suites(search=args.search, limit=args.limit)
    except Exception as exc:
        print(f"Error fetching suites: {exc}", file=sys.stderr)
        return 1

    if args.json_mode or not sys.stdout.isatty():
        print(json.dumps(suites))
        return 0

    if not suites:
        print("No simulation suites found.")
    else:
        print(f"Found {len(suites)} suite(s):\n")
        for i, suite in enumerate(suites, 1):
            print(format_suite_row(suite, i))
    return 0


def _cmd_run(args, poller: SimulatePoller) -> int:
    is_uuid, target = parse_run_arg(args.target)

    if is_uuid:
        run_test_id = target
    else:
        try:
            suites = poller.list_suites(search=target)
        except Exception as exc:
            print(f"Error resolving suite name: {exc}", file=sys.stderr)
            return 1
        try:
            run_test_id = resolve_name(suites, target)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    headless = args.json_mode or not sys.stdout.isatty()

    try:
        if headless:
            state = poller.run(run_test_id)
        else:
            state = _run_headed(poller, run_test_id, args.threshold)
    except KeyboardInterrupt:
        if headless:
            print(json.dumps({"error": "interrupted", "exit_code": 1}))
        return 1

    if headless:
        _headless_output(state)

    return state.exit_code if state.exit_code != -1 else 1


def _cmd_status(args, poller: SimulatePoller) -> int:
    try:
        data = poller.fetch_status(args.run_test_id, args.execution_id)
    except Exception as exc:
        print(f"Error fetching status: {exc}", file=sys.stderr)
        return 1

    if args.json_mode or not sys.stdout.isatty():
        print(json.dumps(data))
        return 0

    try:
        from rich.console import Console
        from rich.text import Text
        console = Console()
        status = data.get("status", "unknown")
        color = {"completed": "green", "failed": "red", "cancelled": "yellow"}.get(status, "white")
        console.print(Text(f"Status: {status}", style=color))
        if data.get("pass_rate") is not None:
            console.print(f"Pass rate: {data['pass_rate']:.1f}%")
    except ImportError:
        status = data.get("status", "unknown")
        print(f"Status: {status}")
        if data.get("pass_rate") is not None:
            print(f"Pass rate: {data['pass_rate']}")
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.api_key:
        parser.error("--api-key is required (or set FI_API_KEY environment variable)")

    if args.subcommand is None:
        parser.print_help()
        return 2

    poller_kwargs: dict = dict(base_url=args.base_url, api_key=args.api_key)
    if args.subcommand == "run":
        poller_kwargs.update(
            poll_interval_s=args.poll_interval_s,
            max_polls=args.max_polls,
            timeout_s=args.timeout_s,
            threshold=args.threshold,
        )
    poller = SimulatePoller(**poller_kwargs)

    if args.subcommand == "list":
        return _cmd_list(args, poller)
    if args.subcommand == "run":
        return _cmd_run(args, poller)
    if args.subcommand == "status":
        return _cmd_status(args, poller)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
