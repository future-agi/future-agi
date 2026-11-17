"""Bounded child capture with live, value-free operator progress relay."""

import argparse
import subprocess
import tempfile
import threading

from agentcc.hosted_migration_progress import (
    DEFAULT_SCHEMA_DEADLINE_SECONDS,
    schema_deadline_seconds,
)

# Django startup/cleanup are outside the command's schema-phase clock. Give the
# child time to report its own deadline and release the connection before kill.
SCHEMA_PROCESS_GRACE_SECONDS = 30
# Non-schema operator phases (backfill, check, key transitions) get the schema
# budget by default. The old fixed 180 s wrapper was below a measured 236 s
# empty-install phase and killed a healthy install.
DEFAULT_OPERATOR_TIMEOUT_SECONDS = DEFAULT_SCHEMA_DEADLINE_SECONDS


def operator_timeout_seconds(value):
    try:
        return schema_deadline_seconds(value)
    except argparse.ArgumentTypeError:
        raise argparse.ArgumentTypeError(
            "operator wrapper timeout must be an integer in 1..86400 seconds"
        ) from None


def add_operator_timeout_argument(parser):
    parser.add_argument(
        "--operator-timeout-seconds",
        type=operator_timeout_seconds,
        default=DEFAULT_OPERATOR_TIMEOUT_SECONDS,
        help="Wrapper kill limit for non-schema operator phases, 1..86400 seconds (default: 900, the schema budget). The schema phase keeps its deadline plus 30s grace.",
    )


def run_operator(argv, *, env, timeout):
    lines = []
    with tempfile.TemporaryFile(mode="w+t") as errors:
        with subprocess.Popen(
            argv, env=env, stdout=subprocess.PIPE, stderr=errors, text=True
        ) as child:

            def capture():
                for line in child.stdout:
                    lines.append(line)
                    if line.startswith(("migration=", "schema-phase ")):
                        print(line.rstrip(), flush=True)

            reader = threading.Thread(target=capture, daemon=True)
            reader.start()
            try:
                child.wait(timeout=timeout)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
                reader.join()
            errors.seek(0)
            return subprocess.CompletedProcess(
                argv, child.returncode, "".join(lines), errors.read()
            )
