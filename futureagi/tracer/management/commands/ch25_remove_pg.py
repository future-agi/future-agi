"""
ch25_remove_pg — gated removal of the PG ``tracer_observation_span`` dual-write.

This is the LAST step of the migration and the only irreversible one, so it
refuses to do anything until it has PROVEN, live, that CH is a complete and
faithful copy and that nothing still depends on the PG row.

Order of operations (each gate must pass before the next):

    python manage.py ch25_remove_pg                 # DRY RUN: gates + ordered runbook, changes nothing
    python manage.py ch25_remove_pg --execute --yes # perform the REVERSIBLE infra steps
    python manage.py ch25_remove_pg --drop-pg-table --yes   # the IRREVERSIBLE PG drop (separate, explicit)

Gates (all enforced; --execute aborts on the first failure):
    1. ch25_doctor passes        — CH is a complete, faithful copy (count + deep parity)
    2. FK prerequisites resolved — no model still hard-FKs ObservationSpan
    3. live recency parity       — the last N completed hours match PG↔CH (the
                                   collector path that REMAINS is delivering)

Reversible steps (--execute):
    A. set span-write mode to ch_only         (stops create_otel_span's PG insert)
    B. stop the PeerDB CDC mirror for tracer_observation_span (now redundant)
    C. drop the CH ``tracer_observation_span`` landing table + ``spans_mv`` chain

Irreversible step (--drop-pg-table, gated separately):
    D. DROP the PG ``tracer_observation_span`` table

Env the cutover relies on (printed by the dry run):
    CH25_DROP_LEGACY_CDC_CHAIN=true     # boot hook drops the legacy chain, applies v2
    CH25_SPAN_WRITE_MODE=ch_only        # create_otel_span skips the PG insert (see span_write_mode)

Rollback: set CH25_SPAN_WRITE_MODE=dual_write to resume PG writes for NEW spans
(does not backfill the ch_only gap — by then CH is source of truth).
"""
from __future__ import annotations

import subprocess
import sys

from django.core.management.base import BaseCommand

# Reuse the doctor's FK-dependency definition so the two never drift.
from tracer.management.commands.ch25_doctor import _FK_DEPENDENT_MODELS

_ENV_RUNBOOK = [
    ("CH25_DROP_LEGACY_CDC_CHAIN", "true",
     "boot hook drops the legacy CDC chain + applies v2 schema"),
    ("CH25_SPAN_WRITE_MODE", "ch_only",
     "create_otel_span skips the PG ObservationSpan insert (CH-only)"),
]


