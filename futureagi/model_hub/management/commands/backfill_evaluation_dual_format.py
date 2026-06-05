"""Re-populate ``Evaluation`` output_* columns + ``value`` via the
canonical dual-write helper. Idempotent.

Usage: ``python manage.py backfill_evaluation_dual_format [--dry-run] [--limit N] [--since YYYY-MM-DD]``"""

import ast
import json
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.utils import timezone

from evaluations.engine.normalize import (
    _dedupe_preserve_order,
    coerce_to_legacy_scalar,
    dual_write_eval_value,
    eval_config_output,
)
from model_hub.models.evaluation import Evaluation


def _parse(raw):
    """Try JSON then Python repr; fall back to the raw string."""
    if raw is None:
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


def _looks_dict_or_list(raw):
    if not isinstance(raw, str):
        return False
    head = raw.lstrip()[:1]
    return head in ("{", "[")


class Command(BaseCommand):
    help = (
        "Re-populate Evaluation output_* columns + value from the rich "
        "shape so FE never receives a Python-repr-of-dict."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--since", type=str, default=None)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        since = options.get("since")

        qs = (
            Evaluation.objects.select_related("eval_template")
            .filter(value__isnull=False)
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
        if limit:
            qs = qs[:limit]

        scanned = 0
        updated = 0
        skipped_other_output = 0
        skipped_unchanged = 0
        skipped_plain_scalar = 0
        batch: list[Evaluation] = []

        for row in qs.iterator(chunk_size=batch_size):
            scanned += 1

            # Pick the richest source we have: prefer the rich JSON shape in
            # output_str (post-PR new writer), then fall back to ``value`` (old
            # writer wrote ``str(dict)`` here).
            raw = row.output_str if _looks_dict_or_list(row.output_str) else row.value
            if not _looks_dict_or_list(raw):
                skipped_plain_scalar += 1
                continue

            config_output = eval_config_output(row.eval_template)
            if config_output not in ("score", "choices"):
                skipped_other_output += 1
                continue

            parsed = _parse(raw)
            value = parsed if isinstance(parsed, (dict, list, int, float, bool, str)) else raw

            proposed: dict = {}
            dual_write_eval_value(value, config_output, proposed)
            scalar = coerce_to_legacy_scalar(value, config_output)

            changed = False
            if (
                config_output == "score"
                and "output_float" in proposed
                and row.output_float != proposed["output_float"]
            ):
                row.output_float = proposed["output_float"]
                changed = True
            if config_output == "choices":
                if "output_str_list" in proposed:
                    deduped = _dedupe_preserve_order(proposed["output_str_list"])
                    if list(row.output_str_list or []) != deduped:
                        row.output_str_list = deduped
                        changed = True
            if "output_str" in proposed and row.output_str != proposed["output_str"]:
                row.output_str = proposed["output_str"]
                changed = True
            if scalar is not None and row.value != scalar:
                row.value = scalar
                changed = True

            if changed:
                updated += 1
                batch.append(row)
            else:
                skipped_unchanged += 1

            if not dry_run and len(batch) >= batch_size:
                Evaluation.objects.bulk_update(
                    batch,
                    ["output_float", "output_str_list", "output_str", "value"],
                )
                batch.clear()

        if not dry_run and batch:
            Evaluation.objects.bulk_update(
                batch, ["output_float", "output_str_list", "output_str", "value"]
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"scanned={scanned} updated={updated} "
                f"skipped_other_output={skipped_other_output} "
                f"skipped_plain_scalar={skipped_plain_scalar} "
                f"skipped_unchanged={skipped_unchanged} "
                f"dry_run={dry_run}"
            )
        )
