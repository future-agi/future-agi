"""Who provisions the Hub bucket (HUB-19, fix round 3).

Round 2 removed the lazy bucket creation and left `manage.py omega_hub_storage` as the only
provisioning path, which nothing ran. These tests pin the replacement owners: the post_migrate
receiver every bootstrap path reaches, the operations check that repairs and pages, and typed 503s
instead of raw storage errors on the request path. The fake below speaks the minio client API.
"""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from minio.error import S3Error

from omega_hub.errors import HubError
from omega_hub.models import Submission
from omega_hub.services import metrics, ops, storage, storage_provision


def s3_error(code: str, message: str = "") -> S3Error:
    return S3Error(None, code, message or code, "/omega-hub", "req", "host")


class FakeS3:
    """Enough of the minio client for the Hub: buckets, versioning, lock, lifecycle and versions."""

    def __init__(self, *, buckets=None, slow: float = 0.0):
        self.buckets = buckets or {}
        self.writes: list[tuple] = []
        self.objects: list[SimpleNamespace] = []
        self.retention: dict[str, datetime] = {}
        self.put_error: S3Error | None = None
        self.slow = slow

    def bucket_exists(self, bucket):
        if self.slow:
            time.sleep(self.slow)
        return bucket in self.buckets

    def make_bucket(self, bucket, object_lock=False):
        self.writes.append(("make_bucket", object_lock))
        self.buckets[bucket] = {"lock": object_lock, "versioning": object_lock, "rules": []}

    def get_bucket_versioning(self, bucket):
        return SimpleNamespace(status="Enabled" if self.buckets[bucket]["versioning"] else "Off")

    def set_bucket_versioning(self, bucket, config):
        self.writes.append(("set_bucket_versioning", config.status))
        self.buckets[bucket]["versioning"] = True

    def get_object_lock_config(self, bucket):
        if not self.buckets[bucket]["lock"]:
            raise s3_error("ObjectLockConfigurationNotFoundError", "Object Lock configuration does not exist")
        return SimpleNamespace()

    def get_bucket_lifecycle(self, bucket):
        rules = self.buckets[bucket]["rules"]
        return SimpleNamespace(rules=list(rules)) if rules else None

    def set_bucket_lifecycle(self, bucket, config):
        self.writes.append(("set_bucket_lifecycle", [rule.rule_id for rule in config.rules]))
        self.buckets[bucket]["rules"] = list(config.rules)

    def put_object(self, bucket, key, data, length, retention=None):
        if self.put_error is not None:
            raise self.put_error
        self.writes.append(("put_object", key, retention is not None))
        return SimpleNamespace(version_id="v-new")

    def list_objects(self, bucket, prefix, recursive, include_version):
        return [item for item in self.objects if item.object_name.startswith(prefix)]

    def get_object_retention(self, bucket, key, version_id=None):
        until = self.retention.get(version_id)
        return SimpleNamespace(retain_until_date=until) if until else None

    def remove_object(self, bucket, key, version_id=None):
        self.writes.append(("remove_object", key, version_id))


def ready_bucket(**overrides):
    rule = SimpleNamespace(rule_id=storage_provision.RAW_LIFECYCLE_RULE_ID)
    return {"omega-hub": {"lock": True, "versioning": True, "rules": [rule], **overrides}}


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "minio")
    return FakeS3()


def test_a_fresh_deploy_gets_a_locked_versioned_bucket(s3):
    result = storage_provision.provision(s3, "omega-hub")
    assert result["ready"] is True
    assert ("make_bucket", True) in s3.writes
    assert ("set_bucket_lifecycle", [storage_provision.RAW_LIFECYCLE_RULE_ID]) in s3.writes


def test_a_ready_bucket_needs_no_write(s3):
    s3.buckets = ready_bucket()
    assert storage_provision.provision(s3, "omega-hub")["ready"] is True
    assert s3.writes == []


def test_provision_keeps_an_operators_other_lifecycle_rules(s3):
    s3.buckets = ready_bucket(rules=[SimpleNamespace(rule_id="operator-own-rule")])
    storage_provision.provision(s3, "omega-hub")
    assert s3.writes == [("set_bucket_lifecycle", ["operator-own-rule", storage_provision.RAW_LIFECYCLE_RULE_ID])]


