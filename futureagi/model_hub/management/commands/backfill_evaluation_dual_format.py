"""Backfill the typed output columns on ``Evaluation`` rows."""

from __future__ import annotations

import ast
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

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        batch_size: int = options["batch_size"]
        since_raw: str | None = options.get("since")
        eval_template_id: str | None = options.get("eval_template_id")
        evaluation_id: str | None = options.get("evaluation_id")

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
            if (
                ev.output_bool is not None
                or ev.output_float is not None
                or ev.output_str_list not in (None, [])
            ):
                skipped_rows += 1
                continue

            tpl = ev.eval_template
            template_config = tpl.config if tpl else {}
            config_output = template_config.get("output") or ev.output_type or "score"
            multi_choice = bool(tpl.multi_choice) if tpl else False

            axes = resolve_eval_axes(
                _parse_value(ev.value), config_output, multi_choice
            )

            changed = False
            if axes["output_pass"] is not None:
                ev.output_bool = axes["output_pass"]
                changed = True
            if axes["output_score"] is not None:
                ev.output_float = axes["output_score"]
                changed = True
            if axes["output_choice"] is not None:
                ev.output_str_list = [axes["output_choice"]]
                changed = True
            elif axes["output_choices"] is not None:
                ev.output_str_list = axes["output_choices"]
                changed = True

            if not changed:
                skipped_rows += 1
                continue

            updated_rows += 1
            pending.append(ev)
            if len(pending) >= batch_size:
                self._flush(pending, update_fields, dry_run=dry_run)
                pending.clear()

        if pending:
            self._flush(pending, update_fields, dry_run=dry_run)
            pending.clear()

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
