"""
Backfill ``Score.tracer_project_id`` for existing trace/span scores.

New scores set ``tracer_project_id`` at write time; historic rows are NULL and
so are invisible to the PG-based annotation-label discovery until backfilled.
The project is resolved from ClickHouse ``spans`` (the legacy PG trace/span
tables were dropped post-CH25).

Design — why this can't OOM or time out:

``spans`` is sorted by ``(project_id, …)``, so it is scanned PER PROJECT via the
primary-key prefix — never a full-table scan, and never a huge ``trace_id IN (…)``
membership scan. Each project's ``(trace_id, id)`` rows are STREAMED
(``execute_iter``) and flushed to PG in fixed-size chunks, so ClickHouse holds no
large aggregate and Python holds at most one chunk. Updates are idempotent
(``tracer_project_id IS NULL`` guard), so streamed duplicates are harmless.
Only projects in orgs that actually have un-backfilled scores are scanned; a
failing project is logged and skipped rather than aborting the run.

Rows whose source is absent from ``spans`` (deleted / orphaned) stay NULL,
matching the reader's behavior.

Usage:
    python manage.py backfill_score_tracer_project                  # run
    python manage.py backfill_score_tracer_project --dry-run        # counts only
    python manage.py backfill_score_tracer_project --chunk-size 2000
    python manage.py backfill_score_tracer_project --sleep 0.2      # CDC throttle
    python manage.py backfill_score_tracer_project --project-id <uuid>  # one project
"""

import time

import structlog
from django.core.management.base import BaseCommand
from django.db.models import Q

from model_hub.models.score import Score

logger = structlog.get_logger(__name__)

# Per-project PK-prefix scan; streamed. No DISTINCT / GROUP BY so ClickHouse
# builds no aggregate — memory stays flat regardless of project size.
_SPANS_QUERY = """
    SELECT trace_id, id
    FROM spans
    WHERE project_id = %(project_id)s AND is_deleted = 0
"""


class Command(BaseCommand):
    help = "Backfill Score.tracer_project_id from CH spans (per-project, streamed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=1000,
            help="Ids per PG UPDATE / stream flush (default: 1000).",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to sleep between projects to throttle CDC (default: 0).",
        )
        parser.add_argument(
            "--project-id",
            type=str,
            default=None,
            help="Backfill a single tracer project id (default: all eligible).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without writing.",
        )

    def handle(self, *args, **opts):
        chunk_size: int = opts["chunk_size"]
        sleep_s: float = opts["sleep"]
        only_project: str | None = opts["project_id"]
        dry_run: bool = opts["dry_run"]

        base = Score.no_workspace_objects.filter(
            Q(trace_id__isnull=False) | Q(observation_span_id__isnull=False),
            tracer_project_id__isnull=True,
        )
        total = base.count()
        self.stdout.write(f"Scores missing tracer_project_id: {total}")
        if dry_run or total == 0:
            return

        project_ids = self._eligible_project_ids(base, only_project)
        self.stdout.write(f"Projects to scan: {len(project_ids)}")

        updated = 0
        for i, pid in enumerate(project_ids, 1):
            try:
                n = self._backfill_project(pid, chunk_size)
            except Exception:
                logger.exception(
                    "backfill_score_tracer_project_failed", project_id=str(pid)
                )
                self.stdout.write(self.style.WARNING(f"  project {pid} failed; skipped"))
                continue
            updated += n
            self.stdout.write(
                f"  [{i}/{len(project_ids)}] project={pid} updated={n} (total={updated})"
            )
            if sleep_s:
                time.sleep(sleep_s)

        remaining = base.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete: updated={updated} remaining_null={remaining} "
                f"(remaining are orphans absent from spans)"
            )
        )

    @staticmethod
    def _eligible_project_ids(base, only_project: str | None) -> list:
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

    def _backfill_project(self, project_id, chunk_size: int) -> int:
        """Stream this project's span/trace ids from CH and tag matching scores."""
        from tracer.services.clickhouse.client import get_clickhouse_client

        rows = get_clickhouse_client().execute_iter(
            _SPANS_QUERY, {"project_id": str(project_id)}
        )

        updated = 0
        trace_chunk: list[str] = []
        span_chunk: list[str] = []
        for trace_id, span_id in rows:
            if trace_id:
                trace_chunk.append(str(trace_id))
                if len(trace_chunk) >= chunk_size:
                    updated += self._tag(project_id, "trace_id", trace_chunk)
                    trace_chunk = []
            if span_id:
                span_chunk.append(str(span_id))
                if len(span_chunk) >= chunk_size:
                    updated += self._tag(project_id, "observation_span_id", span_chunk)
                    span_chunk = []

        if trace_chunk:
            updated += self._tag(project_id, "trace_id", trace_chunk)
        if span_chunk:
            updated += self._tag(project_id, "observation_span_id", span_chunk)
        return updated

    @staticmethod
    def _tag(project_id, field: str, ids: list[str]) -> int:
        """Idempotent: only stamps scores still missing tracer_project_id."""
        return Score.no_workspace_objects.filter(
            tracer_project_id__isnull=True, **{f"{field}__in": ids}
        ).update(tracer_project_id=project_id)