def test_a_bucket_without_object_lock_is_refused_with_the_fix(s3):
    s3.buckets = ready_bucket(lock=False)
    with pytest.raises(storage_provision.StorageNotProvisioned, match="exists without object lock"):
        storage_provision.provision(s3, "omega-hub")
    assert storage_provision.status(s3, "omega-hub")["problems"] == ["object lock is not enabled"]


def test_gcs_is_verified_not_created(monkeypatch):
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "gcs")
    client = FakeS3()
    with pytest.raises(storage_provision.StorageNotProvisioned, match="Terraform"):
        storage_provision.provision(client, "omega-hub")
    assert client.writes == []
    client.buckets = {"omega-hub": {"lock": False, "versioning": True, "rules": []}}
    assert storage_provision.provision(client, "omega-hub")["ready"] is True
    assert client.writes == []


def test_reconcile_is_bounded_when_storage_does_not_answer(s3, settings):
    settings.OMEGA_HUB_STORAGE = "object"
    s3.slow = 5
    started = time.monotonic()
    result = storage_provision.reconcile(timeout=0.3, client=s3)
    assert time.monotonic() - started < 2
    assert result["ready"] is False
    assert "did not answer within 0.3 s" in result["problems"][0]


def test_bootstrap_waits_for_storage_that_is_still_starting(s3, settings):
    settings.OMEGA_HUB_STORAGE = "object"

    class Starting(FakeS3):
        refusals = 2

        def bucket_exists(self, bucket):
            if Starting.refusals:
                Starting.refusals -= 1
                raise ConnectionRefusedError(61, "Connection refused")
            return super().bucket_exists(bucket)

    client = Starting()
    assert storage_provision.reconcile(timeout=20, client=client, wait_for_storage=True)["ready"] is True
    assert ("make_bucket", True) in client.writes
    Starting.refusals = 1
    assert storage_provision.reconcile(client=Starting())["ready"] is False  # the operations check does not wait


def test_reconcile_reports_a_refused_credential(s3, settings):
    settings.OMEGA_HUB_STORAGE = "object"

    class Denied(FakeS3):
        def bucket_exists(self, bucket):
            raise s3_error("AccessDenied", "Access Denied.")

    result = storage_provision.reconcile(client=Denied())
    assert result["ready"] is False
    assert result["problems"][0].startswith("AccessDenied")


def test_post_migrate_provisions_outside_the_test_settings(s3, settings, monkeypatch):
    settings.OMEGA_HUB_STORAGE = "object"
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    assert storage_provision.provision_after_migrate(sender=None, stdout=io.StringIO()) is None  # test settings
    assert s3.writes == []
    settings.TESTING = False
    out = io.StringIO()
    result = storage_provision.provision_after_migrate(sender=None, stdout=out, verbosity=1)
    assert result["ready"] is True
    assert ("make_bucket", True) in s3.writes
    assert "Omega Hub storage: bucket omega-hub is ready" in out.getvalue()


def test_post_migrate_never_fails_the_migration(s3, settings, monkeypatch):
    settings.OMEGA_HUB_STORAGE = "object"
    settings.TESTING = False
    s3.buckets = ready_bucket(lock=False)
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    out = io.StringIO()
    result = storage_provision.provision_after_migrate(sender=None, stdout=out, verbosity=1)
    assert result["ready"] is False
    assert "Omega Hub storage NOT ready" in out.getvalue()


def test_the_migrate_receiver_is_connected_to_the_hub_app():
    from django.apps import apps
    from django.db.models.signals import post_migrate

    hub = apps.get_app_config("omega_hub")
    receivers = post_migrate._live_receivers(hub)
    receivers = receivers[0] if isinstance(receivers, tuple) else receivers
    assert storage_provision.provision_after_migrate in receivers


