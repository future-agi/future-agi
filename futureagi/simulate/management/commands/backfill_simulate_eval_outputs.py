"""Backfill the per-axis filter keys on ``CallExecution.eval_outputs`` rows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from evaluations.engine.normalize import (
    AXIS_KEYS,
    eval_config_multi_choice,
    eval_config_output,
    resolve_eval_axes,
)
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import CallExecution

logger = structlog.get_logger(__name__)


class Command(BaseCommand):
    help = (
        "Backfill output_pass / output_score / output_choices "
        "on CallExecution.eval_outputs rows."
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
            help="Maximum number of CallExecution rows to process (0 = no limit).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Bulk-update batch size.",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help=(
                "Only consider call executions created on/after this date (YYYY-MM-DD)."
            ),
        )
        parser.add_argument(
            "--test-execution-id",
            type=str,
            default=None,
            help="Restrict to one test_execution_id.",
        )
        parser.add_argument(
            "--eval-config-id",
            type=str,
            default=None,
            help=(
                "Restrict to entries keyed by this eval_config_id. Other entries "
                "in the same CallExecution are left untouched."
            ),
        )
        parser.add_argument(
            "--sample-count",
            type=int,
            default=5,
            help=(
                "Print first N before/after conversion samples to stdout. "
                "0 disables sampling. Sampling has no effect on writes."
            ),
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        batch_size: int = options["batch_size"]
        since_raw: str | None = options.get("since")
        test_execution_id: str | None = options.get("test_execution_id")
        eval_config_filter: str | None = options.get("eval_config_id")
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

        qs = CallExecution.objects.exclude(eval_outputs=None).exclude(eval_outputs={})
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        if test_execution_id:
            qs = qs.filter(test_execution_id=test_execution_id)
        qs = qs.order_by("created_at", "id")
        if limit:
            qs = qs[:limit]

        eval_cfg_cache: dict[str, tuple[str, bool]] = {}
        processed = 0
        updated_rows = 0
        updated_entries = 0
        skipped_entries = 0
        pending: list[CallExecution] = []

        for call in qs.iterator(chunk_size=batch_size):
            processed += 1
            blob: dict[str, Any] = call.eval_outputs or {}
            row_changed = False
            for eval_id, entry in list(blob.items()):
                if not isinstance(entry, dict):
                    continue
                if eval_config_filter and eval_id != eval_config_filter:
                    continue
                if all(k in entry for k in AXIS_KEYS):
                    skipped_entries += 1
                    continue

                config_output, multi_choice = self._resolve_eval_config(
                    eval_id, eval_cfg_cache
                )
                axes = resolve_eval_axes(entry.get("output"), config_output, multi_choice)
                # Prefer samples that actually demonstrate a conversion
                # (non-null runner output AND at least one axis populated).
                if (
                    len(samples) < sample_count
                    and entry.get("output") is not None
                    and any(v is not None for v in axes.values())
                ):
                    samples.append(
                        {
                            "call_execution_id": str(call.id),
                            "eval_config_id": eval_id,
                            "config_output": config_output,
                            "multi_choice": multi_choice,
                            "output_value": entry.get("output"),
                            "before_axes": {k: entry.get(k) for k in AXIS_KEYS},
                            "after_axes": axes,
                        }
                    )
                entry.update(axes)
                blob[eval_id] = entry
                row_changed = True
                updated_entries += 1

            if not row_changed:
                continue

            call.eval_outputs = blob
            updated_rows += 1
            pending.append(call)
            if len(pending) >= batch_size:
                self._flush(pending, dry_run=dry_run)
                pending.clear()

        if pending:
            self._flush(pending, dry_run=dry_run)
            pending.clear()

        if samples:
            self.stdout.write(f">>> --- Sample conversions ({len(samples)}) ---")
            for i, s in enumerate(samples, 1):
                self.stdout.write(f">>> [{i}] call_execution_id={s['call_execution_id']}")
                self.stdout.write(f">>>     eval_config_id   ={s['eval_config_id']}")
                self.stdout.write(
                    f">>>     config_output    ={s['config_output']!r}"
                    f"  multi_choice={s['multi_choice']}"
                )
                self.stdout.write(f">>>     runner_output    ={json.dumps(s['output_value'])}")
                self.stdout.write(f">>>     before_axes      ={json.dumps(s['before_axes'])}")
                self.stdout.write(f">>>     after_axes       ={json.dumps(s['after_axes'])}")

        self.stdout.write(
            self.style.SUCCESS(
                f"processed={processed} updated_rows={updated_rows} "
                f"updated_entries={updated_entries} skipped_entries={skipped_entries} "
                f"dry_run={dry_run}"
            )
        )
        logger.info(
            "backfill_simulate_eval_outputs_done",
            processed=processed,
            updated_rows=updated_rows,
            updated_entries=updated_entries,
            skipped_entries=skipped_entries,
            dry_run=dry_run,
        )

    @staticmethod
    def _resolve_eval_config(
        eval_id: str, cache: dict[str, tuple[str, bool]]
    ) -> tuple[str, bool]:
        cached = cache.get(eval_id)
        if cached is not None:
            return cached
        try:
            cfg = (
                SimulateEvalConfig.objects.select_related("eval_template")
                .only("id", "eval_template__config", "eval_template__multi_choice")
                .get(id=eval_id)
            )
        except SimulateEvalConfig.DoesNotExist:
            resolved = ("score", False)
        else:
            resolved = (eval_config_output(cfg), eval_config_multi_choice(cfg))
        cache[eval_id] = resolved
        return resolved

    @staticmethod
    def _flush(rows: list[CallExecution], *, dry_run: bool) -> None:
        if dry_run or not rows:
            return
        with transaction.atomic():
            CallExecution.objects.bulk_update(rows, ["eval_outputs"])
