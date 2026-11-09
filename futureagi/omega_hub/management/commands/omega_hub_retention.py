"""Retention (SPEC §8): raw uploads of non-approved terminal submissions after 30 days; a removed
entry's bundles and casts after 90 days; sent and dead outbox rows after 90 days. Approved bundles
stay forever; blocklist rows stay forever. On an object store with object lock, the run first
extends the lock on every live entry's bundle that is due (services/bundle_locks.py), so an approved
pin stays undeletable by the app credential for as long as its entry is live.

One key's storage error never stops the run: it is logged and counted, every other key and the
outbox cleanup still run, and the command fails at the end (so the scheduled run is marked failed
and the next night retries). A bundle version still inside its object-lock retention is deferred,
not failed: it is purged by a later run once the lock lapses.
"""

from datetime import timedelta

import structlog
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from omega_hub.models import EmailOutbox, EmailOutboxStatus, Entry, Submission, Version
from omega_hub.services import bundle_locks, storage

logger = structlog.get_logger(__name__)

RAW_DAYS = 30
REMOVED_DAYS = 90
OUTBOX_DAYS = 90
NOT_KEPT = (Submission.Status.CHECKS_FAILED, Submission.Status.CHANGES_REQUESTED, Submission.Status.REJECTED,
            Submission.Status.WITHDRAWN, Submission.Status.SUPERSEDED, Submission.Status.CHECK_ERROR)


class Command(BaseCommand):
    help = "Delete expired Omega Hub uploads and removed entries' bundles."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def _purge(self, store, key: str, counts: dict) -> bool:
        """Purge one key; True when nothing is left of it."""
        try:
            deferred = store.purge(key) or 0
        except Exception as exc:  # noqa: BLE001 - isolated per key, reported at the end
            counts["failed"] += 1
            logger.error("omega_hub_retention_purge_failed", key=key, error=f"{exc.__class__.__name__}: {exc}"[:500])
            return False
        counts["deferred"] += deferred
        return deferred == 0

    def handle(self, *args, dry_run, **options):
        store = storage.get_store()
        now = timezone.now()
        counts = {"raw": 0, "files": 0, "deferred": 0, "failed": 0}
        locks = bundle_locks.renew_due(store, now=now, dry_run=dry_run)
        counts["failed"] += locks["failed"]
        raws = Submission.objects.filter(status__in=NOT_KEPT, updated_at__lt=now - timedelta(days=RAW_DAYS)).exclude(raw_key="")
        for submission in raws:
            if dry_run:
                counts["raw"] += 1
            elif self._purge(store, submission.raw_key, counts):
                counts["raw"] += 1
                Submission.objects.filter(pk=submission.pk).update(raw_key="")
        old_removed = Entry.objects.filter(status=Entry.Status.REMOVED, removed_at__lt=now - timedelta(days=REMOVED_DAYS))
        for version in Version.objects.filter(entry__in=old_removed):
            for key in (version.bundle_key, version.cast_key):
                if not key:
                    continue
                in_use = Version.objects.exclude(entry__status=Entry.Status.REMOVED).filter(bundle_key=key).exists()
                try:
                    present = not in_use and store.exists(key)
                except Exception as exc:  # noqa: BLE001
                    counts["failed"] += 1
                    logger.error("omega_hub_retention_purge_failed", key=key, error=f"{exc.__class__.__name__}: {exc}"[:500])
                    continue
                if present and (dry_run or self._purge(store, key, counts)):
                    counts["files"] += 1
        old_outbox = EmailOutbox.objects.filter(
            status__in=(EmailOutboxStatus.SENT, EmailOutboxStatus.DEAD),
            updated_at__lt=now - timedelta(days=OUTBOX_DAYS),
        )
        outbox_count = old_outbox.count()
        if not dry_run:
            old_outbox.delete()
        verb = "would delete" if dry_run else "deleted"
        renewal = (f"; {'would renew' if dry_run else 'renewed'} the lock on {locks['renewed']} live bundles"
                   if locks["applies"] else "")
        self.stdout.write(
            f"{verb} {counts['raw']} raw uploads, {counts['files']} removed-entry files, "
            f"and {outbox_count} old email rows; {counts['deferred']} locked bundle versions deferred, "
            f"{counts['failed']} keys failed{renewal}"
        )
        if counts["failed"]:
            raise CommandError(f"retention could not purge or renew {counts['failed']} key(s); see "
                               "omega_hub_retention_purge_failed and omega_hub_bundle_lock_renew_failed")
