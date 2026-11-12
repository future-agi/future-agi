"""A live entry's bundle stays retention-locked past day 90 (HUB-19, fix round 3).

The verifier found that a bundle's 90-day GOVERNANCE lock was never renewed, while the app credential
may delete object versions: from day 91 an approved pin could be permanently deleted by the app or a
bug in it. The daily retention run now extends the lock on every live entry's bundle before it lapses.
The fake below is test_storage_provision's minio stand-in, with object retention that can be set.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from omega_hub.models import Entry, Version
from omega_hub.services import bundle_locks, metrics, ops, storage
from omega_hub.tests.test_storage_provision import FakeS3


class LockingS3(FakeS3):
    def __init__(self):
        super().__init__()
        self.set_calls: list[tuple[str, str, datetime]] = []

    def set_object_retention(self, bucket, key, config, version_id=None):
        # A GOVERNANCE lock can be extended without a bypass right; shortening one cannot.
        current = self.retention.get(version_id)
        if current is not None and config.retain_until_date < current:
            raise AssertionError("the app tried to shorten a GOVERNANCE lock")
        self.set_calls.append((key, version_id, config.retain_until_date))
        self.retention[version_id] = config.retain_until_date

    def add(self, key: str, *version_ids: str, delete_marker: bool = False):
        self.objects.extend(SimpleNamespace(object_name=key, version_id=v, is_delete_marker=False) for v in version_ids)
        if delete_marker:
            self.objects.append(SimpleNamespace(object_name=key, version_id="marker", is_delete_marker=True))


@pytest.fixture
def s3(monkeypatch):
    """A MinIO-like object store with object lock. `activate()` makes it the Hub's store; a test that
    publishes first does so on the suite's filesystem store, then activates this one."""
    client = LockingS3()
    holder = SimpleNamespace(client=client, store=None)

    def activate():
        monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "minio")
        monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: client)
        holder.store = storage.ObjectStore("omega-hub")
        monkeypatch.setattr(storage, "get_store", lambda: holder.store)
        return holder

    holder.activate = activate
    return holder


def _live_version(world, s3) -> Version:
    from omega_hub.tests.flows import publish

    version = publish(world).latest_version
    s3.activate()
    return version


# The store -------------------------------------------------------------------------------------------


def test_extend_retention_extends_every_retained_version_and_never_shortens(s3):
    s3.activate()
    key = "bundles/" + "a" * 64 + ".tar.gz"
    now = datetime.now(UTC)
    s3.client.add(key, "old", "current", delete_marker=True)
    s3.client.retention = {"old": now + timedelta(days=5), "current": now + timedelta(days=400)}
    until = now + timedelta(days=90)
    assert s3.store.extend_retention(key, until) == 1
    assert s3.client.set_calls == [(key, "old", until)]
    assert s3.client.retention["current"] == now + timedelta(days=400)


def test_extend_retention_refuses_what_is_not_a_bundle_and_a_bundle_with_no_version(s3):
    s3.activate()
    with pytest.raises(ValueError):
        s3.store.extend_retention("raw/upload", datetime.now(UTC))
    with pytest.raises(FileNotFoundError):
        s3.store.extend_retention("bundles/" + "b" * 64 + ".tar.gz", datetime.now(UTC))


def test_the_filesystem_store_has_no_lock_to_extend(tmp_path):
    assert storage.FilesystemStore(str(tmp_path)).extend_retention("bundles/x.tar.gz", datetime.now(UTC)) == 0


# The renewal -----------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_lapsed_lock_on_a_live_bundle_is_renewed_so_purge_can_no_longer_remove_it(world, s3):
    # Day 91 without renewal: the check-time lock has lapsed and purge would delete the approved pin.
    version = _live_version(world, s3)
    now = datetime.now(UTC)
    s3.client.add(version.bundle_key, "v1")
    s3.client.retention = {"v1": now - timedelta(days=1)}
    result = bundle_locks.renew_due(now=timezone.now())
    assert result == {"renewed": 1, "failed": 0, "applies": True}
    version.refresh_from_db()
    assert version.bundle_locked_until > timezone.now() + timedelta(days=89)
    assert s3.client.retention["v1"] == version.bundle_locked_until
    assert s3.store.purge(version.bundle_key) == 1  # deferred: still locked
    assert not [w for w in s3.client.writes if w[0] == "remove_object"]


