"""Backfill the 4 axis keys inside ``Cell.value_infos`` for eval cells."""

from __future__ import annotations

import ast
import json
from datetime import datetime
from typing import Any

import structlog
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from evaluations.engine.normalize import AXIS_KEYS, resolve_eval_axes
from model_hub.models.develop_dataset import Cell

logger = structlog.get_logger(__name__)


def _parse_value(raw: Any) -> Any:
    if raw is None or not isinstance(raw, str):
        return raw
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


class Command(BaseCommand):
    help = (
        "Backfill axis keys (output_pass / output_score / output_choice / "
        "output_choices) inside Cell.value_infos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--since", type=str, default=None)
        parser.add_argument("--column-id", type=str, default=None)
        parser.add_argument("--dataset-id", type=str, default=None)

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        batch_size: int = options["batch_size"]
        since_raw: str | None = options.get("since")
        column_id: str | None = options.get("column_id")
        dataset_id: str | None = options.get("dataset_id")

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

        qs = Cell.objects.exclude(value_infos__isnull=True).exclude(value_infos="")
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        if column_id:
            qs = qs.filter(column_id=column_id)
        if dataset_id:
            qs = qs.filter(dataset_id=dataset_id)
        qs = qs.select_related("column__eval_template").order_by("created_at", "id")
        if limit:
            qs = qs[:limit]

        processed = 0
        updated_rows = 0
        skipped_rows = 0
        pending: list[Cell] = []

        for cell in qs.iterator(chunk_size=batch_size):
            processed += 1

            try:
                infos = (
                    json.loads(cell.value_infos)
                    if isinstance(cell.value_infos, str)
                    else dict(cell.value_infos or {})
                )
            except (json.JSONDecodeError, TypeError):
                skipped_rows += 1
                continue
            if not isinstance(infos, dict):
                skipped_rows += 1
                continue
            if all(k in infos for k in AXIS_KEYS):
                skipped_rows += 1
                continue

            tpl = getattr(cell.column, "eval_template", None)
            template_config = tpl.config if tpl else {}
            config_output = template_config.get("output") or "score"
            multi_choice = bool(tpl.multi_choice) if tpl else False

            axes = resolve_eval_axes(
                _parse_value(cell.value), config_output, multi_choice
            )
            for key, axis_value in axes.items():
                infos.setdefault(key, axis_value)

            cell.value_infos = json.dumps(infos)
            updated_rows += 1
            pending.append(cell)
            if len(pending) >= batch_size:
                self._flush(pending, dry_run=dry_run)
                pending.clear()

        if pending:
            self._flush(pending, dry_run=dry_run)
            pending.clear()

        self.stdout.write(
            self.style.SUCCESS(
                f"processed={processed} updated_rows={updated_rows} "
                f"skipped_rows={skipped_rows} dry_run={dry_run}"
            )
        )
        logger.info(
            "backfill_cell_eval_axes_done",
            processed=processed,
            updated_rows=updated_rows,
            skipped_rows=skipped_rows,
            dry_run=dry_run,
        )

    @staticmethod
    def _flush(rows: list[Cell], *, dry_run: bool) -> None:
        if dry_run or not rows:
            return
        with transaction.atomic():
            Cell.objects.bulk_update(rows, ["value_infos"])
