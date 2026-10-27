"""
ch25_doctor — is it safe to remove the PG observation_span dual-write?

Answers one question: **is every eligible PG ``tracer_observation_span`` row
present and faithful in CH ``spans``?** If yes, you're clear to cut over. If
no, ``--fix`` heals the gaps by re-backfilling exactly the short windows
(idempotent — ReplacingMergeTree dedups), then re-checks.

    python manage.py ch25_doctor                      # CHECK: go / no-go verdict (Layer A counts)
    python manage.py ch25_doctor --deep               # also Layer B field-equality on a sample
    python manage.py ch25_doctor --since 2026-03-01    # scope to a window (e.g. the retention window)
    python manage.py ch25_doctor --fix                # HEAL: re-backfill short buckets until clean
    python manage.py ch25_doctor --fix --max-iterations 5

Exit codes:
    0  SAFE   — CH is a complete, faithful copy for the checked scope
    2  NOT SAFE — count/field gaps remain, or a prerequisite is unmet

Note: "complete" is scoped to the BACKFILL ELIGIBILITY rule
(``deleted=false AND start_time IS NOT NULL``) and to any ``--since/--until``
window you pass. If the v2 ``spans`` schema still carries the 90-day DELETE
TTL, history older than retention is intentionally absent — scope the check
to the retention window or the doctor will (correctly) report those buckets
as short.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand


# Models whose ForeignKey -> ObservationSpan must be decoupled BEFORE the PG
# write can be removed: once a new span has no PG row, these features break for
# it — and the physical DROP TABLE fails on the FK constraint regardless of
# on_delete. The doctor refuses a clean bill of health while any still FK.
#
# This is the COMPLETE set of FK referrers of ObservationSpan (authoritative:
# `ObservationSpan._meta.related_objects`). `Score` and `QueueItem` were
# previously missing — both hold a real FK to tracer_observation_span
# (SET_NULL), so the drop would fail even though the doctor reported "safe".
# See internal-docs SCALE_ARCHITECTURE.md §5.
_FK_DEPENDENT_MODELS = [
    ("tracer", "SpanNotes"),           # .span             CASCADE, NOT NULL (hard block)
    ("tracer", "TraceAnnotation"),     # .observation_span CASCADE
    ("tracer", "ErrorClusterTraces"),  # .span             CASCADE
    ("tracer", "EvalLogger"),          # .observation_span CASCADE
    ("model_hub", "Score"),            # .observation_span SET_NULL
    ("model_hub", "QueueItem"),        # .observation_span SET_NULL
]


class Command(BaseCommand):
    help = "Verify (and optionally heal) CH parity so PG dual-write can be removed."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true",
                            help="Heal: re-backfill short buckets until parity holds.")
        parser.add_argument("--deep", action="store_true",
                            help="Also run Layer B field-equality (slower).")
        parser.add_argument("--sample-size", type=int, default=500)
        parser.add_argument("--since", default=None, help="ISO8601 UTC lower bound on start_time")
        parser.add_argument("--until", default=None, help="ISO8601 UTC upper bound on start_time")
        parser.add_argument("--project-id", action="append", default=None)
        parser.add_argument("--max-iterations", type=int, default=3,
                            help="--fix: max heal passes before giving up.")
        parser.add_argument("--skip-prereqs", action="store_true",
                            help="Skip the FK-dependency prerequisite check (data-only verdict).")

    # ── helpers ──────────────────────────────────────────────────────────────
    def _manage(self, *argv: str) -> int:
        """Run another management command as a subprocess (matches the proven
        operator path; avoids call_command arg-binding quirks for the
        shell-out ch25_* commands)."""
        return subprocess.call([sys.executable, "manage.py", *argv])

    def _ch_client(self):
        import clickhouse_connect
        from tracer.services.clickhouse.v2 import get_v2_config
        cfg = get_v2_config()
        return clickhouse_connect.get_client(
            host=cfg["host"], port=cfg["http_port"],
            username=cfg["user"], password=cfg["password"] or "",
            database=cfg["database"],
        )

    def _clear_checkpoints(self, project_id: str, day: datetime):
        """ch25_backfill SKIPS windows already in backfill_checkpoints (it's a
        resume optimisation). To re-fill a bucket whose CH rows were lost, the
        checkpoint for that (project, day) must be cleared first, or the
        re-backfill is a no-op."""
        try:
            ch = self._ch_client()
            ch.command(
                "ALTER TABLE backfill_checkpoints DELETE "
                "WHERE project_id = %(p)s AND toDate(hour_bucket) = toDate(%(d)s)",
                parameters={"p": project_id, "d": day.date().isoformat()},
                settings={"mutations_sync": 1},
            )
        except Exception as e:
            self.stderr.write(f"   (checkpoint clear failed for {project_id[:8]}/{day.date()}: {e})")

    def _validate(self, opts, *, deep: bool) -> dict:
        """Run ch25_validate --report, return its JSON report."""
        tmp = Path(tempfile.mkstemp(suffix=".json", prefix="ch25_doctor_")[1])
        argv = ["ch25_validate", "--counts", "--report", str(tmp)]
        if deep:
            argv += ["--deep", "--sample-size", str(opts["sample_size"])]
        for pid in (opts["project_id"] or []):
            argv += ["--project-id", pid]
        if opts["since"]:
            argv += ["--since", opts["since"]]
        if opts["until"]:
            argv += ["--until", opts["until"]]
        self._manage(*argv)  # exit 2 on fail is fine; the report carries detail
        try:
            return json.loads(tmp.read_text())
        except Exception as e:
            self.stderr.write(f"could not read validate report: {e}")
            return {"overall_status": "fail", "layers": {}}
        finally:
            tmp.unlink(missing_ok=True)

    def _short_buckets(self, report: dict) -> list[dict]:
        """(project, day) buckets where CH has FEWER rows than PG (delta < 0)."""
        counts = report.get("layers", {}).get("counts", {})
        return [d for d in counts.get("diff_examples", []) if d.get("delta", 0) < 0]

    def _rebackfill_bucket(self, b: dict):
        day = datetime.fromisoformat(b["day"])
        until = (day + timedelta(days=1)).isoformat()
        self.stdout.write(f"   → re-backfill project={b['project_id'][:8]} day={b['day'][:10]} "
                          f"(pg={b['pg']} ch={b['ch']})")
        self._clear_checkpoints(b["project_id"], day)   # else the backfill skips the window
        self._manage("ch25_backfill", "--project-id", b["project_id"],
                     "--since", day.isoformat(), "--until", until)

    def _check_prereqs(self) -> list[str]:
        """Code-level prerequisites that data parity can't see."""
        from django.apps import apps
        problems = []
        for app_label, model_name in _FK_DEPENDENT_MODELS:
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError:
                continue
            for f in model._meta.get_fields():
                if getattr(f, "is_relation", False) and getattr(f, "related_model", None) is not None:
                    if f.related_model.__name__ == "ObservationSpan":
                        nullable = getattr(f, "null", True)
                        problems.append(
                            f"{model_name}.{f.name} still FK→ObservationSpan"
                            f"{'' if nullable else ' (NOT NULL — hard block)'}")
        return problems

    # ── main ─────────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        prereqs = [] if opts["skip_prereqs"] else self._check_prereqs()

        if opts["fix"]:
            self.stdout.write(self.style.MIGRATE_HEADING("ch25_doctor --fix: healing CH gaps"))
            healed = 0
            for it in range(1, opts["max_iterations"] + 1):
                report = self._validate(opts, deep=False)
                shorts = self._short_buckets(report)
                counts = report.get("layers", {}).get("counts", {})
                self.stdout.write(f"iteration {it}: buckets_diff={counts.get('buckets_diff', '?')} "
                                  f"short(ch<pg)={len(shorts)}")
                if not shorts:
                    self.stdout.write(self.style.SUCCESS("no short buckets — CH is complete."))
                    break
                for b in shorts:
                    self._rebackfill_bucket(b)
                    healed += 1
            else:
                self.stdout.write(self.style.WARNING(
                    f"reached max-iterations; re-run or widen scope. healed {healed} bucket(s)."))
            self.stdout.write(f"healed {healed} bucket(s) total.")

        # Final verdict (always runs the check)
        report = self._validate(opts, deep=opts["deep"])
        overall = report.get("overall_status", "fail")
        counts = report.get("layers", {}).get("counts", {})
        deep = report.get("layers", {}).get("deep_equal", {})

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=== ch25_doctor verdict ==="))
        self.stdout.write(f"Layer A counts : {counts.get('status','?')} "
                          f"({counts.get('buckets_matched','?')}/{counts.get('buckets_checked','?')} buckets, "
                          f"{counts.get('buckets_diff','?')} diff)")
        if opts["deep"]:
            self.stdout.write(f"Layer B deep   : {deep.get('status','?')} "
                              f"({deep.get('matched','?')}/{deep.get('sampled','?')} matched, "
                              f"{deep.get('diff_rows','?')} diff, {deep.get('missing_in_ch','?')} missing)")
        if prereqs:
            self.stdout.write(self.style.WARNING("Prerequisites NOT met (decouple before removing PG write):"))
            for p in prereqs:
                self.stdout.write(f"  • {p}")

        data_ok = overall == "pass"
        safe = data_ok and not prereqs
        self.stdout.write("")
        if safe:
            self.stdout.write(self.style.SUCCESS(
                "✓ SAFE — CH is a complete, faithful copy for the checked scope and no "
                "FK prerequisites block removal. You may proceed to ch25_remove_pg."))
        else:
            why = []
            if not data_ok:
                why.append("CH parity gaps (run with --fix)")
            if prereqs:
                why.append(f"{len(prereqs)} unmet FK prerequisite(s)")
            self.stdout.write(self.style.ERROR("✗ NOT SAFE — " + "; ".join(why)))

        if not safe:
            raise SystemExit(2)
