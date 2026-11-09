"""Approved bundles stay retention-locked for as long as their entry is live (HUB-19).

`storage.ObjectStore.put` writes a bundle under a 90-day GOVERNANCE retention. Without renewal that
lock lapses on day 90, and from then on the app credential, which may delete object versions (for
erasure and for retention), could permanently delete an approved pin. The daily retention run
(`omega_hub_retention`) therefore extends the lock on every retained version of each live entry's
bundle before it comes within RENEW_WITHIN_DAYS of lapsing. GOVERNANCE retention can always be
extended without a bypass right, and the app credential holds none (ops/iam/app-policy.json), so no
code path of the app can shorten or lift a lock. A removed entry's bundles are no longer renewed:
their lock lapses and retention purges them once the entry has been removed for 90 days.

The operations check pages `omega_hub_bundle_lock_renewal_overdue` when a live bundle's lock is
within ALERT_WITHIN_DAYS of lapsing, or an approval older than UNRENEWED_GRACE has never been
renewed, so a stopped retention schedule is seen weeks before any lock lapses.

Only an object store with object lock (S3, MinIO) has locks. GCS interop and the development
filesystem store have none, and nothing here applies to them.
"""

from __future__ import annotations

from datetime import timedelta

import structlog
from django.db.models import Q
from django.utils import timezone

from omega_hub.models import Entry, Version
from omega_hub.services import storage, storage_provision

logger = structlog.get_logger(__name__)

LOCK_DAYS = storage.ObjectStore.BUNDLE_RETENTION_DAYS
RENEW_WITHIN_DAYS = 30
ALERT_WITHIN_DAYS = 14
UNRENEWED_GRACE = timedelta(days=2)


def applies(store=None) -> bool:
    store = store if store is not None else storage.get_store()
    return isinstance(store, storage.ObjectStore) and storage_provision.object_lock_supported()


def _live():
    return Version.objects.exclude(entry__status=Entry.Status.REMOVED).exclude(bundle_key="")


def due_keys(now) -> list[str]:
    """Live bundle keys never renewed, or whose lock lapses within RENEW_WITHIN_DAYS."""
    due = _live().filter(Q(bundle_locked_until__isnull=True)
                         | Q(bundle_locked_until__lt=now + timedelta(days=RENEW_WITHIN_DAYS)))
    return list(due.order_by("bundle_key").values_list("bundle_key", flat=True).distinct())


def renew_due(store=None, *, now=None, dry_run: bool = False) -> dict:
    """Extend each due live bundle's lock to now + LOCK_DAYS. One key's failure is logged and counted;
    every other key still runs (the retention command then fails, so the next night retries)."""
    store = store if store is not None else storage.get_store()
    now = now or timezone.now()
    if not applies(store):
        return {"renewed": 0, "failed": 0, "applies": False}
    keys = due_keys(now)
    if dry_run:
        return {"renewed": len(keys), "failed": 0, "applies": True}
    until = now + timedelta(days=LOCK_DAYS)
    renewed = failed = 0
    for key in keys:
        try:
            store.extend_retention(key, until)
        except Exception as exc:  # noqa: BLE001 - isolated per key, reported by the caller
            failed += 1
            logger.error("omega_hub_bundle_lock_renew_failed", key=key, error=f"{exc.__class__.__name__}: {exc}"[:500])
            continue
        Version.objects.filter(bundle_key=key).update(bundle_locked_until=until)
        renewed += 1
    return {"renewed": renewed, "failed": failed, "applies": True}


def overdue(now=None) -> int:
    """Live bundles whose lock lapses within ALERT_WITHIN_DAYS, or that were never renewed although
    approved more than UNRENEWED_GRACE ago. Zero where locks do not apply."""
    if not applies():
        return 0
    now = now or timezone.now()
    late = _live().filter(Q(bundle_locked_until__lt=now + timedelta(days=ALERT_WITHIN_DAYS))
                          | Q(bundle_locked_until__isnull=True, approved_at__lt=now - UNRENEWED_GRACE))
    return late.values("bundle_key").distinct().count()
