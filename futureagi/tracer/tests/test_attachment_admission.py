"""Startup policy and real shared-Redis admission, not LocMem qualification.

The four forked workers call the production admission function with independent
cache clients; they do not inherit a Redis connection or touch PostgreSQL.
HTTP controls use real access rows/PG and the existing declared CH/storage/
parser doubles. Redis is the test-compose sidecar, never a deployment cache.
"""

import multiprocessing
import os
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.conf import settings as django_settings
from django.core.cache import CacheHandler, caches
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

import tracer
from tracer.apps import SdkConfig
from tracer.models.attachment import AttachmentMediaSession
from tracer.services.attachments import playback, storage
from tracer.services.attachments.cache_config import (
    REDIS_BACKEND,
    REDIS_CLIENT,
    verify_admission_cache,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.occurrences import Viewer
from tracer.tests import test_attachment_api as http
from tracer.tests import test_attachment_media_binding as binding

api = http.api


def redis_config():
    return {
        "BACKEND": REDIS_BACKEND,
        "LOCATION": "redis://127.0.0.1:16379/15",
        "KEY_PREFIX": f"attachment-admission-test-{uuid.uuid4().hex}",
        "OPTIONS": {
            "CLIENT_CLASS": REDIS_CLIENT,
            "SOCKET_CONNECT_TIMEOUT": 1,
            "SOCKET_TIMEOUT": 1,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "backend",
    [
        "django.core.cache.backends.locmem.LocMemCache",
        "django.core.cache.backends.dummy.DummyCache",
        "django.core.cache.backends.db.DatabaseCache",
        "django.core.cache.backends.filebased.FileBasedCache",
        "django.core.cache.backends.redis.RedisCache",
        "django.core.cache.backends.memcached.PyMemcacheCache",
        "custom.AtomicLookingCache",
    ],
)
def test_enabled_startup_rejects_unqualified_backend_before_storage(
    settings, monkeypatch, backend
):
    settings.FI_ATTACHMENTS_ENABLED = True
    settings.CACHES = {"default": {"BACKEND": backend, "LOCATION": "fixture"}}
    factory = Mock()
    monkeypatch.setattr(storage, "from_settings", factory)
    with pytest.raises(ImproperlyConfigured, match="shared atomic admission cache"):
        SdkConfig("tracer", tracer).ready()
    factory.assert_not_called()


@pytest.mark.unit
def test_disabled_locmem_startup_does_not_inspect_cache_or_storage(
    settings, monkeypatch
):
    settings.FI_ATTACHMENTS_ENABLED = False
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    factory = Mock()
    monkeypatch.setattr(storage, "from_settings", factory)
    with patch(
        "tracer.services.attachments.cache_config.verify_admission_cache"
    ) as check:
        SdkConfig("tracer", tracer).ready()
    check.assert_not_called()
    factory.assert_not_called()
    # Even incomplete cache settings remain untouched while disabled.
    verify_admission_cache(SimpleNamespace(FI_ATTACHMENTS_ENABLED=False))


@pytest.mark.unit
@pytest.mark.parametrize("scheme", ["redis", "rediss"])
def test_standard_redis_startup_permits_storage_readiness_without_cache_io(
    settings, monkeypatch, scheme
):
    settings.FI_ATTACHMENTS_ENABLED = True
    config = redis_config()
    config["LOCATION"] = f"{scheme}://fixture.invalid:6379/0"
    settings.CACHES = {"default": config}
    factory = Mock()
    monkeypatch.setattr(storage, "from_settings", factory)
    with patch(
        "django_redis.cache.RedisCache.__init__",
        side_effect=AssertionError("no cache I/O at startup"),
    ):
        SdkConfig("tracer", tracer).ready()
    factory.assert_called_once_with(django_settings)
    assert django_settings.CACHES["default"] == config
    factory.return_value.verify_readiness.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.parametrize(
    "change",
    [
        {"LOCATION": ["redis://one/0", "redis://two/0"]},
        {"LOCATION": "redis://one/0,redis://two/0"},
        {"LOCATION": ""},
        {"LOCATION": "redis://[invalid"},
        {"LOCATION": "https://not-redis/0"},
        {"KEY_FUNCTION": "custom.per_process_key"},
        {"OPTIONS": {"CLIENT_CLASS": "django_redis.client.ShardClient"}},
        {"OPTIONS": {"CLIENT_CLASS": "django_redis.client.HerdClient"}},
        {"OPTIONS": {"IGNORE_EXCEPTIONS": True}},
        {"OPTIONS": {"CONNECTION_POOL_CLASS": "custom.Pool"}},
        {"OPTIONS": {"CONNECTION_FACTORY": "custom.Factory"}},
        {"OPTIONS": {"SERIALIZER": "custom.Serializer"}},
    ],
)
def test_redis_label_does_not_qualify_custom_or_fail_open_counters(settings, change):
    settings.FI_ATTACHMENTS_ENABLED = True
    settings.CACHES = {"default": {**redis_config(), **change}}
    with pytest.raises(ImproperlyConfigured, match="shared atomic admission cache"):
        verify_admission_cache(settings)


@pytest.mark.unit
@pytest.mark.parametrize(
    "name,value",
    [
        ("DJANGO_REDIS_IGNORE_EXCEPTIONS", True),
        ("DJANGO_REDIS_CONNECTION_FACTORY", "custom.Factory"),
    ],
)
def test_global_redis_overrides_cannot_hide_unsupported_behavior(settings, name, value):
    settings.FI_ATTACHMENTS_ENABLED = True
    config = redis_config()
    config["LOCATION"] = "redis://user:do-not-print@fixture.invalid/0"
    settings.CACHES = {"default": config}
    setattr(settings, name, value)
    with pytest.raises(ImproperlyConfigured) as error:
        verify_admission_cache(settings)
    assert "do-not-print" not in str(error.value)
    assert "fixture.invalid" not in str(error.value)


@pytest.fixture
def shared_redis(settings):
    config = redis_config()
    settings.CACHES = {"default": config}
    settings.FI_ATTACHMENTS_ENABLED = True
    verify_admission_cache(settings)
    cache = caches["default"]
    # Fail, never skip or substitute LocMem if the test sidecar is unavailable.
    assert cache.add("fixture-probe", 1, timeout=120)
    assert cache.incr("fixture-probe") == 2
    yield config
    # Only this test's UUID prefix, not FLUSHDB or another test's state.
    cache.delete_pattern("*")
    cache.close()


def _worker(config, viewer, now, start, results, attempts):
    # A new handler/client per OS process, not an inherited connection pool.
    cache = CacheHandler({"default": config})["default"]
    admitted = denied = 0
    try:
        with (
            patch.object(playback, "cache", cache),
            patch.object(timezone, "now", return_value=now),
        ):
            assert start.wait(timeout=20)
            for _ in range(attempts):
                try:
                    playback._admit_exchange(viewer)
                    admitted += 1
                except AttachmentError as error:
                    assert error.code == "throttled"
                    denied += 1
        results.put((os.getpid(), admitted, denied))
    finally:
        cache.close()


def run_workers(config, viewer, now, attempts):
    context = multiprocessing.get_context("fork")
    start, results = context.Event(), context.Queue()
    workers = [
        context.Process(
            target=_worker, args=(config, viewer, now, start, results, attempts)
        )
        for _ in range(4)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        reports = [results.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert len({pid for pid, _, _ in reports}) == 4
        return sum(ok for _, ok, _ in reports), sum(denied for _, _, denied in reports)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)
        results.close()


@pytest.mark.django_db
@pytest.mark.integration
def test_four_workers_and_restarts_share_one_allowance_per_access_row_org(
    api, shared_redis, monkeypatch
):
    _, body, _, _ = http.ready_occurrence(api)
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    first = binding.exchange(api)
    row = AttachmentMediaSession.objects.get(pk=first[playback.SESSION_HEADER])
    viewer = Viewer(
        row.user_id, row.application_session_id, row.organization_id, None, None
    )
    item = binding.resolved(api, body["occurrence_id"], first)
    admitted, denied = run_workers(shared_redis, viewer, now, attempts=20)
    assert admitted + 1 == playback.EXCHANGES_PER_MINUTE == 60
    assert denied == 21  # 81 attempts including the first real HTTP exchange.
    # New OS processes do not obtain a fresh allowance.
    assert run_workers(shared_redis, viewer, now, attempts=1) == (0, 4)
    count = AttachmentMediaSession.objects.count()
    http.assert_error(http.media_exchange(api), 429, "throttled")
    assert AttachmentMediaSession.objects.count() == count
    assert binding.read(item["gateway_path"], first).status_code == 200
    # Same user and access row, but another genuinely authorized organization.
    other = http.other_tenant()
    http.member(api.user, other.organization, other.workspace)
    second = binding.exchange(api, HTTP_X_ORGANIZATION_ID=str(other.organization.id))
    other_row = AttachmentMediaSession.objects.get(pk=second[playback.SESSION_HEADER])
    assert other_row.application_session_id == row.application_session_id
    assert other_row.organization_id == other.organization.id
    other_viewer = Viewer(
        row.user_id, row.application_session_id, other.organization.id, None, None
    )
    assert run_workers(shared_redis, other_viewer, now, attempts=20) == (59, 21)
    http.assert_error(
        http.media_exchange(api, HTTP_X_ORGANIZATION_ID=str(other.organization.id)), 429
    )
    row.refresh_from_db()
    assert row.revoked_at is None
    assert binding.read(item["gateway_path"], first).status_code == 200


@pytest.mark.django_db
@pytest.mark.integration
def test_shared_redis_preserves_equal_expiry_explicit_handle_pairs(
    api, shared_redis, monkeypatch
):
    # Reuse the unchanged interleaving/cookie-pair assertions with real Redis.
    binding.test_equal_time_interleaving_keeps_both_exact_pairs(api, monkeypatch)


@pytest.mark.django_db
@pytest.mark.integration
def test_real_cache_connection_failure_denies_issuance_without_revoking_handle(
    api, shared_redis
):
    _, body, _, _ = http.ready_occurrence(api)
    first = binding.exchange(api)
    item = binding.resolved(api, body["occurrence_id"], first)
    count = AttachmentMediaSession.objects.count()
    unavailable = {**shared_redis, "LOCATION": "redis://127.0.0.1:1/15"}
    failed_cache = CacheHandler({"default": unavailable})["default"]
    try:
        # Isolate the admission-cache outage from authentication's cache reads.
        with patch.object(playback, "cache", failed_cache):
            http.assert_error(http.media_exchange(api), 429, "throttled")
    finally:
        failed_cache.close()
    assert AttachmentMediaSession.objects.count() == count
    assert binding.read(item["gateway_path"], first).status_code == 200
