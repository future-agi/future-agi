from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from omega_hub.models import CheckJob, EmailOutbox, EmailOutboxStatus, Submission
from omega_hub.services import metrics, ops, outbox, storage
from omega_hub.temporal import deliver_outbox

pytestmark = pytest.mark.django_db


def test_email_outbox_delivers_on_third_attempt(world):
    now = timezone.now()
    row = EmailOutbox.objects.create(
        template="test",
        recipients=[world.maint_a.email],
        subject="Hub test",
        body="retry",
        next_attempt_at=now,
    )
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("ESP unavailable")
        return 1

    assert outbox.deliver_one(str(row.pk), send=send, now=now) == EmailOutboxStatus.PENDING
    EmailOutbox.objects.filter(pk=row.pk).update(next_attempt_at=now)
    assert outbox.deliver_one(str(row.pk), send=send, now=now + timedelta(seconds=2)) == EmailOutboxStatus.PENDING
    EmailOutbox.objects.filter(pk=row.pk).update(next_attempt_at=now)
    assert outbox.deliver_one(str(row.pk), send=send, now=now + timedelta(seconds=8)) == EmailOutboxStatus.SENT
    assert len(calls) == 3
    assert EmailOutbox.objects.get(pk=row.pk).attempts == 3


def test_outbox_uses_django_email_backend(world):
    mail.outbox.clear()
    row = EmailOutbox.objects.create(
        template="test",
        recipients=[world.maint_a.email],
        subject="Hub test",
        body="delivered",
    )
    assert outbox.deliver_one(str(row.pk)) == EmailOutboxStatus.SENT
    assert len(mail.outbox) == 1


def test_temporal_outbox_activity_reaches_third_attempt(world, monkeypatch):
    row = EmailOutbox.objects.create(
        template="test",
        recipients=[world.maint_a.email],
        subject="Hub test",
        body="retry",
    )
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("ESP unavailable")
        return 1

    from omega_hub.services import notify

    monkeypatch.setattr(notify, "_deliver_message", send)
    now = timezone.now()
    assert deliver_outbox._original_func(str(row.pk))["status"] == EmailOutboxStatus.PENDING
    EmailOutbox.objects.filter(pk=row.pk).update(next_attempt_at=now)
    assert deliver_outbox._original_func(str(row.pk))["status"] == EmailOutboxStatus.PENDING
    EmailOutbox.objects.filter(pk=row.pk).update(next_attempt_at=now)
    assert deliver_outbox._original_func(str(row.pk))["status"] == EmailOutboxStatus.SENT
    assert len(calls) == 3


def test_expired_running_job_is_reaped_without_claim(world):
    now = timezone.now()
    submission = Submission.objects.create(
        publisher=world.official,
        submitted_by=world.maint_a.user,
        channel=Submission.Channel.WEB,
        raw_sha256="c" * 64,
        raw_bytes=1,
        status=Submission.Status.CHECKING,
    )
    job = CheckJob.objects.create(
        submission=submission,
        status=CheckJob.Status.RUNNING,
        attempt=1,
        leased_by="dead-checker",
        claimed_at=now - timedelta(hours=2),
        lease_expires_at=now - timedelta(minutes=1),
    )
    assert ops.check_and_page(now=now)["reaped"] == 1
    job.refresh_from_db()
    assert job.status == CheckJob.Status.QUEUED
    assert job.lease_expires_at is None


def test_stalled_checker_alert_pages_once(world, monkeypatch):
    now = timezone.now()
    submission = Submission.objects.create(
        publisher=world.official,
        submitted_by=world.maint_a.user,
        channel=Submission.Channel.WEB,
        raw_sha256="b" * 64,
        raw_bytes=1,
        status=Submission.Status.RECEIVED,
    )
    job = CheckJob.objects.create(submission=submission, status=CheckJob.Status.QUEUED)
    CheckJob.objects.filter(pk=job.pk).update(created_at=now - timedelta(minutes=11), queued_at=now - timedelta(minutes=11))
    sent = []
    monkeypatch.setattr(ops.notify, "ops_alert", lambda alert: sent.append(alert) or True)
    result = ops.check_and_page(now=now)
    assert result["alerts"][0]["id"] == "omega_hub_checker_stalled"
    assert result["paged"] == 1
    assert ops.check_and_page(now=now)["paged"] == 0
    assert len(sent) == 1


def test_hub_metrics_are_state_derived(world):
    now = timezone.now()
    submission = Submission.objects.create(
        publisher=world.official,
        submitted_by=world.maint_a.user,
        channel=Submission.Channel.WEB,
        raw_sha256="a" * 64,
        raw_bytes=1,
        status=Submission.Status.IN_REVIEW,
    )
    job = CheckJob.objects.create(submission=submission, status=CheckJob.Status.QUEUED)
    CheckJob.objects.filter(pk=job.pk).update(created_at=now - timedelta(minutes=12), queued_at=now - timedelta(minutes=12))
    values = metrics.snapshot(now=now)
    assert values["omega_hub_checker_queued_jobs"] == 1
    assert values["omega_hub_checker_oldest_queued_age_seconds"] >= 719
    assert values["omega_hub_checker_seconds_since_last_claim"] == 0
    job.claimed_at = now - timedelta(minutes=20)
    job.save(update_fields=["claimed_at"])
    assert metrics.snapshot(now=now)["omega_hub_checker_seconds_since_last_claim"] >= 1199


