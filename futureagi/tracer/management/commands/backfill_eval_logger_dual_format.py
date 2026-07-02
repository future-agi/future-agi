"""
Backfill EvalLogger rows whose categorical/numeric result was written into
``output_str`` only (the broken dispatch path) so that ``output_float`` and
``output_str_list`` are re-populated for FE readers that still consume the
typed columns.

The row-level routing logic lives in
:func:`tracer.utils.eval._dual_write_eval_value` — this command parses the
existing ``output_str`` back into a Python value and feeds it through that
same helper, then applies the result with idempotent gating:

  * ``output_float`` is only set when ``config["output"] == "score"`` AND it
    is currently NULL.
  * ``output_str_list`` is only set when ``config["output"] == "choices"`` AND
    it is currently empty; any already-populated list is deduped in place per
    the universal "no element repeated" rule.
  * ``output_bool`` is never touched.
  * Rows for any other ``output`` type are skipped.

Inherits the helper's full list handling automatically:
  * score + list / list-of-{score: …} dicts → averaged into output_float.
  * choices + list / list-of-{choice|choices} dicts → flattened + deduped into
    output_str_list.

Usage:
    python manage.py backfill_eval_logger_dual_format --dry-run
    python manage.py backfill_eval_logger_dual_format
    python manage.py backfill_eval_logger_dual_format --limit 100
    python manage.py backfill_eval_logger_dual_format --eval-task-id <uuid>
    python manage.py backfill_eval_logger_dual_format --since 2026-05-01
"""

import ast
import json
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.utils import timezone

from tracer.models.observation_span import EvalLogger
from tracer.utils.eval import _dedupe_preserve_order, _dual_write_eval_value

SENTINEL_STRINGS = {"", "ERROR", "Passed", "Failed"}


def _parse_output_str(raw):
    """Best-effort parse of an ``output_str`` payload.

    Returns the parsed value (dict / list / scalar) on success, or the raw
    string if neither JSON nor Python repr could decode it. The caller
    inspects the type to decide how to feed it into the helper.
    """
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    try:
        return ast.literal_eval(raw)
    except (TypeError, ValueError, SyntaxError):
        pass
    return raw


def _config_output_for(row):
    """Read ``eval_template.config["output"]`` for the row's eval."""
    try:
        return row.custom_eval_config.eval_template.config.get("output", "score")
    except (AttributeError, TypeError):
        return None


class Command(BaseCommand):
    help = (
        "Re-populate EvalLogger.output_float / output_str_list from output_str "
        "for score / choices evals affected by the dispatch-path regression."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of rows to process (0 = no limit).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Bulk-update batch size.",
        )
        parser.add_argument(
            "--eval-task-id",
            type=str,
            default=None,
            help="Restrict to one eval_task_id (for testing on a single task).",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Only consider rows created on/after this date (YYYY-MM-DD).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        eval_task_id = options.get("eval_task_id")
        since = options.get("since")

        qs = (
            EvalLogger.objects.select_related("custom_eval_config__eval_template")
            .filter(error=False)
            .exclude(output_str__in=SENTINEL_STRINGS)
            .exclude(output_str__isnull=True)
            .order_by("id")
        )
        if eval_task_id:
            qs = qs.filter(eval_task_id=eval_task_id)
        if since:
            since_dt = timezone.make_aware(
                datetime.combine(
                    datetime.strptime(since, "%Y-%m-%d").date(),
                    time.min,
                )
            )
            qs = qs.filter(created_at__gte=since_dt)
        if limit:
            qs = qs[:limit]

        scanned = 0
        updated = 0
        skipped_other_output = 0
        skipped_unchanged = 0
        batch = []

        for row in qs.iterator(chunk_size=batch_size):
            scanned += 1
            config_output = _config_output_for(row)
            if config_output not in ("score", "choices"):
                skipped_other_output += 1
                continue

            parsed = _parse_output_str(row.output_str)
            # Helper expects the actual value; for unparseable input fall back
            # to the raw string so the helper's str-branch still applies.
            value = (
                parsed
                if isinstance(parsed, (dict, list, int, float, bool, str))
                else row.output_str
            )

            proposed = {}
            _dual_write_eval_value(value, config_output, proposed)

            changed = False

            # output_float: write only when missing AND helper produced one.
            if (
                config_output == "score"
                and "output_float" in proposed
                and row.output_float is None
            ):
                row.output_float = proposed["output_float"]
                changed = True

            # output_str_list: write only when empty; otherwise dedupe in place.
            if config_output == "choices":
                if not row.output_str_list and "output_str_list" in proposed:
                    row.output_str_list = proposed["output_str_list"]
                    changed = True
                elif row.output_str_list:
                    deduped = _dedupe_preserve_order(row.output_str_list)
                    if deduped != list(row.output_str_list):
                        row.output_str_list = deduped
                        changed = True

            # output_str: normalize to the helper's preferred form (JSON for
            # dict/list, raw string for plain text). Only when it differs.
            if "output_str" in proposed and row.output_str != proposed["output_str"]:
                row.output_str = proposed["output_str"]
                changed = True

            if changed:
                updated += 1
                batch.append(row)
            else:
                skipped_unchanged += 1

            if not dry_run and len(batch) >= batch_size:
                EvalLogger.objects.bulk_update(
                    batch, ["output_float", "output_str_list", "output_str"]
                )
                batch.clear()

        if not dry_run and batch:
            EvalLogger.objects.bulk_update(
                batch, ["output_float", "output_str_list", "output_str"]
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"scanned={scanned} updated={updated} "
                f"skipped_other_output={skipped_other_output} "
                f"skipped_unchanged={skipped_unchanged} "
                f"dry_run={dry_run}"
            )
        )
