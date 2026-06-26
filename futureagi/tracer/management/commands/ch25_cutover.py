"""
ch25_cutover — single-command orchestrator for the production cutover.

What it does (each phase exits non-zero if not safe to proceed):
    1. Schema:    ensure CH 25.3 schema is current via ch25_apply_schema
    2. Backfill:  copy PG → CH (resumable; skip if --backfill=no)
    3. Validate:  Layer A + B + C parity check
    4. Flag:      print exact env-var to flip for read cutover
                  (does NOT auto-flip — operator does that via deploy)

Operator UX:
    python manage.py ch25_cutover                          # interactive (asks before each phase)
    python manage.py ch25_cutover --yes                    # non-interactive (CI-friendly)
    python manage.py ch25_cutover --phase=schema           # one phase only
    python manage.py ch25_cutover --backfill=no            # skip backfill (already done)
    python manage.py ch25_cutover --sample-size 5000       # bigger Layer B sample

This is intentionally a thin wrapper that calls the other ch25_* commands.
Each phase's exit code propagates; the operator can resume from any failed
phase by re-running with --phase=<N> after triage.
"""
from __future__ import annotations

import sys
import time
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


PHASES = ("schema", "backfill", "validate", "flag")


class Command(BaseCommand):
    help = "Single-command CH 25.3 cutover orchestrator."

    def add_arguments(self, parser):
        parser.add_argument("--phase", choices=PHASES, default=None,
                            help=f"Run only this phase (one of {PHASES}); default = all in order")
        parser.add_argument("--yes", action="store_true",
                            help="Skip interactive confirmation between phases")
        parser.add_argument("--backfill", choices=("yes", "no"), default="yes",
                            help="Skip backfill phase (use after a one-off backfill)")
        parser.add_argument("--sample-size", type=int, default=1000,
                            help="Layer B sample size for the validate phase")
        parser.add_argument("--report", default=None,
                            help="Write validate report to this path")

    def handle(self, *args, **opts):
        phases_to_run = [opts["phase"]] if opts["phase"] else list(PHASES)

        for phase in phases_to_run:
            self.stdout.write(self.style.HTTP_INFO(f"\n=== phase: {phase} ==="))
            if not opts["yes"] and phase in ("backfill", "flag"):
                resp = input(f"proceed with phase '{phase}'? [y/N] ").strip().lower()
                if resp not in ("y", "yes"):
                    self.stdout.write(self.style.WARNING(f"skipped phase '{phase}' on operator decline"))
                    continue
            t0 = time.time()
            try:
                self._run_phase(phase, opts)
            except CommandError as e:
                raise CommandError(f"phase '{phase}' failed: {e}")
            self.stdout.write(self.style.SUCCESS(
                f"✓ phase '{phase}' completed in {time.time() - t0:.1f}s"))

        self.stdout.write(self.style.SUCCESS(
            "\n✓ all requested phases done. See ch25_cutover output above for next-step env-var flip."))

    def _run_phase(self, phase: str, opts: dict) -> None:
        if phase == "schema":
            call_command("ch25_apply_schema")
        elif phase == "backfill":
            if opts["backfill"] == "no":
                self.stdout.write("(skipped per --backfill=no)")
                return
            # Sensible defaults for resource-constrained nodes — operator can
            # override via `manage.py ch25_backfill` directly for more control.
            call_command("ch25_backfill", optimize_every=500)
        elif phase == "validate":
            kwargs = {"all": True, "sample_size": opts["sample_size"]}
            if opts.get("report"):
                kwargs["report"] = opts["report"]
            call_command("ch25_validate", **kwargs)
        elif phase == "flag":
            self.stdout.write(self.style.HTTP_INFO(
                "Read cutover: set the following env var on the next backend deploy:"))
            self.stdout.write(self.style.NOTICE(
                "  EVAL_SPAN_READ_SOURCE=clickhouse"))
            self.stdout.write(
                "Rollback: flip back to 'postgres' and redeploy; PG path remains alive "
                "until the legacy CDC pipeline is dropped (see RUNBOOK §4.3).")
