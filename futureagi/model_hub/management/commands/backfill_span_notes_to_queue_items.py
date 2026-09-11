"""Mirror legacy ``SpanNotes`` rows into per-queue ``QueueItemNote`` rows.

Pre-revamp, whole-item notes were stored exclusively as ``SpanNotes`` on
the source span. Under the new per-queue scoping:

- The editable "existing notes" box on each queue's drawer reads
  ``QueueItemNote`` scoped to that queue's item.
- ``SpanNotes`` is shown read-only and the requester's own SpanNote is
  filtered out (to avoid the see-but-can't-edit UX trap).

Without backfill, every legacy SpanNote becomes invisible in queue UIs
even though the user wrote it. This command attaches each SpanNote's
content to the default queue's item for the same span, creating a
``QueueItemNote`` so the note reappears in the default queue's editable
field.

Safe to run repeatedly: only creates a QueueItemNote if one doesn't
already exist for (queue_item, annotator).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from model_hub.models.annotation_queues import QueueItemNote
from model_hub.utils.annotation_queue_helpers import (
    resolve_default_queue_item_for_source,
)
from tracer.models.span_notes import SpanNotes


class Command(BaseCommand):
    help = "Mirror legacy SpanNotes into per-queue QueueItemNote rows."

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
            help="Chunk size for iterator (default 500).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        notes_qs = SpanNotes.objects.select_related(
            "span", "span__project", "created_by_user"
        )

        total = notes_qs.count()
        self.stdout.write(self.style.NOTICE(f"Found {total} SpanNotes rows."))
        if total == 0:
            return

        stats = {
            "mirrored": 0,
            "skipped_no_scope": 0,
            "skipped_no_user": 0,
            "skipped_already_mirrored": 0,
        }

        for span_note in notes_qs.iterator(chunk_size=batch_size):
            span = span_note.span
            user = span_note.created_by_user
            if span is None:
                stats["skipped_no_scope"] += 1
                continue
            if user is None:
                stats["skipped_no_user"] += 1
                continue

            organization = getattr(span, "project", None)
            organization = getattr(organization, "organization", None)
            if organization is None:
                stats["skipped_no_scope"] += 1
                continue

            with transaction.atomic():
                queue_item = resolve_default_queue_item_for_source(
                    "observation_span",
                    span,
                    organization,
                    user,
                )
                if queue_item is None:
                    stats["skipped_no_scope"] += 1
                    continue

                existing = QueueItemNote.no_workspace_objects.filter(
                    queue_item=queue_item,
                    annotator=user,
                    deleted=False,
                ).exists()
                if existing:
                    stats["skipped_already_mirrored"] += 1
                    continue

                if dry_run:
                    stats["mirrored"] += 1
                    # Roll back the queue/item creation so dry-run leaves
                    # the database untouched.
                    transaction.set_rollback(True)
                    continue

                QueueItemNote.no_workspace_objects.create(
                    queue_item=queue_item,
                    annotator=user,
                    notes=span_note.notes,
                    organization=organization,
                    workspace=queue_item.workspace,
                )
                stats["mirrored"] += 1

        self.stdout.write("")
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}SpanNote backfill summary"))
        self.stdout.write(f"  Total SpanNotes scanned:           {total}")
        self.stdout.write(f"  Mirrored to QueueItemNote:         {stats['mirrored']}")
        self.stdout.write(
            f"  Skipped (no resolvable scope):     {stats['skipped_no_scope']}"
        )
        self.stdout.write(
            f"  Skipped (no created_by_user):      {stats['skipped_no_user']}"
        )
        self.stdout.write(
            f"  Skipped (already mirrored):        {stats['skipped_already_mirrored']}"
        )
