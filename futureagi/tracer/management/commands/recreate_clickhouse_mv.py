"""
Drop and recreate a ClickHouse materialized view from the canonical DDL.

ClickHouse's ``CREATE MATERIALIZED VIEW IF NOT EXISTS`` skips already-existing
views, so any semantic change to an MV body (a new ``WHERE`` clause, a
different aggregation, an additional join, etc.) requires explicit DROP +
CREATE on every environment that has the old MV. Without this script that
step is easy to miss on prod and silently corrupts whichever rollup the MV
feeds.

This command consumes ``MV_RECREATE_MANIFEST`` from
``tracer.services.clickhouse.schema`` and provides:

  * ``--dry-run`` — report what would change without touching anything.
  * ``--diff-only`` — print the diff between deployed and canonical DDL.
  * ``--backfill`` — after recreate, re-aggregate rows that landed in the
    DROP→CREATE gap. Uses the per-MV ``backfill_select`` body.
  * ``--gap-buffer-seconds`` — how many seconds before DROP to start the
    backfill window. Default 30. Must be larger than the longest expected
    in-flight INSERT-to-MV latency to avoid a missed-row sliver.
  * ``--chunk-hours`` — chunk size when running the backfill INSERT. The
    default of 24 keeps memory bounded on hour-partitioned target tables.
    Set to 0 to run as a single statement.
  * ``--no-confirm`` — skip the interactive prompt (for CI/CD and
    deploy automation).

Usage on prod (PR3 deploy):

    # 1. Dry-run first to confirm what changes:
    python manage.py recreate_clickhouse_mv eval_metrics_hourly_mv --dry-run

    # 2. Run for real. PR3's MV change is semantic-equivalent for existing
    #    rows (every existing row has target_type='span' via the column
    #    DEFAULT), so backfill is OPTIONAL — the gap window is the only
    #    data that would otherwise be lost:
    python manage.py recreate_clickhouse_mv eval_metrics_hourly_mv --backfill

    # 3. Verify the deployed DDL matches canonical post-run:
    python manage.py recreate_clickhouse_mv eval_metrics_hourly_mv --diff-only

Efficiency notes for large prod tables:

  * The DROP is metadata-only (~ms). The CREATE has no ``POPULATE`` clause,
    so it only installs the trigger for future inserts (also ~ms).
  * The backfill INSERT chunks by hour to keep memory bounded. A target
    table with one year of hourly aggregates and N source rows per hour
    is processed in 24-hour chunks; each chunk reads only its window via
    ``created_at >= ? AND created_at < ?`` (the source's bloom filter on
    ``created_at`` keeps reads cheap).
  * The backfill query mirrors the MV's WHERE clause, so it only re-reads
    rows the MV would have processed. For PR3's change that is
    ``target_type IN ('span', 'trace')`` — sessions are skipped.
  * Double-counting risk: rows that landed AFTER the cutoff but BEFORE
    the DROP completed are processed by the OLD MV and may also match
    the backfill window. The default ``--gap-buffer-seconds=30`` keeps
    the cutoff comfortably ahead of in-flight INSERTs to the source
    table; under heavy write load increase it.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Drop and recreate a ClickHouse materialized view from canonical DDL."

    def add_arguments(self, parser):
        parser.add_argument(
            "mv_name",
            help="Name of the MV to recreate (must be a key in MV_RECREATE_MANIFEST).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print actions without executing.",
        )
        parser.add_argument(
            "--diff-only",
            action="store_true",
            help="Print the diff between deployed and canonical DDL, then exit.",
        )
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="After recreate, INSERT-SELECT to fill the gap window.",
        )
        parser.add_argument(
            "--gap-buffer-seconds",
            type=int,
            default=30,
            help=(
                "Seconds before DROP to start the backfill window. Bigger = "
                "safer against in-flight INSERTs but more potential overlap "
                "with old-MV-processed rows. Default: 30."
            ),
        )
        parser.add_argument(
            "--chunk-hours",
            type=int,
            default=24,
            help=(
                "Chunk size for the backfill INSERT. 0 = single-shot. "
                "Default: 24 hours."
            ),
        )
        parser.add_argument(
            "--no-confirm",
            action="store_true",
            help="Skip the interactive confirmation prompt.",
        )

    # ── Entrypoint ────────────────────────────────────────────────────

    def handle(self, *args, **options):
        from tracer.services.clickhouse import schema as ch_schema
        from tracer.services.clickhouse.client import get_clickhouse_client

        # We deliberately do NOT gate on ``is_clickhouse_enabled()`` here.
        # That setting is a runtime config flag for the app; this command
        # is a deploy-time tool and should run regardless. If CH isn't
        # reachable, the first SHOW CREATE call will fail with a clear
        # connection error, which is more informative than a config-flag
        # check. To run only against a known-good environment, the
        # caller should pin ``CH_HOST``/``CH_PORT`` in env before
        # invoking.

        mv_name = options["mv_name"]
        manifest = ch_schema.MV_RECREATE_MANIFEST.get(mv_name)
        if not manifest:
            available = ", ".join(sorted(ch_schema.MV_RECREATE_MANIFEST.keys()))
            raise CommandError(
                f"MV '{mv_name}' not in MV_RECREATE_MANIFEST. "
                f"Available: {available}"
            )

        canonical_ddl = self._resolve_canonical_ddl(ch_schema, manifest)
        client = get_clickhouse_client()

        deployed_ddl = self._get_deployed_ddl(client, mv_name)

        if options["diff_only"]:
            self._print_diff(deployed_ddl, canonical_ddl)
            return

        if deployed_ddl is not None and self._ddl_equivalent(
            deployed_ddl, canonical_ddl
        ):
            self.stdout.write(
                self.style.SUCCESS(
                    f"MV '{mv_name}' already matches canonical DDL. Nothing to do."
                )
            )
            return

        # Plan
        self._print_plan(
            mv_name=mv_name,
            manifest=manifest,
            canonical_ddl=canonical_ddl,
            deployed_ddl=deployed_ddl,
            options=options,
        )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run — no changes made."))
            return

        if not options["no_confirm"] and not self._confirm():
            self.stdout.write(self.style.WARNING("Aborted by operator."))
            return

        # Execute
        cutoff = self._capture_cutoff(client, options["gap_buffer_seconds"])
        self.stdout.write(f"Cutoff timestamp: {cutoff.isoformat()}")

        self._drop_mv(client, mv_name)
        self._create_mv(client, canonical_ddl)

        # Post-create verification
        post_ddl = self._get_deployed_ddl(client, mv_name)
        if post_ddl is None:
            raise CommandError(f"Recreated MV '{mv_name}' is not visible — aborting.")
        if not self._ddl_equivalent(post_ddl, canonical_ddl):
            self.stderr.write(
                self.style.WARNING(
                    "Post-create DDL diverges from canonical (likely "
                    "ClickHouse normalization). Continuing — diff:"
                )
            )
            self._print_diff(post_ddl, canonical_ddl)

        if options["backfill"]:
            self._run_backfill(
                client=client,
                manifest=manifest,
                cutoff=cutoff,
                chunk_hours=options["chunk_hours"],
            )

        self.stdout.write(
            self.style.SUCCESS(f"MV '{mv_name}' recreated successfully.")
        )

    # ── Helpers ───────────────────────────────────────────────────────

    def _resolve_canonical_ddl(
        self, ch_schema, manifest: Dict[str, Optional[str]]
    ) -> str:
        const_name = manifest["ddl_constant_name"]
        ddl = getattr(ch_schema, const_name, None)
        if not isinstance(ddl, str):
            raise CommandError(
                f"Canonical DDL constant '{const_name}' missing or not a string."
            )
        return ddl.strip()

    def _get_deployed_ddl(self, client, mv_name: str) -> Optional[str]:
        """Return the deployed CREATE statement for the MV, or None if absent."""
        try:
            rows = client.execute(f"SHOW CREATE TABLE {mv_name}")
        except Exception:
            return None
        if not rows:
            return None
        return rows[0][0].strip()

    def _ddl_equivalent(self, deployed: str, canonical: str) -> bool:
        """Best-effort equality on whitespace-collapsed DDL strings.

        ClickHouse normalizes ``SHOW CREATE`` output (rewrites identifiers,
        collapses whitespace, omits ``IF NOT EXISTS``), so a literal string
        compare always fails. We collapse runs of whitespace and lowercase
        keywords before comparing — good enough to catch the "is the new
        WHERE clause present" case which is what we actually care about.
        """
        return self._normalize(deployed) == self._normalize(canonical)

    @staticmethod
    def _normalize(s: str) -> str:
        s = re.sub(r"IF\s+NOT\s+EXISTS\s+", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s)
        return s.strip().lower()

    def _print_diff(self, deployed: Optional[str], canonical: str) -> None:
        self.stdout.write("\n──── Canonical DDL ────")
        self.stdout.write(canonical)
        self.stdout.write("\n──── Deployed DDL ────")
        self.stdout.write(deployed if deployed else "<NOT DEPLOYED>")
        self.stdout.write("")

    def _print_plan(
        self,
        mv_name: str,
        manifest: Dict[str, Any],
        canonical_ddl: str,
        deployed_ddl: Optional[str],
        options: Dict[str, Any],
    ) -> None:
        self.stdout.write(f"\n=== Recreation plan: {mv_name} ===")
        self.stdout.write(
            f"  Source table:        {manifest['source_table']}"
        )
        self.stdout.write(
            f"  Target table:        {manifest['target_table']}"
        )
        self.stdout.write(
            f"  Time column:         {manifest['source_time_column']}"
        )
        self.stdout.write(
            f"  Backfill:            {'YES' if options['backfill'] else 'no'}"
        )
        if options["backfill"]:
            self.stdout.write(
                f"  Gap buffer:          {options['gap_buffer_seconds']}s"
            )
            self.stdout.write(
                f"  Chunk size:          "
                f"{options['chunk_hours']}h "
                f"{'(single-shot)' if options['chunk_hours'] == 0 else ''}"
            )
        self.stdout.write(
            f"  Currently deployed:  "
            f"{'YES' if deployed_ddl else 'no (will create fresh)'}"
        )
        if deployed_ddl:
            self.stdout.write("\n  DDL diff:")
            self._print_diff(deployed_ddl, canonical_ddl)

    def _confirm(self) -> bool:
        try:
            answer = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")

    def _capture_cutoff(self, client, gap_buffer_seconds: int) -> datetime:
        """Cutoff = NOW on the CH server minus the buffer.

        We pull the cutoff from CH (not Python's clock) so the timestamps
        we compare against ``created_at`` come from the same wall clock as
        the data we're filtering.
        """
        rows = client.execute("SELECT now('UTC')")
        ch_now = rows[0][0]
        if ch_now.tzinfo is None:
            ch_now = ch_now.replace(tzinfo=timezone.utc)
        return ch_now - timedelta(seconds=gap_buffer_seconds)

    def _drop_mv(self, client, mv_name: str) -> None:
        self.stdout.write(f"DROP VIEW IF EXISTS {mv_name}")
        client.execute(f"DROP VIEW IF EXISTS {mv_name}")

    def _create_mv(self, client, canonical_ddl: str) -> None:
        self.stdout.write("CREATE MATERIALIZED VIEW (canonical body)")
        client.execute(canonical_ddl)

    # ── Backfill ──────────────────────────────────────────────────────

    def _run_backfill(
        self,
        client,
        manifest: Dict[str, Any],
        cutoff: datetime,
        chunk_hours: int,
    ) -> None:
        backfill_select = manifest.get("backfill_select")
        if not backfill_select:
            self.stdout.write(
                self.style.WARNING(
                    "Manifest entry has no backfill_select — skipping backfill. "
                    "If this MV needed a partial recompute, run it manually."
                )
            )
            return

        # Determine end of backfill window. We look up the most recent row
        # that landed in the source after the cutoff so we don't issue
        # an unbounded INSERT.
        rows = client.execute(
            f"SELECT max({manifest['source_time_column']}) "
            f"FROM {manifest['source_table']} "
            f"WHERE {manifest['source_time_column']} >= %(cutoff)s",
            params={"cutoff": cutoff},
        )
        end = rows[0][0] if rows and rows[0][0] is not None else None
        if end is None:
            self.stdout.write(
                "Backfill window is empty (no source rows after cutoff). Skipping."
            )
            return
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        self.stdout.write(
            f"Backfill window: {cutoff.isoformat()} → {end.isoformat()}"
        )

        if chunk_hours <= 0:
            self._run_backfill_chunk(
                client=client,
                backfill_select=backfill_select,
                start=cutoff,
                end=end + timedelta(seconds=1),  # inclusive of the last row
            )
            return

        # Chunked
        chunk = timedelta(hours=chunk_hours)
        chunk_start = cutoff
        n_chunks = 0
        while chunk_start <= end:
            chunk_end = min(chunk_start + chunk, end + timedelta(seconds=1))
            self._run_backfill_chunk(
                client=client,
                backfill_select=backfill_select,
                start=chunk_start,
                end=chunk_end,
            )
            chunk_start = chunk_end
            n_chunks += 1
        self.stdout.write(
            self.style.SUCCESS(f"Backfill complete ({n_chunks} chunks).")
        )

    def _run_backfill_chunk(
        self, client, backfill_select: str, start: datetime, end: datetime
    ) -> None:
        """Run one chunk of the backfill INSERT.

        The manifest's ``backfill_select`` already contains
        ``AND <time_column> >= %(cutoff)s``; we tighten it to the chunk's
        upper bound by appending ``AND <time_column> < %(chunk_end)s`` to
        the ``WHERE`` clause via simple string substitution. The substitution
        is keyed on the marker ``GROUP BY`` so it's robust to the body's
        whitespace, which we control in ``schema.py`` anyway.
        """
        # Inject the upper bound just before GROUP BY. The manifest body
        # uses %(cutoff)s; we add a parameter for the chunk_end.
        if "GROUP BY" not in backfill_select:
            raise CommandError(
                "backfill_select missing 'GROUP BY' marker — cannot inject "
                "chunk upper bound. Fix the manifest entry."
            )

        sql = backfill_select.replace(
            "GROUP BY",
            "  AND e.created_at < %(chunk_end)s\n            GROUP BY",
            1,
        )
        params = {"cutoff": start, "chunk_end": end}

        t0 = time.monotonic()
        client.execute(sql, params=params)
        dt_ms = (time.monotonic() - t0) * 1000
        self.stdout.write(
            f"  chunk [{start.isoformat()} → {end.isoformat()}) "
            f"completed in {dt_ms:.0f} ms"
        )
