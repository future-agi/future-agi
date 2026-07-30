"""
Backfill ``Score.tracer_project_id`` for existing trace/span scores.

New scores set ``tracer_project_id`` at write time; historic rows are NULL and
so are invisible to the PG-based annotation-label discovery until backfilled.
The project is resolved from ClickHouse ``spans`` (the legacy PG trace/span
tables were dropped post-CH25).

The core logic lives in :func:`backfill_tracer_project_ids` so the data
migration (``model_hub/migrations/0120_backfill_score_tracer_project.py``) and
this command share one idempotent implementation.

Design — why this can't OOM or time out:

``spans`` is sorted by ``(project_id, …)``, so it is scanned PER PROJECT via the
primary-key prefix — never a full-table scan, and never a huge ``trace_id IN (…)``
membership scan. Each project's ``(trace_id, id)`` rows are STREAMED
(``execute_iter``) and flushed to PG in fixed-size chunks, so ClickHouse holds no
large aggregate and Python holds at most one chunk. Updates are idempotent
(``tracer_project_id IS NULL`` guard), so streamed duplicates are harmless and
re-runs / partial-failure retries are no-ops. Only projects in orgs that have
un-backfilled scores are scanned; a failing project is logged and skipped.

Rows whose source is absent from ``spans`` (deleted / orphaned) stay NULL,
matching the reader's behavior.

NOTE: legacy ``backfill_scores`` creates trace/span Scores WITHOUT
``tracer_project_id``; if it is ever run after this backfill, re-run this to
stamp those rows (see that command's docstring).

Usage:
    python manage.py backfill_score_tracer_project                  # run
    python manage.py backfill_score_tracer_project --dry-run        # counts only
    python manage.py backfill_score_tracer_project --chunk-size 2000
    python manage.py backfill_score_tracer_project --sleep 0.2      # CDC throttle
    python manage.py backfill_score_tracer_project --project-id <uuid>  # one project
"""

import time
from typing import Callable, Optional

import structlog
from django.core.management.base import BaseCommand
from django.db.models import Q

logger = structlog.get_logger(__name__)

# Per-project PK-prefix scan; streamed. No DISTINCT / GROUP BY so ClickHouse
# builds no aggregate — memory stays flat regardless of project size.
_SPANS_QUERY = """
    SELECT trace_id, id
    FROM spans
    WHERE project_id = %(project_id)s AND is_deleted = 0
"""


def _pending_scores():
    from model_hub.models.score import Score

    return Score.no_workspace_objects.filter(
        Q(trace_id__isnull=False) | Q(observation_span_id__isnull=False),
        tracer_project_id__isnull=True,
    )


def _eligible_project_ids(base, only_project: Optional[str]) -> list:
    """Tracer projects worth scanning: those in orgs that have un-backfilled
    scores. Prunes projects whose org never annotates."""
    from tracer.models.project import Project

    if only_project:
        return [only_project]
    org_ids = list(base.values_list("organization_id", flat=True).distinct())
    if not org_ids:
        return []
    return list(
        Project.objects.filter(organization_id__in=org_ids).values_list(
            "id", flat=True
        )
    )


def _tag(project_id, field: str, ids: list) -> int:
    """Idempotent: only stamps scores still missing tracer_project_id."""
    from model_hub.models.score import Score

    return Score.no_workspace_objects.filter(
        tracer_project_id__isnull=True, **{f"{field}__in": ids}
    ).update(tracer_project_id=project_id)


def _backfill_project(project_id, chunk_size: int) -> int:
    """Stream this project's span/trace ids from CH and tag matching scores."""
    from tracer.services.clickhouse.client import get_clickhouse_client

    rows = get_clickhouse_client().execute_iter(
        _SPANS_QUERY, {"project_id": str(project_id)}
    )

    updated = 0
    trace_chunk: list = []
    span_chunk: list = []
    for trace_id, span_id in rows:
        if trace_id:
            trace_chunk.append(str(trace_id))
            if len(trace_chunk) >= chunk_size:
                updated += _tag(project_id, "trace_id", trace_chunk)
                trace_chunk = []
        if span_id:
            span_chunk.append(str(span_id))
            if len(span_chunk) >= chunk_size:
                updated += _tag(project_id, "observation_span_id", span_chunk)
                span_chunk = []

    if trace_chunk:
        updated += _tag(project_id, "trace_id", trace_chunk)
    if span_chunk:
        updated += _tag(project_id, "observation_span_id", span_chunk)
    return updated


def backfill_tracer_project_ids(
    chunk_size: int = 1000,
    sleep_s: float = 0.0,
    only_project: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """Idempotent backfill. Returns ``{"updated", "remaining", "total"}``.

    Safe to call from a data migration or the management command; re-runs only
    touch rows still missing ``tracer_project_id``.
    """
    emit = log or (lambda _msg: None)

    base = _pending_scores()
    total = base.count()
    emit(f"Scores missing tracer_project_id: {total}")
    if total == 0:
        return {"updated": 0, "remaining": 0, "total": 0}

    project_ids = _eligible_project_ids(base, only_project)
    emit(f"Projects to scan: {len(project_ids)}")

    updated = 0
    for i, pid in enumerate(project_ids, 1):
        try:
            n = _backfill_project(pid, chunk_size)
        except Exception:
            logger.exception(
                "backfill_score_tracer_project_failed", project_id=str(pid)
            )
            emit(f"  project {pid} failed; skipped")
            continue
        updated += n
        emit(f"  [{i}/{len(project_ids)}] project={pid} updated={n} (total={updated})")
        if sleep_s:
            time.sleep(sleep_s)

    remaining = base.count()
    emit(
        f"Backfill complete: updated={updated} remaining_null={remaining} "
        f"(remaining are orphans absent from spans)"
    )
    return {"updated": updated, "remaining": remaining, "total": total}


class Command(BaseCommand):
    help = "Backfill Score.tracer_project_id from CH spans (per-project, streamed)."

    def add_arguments(self, parser):
        parser.add_argument("--chunk-size", type=int, default=1000)
        parser.add_argument("--sleep", type=float, default=0.0)
        parser.add_argument("--project-id", type=str, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if opts["dry_run"]:
            total = _pending_scores().count()
            self.stdout.write(f"Scores missing tracer_project_id: {total}")
            return
        backfill_tracer_project_ids(
            chunk_size=opts["chunk_size"],
            sleep_s=opts["sleep"],
            only_project=opts["project_id"],
            log=self.stdout.write,
        )
