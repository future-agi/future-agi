"""
ch25_validate — 3-layer parity check between PG `tracer_observation_span` and
CH `spans`. Run after backfill, run before cutover, run as a sanity check
whenever you doubt the data.

Layers:
    A — counts: per-(project, day) row count parity (PG vs CH FINAL)
    B — deep-equal: N random spans, every field compared after adapter round-trip
    C — query parity: 5 representative analytical queries on both sides

Operator UX:
    python manage.py ch25_validate --all                                # all layers
    python manage.py ch25_validate --counts                             # A only (fastest)
    python manage.py ch25_validate --deep --sample-size 1000            # B only
    python manage.py ch25_validate --queries                            # C only
    python manage.py ch25_validate --all --report /tmp/validation.json  # save report

Exit codes match the script: 0=pass, 2=fail.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config


class Command(BaseCommand):
    help = "3-layer parity validation between PG and CH 25.3 spans."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Run all three layers")
        parser.add_argument("--counts", action="store_true", help="Layer A only")
        parser.add_argument("--deep", action="store_true", help="Layer B only")
        parser.add_argument("--queries", action="store_true", help="Layer C only")
        parser.add_argument("--sample-size", type=int, default=1000,
                            help="Layer B sample size (default 1000)")
        parser.add_argument("--seed", type=int, default=None,
                            help="RNG seed for reproducible Layer B samples")
        parser.add_argument("--project-id", action="append", default=None)
        parser.add_argument("--since", default=None)
        parser.add_argument("--until", default=None)
        parser.add_argument("--report", default=None,
                            help="Write JSON report to this path (otherwise stdout)")

    def handle(self, *args, **opts):
        cfg = get_v2_config()
        pg = settings.DATABASES["default"]

        from tracer.services.clickhouse import v2 as v2_pkg
        candidate = Path(v2_pkg.__file__).resolve().parent / "validate.py"
        if not candidate.is_file():
            candidate = Path(settings.BASE_DIR).parent.parent / "planning" / "clickhouse-rearch" / "migration" / "scripts" / "validate_migration.py"
        if not candidate.is_file():
            raise CommandError(f"validator script not found at {candidate}")

        argv = [
            sys.executable, str(candidate),
            # Coerce passwords to str — a None (e.g. legacy CLICKHOUSE.CH_PASSWORD
            # unset) makes subprocess.call raise "expected str, not NoneType".
            "--pg-host", pg["HOST"], "--pg-port", str(pg["PORT"]),
            "--pg-user", pg["USER"], "--pg-pass", pg["PASSWORD"] or "", "--pg-db", pg["NAME"],
            "--ch-host", cfg["host"], "--ch-http-port", str(cfg["http_port"]),
            "--ch-user", cfg["user"], "--ch-pass", cfg["password"] or "",
            "--ch-db", cfg["database"],
        ]
        if opts["all"]:
            argv.append("--all")
        if opts["counts"]:
            argv.append("--counts")
        if opts["deep"]:
            argv.extend(["--deep", "--sample-size", str(opts["sample_size"])])
        if opts["queries"]:
            argv.append("--queries")
        if opts["seed"] is not None:
            argv.extend(["--seed", str(opts["seed"])])
        if opts["project_id"]:
            for pid in opts["project_id"]:
                argv.extend(["--project-id", pid])
        if opts["since"]:
            argv.extend(["--since", opts["since"]])
        if opts["until"]:
            argv.extend(["--until", opts["until"]])
        if opts["report"]:
            argv.extend(["--report", opts["report"]])

        rc = subprocess.call(argv)
        if rc == 0:
            self.stdout.write(self.style.SUCCESS("✓ validation PASSED across all requested layers."))
        elif rc == 2:
            raise CommandError("validation FAILED — see report for diff_examples.")
        else:
            raise CommandError(f"validator exited unexpectedly with code {rc}")
