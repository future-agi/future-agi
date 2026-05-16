"""Attach orphan ``Score`` rows (``queue_item IS NULL``) to a default queue.

Pre-revamp, many code paths wrote Score with no ``queue_item`` attribution
(legacy span-annotation endpoint, agent tools, inline ``/scores/`` writes
before the queue resolver landed). Under the new per-queue uniqueness
contract those rows are invisible in every queue's drawer/history/export —
the read path strictly filters by ``queue_item == this item``.

This command iterates orphans grouped by source, resolves the default
queue for the source's scope (creating one if missing), gets-or-creates
a ``QueueItem`` on that queue for the source, and updates each orphan's
``queue_item_id`` to point at it. Source-level scope (project / dataset /
agent_definition) is the same resolution the live write paths use via
``resolve_default_queue_item_for_source``.

Safe to run repeatedly: the filter ``queue_item__isnull=True`` makes
the work idempotent — re-running finds nothing.

Collision policy: if a per-queue Score already occupies the destination
key ``(source, label, annotator, default_item)``, the orphan is
soft-deleted (the per-queue row is newer / more authoritative).
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from model_hub.utils.annotation_queue_helpers import (
    resolve_default_queue_item_for_source,
)


class Command(BaseCommand):
    help = "Attach orphan Score rows to the default queue's item for their source."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without writing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of orphans to process per transaction (default 500).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        orphan_qs = Score.objects.filter(
            queue_item__isnull=True, deleted=False
        ).select_related(
            "trace",
            "observation_span",
            "trace_session",
            "call_execution",
            "prototype_run",
            "dataset_row",
            "annotator",
            "organization",
        )

        total = orphan_qs.count()
        self.stdout.write(self.style.NOTICE(f"Found {total} orphan Score rows."))
        if total == 0:
            return

        # Group orphans by (source_type, source_id) so we resolve the
        # queue + queue_item once per group instead of per-row.
        by_source = defaultdict(list)
        for score in orphan_qs.iterator(chunk_size=batch_size):
            fk_field = SCORE_SOURCE_FK_MAP.get(score.source_type)
            if not fk_field:
                continue
            source_id = getattr(score, f"{fk_field}_id", None)
            if not source_id:
                continue
            by_source[(score.source_type, str(source_id))].append(score)

        stats = {
            "attached": 0,
            "skipped_no_scope": 0,
            "soft_deleted_collision": 0,
            "queues_created_or_reused": 0,
            "items_created_or_reused": 0,
        }

        for (source_type, _source_id), scores in by_source.items():
            sample = scores[0]
            fk_field = SCORE_SOURCE_FK_MAP[source_type]
            source_obj = getattr(sample, fk_field, None)
            if source_obj is None:
                stats["skipped_no_scope"] += len(scores)
                continue

            # Use the first score's annotator as the "creator" for any
            # auto-created queue. The queue is shared across annotators
            # anyway — created_by is only meta.
            creator = sample.annotator or None
            if creator is None:
                # SpanNotes/agent-auto can be annotator-less; fall back to
                # one of the org's members via the organization owner.
                creator = sample.organization.owner if hasattr(
                    sample.organization, "owner"
                ) else None
            if creator is None:
                stats["skipped_no_scope"] += len(scores)
                continue

            with transaction.atomic():
                queue_item = resolve_default_queue_item_for_source(
                    source_type,
                    source_obj,
                    sample.organization,
                    creator,
                )
                if queue_item is None:
                    stats["skipped_no_scope"] += len(scores)
                    if dry_run:
                        transaction.set_rollback(True)
                    continue
                stats["items_created_or_reused"] += 1

                for score in scores:
                    # Detect collisions up-front so dry-run reports
                    # accurately and the real run avoids round-tripping
                    # an IntegrityError.
                    fk_lookup = {f"{fk_field}_id": getattr(score, f"{fk_field}_id")}
                    collision = (
                        Score.objects.filter(
                            **fk_lookup,
                            label=score.label,
                            annotator=score.annotator,
                            queue_item=queue_item,
                            deleted=False,
                        )
                        .exclude(pk=score.pk)
                        .exists()
                    )
                    if dry_run:
                        if collision:
                            stats["soft_deleted_collision"] += 1
                        else:
                            stats["attached"] += 1
                        continue
                    if collision:
                        score.queue_item = None
                        score.deleted = True
                        score.deleted_at = timezone.now()
                        score.save(
                            update_fields=[
                                "queue_item",
                                "deleted",
                                "deleted_at",
                                "updated_at",
                            ]
                        )
                        stats["soft_deleted_collision"] += 1
                    else:
                        score.queue_item = queue_item
                        score.save(update_fields=["queue_item", "updated_at"])
                        stats["attached"] += 1

                if dry_run:
                    transaction.set_rollback(True)

        self.stdout.write("")
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}Score backfill summary"))
        self.stdout.write(f"  Total orphans:                   {total}")
        self.stdout.write(f"  Attached to default queue item:  {stats['attached']}")
        self.stdout.write(
            f"  Skipped (no resolvable scope):   {stats['skipped_no_scope']}"
        )
        self.stdout.write(
            f"  Soft-deleted (collision):        {stats['soft_deleted_collision']}"
        )
        self.stdout.write(
            f"  Queue items created or reused:   {stats['items_created_or_reused']}"
        )
