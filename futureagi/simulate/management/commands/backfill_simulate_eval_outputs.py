"""Add ``output_scalar`` / ``output_dict`` keys to each per-template entry
in ``CallExecution.eval_outputs`` and ``CallExecutionSnapshot.eval_outputs``.
Preserves the original ``output`` verbatim. Idempotent.

Usage: ``python manage.py backfill_simulate_eval_outputs [--dry-run] [--limit N] [--since YYYY-MM-DD] [--skip-snapshots]``"""

from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.utils import timezone

from evaluations.engine.normalize import coerce_to_legacy_scalar
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import CallExecution, CallExecutionSnapshot


def _resolve_config_outputs(eval_config_ids):
    """Bulk-resolve ``config["output"]`` for the SimulateEvalConfig ids
    appearing in this batch — one query instead of one-per-cell.
    """
    out: dict[str, str] = {}
    if not eval_config_ids:
        return out
    qs = SimulateEvalConfig.objects.select_related("eval_template").filter(
        id__in=eval_config_ids
    )
    for sec in qs:
        try:
            out[str(sec.id)] = (sec.eval_template.config or {}).get("output", "score")
        except (AttributeError, TypeError):
            out[str(sec.id)] = "score"
    return out


def _normalize_entry(entry, config_output):
    """Return a copy of ``entry`` with ``output_scalar`` and ``output_dict``
    populated if missing. Returns ``None`` when no change is needed.
    """
    if not isinstance(entry, dict):
        return None
    output = entry.get("output")
    new_scalar = coerce_to_legacy_scalar(output, config_output)
    new_dict = output if isinstance(output, dict) else None
    if entry.get("output_scalar") == new_scalar and entry.get("output_dict") == new_dict:
        return None
    new_entry = dict(entry)
    new_entry["output_scalar"] = new_scalar
    new_entry["output_dict"] = new_dict
    return new_entry


def _process_row(row, eval_outputs_attr):
    eval_outputs = getattr(row, eval_outputs_attr) or {}
    if not eval_outputs:
        return False
    config_outputs = _resolve_config_outputs(list(eval_outputs.keys()))

    new_outputs: dict = {}
    changed = False
    for eval_config_id, entry in eval_outputs.items():
        co = config_outputs.get(str(eval_config_id), "score")
        updated_entry = _normalize_entry(entry, co)
        if updated_entry is not None:
            new_outputs[eval_config_id] = updated_entry
            changed = True
        else:
            new_outputs[eval_config_id] = entry
    if changed:
        setattr(row, eval_outputs_attr, new_outputs)
    return changed


class Command(BaseCommand):
    help = (
        "Add output_scalar / output_dict to existing CallExecution.eval_outputs "
        "(and CallExecutionSnapshot.eval_outputs) entries."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--since", type=str, default=None)
        parser.add_argument(
            "--skip-snapshots",
            action="store_true",
            help="Only process CallExecution, not CallExecutionSnapshot.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        since = options.get("since")
        skip_snapshots = options["skip_snapshots"]

        since_dt = None
        if since:
            since_dt = timezone.make_aware(
                datetime.combine(
                    datetime.strptime(since, "%Y-%m-%d").date(), time.min
                )
            )

        # ── CallExecution ────────────────────────────────────────────────
        ce_scanned, ce_updated = self._backfill(
            model=CallExecution,
            attr="eval_outputs",
            since_dt=since_dt,
            limit=limit,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        # ── CallExecutionSnapshot (frozen rerun copies) ──────────────────
        snap_scanned = snap_updated = 0
        if not skip_snapshots:
            snap_scanned, snap_updated = self._backfill(
                model=CallExecutionSnapshot,
                attr="eval_outputs",
                since_dt=since_dt,
                limit=limit,
                batch_size=batch_size,
                dry_run=dry_run,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"call_execution scanned={ce_scanned} updated={ce_updated} | "
                f"snapshot scanned={snap_scanned} updated={snap_updated} | "
                f"dry_run={dry_run}"
            )
        )

    def _backfill(self, *, model, attr, since_dt, limit, batch_size, dry_run):
        qs = model.objects.filter(**{f"{attr}__isnull": False}).exclude(
            **{f"{attr}": {}}
        ).order_by("id")
        if since_dt is not None:
            qs = qs.filter(created_at__gte=since_dt)
        if limit:
            qs = qs[:limit]

        scanned = 0
        updated = 0
        batch: list = []

        for row in qs.iterator(chunk_size=batch_size):
            scanned += 1
            if _process_row(row, attr):
                updated += 1
                batch.append(row)
            if not dry_run and len(batch) >= batch_size:
                model.objects.bulk_update(batch, [attr])
                batch.clear()

        if not dry_run and batch:
            model.objects.bulk_update(batch, [attr])

        return scanned, updated
