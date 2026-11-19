"""ch25_backfill_traces — copy PG ``tracer_trace`` rows into the CH ``traces`` table.

The CH ``traces`` table (schema 015) is a read replica of PG traces.
The root span supplies its trace name: the dictionary cannot reliably do so
because spans arrive before traces. In the legacy world it was filled by
PeerDB CDC; v2 removes CDC, so the history is loaded here once and kept fresh
by the app-level dual-write (``trace_writer.mirror_traces_to_clickhouse``).

Idempotent: ``traces`` is a ReplacingMergeTree keyed on ``id`` with
``_version = updated_at`` ns, so re-running is a no-op (latest version wins).
Uses ``Trace.all_objects`` so soft-deleted traces are mirrored too (with
``is_deleted = 1``), matching the raw PG table count.

Operator UX:
    python manage.py ch25_backfill_traces                       # everything
    python manage.py ch25_backfill_traces --project-id <UUID>   # one project (repeatable)
    python manage.py ch25_backfill_traces --since 2026-03-01    # created_at >= (retention window)
    python manage.py ch25_backfill_traces --dry-run             # count only, no writes

Never materialize spans.trace_name: that would erase historical stored names.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.management.base import BaseCommand

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.trace_writer import _TRACE_COLUMNS, _trace_to_row


class Command(BaseCommand):
    help = "Backfill PG tracer_trace → CH traces (idempotent, ReplacingMergeTree)."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=10_000)
        parser.add_argument(
            "--project-id",
            action="append",
            default=None,
            help="Limit to this project (repeatable). Default: all.",
        )
        parser.add_argument("--since", default=None, help="created_at >= ISO8601")
        parser.add_argument("--until", default=None, help="created_at < ISO8601")
        parser.add_argument(
            "--dry-run", action="store_true", help="Count only; no CH writes."
        )

    def _client(self):
        import clickhouse_connect

        cfg = get_v2_config()
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=120,
        )

    def handle(self, *args, **opts):
        from tracer.models.trace import Trace

        manager = getattr(Trace, "all_objects", Trace.objects)
        qs = manager.all().order_by("created_at", "id")
        if opts["project_id"]:
            qs = qs.filter(project_id__in=opts["project_id"])
        if opts["since"]:
            qs = qs.filter(
                created_at__gte=datetime.fromisoformat(opts["since"]).replace(
                    tzinfo=UTC
                )
                if "T" not in opts["since"]
                else datetime.fromisoformat(opts["since"])
            )
        if opts["until"]:
            qs = qs.filter(created_at__lt=datetime.fromisoformat(opts["until"]))

        total = qs.count()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"ch25_backfill_traces — {total} PG trace(s) to mirror "
                f"(dry_run={opts['dry_run']})"
            )
        )
        if opts["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(f"DRY RUN — would mirror {total} trace(s).")
            )
            return
        if total == 0:
            self.stdout.write("nothing to backfill.")
            return

        client = self._client()
        bs = opts["batch_size"]
        written = 0
        batch: list[list] = []
        for t in qs.iterator(chunk_size=bs):
            # version_from_updated_at: historical rows version by their own
            # updated_at so a re-run is idempotent and never outranks a live
            # mirror's now()-based version.
            batch.append(_trace_to_row(t, version_from_updated_at=True))
            if len(batch) >= bs:
                client.insert("traces", batch, column_names=list(_TRACE_COLUMNS))
                written += len(batch)
                self.stdout.write(f"  …{written}/{total}")
                batch = []
        if batch:
            client.insert("traces", batch, column_names=list(_TRACE_COLUMNS))
            written += len(batch)

        self.stdout.write(
            self.style.SUCCESS(f"✓ inserted {written} trace row(s) into CH `traces`.")
        )

        # Quick parity readout (operator sanity check, not the full validator).
        ch_count = client.query(
            "SELECT count() FROM traces FINAL WHERE is_deleted = 0"
        ).result_rows[0][0]
        self.stdout.write(f"CH traces (FINAL, not deleted): {ch_count}")