@pytest.mark.django_db
def test_the_operations_check_pages_unprovisioned_storage(world, monkeypatch):
    monkeypatch.setattr(storage_provision, "reconcile", lambda **kw: {"ready": False, "bucket": "omega-hub",
                                                                       "problems": ["bucket omega-hub does not exist"]})
    paged = []
    monkeypatch.setattr(ops.notify, "ops_alert", lambda alert: paged.append(alert) or True)
    result = ops.check_and_page()
    assert [alert["id"] for alert in result["alerts"]] == ["omega_hub_storage_unprovisioned"]
    assert paged[0]["problems"] == ["bucket omega-hub does not exist"]
    assert "omega_hub_storage_ready 0" in metrics.prometheus()
    monkeypatch.setattr(storage_provision, "reconcile", lambda **kw: {"ready": True})
    assert ops.check_and_page()["alerts"] == []
    assert "omega_hub_storage_ready 1" in metrics.prometheus()


@pytest.mark.parametrize(
    "error, code",
    [
        (s3_error("NoSuchBucket", "The specified bucket does not exist"), "OMEGA_HUB_STORAGE_NOT_PROVISIONED"),
        (s3_error("InvalidRequest", "Bucket is missing ObjectLockConfiguration"), "OMEGA_HUB_STORAGE_NOT_PROVISIONED"),
        (s3_error("AccessDenied", "Access Denied."), "OMEGA_HUB_STORAGE_DENIED"),
    ],
)
def test_object_writes_the_bucket_refuses_are_typed_503s(s3, monkeypatch, error, code):
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    s3.put_error = error
    with pytest.raises(HubError) as raised:
        storage.ObjectStore("omega-hub").put("bundles/" + "a" * 64 + ".tar.gz", io.BytesIO(b"x"))
    assert (raised.value.code, raised.value.status) == (code, 503)


@pytest.mark.django_db
def test_an_upload_to_an_unprovisioned_bucket_answers_503_and_keeps_nothing(world, s3, settings, monkeypatch):
    from omega_hub.tests.helpers import entry_files, submit, token_client

    settings.OMEGA_HUB_STORAGE = "object"
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    monkeypatch.setattr(storage, "_store", None)
    s3.put_error = s3_error("NoSuchBucket", "The specified bucket does not exist")
    response = submit(token_client(world.token_a), "future-agi", entry_files("first-agent"))
    assert response.status_code == 503, response.content
    assert response.json()["error"]["code"] == "OMEGA_HUB_STORAGE_NOT_PROVISIONED"
    assert response["Retry-After"] == "300"
    assert Submission.objects.count() == 0


def test_bundle_puts_carry_no_lock_headers_on_gcs(monkeypatch):
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "gcs")
    client = FakeS3()
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: client)
    storage.ObjectStore("omega-hub").put("bundles/" + "b" * 64 + ".tar.gz", io.BytesIO(b"x"))
    assert client.writes == [("put_object", "bundles/" + "b" * 64 + ".tar.gz", False)]


def test_purge_defers_bundle_versions_still_under_retention(s3, monkeypatch):
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    key = "bundles/" + "c" * 64 + ".tar.gz"
    s3.objects = [SimpleNamespace(object_name=key, version_id=v, is_delete_marker=False) for v in ("locked", "lapsed")]
    now = datetime.now(UTC)
    s3.retention = {"locked": now + timedelta(days=30), "lapsed": now - timedelta(days=1)}
    assert storage.ObjectStore("omega-hub").purge(key) == 1
    assert s3.writes == [("remove_object", key, "lapsed")]


def test_purge_on_gcs_never_asks_for_object_retention(monkeypatch):
    # GCS interop has no object retention: asking would fail every bundle purge there.
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "gcs")
    client = FakeS3()
    key = "bundles/" + "d" * 64 + ".tar.gz"
    client.objects = [SimpleNamespace(object_name=key, version_id="v1", is_delete_marker=False)]

    def no_retention_api(*args, **kwargs):
        raise s3_error("NotImplemented", "A header you provided implies functionality that is not implemented")

    client.get_object_retention = no_retention_api
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: client)
    assert storage.ObjectStore("omega-hub").purge(key) == 0
    assert client.writes == [("remove_object", key, "v1")]


