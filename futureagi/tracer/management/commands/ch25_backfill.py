"""
ch25_backfill — copy historical `tracer_observation_span` rows from PG into the
new CH 25.3 `spans` table.

Resumable: state lives in CH `backfill_checkpoints`. Killing and re-running
picks up where it left off; ReplacingMergeTree handles dedup of any rows that
were already inserted.

Operator UX:
    python manage.py ch25_backfill                              # everything
    python manage.py ch25_backfill --project-id <UUID>          # one project
    python manage.py ch25_backfill --since 2026-04-09T00:00:00 --until 2026-04-10
    python manage.py ch25_backfill --dry-run                    # count only
    python manage.py ch25_backfill --status                     # progress check

Memory-constrained nodes:
    python manage.py ch25_backfill --optimize-every 500 --max-memory-per-query 4000000000
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config


class Command(BaseCommand):
    help = "Backfill PG tracer_observation_span → CH 25.3 spans (resumable)."

    def add_arguments(self, parser):
        # Run controls
        parser.add_argument("--batch-size", type=int, default=50_000)
        parser.add_argument("--project-id", action="append", default=None,
                            help="Limit to this project (repeatable). Default: all projects.")
        parser.add_argument("--since", default=None,
                            help="Only windows where start_time >= this (ISO8601 UTC)")
        parser.add_argument("--until", default=None,
                            help="Only windows where start_time < this (ISO8601 UTC)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Discover + count rows without writing to CH")
        parser.add_argument("--max-windows", type=int, default=None,
                            help="Cap windows per run (smoke testing)")
        parser.add_argument("--status", action="store_true",
                            help="Print checkpoint summary and exit")
        # Memory tunables (per DECISIONS #021/#025)
        parser.add_argument("--optimize-every", type=int, default=0,
                            help="Run OPTIMIZE TABLE every N windows (default 0=off). "
                                 "Bounds active-part count on small CH nodes.")
        parser.add_argument("--max-memory-per-query", type=int, default=0,
                            help="Apply max_memory_usage setting to every CH connection (bytes).")

    def handle(self, *args, **opts):
        # Import here so `manage.py help` doesn't require CH driver to be installed.
        # The backfill orchestrator script lives next to v2/ for now; it's the
        # validated implementation. After full integration it'll move into
        # tracer/services/clickhouse/v2/backfill.py as a proper module.
        # For this phase we shell out so we don't have to refactor the script's
        # main() into a callable with kwargs.
        import subprocess

        cfg = get_v2_config()
        pg = settings.DATABASES["default"]

        # Locate the orchestrator. Phase 1: still in planning/.../scripts/.
        # Phase 2 (next): copy it next to v2/ and call its main() directly.
        from tracer.services.clickhouse import v2 as v2_pkg
        candidate = Path(v2_pkg.__file__).resolve().parent / "backfill.py"
        if not candidate.is_file():
            # Fall back to the migration-repo source until the copy lands.
            candidate = Path(settings.BASE_DIR).parent.parent / "planning" / "clickhouse-rearch" / "migration" / "scripts" / "backfill_pg_to_ch.py"
        if not candidate.is_file():
            raise CommandError(f"backfill script not found at {candidate}")

        argv = [
            sys.executable, str(candidate),
            # Coerce passwords to str — None (e.g. legacy CLICKHOUSE.CH_PASSWORD
            # unset) makes subprocess.call raise "expected str, not NoneType".
            "--pg-host", pg["HOST"], "--pg-port", str(pg["PORT"]),
            "--pg-user", pg["USER"], "--pg-pass", pg["PASSWORD"] or "", "--pg-db", pg["NAME"],
            "--ch-host", cfg["host"],
            "--ch-http-port", str(cfg["http_port"]),
            "--ch-tcp-port", str(cfg["tcp_port"]),
            "--ch-user", cfg["user"],
            "--ch-pass", cfg["password"] or "",
            "--ch-db", cfg["database"],
            "--batch-size", str(opts["batch_size"]),
        ]
        if opts["status"]:
            argv.append("--status")
        if opts["dry_run"]:
            argv.append("--dry-run")
        if opts["project_id"]:
            for pid in opts["project_id"]:
                argv.extend(["--project-id", pid])
        if opts["since"]:
            argv.extend(["--since", opts["since"]])
        if opts["until"]:
            argv.extend(["--until", opts["until"]])
        if opts["max_windows"] is not None:
            argv.extend(["--max-windows", str(opts["max_windows"])])
        if opts["optimize_every"]:
            argv.extend(["--optimize-every", str(opts["optimize_every"])])
        if opts["max_memory_per_query"]:
            argv.extend(["--max-memory-per-query", str(opts["max_memory_per_query"])])

        rc = subprocess.call(argv)
        if rc != 0:
            raise CommandError(f"ch25_backfill exited {rc} — see structured logs above. "
                               f"3=at-least-one-window-raised, 4=count-mismatch (failed_validation).")
        self.stdout.write(self.style.SUCCESS("✓ backfill clean."))
