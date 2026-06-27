"""Backfill the typed output columns on ``Evaluation`` rows."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import structlog
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from evaluations.engine.normalize import parse_legacy_value, resolve_eval_axes
from model_hub.models.evaluation import Evaluation

logger = structlog.get_logger(__name__)

_DEFAULT_BATCH_SIZE = 1000
_SAMPLE_COUNT = 5
_PROGRESS_EVERY_N_BATCHES = 10
_UPDATE_FIELDS = ["output_bool", "output_float", "output_str_list", "output_str"]


class Command(BaseCommand):
    help = "Backfill output_bool / output_float / output_str_list on Evaluation rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH_SIZE)
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Only consider rows created on/after YYYY-MM-DD.",
        )
        parser.add_argument(
            "--eval-template-id",
            type=str,
            default=None,
            help="Restrict to one eval_template_id (pre-flight smoke test).",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        batch_size: int = options["batch_size"]
        since: datetime | None = _parse_since(options.get("since"))
        eval_template_id: str | None = options.get("eval_template_id")
        samples: list[dict[str, Any]] = []

        qs = (
            Evaluation.objects.exclude(value__isnull=True)
            .exclude(value="")
            .select_related("eval_template")
            .order_by("id")
        )
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        if eval_template_id:
            qs = qs.filter(eval_template_id=eval_template_id)
        if limit:
            qs = qs[:limit]

        total_in_scope = qs.count()
        self.stdout.write(
            f">>> Pre-flight: {total_in_scope} rows in scope "
            f"(batch_size={batch_size}, dry_run={dry_run})"
        )

        processed = 0
        updated_rows = 0
        skipped_unchanged = 0
        skipped_dispatch_error = 0
        pending: list[Evaluation] = []
        start = time.monotonic()
        batches_flushed = 0

        for ev in qs.iterator(chunk_size=batch_size):
            processed += 1
            tpl = ev.eval_template
            template_config = tpl.config if tpl else {}
            config_output = template_config.get("output") or ev.output_type or "score"

            try:
                parsed_value = parse_legacy_value(ev.value)
                projected = resolve_eval_axes(
                    parsed_value, config_output, include_output_str=True
                )
            except (TypeError, ValueError, KeyError, AttributeError):
                logger.warning(
                    "backfill_evaluation_dispatch_failed",
                    evaluation_id=str(ev.id),
                    eval_template_id=str(tpl.id) if tpl else None,
                    config_output=config_output,
                    exc_info=True,
                )
                skipped_dispatch_error += 1
                continue

            before = {
                "output_bool": ev.output_bool,
                "output_float": ev.output_float,
                "output_str_list": list(ev.output_str_list)
                if ev.output_str_list
                else ev.output_str_list,
                "output_str": ev.output_str,
            }
            changed = False
            for col, projected_value in projected.items():
                if projected_value is not None and getattr(ev, col) is None:
                    setattr(ev, col, projected_value)
                    changed = True

            if not changed:
                skipped_unchanged += 1
                continue

            if len(samples) < _SAMPLE_COUNT:
                samples.append(
                    {
                        "evaluation_id": str(ev.id),
                        "eval_template_id": str(tpl.id) if tpl else None,
                        "config_output": config_output,
                        "value": parsed_value,
                        "before": before,
                        "after": {
                            "output_bool": ev.output_bool,
                            "output_float": ev.output_float,
                            "output_str_list": ev.output_str_list,
                            "output_str": ev.output_str,
                        },
                        "projected": projected,
                    }
                )

            updated_rows += 1
            pending.append(ev)
            if len(pending) >= batch_size:
                self._flush(pending, dry_run=dry_run, batch_size=batch_size)
                pending.clear()
                batches_flushed += 1
                if batches_flushed % _PROGRESS_EVERY_N_BATCHES == 0:
                    _emit_progress(
                        self,
                        processed,
                        total_in_scope,
                        updated_rows,
                        skipped_unchanged + skipped_dispatch_error,
                        start,
                    )

        if pending:
            self._flush(pending, dry_run=dry_run, batch_size=batch_size)
            pending.clear()

        if samples:
            self.stdout.write(f">>> --- Sample conversions ({len(samples)}) ---")
            for i, s in enumerate(samples, 1):
                self.stdout.write(f">>> [{i}] evaluation_id   ={s['evaluation_id']}")
                self.stdout.write(f">>>     eval_template_id={s['eval_template_id']}")
                self.stdout.write(f">>>     config_output   ={s['config_output']!r}")
                self.stdout.write(
                    f">>>     value           ={json.dumps(s['value'], default=str)}"
                )
                self.stdout.write(
                    f">>>     projected       ={json.dumps(s['projected'], default=str)}"
                )
                self.stdout.write(
                    f">>>     before_columns  ={json.dumps(s['before'], default=str)}"
                )
                self.stdout.write(
                    f">>>     after_columns   ={json.dumps(s['after'], default=str)}"
                )

        elapsed = time.monotonic() - start
        self.stdout.write(
            self.style.SUCCESS(
                f"processed={processed} updated_rows={updated_rows} "
                f"skipped_unchanged={skipped_unchanged} "
                f"skipped_dispatch_error={skipped_dispatch_error} "
                f"elapsed={elapsed:.1f}s dry_run={dry_run}"
            )
        )
        logger.info(
            "backfill_evaluation_dual_format_done",
            processed=processed,
            updated_rows=updated_rows,
            skipped_unchanged=skipped_unchanged,
            skipped_dispatch_error=skipped_dispatch_error,
            elapsed_s=round(elapsed, 1),
            dry_run=dry_run,
        )

    @staticmethod
    def _flush(rows: list[Evaluation], *, dry_run: bool, batch_size: int) -> None:
        if dry_run or not rows:
            return
        with transaction.atomic():
            Evaluation.objects.bulk_update(rows, _UPDATE_FIELDS, batch_size=batch_size)


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(
            tzinfo=timezone.get_current_timezone()
        )
    except ValueError as exc:
        raise CommandError(f"--since must be YYYY-MM-DD, got {raw!r}") from exc


def _emit_progress(cmd, processed, total, updated, skipped, start):
    elapsed = time.monotonic() - start
    rate = processed / elapsed if elapsed > 0 else 0
    eta = (total - processed) / rate if rate > 0 else 0
    cmd.stdout.write(
        f">>> progress: {processed}/{total} "
        f"updated={updated} skipped={skipped} "
        f"elapsed={elapsed:.0f}s eta={eta:.0f}s"
    )