@pytest.mark.django_db
def test_only_live_bundles_that_are_due_are_renewed(world, s3):
    version = _live_version(world, s3)
    now = timezone.now()
    s3.client.add(version.bundle_key, "v1")
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=now + timedelta(days=60))
    assert bundle_locks.renew_due(now=now)["renewed"] == 0  # 60 days left: not due
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=now + timedelta(days=20))
    assert bundle_locks.renew_due(now=now)["renewed"] == 1
    Entry.objects.filter(pk=version.entry_id).update(status=Entry.Status.REMOVED, removed_at=now)
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=now + timedelta(days=1))
    assert bundle_locks.renew_due(now=now)["renewed"] == 0  # a removed entry's lock is left to lapse


@pytest.mark.django_db
def test_renewal_does_not_apply_without_object_lock(world, monkeypatch):
    from omega_hub.tests.flows import publish

    publish(world)
    assert bundle_locks.renew_due() == {"renewed": 0, "failed": 0, "applies": False}  # filesystem store
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "gcs")
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: FakeS3())
    assert bundle_locks.renew_due(storage.ObjectStore("omega-hub"))["applies"] is False


@pytest.mark.django_db
def test_the_retention_run_renews_and_fails_loudly_when_a_renewal_fails(world, s3):
    version = _live_version(world, s3)
    out = io.StringIO()
    with pytest.raises(CommandError, match="could not purge or renew 1 key"):
        call_command("omega_hub_retention", stdout=out)  # no retained version: an incident, counted
    assert "renewed the lock on 0 live bundles" in out.getvalue()
    s3.client.add(version.bundle_key, "v1")
    out = io.StringIO()
    call_command("omega_hub_retention", stdout=out)
    assert "renewed the lock on 1 live bundles" in out.getvalue()
    dry = io.StringIO()
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=None)
    call_command("omega_hub_retention", "--dry-run", stdout=dry)
    assert "would renew the lock on 1 live bundles" in dry.getvalue()
    assert Version.objects.get(pk=version.pk).bundle_locked_until is None


# The page --------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_stopped_renewal_pages_weeks_before_a_lock_lapses(world, s3):
    version = _live_version(world, s3)
    now = timezone.now()
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=now + timedelta(days=40))
    assert metrics.snapshot(now=now)["omega_hub_bundle_locks_overdue"] == 0
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=now + timedelta(days=10))
    assert metrics.snapshot(now=now)["omega_hub_bundle_locks_overdue"] == 1
    assert "omega_hub_bundle_lock_renewal_overdue" in [a["id"] for a in ops.alerts(now=now)]
    # Never renewed: within the grace after approval it is not late; after it, it is.
    Version.objects.filter(pk=version.pk).update(bundle_locked_until=None, approved_at=now - timedelta(hours=6))
    assert bundle_locks.overdue(now) == 0
    Version.objects.filter(pk=version.pk).update(approved_at=now - timedelta(days=3))
    assert bundle_locks.overdue(now) == 1


@pytest.mark.django_db
def test_bootstrap_renews_live_locks_so_a_new_deploy_is_not_paged_until_the_first_night(world, s3, settings, monkeypatch):
    # Every version approved before this code shipped has no bundle_locked_until; without a bootstrap
    # renewal the operations check would page omega_hub_bundle_lock_renewal_overdue until 02:17.
    from omega_hub.services import storage_provision

    version = _live_version(world, s3)
    s3.client.add(version.bundle_key, "v1")
    Version.objects.filter(pk=version.pk).update(approved_at=timezone.now() - timedelta(days=200))
    assert bundle_locks.overdue() == 1
    settings.TESTING = False
    settings.OMEGA_HUB_STORAGE = "object"
    monkeypatch.setattr(storage_provision, "reconcile", lambda **kwargs: {"ready": True, "bucket": "omega-hub"})
    out = io.StringIO()
    result = storage_provision.provision_after_migrate(stdout=out, verbosity=1)
    assert result["bundleLocks"] == {"renewed": 1, "failed": 0, "applies": True}
    assert "renewed the lock on 1 live bundles" in out.getvalue()
    assert bundle_locks.overdue() == 0


@pytest.mark.django_db
def test_a_bootstrap_renewal_failure_never_fails_the_migration(world, monkeypatch):
    from omega_hub.services import storage_provision

    def boom(*args, **kwargs):
        raise RuntimeError("storage refused")

    monkeypatch.setattr(bundle_locks, "renew_due", boom)
    assert storage_provision._renew_bundle_locks(io.StringIO())["error"] == "RuntimeError"