def test_metrics_endpoint_uses_a_dedicated_read_only_secret(world):
    assert world.c.get("/omega-hub/internal/ops/metrics").status_code == 401
    from omega_hub.tests.helpers import checker_client, metrics_client

    assert checker_client().get("/omega-hub/internal/ops/metrics").status_code == 401
    response = metrics_client().get("/omega-hub/internal/ops/metrics")
    assert response.status_code == 200
    assert b"omega_hub_checker_queued_jobs" in response.content


def test_no_page_is_deduped_when_no_maintainer_can_receive_it(world, monkeypatch):
    now = timezone.now()
    submission = Submission.objects.create(
        publisher=world.official,
        submitted_by=world.maint_a.user,
        channel=Submission.Channel.WEB,
        raw_sha256="d" * 64,
        raw_bytes=1,
        status=Submission.Status.RECEIVED,
    )
    CheckJob.objects.create(submission=submission, status=CheckJob.Status.QUEUED)
    from django.contrib.auth.models import Permission

    Permission.objects.filter(codename="hub_maintainer").delete()
    result = ops.check_and_page(now=now + timedelta(minutes=30))
    assert result["paged"] == 0
    assert EmailOutbox.objects.count() == 0


def test_filesystem_store_can_restore_a_previous_version(tmp_path):
    store = storage.FilesystemStore(str(tmp_path))
    first = store.put("bundles/pin.tar.gz", io.BytesIO(b"v1"))
    second = store.put("bundles/pin.tar.gz", io.BytesIO(b"v2"))
    # Every put is a retained version, newest first, as on a versioned bucket.
    assert store.versions("bundles/pin.tar.gz") == [second, first]
    store.delete("bundles/pin.tar.gz")
    store.restore("bundles/pin.tar.gz", first)
    with store.open("bundles/pin.tar.gz") as restored:
        assert restored.read() == b"v1"


def test_filesystem_store_restores_a_pin_deleted_after_one_put(tmp_path):
    # The HUB-19 drill: an approved bundle is written once; delete it, restore its version, and the
    # same bytes come back. Round 3's store kept no version of a first put, so this could not work.
    store = storage.FilesystemStore(str(tmp_path))
    data = b"approved bundle"
    key = storage.bundle_key(storage.sha256_hex(data))
    version = store.put(key, io.BytesIO(data), storage.sha256_hex(data))
    assert store.versions(key) == [version]
    store.delete(key)
    assert not store.exists(key)
    assert store.versions(key) == [version]
    store.restore(key, version)
    with store.open(key) as restored:
        assert storage.sha256_hex(restored.read()) == storage.sha256_hex(data)


def test_filesystem_purge_removes_all_raw_versions(tmp_path):
    store = storage.FilesystemStore(str(tmp_path))
    store.put("raw/upload.tar.gz", io.BytesIO(b"old"))
    store.put("raw/upload.tar.gz", io.BytesIO(b"new"))
    assert store.versions("raw/upload.tar.gz")
    store.purge("raw/upload.tar.gz")
    assert not store.exists("raw/upload.tar.gz")
    assert store.versions("raw/upload.tar.gz") == []


def test_object_store_put_does_not_need_bucket_admin(monkeypatch):
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "minio")
    calls = []

    class Result:
        version_id = "object-version"

    class Client:
        def put_object(self, bucket, key, data, length, **kwargs):
            calls.append((bucket, key, length, kwargs.get("retention")))
            return Result()

        def bucket_exists(self, bucket):
            raise AssertionError("object credentials must not probe bucket state")

        def get_bucket_versioning(self, bucket):
            raise AssertionError("object credentials must not read bucket versioning")

    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: Client())
    store = storage.ObjectStore("omega-hub")
    assert store.put("bundles/sha.tar.gz", io.BytesIO(b"bundle")) == "object-version"
    assert calls[0][0:3] == ("omega-hub", "bundles/sha.tar.gz", 6)
    assert calls[0][3] is not None


def test_temporal_hub_schedules_and_activities_are_registered():
    from tfc.temporal.common.registry import _import_temporal_activity_modules
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.schedules import ALL_SCHEDULES

    _import_temporal_activity_modules()
    hub_schedules = {item.schedule_id: item for item in ALL_SCHEDULES if item.schedule_id.startswith("omega-hub-")}
    assert set(hub_schedules) == {
        "omega-hub-operations-check",
        "omega-hub-outbox-drain",
        "omega-hub-retention",
        "omega-hub-rules-notify",
    }
    assert {
        "omega_hub.check_operations",
        "omega_hub.deliver_outbox",
        "omega_hub.retention",
        "omega_hub.rules_notify",
    } <= set(_ACTIVITY_REGISTRY)
    assert hub_schedules["omega-hub-outbox-drain"].activity_name == "omega_hub.deliver_outbox"
