"""Rewrite ``Cell.value`` for eval cells holding a stringified dict into
the canonical scalar projection. Idempotent. ``value_infos`` untouched.

Usage: ``python manage.py backfill_cell_eval_value [--dry-run] [--limit N] [--since YYYY-MM-DD] [--column-id UUID]``"""

import ast
import json
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.utils import timezone

from evaluations.engine.normalize import coerce_to_legacy_scalar
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell
from model_hub.models.evals_metric import UserEvalMetric

# These ``source`` strings are populated by the eval runner — non-eval
# columns (run_prompt, annotation_label, OTHERS) are skipped.
EVAL_SOURCES = {
    SourceChoices.EVALUATION.value,
    SourceChoices.EXPERIMENT_EVALUATION.value,
    SourceChoices.OPTIMISATION_EVALUATION.value,
}


def _parse_value(raw):
    """Best-effort decode of a stringified eval value.

    Returns the parsed object on success, ``None`` for empty input, or the
    raw string when neither JSON nor Python repr could decode it. Callers
    use the type to decide whether the value still needs normalization.
    """
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    try:
        return ast.literal_eval(raw)
    except (TypeError, ValueError, SyntaxError):
        pass
    return raw


def _looks_like_dict_or_list(raw):
    """Cheap pre-filter so we don't ``json.loads`` every cell in the dataset."""
    if not isinstance(raw, str):
        return False
    head = raw.lstrip()[:1]
    return head in ("{", "[")


class Command(BaseCommand):
    help = (
        "Re-write Cell.value for eval-column cells where a post-revamp dict "
        "leaked into the legacy text column."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--since", type=str, default=None)
        parser.add_argument("--column-id", type=str, default=None)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        since = options.get("since")
        column_id = options.get("column_id")

        qs = (
            Cell.objects.select_related("column")
            .filter(
                column__source__in=EVAL_SOURCES,
                deleted=False,
                value__isnull=False,
            )
            .exclude(value="")
            .order_by("id")
        )
        if since:
            since_dt = timezone.make_aware(
                datetime.combine(
                    datetime.strptime(since, "%Y-%m-%d").date(), time.min
                )
            )
            qs = qs.filter(created_at__gte=since_dt)
        if column_id:
            qs = qs.filter(column_id=column_id)
        if limit:
            qs = qs[:limit]

        # Cache template lookup by user_eval_metric_id; touching the FK chain
        # once per metric, not once per cell, keeps the backfill fast on large
        # datasets where one column has thousands of rows.
        template_cache: dict[str, str | None] = {}

        def _config_output_for_column(col):
            uem_id = str(col.source_id) if col.source_id else None
            if uem_id is None:
                return None
            if uem_id in template_cache:
                return template_cache[uem_id]
            try:
                uem = UserEvalMetric.objects.select_related("template").get(id=uem_id)
                co = (uem.template.config or {}).get("output") if uem.template else None
            except UserEvalMetric.DoesNotExist:
                co = None
            template_cache[uem_id] = co
            return co

        scanned = 0
        updated = 0
        skipped_other_output = 0
        skipped_plain_scalar = 0
        skipped_unchanged = 0
        batch: list[Cell] = []

        for cell in qs.iterator(chunk_size=batch_size):
            scanned += 1
            if not _looks_like_dict_or_list(cell.value):
                skipped_plain_scalar += 1
                continue

            config_output = _config_output_for_column(cell.column)
            if config_output not in ("score", "choices"):
                skipped_other_output += 1
                continue

            parsed = _parse_value(cell.value)
            if not isinstance(parsed, (dict, list)):
                skipped_plain_scalar += 1
                continue

            new_value = coerce_to_legacy_scalar(parsed, config_output)
            if new_value == cell.value or new_value is None:
                skipped_unchanged += 1
                continue

            cell.value = new_value
            updated += 1
            batch.append(cell)

            if not dry_run and len(batch) >= batch_size:
                Cell.objects.bulk_update(batch, ["value"])
                batch.clear()

        if not dry_run and batch:
            Cell.objects.bulk_update(batch, ["value"])

        self.stdout.write(
            self.style.SUCCESS(
                f"scanned={scanned} updated={updated} "
                f"skipped_other_output={skipped_other_output} "
                f"skipped_plain_scalar={skipped_plain_scalar} "
                f"skipped_unchanged={skipped_unchanged} "
                f"dry_run={dry_run}"
            )
        )
