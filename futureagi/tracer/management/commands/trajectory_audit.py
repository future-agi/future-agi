"""Report-only reference audit of the trajectory item store.

Samples recent ``items_v1`` spans (up to 2,000 per partition bucket) and counts
referenced items that are missing, or whose newest copy is older than the
collector's reuse bound allows. Prints counts only and deletes nothing. The
daily GC runs the same audit and deletes nothing while it reports a violation.

After a restore, run it with ``--since <backup time>``; it should report 0.

A violation found by the daily GC also sets the GC latch (``traj:gc:latch``):
GC then deletes nothing, even after the violating span leaves the audit
window, until an operator runs this command with ``--clear-gc-latch``. That
clears the latch only if the audit is clean over ``--since`` (pass the latch
time or earlier); ``--force`` clears it regardless. On replicated ClickHouse
the audit reads every replica; it refuses (and clears nothing) on a topology
GC does not support.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2.trajectory_lifecycle import (
    clear_trajectory_gc_latch,
    run_audit,
)


def _since_us(value: str) -> int:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise CommandError("--since must be an ISO 8601 timestamp") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000)


class Command(BaseCommand):
    help = "Count missing or over-age trajectory item references; optionally clear the GC latch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            help="Audit spans created at or after this ISO 8601 time "
            "(default: the last two days).",
        )
        parser.add_argument(
            "--clear-gc-latch",
            action="store_true",
            help="Clear the GC latch if this audit finds no violation.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="With --clear-gc-latch: clear it even if this audit finds violations.",
        )

    def handle(self, *args, **options):
        since = _since_us(options["since"]) if options.get("since") else None
        report = run_audit(since_us=since)
        self.stdout.write(json.dumps(report, sort_keys=True))
        status = report.get("status")
        if options.get("clear_gc_latch"):
            if status != "ok" and not options.get("force"):
                # A refused audit read nothing: it is no proof either way.
                raise CommandError(
                    f"the audit is not clean ({status}); the GC latch stays set "
                    "(--force clears it anyway)"
                )
            cleared = clear_trajectory_gc_latch()
            self.stdout.write(json.dumps({"gc_latch_cleared": cleared}))
        if status == "refused":
            raise CommandError(f"trajectory audit refused: {report.get('reason')}")
        if report.get("violations"):
            raise CommandError(
                f"{report['violations']} trajectory item reference violation(s)"
            )
