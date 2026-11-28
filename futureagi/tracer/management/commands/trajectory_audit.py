"""Report-only reference audit of the trajectory item store.

Samples recent ``items_v1`` spans (up to 2,000 per partition bucket) and counts
referenced items that are missing, or whose newest copy is older than the
collector's reuse bound allows. Prints counts only and deletes nothing. The
daily GC runs the same audit and deletes nothing while it reports a violation.

After a restore, run it with ``--since <backup time>``; it should report 0.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2.trajectory_lifecycle import run_audit


def _since_us(value: str) -> int:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise CommandError("--since must be an ISO 8601 timestamp") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000)


class Command(BaseCommand):
    help = "Count missing or over-age trajectory item references (report only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            help="Audit spans created at or after this ISO 8601 time "
            "(default: the last two days).",
        )

    def handle(self, *args, **options):
        since = _since_us(options["since"]) if options.get("since") else None
        report = run_audit(since_us=since)
        self.stdout.write(json.dumps(report, sort_keys=True))
        if report.get("violations"):
            raise CommandError(
                f"{report['violations']} trajectory item reference violation(s)"
            )
