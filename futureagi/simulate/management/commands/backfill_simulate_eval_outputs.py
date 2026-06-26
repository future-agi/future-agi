"""Backfill the per-axis filter keys on ``CallExecution.eval_outputs`` rows."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import structlog
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from evaluations.engine.normalize import (
    AXIS_KEYS,
    eval_config_output,
    resolve_eval_axes,
)
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import CallExecution

logger = structlog.get_logger(__name__)

_DEFAULT_BATCH_SIZE = 1000
_SAMPLE_COUNT = 5
_PROGRESS_EVERY_N_BATCHES = 10


class Command(BaseCommand):
    help = (
        "Backfill output_pass / output_score / output_choices "
        "on CallExecution.eval_outputs rows."
    )

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
            "--test-execution-id",
            type=str,
            default=None,
            help="Restrict to one test_execution (pre-flight smoke test).",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        batch_size: int = options["batch_size"]
        since: datetime | None = _parse_since(options.get("since"))
        test_execution_id: str | None = options.get("test_execution_id")
        samples: list[dict[str, Any]] = []

        qs = (
            CallExecution.objects.exclude(eval_outputs=None)
            .exclude(eval_outputs={})
            .order_by("id")
        )
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        if test_execution_id:
            qs = qs.filter(test_execution_id=test_execution_id)
        if limit:
            qs = qs[:limit]

        total_in_scope = qs.count()
        self.stdout.write(
            f">>> Pre-flight: {total_in_scope} rows in scope "
            f"(batch_size={batch_size}, dry_run={dry_run})"
        )

        eval_cfg_cache: dict[str, str] = {}
        processed = 0
        updated_rows = 0
        updated_entries = 0
        skipped_already_canonical = 0
        skipped_non_uuid_key = 0
        skipped_dispatch_error = 0
        pending: list[CallExecution] = []
        start = time.monotonic()
        batches_flushed = 0

        for call in qs.iterator(chunk_size=batch_size):
            processed += 1
            blob: dict[str, Any] = call.eval_outputs or {}
            row_changed = False
            for eval_id, entry in list(blob.items()):
                if not isinstance(entry, dict):
                    continue
                if all(k in entry for k in AXIS_KEYS):
                    skipped_already_canonical += 1
                    continue

                config_output = self._resolve_config_output(eval_id, eval_cfg_cache)
                if config_output is None:
                    skipped_non_uuid_key += 1
                    continue
                try:
                    axes = resolve_eval_axes(entry.get("output"), config_output)
                except (TypeError, ValueError, KeyError, AttributeError):
                    logger.warning(
                        "backfill_simulate_dispatch_failed",
                        call_execution_id=str(call.id),
                        eval_config_id=eval_id,
                        config_output=config_output,
                        exc_info=True,
                    )
                    skipped_dispatch_error += 1
                    continue
                if (
                    len(samples) < _SAMPLE_COUNT
                    and entry.get("output") is not None
                    and any(v is not None for v in axes.values())
                ):
                    samples.append(
                        {
                            "call_execution_id": str(call.id),
                            "eval_config_id": eval_id,
                            "config_output": config_output,
                            "output_value": entry.get("output"),
                            "before_axes": {k: entry.get(k) for k in AXIS_KEYS},
                            "after_axes": axes,
                        }
                    )
                for key, axis_value in axes.items():
                    entry.setdefault(key, axis_value)
                blob[eval_id] = entry
                row_changed = True
                updated_entries += 1

            if not row_changed:
                continue

            call.eval_outputs = blob
            updated_rows += 1
            pending.append(call)
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
                        skipped_already_canonical
                        + skipped_non_uuid_key
                        + skipped_dispatch_error,
                        start,
                    )

        if pending:
            self._flush(pending, dry_run=dry_run, batch_size=batch_size)
            pending.clear()

        if samples:
            self.stdout.write(f">>> --- Sample conversions ({len(samples)}) ---")
            for i, s in enumerate(samples, 1):
                self.stdout.write(
                    f">>> [{i}] call_execution_id={s['call_execution_id']}"
                )
                self.stdout.write(f">>>     eval_config_id   ={s['eval_config_id']}")
                self.stdout.write(f">>>     config_output    ={s['config_output']!r}")
                self.stdout.write(
                    f">>>     runner_output    ={json.dumps(s['output_value'])}"
                )
                self.stdout.write(
                    f">>>     before_axes      ={json.dumps(s['before_axes'])}"
                )
                self.stdout.write(
                    f">>>     after_axes       ={json.dumps(s['after_axes'])}"
                )

        elapsed = time.monotonic() - start
        self.stdout.write(
            self.style.SUCCESS(
                f"processed={processed} updated_rows={updated_rows} "
                f"updated_entries={updated_entries} "
                f"skipped_already_canonical={skipped_already_canonical} "
                f"skipped_non_uuid_key={skipped_non_uuid_key} "
                f"skipped_dispatch_error={skipped_dispatch_error} "
                f"elapsed={elapsed:.1f}s dry_run={dry_run}"
            )
        )
        logger.info(
            "backfill_simulate_eval_outputs_done",
            processed=processed,
            updated_rows=updated_rows,
            updated_entries=updated_entries,
            skipped_already_canonical=skipped_already_canonical,
            skipped_non_uuid_key=skipped_non_uuid_key,
            skipped_dispatch_error=skipped_dispatch_error,
            elapsed_s=round(elapsed, 1),
            dry_run=dry_run,
        )

    @staticmethod
    def _resolve_config_output(eval_id: str, cache: dict[str, str]) -> str | None:
        """Returns ``None`` when ``eval_id`` is not a parseable UUID (skip the entry)."""
        cached = cache.get(eval_id)
        if cached is not None:
            return cached
        try:
            cfg = (
                SimulateEvalConfig.objects.select_related("eval_template")
                .only("id", "eval_template__config")
                .get(id=eval_id)
            )
        except SimulateEvalConfig.DoesNotExist:
            resolved = "score"
        except (ValueError, ValidationError):
            return None
        else:
            resolved = eval_config_output(cfg)
        cache[eval_id] = resolved
        return resolved

    @staticmethod
    def _flush(rows: list[CallExecution], *, dry_run: bool, batch_size: int) -> None:
        if dry_run or not rows:
            return
        with transaction.atomic():
            CallExecution.objects.bulk_update(
                rows, ["eval_outputs"], batch_size=batch_size
            )


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
