"""The one-off sweep of expired comparison objects (sweep_compare_objects)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from model_hub.tests.test_dataset_compare_storage import _FakeBucketClient
from tfc.utils import storage_client


def _storage(monkeypatch, settings, objects, fail_remove=()):
    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = "test-upload-compare"
    client = _FakeBucketClient(objects, fail_remove=fail_remove)
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    return client


def test_removes_expired_comparisons_from_both_buckets(monkeypatch, settings):
    now = datetime.now(UTC)
    old, recent = now - timedelta(hours=3), now - timedelta(minutes=10)
    expired, fresh, legacy = (str(uuid.uuid4()) for _ in range(3))
    client = _storage(
        monkeypatch,
        settings,
        {
            f"test-upload-compare/compare/{expired}/metadata.json": old,
            f"test-upload-compare/compare/{expired}/page_1.json": old,
            f"test-upload-compare/compare/{fresh}/metadata.json": recent,
            f"test-upload/compare/{legacy}/page_1.json": old,
            "test-upload/images/kept.png": old,
        },
    )

    call_command("sweep_compare_objects")

    assert sorted(client.objects) == [
        f"test-upload-compare/compare/{fresh}/metadata.json",
        "test-upload/images/kept.png",
    ]


def test_fails_when_an_expired_comparison_is_left(monkeypatch, settings):
    old = datetime.now(UTC) - timedelta(hours=3)
    stuck, legacy = str(uuid.uuid4()), str(uuid.uuid4())
    client = _storage(
        monkeypatch,
        settings,
        {
            f"test-upload-compare/compare/{stuck}/page_1.json": old,
            f"test-upload/compare/{legacy}/page_1.json": old,
        },
        fail_remove={stuck},
    )

    with pytest.raises(CommandError, match="Run the command again"):
        call_command("sweep_compare_objects")
    # The other bucket was still swept.
    assert sorted(client.objects) == [
        f"test-upload-compare/compare/{stuck}/page_1.json"
    ]

    # The next run removes it.
    client.fail_remove.clear()
    call_command("sweep_compare_objects")
    assert client.objects == {}


def test_fails_when_it_stops_at_its_time_limit(monkeypatch, settings):
    old = datetime.now(UTC) - timedelta(hours=3)
    expired = str(uuid.uuid4())
    client = _storage(
        monkeypatch,
        settings,
        {f"test-upload-compare/compare/{expired}/page_1.json": old},
    )
    # The clock passes the limit right after the command sets it.
    readings = iter([0.0])
    monkeypatch.setattr(
        "model_hub.utils.compare_storage.time.monotonic",
        lambda: next(readings, 5.0),
    )

    with pytest.raises(CommandError, match="Run the command again"):
        call_command("sweep_compare_objects", "--max-seconds", "1")
    assert list(client.objects) == [
        f"test-upload-compare/compare/{expired}/page_1.json"
    ]


def test_rejects_a_time_limit_that_is_not_positive(monkeypatch, settings):
    _storage(monkeypatch, settings, {})
    with pytest.raises(CommandError, match="--max-seconds must be positive"):
        call_command("sweep_compare_objects", "--max-seconds", "0")
