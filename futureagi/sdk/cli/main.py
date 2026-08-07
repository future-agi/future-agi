"""
fi-simulate — CLI for running FutureAGI Simulate test runs from the command line.

Usage:
    fi-simulate run --test-id <id> --api-key <key>
    fi-simulate status --test-id <id> --api-key <key>

Auth:
    Pass a Bearer JWT or an "api_key:secret_key" pair via --api-key / FI_API_KEY.
    If the value contains a colon it is split into api_key + secret_key for the
    X-Api-Key / X-Secret-Key header pair; otherwise it is sent as Bearer token.

Exit codes:
    0  — execution completed and pass rate >= threshold
    1  — execution completed but pass rate < threshold (regression)
    2  — execution failed, cancelled, or timed out
    3  — usage / auth / network error
"""

import argparse
import os
import sys

from .client import FiSimulateClient, SimulateClientError, SimulateTimeoutError
from .output import print_text, print_json, write_github_summary

# Exit codes
EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_FAILURE = 2
EXIT_ERROR = 3

DEFAULT_BASE_URL = "https://app.futureagi.com"
DEFAULT_THRESHOLD = 0.8
DEFAULT_TIMEOUT = 1800
DEFAULT_POLL_INTERVAL = 5


def _build_client(args: argparse.Namespace) -> FiSimulateClient:
    raw_key = args.api_key or os.environ.get("FI_API_KEY", "")
    if not raw_key:
        print(
            "Error: --api-key or FI_API_KEY environment variable is required.",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)

    base_url = args.base_url or os.environ.get("FI_BASE_URL", DEFAULT_BASE_URL)

    # "key:secret" format → X-Api-Key + X-Secret-Key
    # plain string       → Bearer token
    if ":" in raw_key:
        parts = raw_key.split(":", 1)
        api_key, secret_key = parts[0], parts[1]
    else:
        api_key, secret_key = raw_key, None

    return FiSimulateClient(
        base_url=base_url,
        api_key=api_key,
        secret_key=secret_key,
    )


def _parse_scenario_ids(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


# ------------------------------------------------------------------
# Subcommand: run
# ------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    client = _build_client(args)
    test_id = args.test_id
    threshold = args.threshold
    timeout = args.timeout
    poll_interval = args.poll_interval
    output_mode = args.output
    scenario_ids = _parse_scenario_ids(args.scenario_ids)
    simulator_id = args.simulator_id

    try:
        # 1. Start execution
        start_resp = client.start_execution(
            run_test_id=test_id,
            scenario_ids=scenario_ids,
            simulator_id=simulator_id,
        )
        execution_id = start_resp.get("execution_id") or start_resp.get("result", {}).get("execution_id", "")

        if not execution_id:
            # Some success responses are nested under "result"
            result = start_resp.get("result", start_resp)
            execution_id = result.get("execution_id", "")

        if not execution_id:
            print(
                f"Warning: could not extract execution_id from start response: {start_resp}",
                file=sys.stderr,
            )

        if output_mode == "text":
            print(f"\nExecution started  execution_id={execution_id or 'unknown'}")
            print(f"Polling every {poll_interval}s (timeout {timeout}s)…\n")

        # 2. Poll until terminal
        try:
            final_status = client.poll_until_terminal(
                run_test_id=test_id,
                timeout=timeout,
                poll_interval=poll_interval,
            )
        except SimulateTimeoutError as exc:
            print(f"Timeout: {exc}", file=sys.stderr)
            return EXIT_FAILURE

        raw_state = str(final_status.get("status", "")).lower()
        execution_id = final_status.get("execution_id") or execution_id

        if raw_state in ("failed", "cancelled", "error"):
            print(
                f"Execution {raw_state}: {final_status.get('message', final_status.get('error', ''))}",
                file=sys.stderr,
            )
            return EXIT_FAILURE

        # 3. Fetch eval summary
        eval_summary: list = []
        if execution_id:
            try:
                eval_summary = client.get_eval_summary(
                    run_test_id=test_id,
                    execution_id=execution_id,
                )
            except SimulateClientError as exc:
                print(f"Warning: could not fetch eval summary: {exc}", file=sys.stderr)

        pass_rate = client.compute_pass_rate(eval_summary)
        passed = pass_rate >= threshold

        total_calls = int(final_status.get("total_calls", 0))
        completed_calls = int(final_status.get("completed_calls", 0))
        failed_calls = int(final_status.get("failed_calls", 0))

        # 4. Output
        common = dict(
            run_test_id=test_id,
            execution_id=execution_id,
            status=raw_state,
            pass_rate=pass_rate,
            threshold=threshold,
            eval_summary=eval_summary,
            total_calls=total_calls,
            completed_calls=completed_calls,
            failed_calls=failed_calls,
        )

        if output_mode == "json":
            print_json(**common, passed=passed)
        elif output_mode == "github":
            write_github_summary(**common, passed=passed)
        else:
            print_text(**common)

        return EXIT_PASS if passed else EXIT_REGRESSION

    except SimulateClientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


# ------------------------------------------------------------------
# Subcommand: status
# ------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    client = _build_client(args)
    try:
        status = client.get_status(args.test_id)
        import json
        print(json.dumps(status, indent=2, default=str))
        return EXIT_PASS
    except SimulateClientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--test-id", required=True, help="Run-test UUID to target")
    parser.add_argument(
        "--api-key",
        default=None,
        help=(
            "Auth credential. Use a Bearer JWT, or 'api_key:secret_key' pair. "
            "Falls back to FI_API_KEY env var."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"API base URL (default: {DEFAULT_BASE_URL}). Falls back to FI_BASE_URL env var.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fi-simulate",
        description="Run FutureAGI Simulate test runs from the command line.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ---- run ----
    run_p = sub.add_parser("run", help="Start and await a Simulate test run")
    _add_common_args(run_p)
    run_p.add_argument(
        "--scenario-ids",
        default=None,
        help="Comma-separated scenario UUIDs to run (default: all)",
    )
    run_p.add_argument(
        "--simulator-id",
        default=None,
        help="Override the simulator for this execution",
    )
    run_p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum pass rate to exit 0 (default: {DEFAULT_THRESHOLD})",
    )
    run_p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Max seconds to wait for completion (default: {DEFAULT_TIMEOUT})",
    )
    run_p.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between status polls (default: {DEFAULT_POLL_INTERVAL})",
    )
    run_p.add_argument(
        "--output",
        choices=["text", "json", "github"],
        default="text",
        help="Output format: text (default), json, or github (writes $GITHUB_STEP_SUMMARY)",
    )

    # ---- status ----
    status_p = sub.add_parser("status", help="Check the current status of a test run")
    _add_common_args(status_p)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "run":
        sys.exit(cmd_run(args))
    elif args.subcommand == "status":
        sys.exit(cmd_status(args))
    else:
        parser.print_help()
        sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()
