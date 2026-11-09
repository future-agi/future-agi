"""Independent bounded cloud cases; a failed bootstrap never hides other gates.

`empty` is a REQUIRED positive contract, not an expected failure. `keys` uses a
fully migrated synthetic fixture and proves only gateway-domain transition.
Neither case can waive the other. Never fake or reverse a migration.

Run with ``CASE --schema-deadline-seconds 900`` (default 900, integer 1..86400).
The same budget is passed to the real operator, with 30s process startup/cleanup
grace before the wrapper kills/reaps it. Non-schema children (backfill, check,
key transitions) are killed after ``--operator-timeout-seconds`` (default 900,
the schema budget, integer 1..86400); the old fixed 180s wrapper was below a
measured 236s empty-install phase. The whole-case watchdog gives every volume a
full schema budget, a full operator budget and a fixture/backfill allowance; it
must not preempt a valid phase budget. Progress is relayed live.
These synthetic results do NOT qualify the five delegated-helper migrations
still owned by main's mig-alias-helpers-690 follow-up.
"""

import argparse
import json
import os
import signal

import django

from agentcc.hosted_migration_progress import add_schema_deadline_argument
from agentcc.tests.hosted_rehearsal_process import (
    SCHEMA_PROCESS_GRACE_SECONDS,
    add_operator_timeout_argument,
)

FIXTURE_ALLOWANCE_SECONDS = 600


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("empty", "migrations", "keys", "backend"))
    add_schema_deadline_argument(parser)
    add_operator_timeout_argument(parser)
    return parser


def case_watchdog_seconds(
    case, *, schema_deadline_seconds, operator_timeout_seconds, volumes=None
):
    if volumes is None:
        volumes = 3 if case == "migrations" else 1
    return volumes * (
        schema_deadline_seconds
        + SCHEMA_PROCESS_GRACE_SECONDS
        + operator_timeout_seconds
        + FIXTURE_ALLOWANCE_SECONDS
    )


def main():
    args = argument_parser().parse_args()
    signal.alarm(
        case_watchdog_seconds(
            args.case,
            schema_deadline_seconds=args.schema_deadline_seconds,
            operator_timeout_seconds=args.operator_timeout_seconds,
        )
    )
    django.setup()
    from agentcc.tests.hosted_operator_rehearsal import (
        VOLUME_MATRIX,
        key_rehearsal,
        migration_rehearsal,
    )
    from agentcc.tests.hosted_rehearsal_support import Rehearsal

    if args.case == "empty":
        with Rehearsal(
            args.schema_deadline_seconds,
            operator_timeout_seconds=args.operator_timeout_seconds,
        ) as rehearsal:
            rehearsal.apply("schema")
            rehearsal.apply("backfill")
            rehearsal.run("check")
            result = rehearsal.timings
    elif args.case == "migrations":
        result = [
            migration_rehearsal(
                *volume,
                schema_deadline_seconds=args.schema_deadline_seconds,
                operator_timeout_seconds=args.operator_timeout_seconds,
            )
            for volume in VOLUME_MATRIX
        ]
    elif args.case == "keys":
        result = key_rehearsal(
            installed_fixture=True,
            schema_deadline_seconds=args.schema_deadline_seconds,
            operator_timeout_seconds=args.operator_timeout_seconds,
        )
    else:
        from agentcc.tests.hosted_backend_rehearsal import main as backend

        backend()
        result = "synthetic schema fixtures only"
    print(
        json.dumps(
            {
                "source": os.environ["OMEGA_EXEC_SOURCE_HEAD"],
                "case": args.case,
                "result": result,
                "release_gates": "OWED",
            },
            sort_keys=True,
        )
    )


def run_all():
    from agentcc.tests.hosted_operator_rehearsal import (
        VOLUME_MATRIX,
        key_rehearsal,
        migration_rehearsal,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    add_schema_deadline_argument(parser)
    add_operator_timeout_argument(parser)
    args = parser.parse_args()
    signal.alarm(
        case_watchdog_seconds(
            "all",
            schema_deadline_seconds=args.schema_deadline_seconds,
            operator_timeout_seconds=args.operator_timeout_seconds,
            volumes=len(VOLUME_MATRIX) + 1,
        )
    )
    django.setup()
    migrations = [
        migration_rehearsal(
            *volume,
            schema_deadline_seconds=args.schema_deadline_seconds,
            operator_timeout_seconds=args.operator_timeout_seconds,
        )
        for volume in VOLUME_MATRIX
    ]
    keys = key_rehearsal(
        schema_deadline_seconds=args.schema_deadline_seconds,
        operator_timeout_seconds=args.operator_timeout_seconds,
    )
    print(
        json.dumps(
            {
                "source": os.environ["OMEGA_EXEC_SOURCE_HEAD"],
                "scope": "synthetic-command-only",
                "migration_volumes": migrations,
                "keys": keys,
                "release_gates": "OWED",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
