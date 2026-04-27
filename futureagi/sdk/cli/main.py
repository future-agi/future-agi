"""Entry point for the ``fi-simulate`` CLI.

Provides two subcommands:

- ``run``   — start a test execution, poll until done, report results.
- ``status`` — check the status of an existing execution.

Usage::

    fi-simulate run --test-id <UUID> --api-key <KEY> --secret-key <SECRET>
    fi-simulate status --test-id <UUID> --execution-id <UUID> --api-key <KEY> --secret-key <SECRET>
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from sdk.cli import __version__
from sdk.cli.client import (
    EXIT_REGRESSION,
    EXIT_SUCCESS,
    EXIT_TIMEOUT_OR_FAILURE,
    EXIT_USAGE_ERROR,
    DEFAULT_BASE_URL,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    AuthConfig,
    AuthError,
    CLIError,
    PollTimeoutError,
    SimulateClient,
)
from sdk.cli.output import emit, get_formatter


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with ``run`` and ``status`` subcommands.

    Returns:
        Configured ``ArgumentParser``.
    """
    parser = argparse.ArgumentParser(
        prog="fi-simulate",
        description=(
            "Run FutureAGI Simulate test runs from the command line. "
            "Gate CI/CD pipelines on simulation pass rates."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fi-simulate {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # ── run ────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help="Start a test execution, poll until done, and report results.",
    )
    run_parser.add_argument(
        "--test-id",
        required=True,
        help="UUID of the RunTest to execute.",
    )
    run_parser.add_argument(
        "--api-key",
        default=os.environ.get("FI_API_KEY", ""),
        help="FutureAGI API key (or set FI_API_KEY env var).",
    )
    run_parser.add_argument(
        "--secret-key",
        default=os.environ.get("FI_SECRET_KEY", ""),
        help="FutureAGI secret key (or set FI_SECRET_KEY env var).",
    )
    run_parser.add_argument(
        "--scenario-ids",
        default="",
        help="Comma-separated scenario UUIDs (optional; runs all if omitted).",
    )
    run_parser.add_argument(
        "--simulator-id",
        default=None,
        help="Simulator UUID (optional).",
    )
    run_parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="Minimum aggregate pass rate (0.0–1.0). Default: 0.0.",
    )
    run_parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Max seconds to wait. Default: {DEFAULT_TIMEOUT_SECONDS}.",
    )
    run_parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=(
            f"Seconds between status polls. "
            f"Default: {DEFAULT_POLL_INTERVAL_SECONDS}."
        ),
    )
    run_parser.add_argument(
        "--base-url",
        default=os.environ.get("FI_BASE_URL", DEFAULT_BASE_URL),
        help=f"API base URL. Default: {DEFAULT_BASE_URL}.",
    )
    run_parser.add_argument(
        "--output",
        choices=["text", "json", "github"],
        default="text",
        help="Output format. Default: text.",
    )

    # ── status ─────────────────────────────────────────────────────────
    status_parser = subparsers.add_parser(
        "status",
        help="Check the status of an existing execution.",
    )
    status_parser.add_argument(
        "--test-id",
        required=True,
        help="UUID of the RunTest.",
    )
    status_parser.add_argument(
        "--execution-id",
        required=True,
        help="UUID of the specific execution.",
    )
    status_parser.add_argument(
        "--api-key",
        default=os.environ.get("FI_API_KEY", ""),
        help="FutureAGI API key (or set FI_API_KEY env var).",
    )
    status_parser.add_argument(
        "--secret-key",
        default=os.environ.get("FI_SECRET_KEY", ""),
        help="FutureAGI secret key (or set FI_SECRET_KEY env var).",
    )
    status_parser.add_argument(
        "--base-url",
        default=os.environ.get("FI_BASE_URL", DEFAULT_BASE_URL),
        help=f"API base URL. Default: {DEFAULT_BASE_URL}.",
    )
    status_parser.add_argument(
        "--output",
        choices=["text", "json", "github"],
        default="text",
        help="Output format. Default: text.",
    )

    return parser


