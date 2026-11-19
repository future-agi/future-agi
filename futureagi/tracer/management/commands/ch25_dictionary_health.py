"""Read-only ClickHouse dictionary health check and root trace-name audit.

Exit status is 2 when dictionary failures, stale loads or oversized allocations
are found; NOT_LOADED dictionaries are intentionally not flagged. --trace-names
prints daily root-name counts and never reloads dictionaries or changes spans.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.trace_name_sql import TRACE_NAME_SQL
from tracer.services.clickhouse.v2 import get_v2_config

DICTIONARY_SQL = """SELECT database, name, status, element_count, bytes_allocated,
    loading_duration, last_successful_update_time,
    dateDiff('second', last_successful_update_time, now()) AS stale_seconds,
    substring(last_exception,1,300) AS last_exception
FROM system.dictionaries
WHERE last_exception != ''
   OR status IN ('FAILED','FAILED_AND_RELOADING')
   OR (status IN ('LOADED','LOADED_AND_RELOADING')
       AND last_successful_update_time < now() - INTERVAL 10 MINUTE)
   OR bytes_allocated > {max_bytes:UInt64}"""

AUDIT_SETTINGS = {
    "readonly": 1,
    "max_threads": 2,
    "max_execution_time": 30,
    "max_memory_usage": 1_000_000_000,
}

AUDIT_SQL = f"""SELECT toDate(start_time) AS day, count() AS roots,
    countIf(trace_name = '') AS stored_empty,
    countIf({TRACE_NAME_SQL} = '') AS resolved_empty
FROM spans
WHERE parent_span_id = '' AND is_deleted = 0
  AND start_time >= {{day_start:DateTime64(6)}}
  AND start_time < {{day_end:DateTime64(6)}}
GROUP BY day ORDER BY day"""


class Command(BaseCommand):
    help = "Audit dictionary health or root-span trace names without writes."

    def add_arguments(self, parser):
        parser.add_argument("--max-bytes", type=int, default=4 * 1024**3)
        parser.add_argument("--trace-names", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--since", help="First UTC day (YYYY-MM-DD), inclusive")
        parser.add_argument("--until", help="Last UTC day (YYYY-MM-DD), exclusive")

    def _client(self):
        import clickhouse_connect

        cfg = get_v2_config()
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=35,
        )

    def handle(self, *args, **opts):
        if opts.get("trace_names"):
            return self._trace_names(opts)
        max_bytes = opts.get("max_bytes", 4 * 1024**3)
        if max_bytes < 0:
            raise CommandError("--max-bytes must not be negative")
        if opts.get("dry_run"):
            self.stdout.write(DICTIONARY_SQL)
            return 0
        rows = (
            self._client()
            .query(DICTIONARY_SQL, parameters={"max_bytes": max_bytes})
            .result_rows
        )
        self.stdout.write(
            "database\tname\tstatus\telement_count\tbytes_allocated\t"
            "loading_duration\tlast_successful_update_time\tstale_seconds\tlast_exception"
        )
        for row in rows:
            self.stdout.write("\t".join(str(value) for value in row))
        if rows:
            raise SystemExit(2)
        return 0

    def _trace_names(self, opts):
        if not opts.get("since") or not opts.get("until"):
            raise CommandError("--trace-names requires --since and --until")
        try:
            since = date.fromisoformat(opts["since"])
            until = date.fromisoformat(opts["until"])
        except ValueError as exc:
            raise CommandError("--since/--until must be ISO dates") from exc
        if until <= since:
            raise CommandError("--until must be after --since")
        if opts.get("dry_run"):
            self.stdout.write(AUDIT_SQL)
            return 0
        client = self._client()
        self.stdout.write("day\troots\tstored_empty\tresolved_empty")
        day = since
        while day < until:
            rows = client.query(
                AUDIT_SQL,
                parameters={
                    "day_start": str(day),
                    "day_end": str(day + timedelta(days=1)),
                },
                settings=AUDIT_SETTINGS,
            ).result_rows
            for row in rows:
                self.stdout.write("\t".join(str(value) for value in row))
            day += timedelta(days=1)
        return 0