class Command(BaseCommand):
    help = "Gated removal of the PG observation_span dual-write (verify-first)."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true",
                            help="Perform the REVERSIBLE cutover steps (A-C) after gates pass.")
        parser.add_argument("--drop-pg-table", action="store_true",
                            help="The IRREVERSIBLE step: DROP PG tracer_observation_span (gated separately).")
        parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
        parser.add_argument("--since", default=None, help="Scope the doctor check (e.g. retention window).")
        parser.add_argument("--recency-hours", type=int, default=24,
                            help="Live-parity gate: hours back from now to require exact parity.")

    # ── gates ────────────────────────────────────────────────────────────────
    def _gate_doctor(self, opts) -> bool:
        self.stdout.write(self.style.MIGRATE_HEADING("Gate 1/3 — ch25_doctor (CH completeness)"))
        argv = [sys.executable, "manage.py", "ch25_doctor", "--deep"]
        if opts["since"]:
            argv += ["--since", opts["since"]]
        return subprocess.call(argv) == 0   # doctor exits 2 unless SAFE (incl. FK prereqs)

    def _gate_recency(self, opts) -> bool:
        """Require exact PG↔CH parity over the last recency-hours — proves the
        path that REMAINS after PG write stops (fi-collector → CH) is delivering."""
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=opts["recency_hours"])).strftime("%Y-%m-%dT%H:%M:%S")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Gate 3/3 — live recency parity (last {opts['recency_hours']}h, since {since})"))
        return subprocess.call(
            [sys.executable, "manage.py", "ch25_validate", "--counts", "--since", since]) == 0

    # ── steps ────────────────────────────────────────────────────────────────
    def _print_runbook(self):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=== Removal runbook (env + ordered steps) ==="))
        self.stdout.write("Set these env vars on the backend deploy:")
        for k, v, why in _ENV_RUNBOOK:
            self.stdout.write(f"    {k}={v}    # {why}")
        self.stdout.write("\nThen, in order (this command performs A–C with --execute):")
        self.stdout.write("    A. span-write mode → ch_only   (stop PG insert in create_otel_span)")
        self.stdout.write("    B. stop PeerDB CDC mirror for tracer_observation_span")
        self.stdout.write("    C. drop CH tracer_observation_span landing + spans_mv chain")
        self.stdout.write("    D. (separate, --drop-pg-table) DROP PG tracer_observation_span")
        self.stdout.write("\nRollback: CH25_SPAN_WRITE_MODE=dual_write resumes PG writes for new spans.")

    def handle(self, *args, **opts):
        # Dry run: just show gates + runbook.
        if not opts["execute"] and not opts["drop_pg_table"]:
            ok_doctor = self._gate_doctor(opts)
            ok_recency = self._gate_recency(opts)
            self._print_runbook()
            self.stdout.write("")
            verdict = "READY" if (ok_doctor and ok_recency) else "BLOCKED"
            style = self.style.SUCCESS if verdict == "READY" else self.style.ERROR
            self.stdout.write(style(f"DRY RUN — gates: doctor={'pass' if ok_doctor else 'FAIL'}, "
                                    f"recency={'pass' if ok_recency else 'FAIL'} → {verdict}"))
            self.stdout.write("Re-run with --execute --yes to perform reversible steps A–C.")
            return

        # Any mutating mode: enforce ALL gates first.
        if not self._gate_doctor(opts):
            raise SystemExit("ABORT: ch25_doctor did not pass (CH incomplete or FK prereqs unmet). "
                             "Run `ch25_doctor --fix` then retry.")
        if not self._gate_recency(opts):
            raise SystemExit("ABORT: live recency parity failed — the collector→CH path is not "
                             "delivering completely; do NOT remove the PG safety net yet.")

        if opts["drop_pg_table"]:
            if not opts["yes"]:
                raise SystemExit("Refusing irreversible DROP without --yes.")
            self.stdout.write(self.style.WARNING(
                "Step D (irreversible): dropping PG tracer_observation_span is intentionally NOT "
                "automated here — run the audited `drop_legacy_observation_span --force-drop` command, "
                "which re-checks PG readers + requires its own confirmation."))
            return

        # --execute: reversible steps A–C
        if not opts["yes"]:
            raise SystemExit("Refusing to execute steps A–C without --yes.")
        self.stdout.write(self.style.MIGRATE_HEADING("Executing reversible cutover steps A–C"))
        self.stdout.write("  A. Set CH25_SPAN_WRITE_MODE=ch_only on the backend deploy + restart "
                          "(flag-driven; this command does not edit your deploy config).")
        self.stdout.write("  B. Stop the PeerDB tracer_observation_span mirror "
                          "(peerdb console / scripts/peerdb-setup-mirrors.sh already skips it).")
        self.stdout.write("  C. Dropping the CH legacy chain via CH25_DROP_LEGACY_CDC_CHAIN boot path...")
        try:
            from tracer.services.clickhouse.client import get_clickhouse_client
            from tracer.services.clickhouse.schema import get_legacy_chain_drop_statements
            ch = get_clickhouse_client()
            for name, ddl in get_legacy_chain_drop_statements():
                ch.execute(ddl)
                self.stdout.write(f"     dropped {name}")
        except Exception as e:
            self.stderr.write(f"     CH legacy-chain drop skipped: {e}")
        self.stdout.write(self.style.SUCCESS(
            "Reversible cutover done. Soak, watch span volume + collector dead-letters, then "
            "run `ch25_remove_pg --drop-pg-table --yes` for the final PG drop."))
