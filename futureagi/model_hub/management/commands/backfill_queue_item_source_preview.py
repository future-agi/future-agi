"""
Backfill ``QueueItem.source_preview`` for existing CH-backed queue items.

New items capture the preview at add time from the source the add path already
resolved. Historic rows are NULL, so every render of them still pays a
``spans FINAL`` merge — ~1.5-2.3s per page on a large voice project (TH-7211).
This stamps them so the grid stops reading ClickHouse to show a name and two
200-char previews.

Only the three CH-backed source types are stamped (``trace`` /
``observation_span`` / ``trace_session``). ``dataset_row`` / ``prototype_run`` /
``call_execution`` resolve from prefetched Postgres rows and were never the
problem, so they are left NULL and keep using the live path.

The core logic lives in :func:`backfill_queue_item_source_previews` so the data
migration (``model_hub/migrations/0122_backfill_queueitem_source_preview.py``)
and this command share one idempotent implementation — same split as
``backfill_score_tracer_project``.

Design — why this can't OOM, time out, or break a deploy:

Work is grouped by ``(project_id, source_type)`` and processed in fixed-size
chunks, so each ClickHouse read is PK-prefix pruned to one tenant and bounded by
``chunk_size`` ids. :class:`CollectorSourceCache` already batches a chunk into
one read per kind. Python holds at most one chunk; PG takes one ``bulk_update``
per chunk, and the migration runs ``atomic = False`` so it is never one giant
transaction. Updates are guarded by ``source_preview__isnull=True``, so re-runs
and partial-failure retries are no-ops.

FAIL OPEN: a chunk whose CH read fails is logged and skipped, and the whole
backfill swallows an unexpected error rather than raising. Those rows stay NULL
and keep rendering via the live fallback — exactly as if this had never run. A
ClickHouse outage must never fail a deploy for a pure cache warm-up.

Usage:
    python manage.py backfill_queue_item_source_preview
    python manage.py backfill_queue_item_source_preview --dry-run
    python manage.py backfill_queue_item_source_preview --chunk-size 200
    python manage.py backfill_queue_item_source_preview --sleep 0.2
    python manage.py backfill_queue_item_source_preview --queue-id <uuid>
"""

import time

import structlog
from django.core.management.base import BaseCommand

logger = structlog.get_logger(__name__)

# The source types whose render costs a ClickHouse read.
CH_SOURCE_TYPES = ("trace", "observation_span", "trace_session")


def backfill_queue_item_source_previews(
    *, chunk_size=200, sleep=0.0, queue_id=None, write=None
):
    """Stamp ``source_preview`` on CH-backed items that lack one.

    Returns ``(stamped, skipped, failed)``. Never raises — see the module
    docstring on why a cache warm-up must not break a deploy.
    """
    from model_hub.models.annotation_queues import QueueItem
    from model_hub.utils.annotation_queue_helpers import (
        CollectorSourceCache,
        resolve_source_preview,
    )

    emit = write or (lambda _msg: None)
    chunk_size = max(1, chunk_size)
    sleep = max(0.0, sleep)
    stamped = skipped = failed = 0

    try:
        base = QueueItem.all_objects.filter(
            deleted=False,
            source_preview__isnull=True,
            source_type__in=CH_SOURCE_TYPES,
        )
        if queue_id:
            base = base.filter(queue_id=queue_id)

        # Group so each CH read is scoped to one tenant on the spans PK prefix.
        # DISTINCT in the DB: without it this streams one row per matching item
        # into Python just to dedup them into a set.
        groups = sorted(
            base.values_list("project_id", "source_type").distinct(),
            key=lambda g: (str(g[0]), g[1]),
        )

        for project_id, source_type in groups:
            if project_id is None:
                # project_id is nullable (pre-denormalization rows). Reading this
                # group means an unscoped, cross-tenant ``spans FINAL`` — the exact
                # shape this whole change exists to avoid. Leave them NULL; they
                # keep rendering via the live fallback.
                n = group_count = base.filter(
                    project_id=None, source_type=source_type
                ).count()
                skipped += n
                logger.info(
                    "queue_item_preview_backfill_skipped_null_project",
                    source_type=source_type,
                    count=group_count,
                )
                emit(f"  project=None {source_type}: skipped={n} (unscoped read)")
                continue
            group_qs = base.filter(project_id=project_id, source_type=source_type)
            # Stamped rows leave the queryset (the isnull guard), so the window
            # does not need to advance for them. Rows we deliberately skip DO
            # stay, so step past exactly those or the same chunk repeats forever.
            offset = 0
            while True:
                chunk = list(group_qs.order_by("id")[offset : offset + chunk_size])
                if not chunk:
                    break
                # One handler for the whole chunk: a PG failure on the write must
                # skip this chunk exactly like a CH failure on the read, instead
                # of escaping to the outer handler and killing every remaining
                # group.
                try:
                    cache = CollectorSourceCache.for_items(chunk)

                    to_update = []
                    chunk_skipped = 0
                    for item in chunk:
                        payload = resolve_source_preview(item, ch_cache=cache)
                        # A source that no longer resolves comes back as the
                        # ``deleted`` sentinel. Don't freeze that into the cache —
                        # leave it NULL so the live path keeps telling the truth.
                        if (
                            not payload
                            or payload.get("deleted")
                            or payload.get("error")
                        ):
                            chunk_skipped += 1
                            continue
                        item.source_preview = payload
                        to_update.append(item)

                    if to_update:
                        QueueItem.all_objects.bulk_update(
                            to_update, ["source_preview"], batch_size=chunk_size
                        )
                        stamped += len(to_update)
                except Exception as exc:
                    failed += len(chunk)
                    logger.warning(
                        "queue_item_preview_backfill_chunk_failed",
                        project_id=str(project_id),
                        source_type=source_type,
                        count=len(chunk),
                        error=str(exc),
                    )
                    break

                skipped += chunk_skipped
                offset += chunk_skipped
                if sleep:
                    time.sleep(sleep)

            emit(
                f"  project={project_id} {source_type}: "
                f"stamped={stamped} skipped={skipped} failed={failed}"
            )
    except Exception as exc:
        # Fail open: a cache warm-up must never break a deploy.
        logger.warning("queue_item_preview_backfill_aborted", error=str(exc))

    return stamped, skipped, failed


class Command(BaseCommand):
    help = "Backfill QueueItem.source_preview for CH-backed items (TH-7211)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--chunk-size", type=int, default=200)
        parser.add_argument("--sleep", type=float, default=0.0)
        parser.add_argument("--queue-id", type=str, default=None)

    def handle(self, *args, **options):
        from model_hub.models.annotation_queues import QueueItem

        base = QueueItem.all_objects.filter(
            deleted=False,
            source_preview__isnull=True,
            source_type__in=CH_SOURCE_TYPES,
        )
        if options["queue_id"]:
            base = base.filter(queue_id=options["queue_id"])

        total = base.count()
        self.stdout.write(f"queue items needing a preview: {total}")
        if options["dry_run"]:
            by_group = {}
            for pid, stype in base.values_list("project_id", "source_type"):
                by_group[(str(pid), stype)] = by_group.get((str(pid), stype), 0) + 1
            for (pid, stype), n in sorted(by_group.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f"  {n:>8}  project={pid} source_type={stype}")
            return
        if not total:
            return

        stamped, skipped, failed = backfill_queue_item_source_previews(
            chunk_size=options["chunk_size"],
            sleep=options["sleep"],
            queue_id=options["queue_id"],
            write=self.stdout.write,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"done. stamped={stamped} skipped(unresolvable)={skipped} "
                f"failed(ch error)={failed}"
            )
        )