def _validate_auth(args: argparse.Namespace) -> AuthConfig:
    """Validate that authentication credentials are provided.

    Args:
        args: Parsed CLI arguments.

    Returns:
        ``AuthConfig`` with validated credentials.

    Raises:
        SystemExit: If credentials are missing.
    """
    api_key = args.api_key
    secret_key = args.secret_key

    if not api_key:
        print(
            "Error: --api-key is required (or set FI_API_KEY env var).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE_ERROR)

    if not secret_key:
        print(
            "Error: --secret-key is required (or set FI_SECRET_KEY env var).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE_ERROR)

    return AuthConfig(api_key=api_key, secret_key=secret_key)


def _cmd_run(args: argparse.Namespace) -> int:
    """Execute the ``run`` subcommand.

    Starts a test execution, polls until completion, fetches the eval summary,
    and returns the appropriate exit code.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 = pass, 1 = regression, 2 = timeout/failure, 3 = error).
    """
    auth = _validate_auth(args)
    formatter = get_formatter(args.output)
    client = SimulateClient(base_url=args.base_url, auth=auth)

    try:
        # Parse scenario IDs.
        scenario_ids: list[str] | None = None
        if args.scenario_ids:
            scenario_ids = [
                s.strip() for s in args.scenario_ids.split(",") if s.strip()
            ]

        # 1. Start execution.
        execution = client.start_execution(
            test_id=args.test_id,
            scenario_ids=scenario_ids,
            simulator_id=args.simulator_id,
        )
        emit(
            formatter.format_execution_started(
                execution_id=execution.execution_id,
                run_test_id=execution.run_test_id,
                total_scenarios=execution.total_scenarios,
            )
        )

        # 2. Poll until terminal state.
        try:
            final_status = client.wait_for_completion(
                test_id=args.test_id,
                execution_id=execution.execution_id,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
            )
        except PollTimeoutError:
            # Genuine timeout — exit code 2.
            emit(
                formatter.format_timeout(
                    execution_id=execution.execution_id,
                    timeout=args.timeout,
                    last_status="unknown",
                )
            )
            return EXIT_TIMEOUT_OR_FAILURE
        except AuthError as exc:
            # Auth failure during polling — exit code 3.
            emit(formatter.format_error(str(exc)), file=sys.stderr)
            return EXIT_USAGE_ERROR
        except CLIError as exc:
            # Network/API error during polling — exit code 3.
            emit(formatter.format_error(str(exc)), file=sys.stderr)
            return EXIT_USAGE_ERROR

        # Handle non-completed terminal states.
        if final_status.status.lower() != "completed":
            emit(
                formatter.format_summary(
                    execution_id=execution.execution_id,
                    status=final_status.status,
                    evals=[],
                    aggregate_pass_rate=0.0,
                    threshold=args.threshold,
                    passed=False,
                )
            )
            return EXIT_TIMEOUT_OR_FAILURE

        # 3. Fetch eval summary.
        summary = client.get_eval_summary(
            test_id=args.test_id,
            execution_id=execution.execution_id,
        )

        # 4. Determine pass/fail.
        passed = summary.aggregate_pass_rate >= args.threshold
        emit(
            formatter.format_summary(
                execution_id=execution.execution_id,
                status=final_status.status,
                evals=summary.evals,
                aggregate_pass_rate=summary.aggregate_pass_rate,
                threshold=args.threshold,
                passed=passed,
            )
        )

        return EXIT_SUCCESS if passed else EXIT_REGRESSION

    except AuthError as exc:
        emit(formatter.format_error(str(exc)), file=sys.stderr)
        return EXIT_USAGE_ERROR

    except CLIError as exc:
        emit(formatter.format_error(str(exc)), file=sys.stderr)
        return EXIT_USAGE_ERROR

    finally:
        client.close()


def _cmd_status(args: argparse.Namespace) -> int:
    """Execute the ``status`` subcommand.

    Fetches and displays the current status of a test execution.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 = success, 3 = error).
    """
    auth = _validate_auth(args)
    formatter = get_formatter(args.output)
    client = SimulateClient(base_url=args.base_url, auth=auth)

    try:
        result = client.poll_status(
            args.test_id, execution_id=args.execution_id
        )
        emit(formatter.format_polling(result.status, result.progress))

        # For JSON output, also emit raw data when polling suppresses output.
        if args.output == "json":
            import json

            emit(
                json.dumps(
                    {
                        "event": "status",
                        "test_id": args.test_id,
                        "execution_id": args.execution_id,
                        "status": result.status,
                        "progress": result.progress,
                    },
                    indent=2,
                )
            )

        return EXIT_SUCCESS

    except AuthError as exc:
        emit(formatter.format_error(str(exc)), file=sys.stderr)
        return EXIT_USAGE_ERROR

    except CLIError as exc:
        emit(formatter.format_error(str(exc)), file=sys.stderr)
        return EXIT_USAGE_ERROR

    finally:
        client.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for the ``fi-simulate`` CLI.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_USAGE_ERROR

    commands = {
        "run": _cmd_run,
        "status": _cmd_status,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE_ERROR

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
