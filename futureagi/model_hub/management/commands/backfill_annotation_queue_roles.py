"""
Backfill annotation queue member multi-role data.

Usage:
    python manage.py backfill_annotation_queue_roles
    python manage.py backfill_annotation_queue_roles --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    normalize_annotator_roles,
    primary_annotator_role,
)
from model_hub.models.choices import AnnotatorRole

CREATOR_FULL_ACCESS_ROLES = [
    AnnotatorRole.MANAGER.value,
    AnnotatorRole.REVIEWER.value,
    AnnotatorRole.ANNOTATOR.value,
]


def merge_roles(*role_groups):
    merged = []
    for group in role_groups:
        for role in normalize_annotator_roles(group, default=None):
            if role not in merged:
                merged.append(role)
    return normalize_annotator_roles(merged, default=None)


class Command(BaseCommand):
    help = (
        "Backfill AnnotationQueueAnnotator.roles from legacy role values and "
        "ensure queue creators have manager, reviewer, and annotator roles."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the changes that would be made without writing them.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with transaction.atomic():
            updated = self._backfill_existing_memberships(dry_run=dry_run)
            created = self._create_missing_creator_memberships(dry_run=dry_run)

            if dry_run:
                transaction.set_rollback(True)

        summary = (
            f"Annotation queue role backfill complete: "
            f"{updated} memberships updated, {created} creator memberships created."
        )
        if dry_run:
            summary = f"DRY RUN: {summary}"
        self.stdout.write(self.style.SUCCESS(summary))

    def _backfill_existing_memberships(self, *, dry_run):
        updated = 0
        memberships = (
            AnnotationQueueAnnotator.all_objects.select_related("queue")
            .filter(queue__isnull=False, user__isnull=False)
            .iterator()
        )

        for membership in memberships:
            roles = normalize_annotator_roles(membership.roles or membership.role)
            if (
                membership.queue_id
                and membership.user_id
                and membership.queue.created_by_id == membership.user_id
            ):
                roles = merge_roles(roles, CREATOR_FULL_ACCESS_ROLES)

            primary_role = primary_annotator_role(roles)
            if membership.role == primary_role and membership.normalized_roles == roles:
                continue

            updated += 1
            if dry_run:
                continue
            membership.role = primary_role
            membership.roles = roles
            membership.save(update_fields=["role", "roles", "updated_at"])

        return updated

    def _create_missing_creator_memberships(self, *, dry_run):
        created = 0
        queues = (
            AnnotationQueue.objects.filter(created_by__isnull=False)
            .only("id", "created_by_id")
            .iterator()
        )

        for queue in queues:
            exists = AnnotationQueueAnnotator.objects.filter(
                queue=queue,
                user_id=queue.created_by_id,
                deleted=False,
            ).exists()
            if exists:
                continue

            created += 1
            if dry_run:
                continue
            AnnotationQueueAnnotator.objects.create(
                queue=queue,
                user_id=queue.created_by_id,
                role=AnnotatorRole.MANAGER.value,
                roles=CREATOR_FULL_ACCESS_ROLES,
            )

        return created
