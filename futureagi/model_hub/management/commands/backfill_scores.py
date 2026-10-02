"""
Management command to backfill existing TraceAnnotation and ItemAnnotation
records into the unified Score model.

ORDERING DEPENDENCY: this creates trace/span Scores WITHOUT
``tracer_project_id``, so any rows it writes are invisible to PG annotation-label
discovery until stamped. If this is ever run after
``backfill_score_tracer_project``, re-run that command afterwards to stamp the
new rows.

Usage:
    python manage.py backfill_scores                       # both sources
    python manage.py backfill_scores --source trace        # only TraceAnnotation
    python manage.py backfill_scores --source item         # only ItemAnnotation
    python manage.py backfill_scores --batch-size 500      # custom batch size
    python manage.py backfill_scores --dry-run             # preview counts only
"""

import structlog
from django.core.management.base import BaseCommand
from django.db import transaction

from model_hub.models.annotation_queues import ItemAnnotation, QueueItem
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.score import Score
from tracer.models.trace_annotation import TraceAnnotation

logger = structlog.get_logger(__name__)


def _trace_annotation_to_score_value(ta):
    """Convert TraceAnnotation typed fields → Score JSON value."""
    label_type = ta.annotation_label.type

    if label_type in (
        AnnotationTypeChoices.NUMERIC.value,
        AnnotationTypeChoices.STAR.value,
    ):
        if ta.annotation_value_float is not None:
            key = (
                "rating" if label_type == AnnotationTypeChoices.STAR.value else "value"
            )
            return {key: ta.annotation_value_float}
        return {"value": 0}

    if label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        if ta.annotation_value_bool is not None:
            return {"value": "up" if ta.annotation_value_bool else "down"}
        return {"value": "up"}

    if label_type == AnnotationTypeChoices.CATEGORICAL.value:
        if ta.annotation_value_str_list:
            return {"selected": ta.annotation_value_str_list}
        return {"selected": []}

    # TEXT or unknown
    return {"text": ta.annotation_value or ""}


class Command(BaseCommand):
    help = "Backfill TraceAnnotation and ItemAnnotation records into Score."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=["trace", "item", "both"],
            default="both",
            help="Which source to backfill (default: both)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Number of records per batch (default: 200)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print counts only, do not write",
        )

    def handle(self, *args, **options):
        source = options["source"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        if source in ("trace", "both"):
            self._backfill_trace_annotations(batch_size, dry_run)
        if source in ("item", "both"):
            self._backfill_item_annotations(batch_size, dry_run)

    def _backfill_trace_annotations(self, batch_size, dry_run):
        qs = (
            TraceAnnotation.objects.filter(deleted=False)
            .select_related(
                "annotation_label",
                "observation_span__trace__project__organization",
                "trace__project__organization",
                "user",
            )
            .order_by("created_at")
        )
        total = qs.count()
        self.stdout.write(f"TraceAnnotation records to process: {total}")
        if dry_run:
            return

        created = 0
        skipped = 0
        offset = 0

        while offset < total:
            batch = list(qs[offset : offset + batch_size])
            if not batch:
                break

            for ta in batch:
                obs_span = ta.observation_span
                trace_obj = ta.trace

                # Skip if BOTH observation_span and trace are NULL
                if not obs_span and not trace_obj:
                    skipped += 1
                    continue

                value = _trace_annotation_to_score_value(ta)

                # Determine source_type, source FK kwargs, and org.
                # NOTE: Score.project FK points to DevelopAI, not tracer.Project,
                # so we do NOT set project from trace annotations.
                org = None
                if obs_span:
                    source_type = "observation_span"
                    source_kwargs = {"observation_span_id": obs_span.pk}
                    if obs_span.trace and hasattr(obs_span.trace, "project"):
                        tracer_proj = obs_span.trace.project
                        org = tracer_proj.organization if tracer_proj else None
                else:
                    source_type = "trace"
                    source_kwargs = {"trace_id": trace_obj.pk}
                    if hasattr(trace_obj, "project"):
                        tracer_proj = trace_obj.project
                        org = tracer_proj.organization if tracer_proj else None

                if not org and ta.user:
                    org = ta.user.organization

                if not org:
                    skipped += 1
                    continue

                try:
                    with transaction.atomic():
                        Score.no_workspace_objects.update_or_create(
                            **source_kwargs,
                            label_id=ta.annotation_label.pk,
                            annotator_id=ta.user.pk if ta.user else None,
                            deleted=False,
                            defaults={
                                "source_type": source_type,
                                "value": value,
                                "score_source": "human",
                                "notes": "",
                                "organization": org,
                            },
                        )
                    created += 1
                except Exception:
                    logger.exception(
                        "backfill_trace_annotation_failed",
                        trace_annotation_id=str(ta.id),
                    )
                    skipped += 1

            offset += batch_size
            self.stdout.write(f"  processed {min(offset, total)}/{total}")

        self.stdout.write(
            self.style.SUCCESS(
                f"TraceAnnotation backfill complete: {created} created, {skipped} skipped"
            )
        )

    def _backfill_item_annotations(self, batch_size, dry_run):
        qs = (
            ItemAnnotation.objects.filter(deleted=False)
            .select_related("queue_item", "label", "annotator")
            .order_by("created_at")
        )
        total = qs.count()
        self.stdout.write(f"ItemAnnotation records to process: {total}")
        if dry_run:
            return

        created = 0
        skipped = 0
        offset = 0

        while offset < total:
            batch = list(qs[offset : offset + batch_size])
            if not batch:
                break

            for ia in batch:
                qi = ia.queue_item
                if not qi:
                    skipped += 1
                    continue

                # Resolve the source FK from the QueueItem
                source_type = qi.source_type
                fk_field = {
                    "trace": "trace",
                    "observation_span": "observation_span",
                    "trace_session": "trace_session",
                    "call_execution": "call_execution",
                    "prototype_run": "prototype_run",
                    "dataset_row": "dataset_row",
                }.get(source_type)

                if not fk_field:
                    skipped += 1
                    continue

                source_obj = getattr(qi, fk_field, None)
                if not source_obj:
                    skipped += 1
                    continue

                try:
                    with transaction.atomic():
                        Score.no_workspace_objects.update_or_create(
                            **{f"{fk_field}_id": source_obj.pk},
                            label_id=ia.label.pk,
                            annotator_id=ia.annotator.pk if ia.annotator else None,
                            deleted=False,
                            defaults={
                                "source_type": source_type,
                                "value": ia.value,
                                "score_source": ia.score_source or "human",
                                "notes": ia.notes or "",
                                "organization": ia.organization,
                                "queue_item": qi,
                            },
                        )
                    created += 1
                except Exception:
                    logger.exception(
                        "backfill_item_annotation_failed",
                        item_annotation_id=str(ia.id),
                    )
                    skipped += 1

            offset += batch_size
            self.stdout.write(f"  processed {min(offset, total)}/{total}")

        self.stdout.write(
            self.style.SUCCESS(
                f"ItemAnnotation backfill complete: {created} created, {skipped} skipped"
            )
        )