@pytest.mark.parametrize("code", ["NoSuchObjectLockConfiguration", "ObjectLockConfigurationNotFoundError"])
def test_purge_removes_a_bundle_version_written_without_retention(s3, monkeypatch, code):
    # An operator's copy, or a version put before bundles were locked, has no retention to wait for.
    key = "bundles/" + "e" * 64 + ".tar.gz"
    s3.objects = [SimpleNamespace(object_name=key, version_id="plain", is_delete_marker=False)]

    def none_set(*args, **kwargs):
        raise s3_error(code)

    s3.get_object_retention = none_set
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    assert storage.ObjectStore("omega-hub").purge(key) == 0
    assert s3.writes == [("remove_object", key, "plain")]


def test_purge_does_not_guess_when_retention_cannot_be_read(s3, monkeypatch):
    # A refused retention read is not "unlocked": the key fails (retention counts and reports it).
    key = "bundles/" + "f" * 64 + ".tar.gz"
    s3.objects = [SimpleNamespace(object_name=key, version_id="v1", is_delete_marker=False)]

    def denied(*args, **kwargs):
        raise s3_error("AccessDenied")

    s3.get_object_retention = denied
    monkeypatch.setattr("tfc.utils.storage_client.get_storage_client", lambda: s3)
    with pytest.raises(S3Error):
        storage.ObjectStore("omega-hub").purge(key)
    assert s3.writes == []


@pytest.mark.django_db
@pytest.mark.parametrize("verdict", [False, True, None])
def test_uploads_pause_only_while_the_bucket_is_known_not_ready(world, verdict):
    # A bucket without object lock takes raw uploads and refuses every bundle: once the operations
    # check has seen that, the Hub stops accepting uploads it could not take to approval.
    from django.core.cache import cache

    from omega_hub.tests.helpers import entry_files, submit, token_client

    if verdict is not None:
        storage_provision.remember({"ready": verdict})
    else:
        cache.delete(storage_provision.STATUS_CACHE_KEY)
    response = submit(token_client(world.token_a), "future-agi", entry_files("first-agent"))
    if verdict is False:
        assert response.status_code == 503, response.content
        assert response.json()["error"]["code"] == "OMEGA_HUB_STORAGE_NOT_PROVISIONED"
        assert response["Retry-After"] == "300"
        assert Submission.objects.count() == 0
    else:
        assert response.status_code == 201, response.content


@pytest.mark.django_db
def test_checker_claims_wait_while_the_bucket_is_known_not_ready(world):
    # A check could not store its bundle, so it must not spend one of the job's three attempts.
    from omega_hub.models import CheckJob
    from omega_hub.tests.helpers import (
        checker_client,
        entry_files,
        submit,
        token_client,
    )

    sub_id = submit(token_client(world.token_a), "future-agi", entry_files("first-agent")).json()["id"]
    storage_provision.remember({"ready": False})
    assert checker_client().post("/omega-hub/internal/checker/claim").status_code == 204
    job = CheckJob.objects.get(submission_id=sub_id)
    assert (job.status, job.attempt) == (CheckJob.Status.QUEUED, 0)
    storage_provision.remember({"ready": True})
    claimed = checker_client().post("/omega-hub/internal/checker/claim")
    assert claimed.status_code == 200 and claimed.json()["submission"] == sub_id and claimed.json()["attempt"] == 1


def test_an_unreachable_cache_never_pauses_uploads(monkeypatch):
    from django.core.cache import cache

    def down(*args, **kwargs):
        raise ConnectionError("cache is down")

    monkeypatch.setattr(cache, "get", down)
    assert storage_provision.known_unready() is False
    storage_provision.require_ready()


@pytest.mark.django_db
def test_the_operator_status_command_resumes_paused_uploads(world):
    # After a fix, `omega_hub_storage status` (or `provision`) lifts the pause at once rather than
    # at the next five-minute operations check.
    from django.core.cache import cache
    from django.core.management import call_command

    storage_provision.remember({"ready": False})
    call_command("omega_hub_storage", "status", stdout=io.StringIO())
    assert cache.get(storage_provision.STATUS_CACHE_KEY) is True
    assert storage_provision.known_unready() is False

