"""Backfill the typed output columns on ``Evaluation`` rows."""

from __future__ import annotations

import ast
import json
from datetime import datetime
from typing import Any

import structlog
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from evaluations.engine.normalize import resolve_eval_axes
from model_hub.models.evaluation import Evaluation

logger = structlog.get_logger(__name__)


def _parse_value(raw: Any) -> Any:
    if raw is None or not isinstance(raw, str):
        return raw
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


class Command(BaseCommand):
    help = "Backfill output_bool / output_float / output_str_list on Evaluation rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--since", type=str, default=None)
        parser.add_argument("--eval-template-id", type=str, default=None)
        parser.add_argument("--evaluation-id", type=str, default=None)
        parser.add_argument(
            "--sample-count",
            type=int,
            default=5,
            help="Print first N before/after conversion samples (0 disables).",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        batch_size: int = options["batch_size"]
        since_raw: str | None = options.get("since")
        eval_template_id: str | None = options.get("eval_template_id")
        evaluation_id: str | None = options.get("evaluation_id")
        sample_count: int = options.get("sample_count", 5)
        samples: list[dict[str, Any]] = []

        since: datetime | None = None
        if since_raw:
            try:
                since = datetime.strptime(since_raw, "%Y-%m-%d").replace(
                    tzinfo=timezone.get_current_timezone()
                )
            except ValueError as exc:
                raise CommandError(
                    f"--since must be YYYY-MM-DD, got {since_raw!r}"
                ) from exc

        qs = Evaluation.objects.exclude(value__isnull=True).exclude(value="")
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        if eval_template_id:
            qs = qs.filter(eval_template_id=eval_template_id)
        if evaluation_id:
            qs = qs.filter(id=evaluation_id)
        qs = qs.select_related("eval_template").order_by("created_at", "id")
        if limit:
            qs = qs[:limit]

        processed = 0
        updated_rows = 0
        skipped_rows = 0
        pending: list[Evaluation] = []
        update_fields = ["output_bool", "output_float", "output_str_list"]

        for ev in qs.iterator(chunk_size=batch_size):
            processed += 1
            tpl = ev.eval_template
            template_config = tpl.config if tpl else {}
            config_output = template_config.get("output") or ev.output_type or "score"
            multi_choice = bool(tpl.multi_choice) if tpl else False

            parsed_value = _parse_value(ev.value)
            axes = resolve_eval_axes(parsed_value, config_output, multi_choice)

            before = {
                "output_bool": ev.output_bool,
                "output_float": ev.output_float,
                "output_str_list": list(ev.output_str_list) if ev.output_str_list else ev.output_str_list,
            }
            # Per-axis additive check: only write when the dispatch produced
            # an axis the row doesn't already carry. Permissive secondary
            # axes (e.g. choice_scores filling output_str_list on a row that
            # already has output_float) get captured this way.
            changed = False
            if ev.output_bool is None and axes["output_pass"] is not None:
                ev.output_bool = axes["output_pass"]
                changed = True
            if ev.output_float is None and axes["output_score"] is not None:
                ev.output_float = axes["output_score"]
                changed = True
            if ev.output_str_list in (None, []) and axes["output_choices"] is not None:
                ev.output_str_list = axes["output_choices"]
                changed = True

            if not changed:
                skipped_rows += 1
                continue

            if len(samples) < sample_count:
                samples.append(
                    {
                        "evaluation_id": str(ev.id),
                        "eval_template_id": str(tpl.id) if tpl else None,
                        "config_output": config_output,
                        "multi_choice": multi_choice,
                        "value": parsed_value,
                        "before": before,
                        "after": {
                            "output_bool": ev.output_bool,
                            "output_float": ev.output_float,
                            "output_str_list": ev.output_str_list,
                        },
                        "axes_dispatched": axes,
                    }
                )

            updated_rows += 1
            pending.append(ev)
            if len(pending) >= batch_size:
                self._flush(pending, update_fields, dry_run=dry_run)
                pending.clear()

        if pending:
            self._flush(pending, update_fields, dry_run=dry_run)
            pending.clear()

        if samples:
            self.stdout.write(f">>> --- Sample conversions ({len(samples)}) ---")
            for i, s in enumerate(samples, 1):
                self.stdout.write(f">>> [{i}] evaluation_id   ={s['evaluation_id']}")
                self.stdout.write(f">>>     eval_template_id={s['eval_template_id']}")
                self.stdout.write(
                    f">>>     config_output   ={s['config_output']!r}"
                    f"  multi_choice={s['multi_choice']}"
                )
                self.stdout.write(f">>>     value           ={json.dumps(s['value'], default=str)}")
                self.stdout.write(f">>>     axes_dispatched ={json.dumps(s['axes_dispatched'])}")
                self.stdout.write(f">>>     before_columns  ={json.dumps(s['before'], default=str)}")
                self.stdout.write(f">>>     after_columns   ={json.dumps(s['after'], default=str)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"processed={processed} updated_rows={updated_rows} "
                f"skipped_rows={skipped_rows} dry_run={dry_run}"
            )
        )
        logger.info(
            "backfill_evaluation_dual_format_done",
            processed=processed,
            updated_rows=updated_rows,
            skipped_rows=skipped_rows,
            dry_run=dry_run,
        )

    @staticmethod
    def _flush(rows: list[Evaluation], fields: list[str], *, dry_run: bool) -> None:
        if dry_run or not rows:
            return
        with transaction.atomic():
            Evaluation.objects.bulk_update(rows, fields)
