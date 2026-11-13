"""Where comparison data is stored, and when it expires.

The storage tests run against the MinIO of docker-compose.test.yml (published
on localhost:19005, or MINIO_ENDPOINT) and skip when it is not reachable.
"""

import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.tests.test_dataset_compare_paging import (
    DELETE_URL,
    ROW_URL,
    _make_dataset,
    _post,
    _replica,
    _result,
    _row_ids,
)
from tfc.utils import storage_client

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT") or "localhost:19005"
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY") or "minioadmin"
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY") or "minioadmin"


def _anonymous_get(bucket, key):
    """Status of a GET of the object by URL, with no credentials."""
    url = f"http://{MINIO_ENDPOINT}/{bucket}/{key}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


@pytest.fixture
def minio_storage(monkeypatch, settings):
    """The test MinIO as the platform's storage, with buckets of this test."""
    from minio import Minio

    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    try:
        client.list_buckets()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test MinIO not reachable at {MINIO_ENDPOINT}: {exc}")

    suffix = uuid.uuid4().hex[:10]
    upload_bucket = f"test-upload-{suffix}"
    compare_bucket = f"test-upload-{suffix}-compare"
    settings.UPLOAD_BUCKET_NAME = upload_bucket
    settings.COMPARE_BUCKET_NAME = compare_bucket
    # The upload helpers read the bucket name once, at import.
    monkeypatch.setattr("tfc.utils.storage.UPLOAD_BUCKET_NAME", upload_bucket)
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    yield SimpleNamespace(
        client=client, upload_bucket=upload_bucket, compare_bucket=compare_bucket
    )
    for bucket in (upload_bucket, compare_bucket):
        if client.bucket_exists(bucket):
            for obj in client.list_objects(bucket, recursive=True):
                client.remove_object(bucket, obj.object_name)
            client.remove_bucket(bucket)


def _compare_objects(storage, compare_id):
    """{bucket: [object names]} of one comparison, in every test bucket."""
    found = {}
    for bucket in (storage.upload_bucket, storage.compare_bucket):
        if not storage.client.bucket_exists(bucket):
            continue
        names = [
            obj.object_name
            for obj in storage.client.list_objects(
                bucket, prefix=f"compare/{compare_id}/", recursive=True
            )
        ]
        if names:
            found[bucket] = sorted(names)
    return found


@pytest.mark.django_db(transaction=True)
def test_compare_pages_and_metadata_are_not_readable_by_url(
    auth_client, organization, workspace, minio_storage, monkeypatch, tmp_path
):
    shared = [f"private-{index:02d}" for index in range(12)]
    left, left_input, _ = _make_dataset(
        organization, workspace, "Private left", shared, "left"
    )
    right, _, _ = _make_dataset(
        organization, workspace, "Private right", shared, "right"
    )
    _replica(monkeypatch, tmp_path, "replica-a")
    first = _result(_post(auth_client, left.id, [right.id], 0, None))
    compare_id = first["metadata"]["compare_id"]

    found = _compare_objects(minio_storage, compare_id)
    names = sorted(name for bucket_names in found.values() for name in bucket_names)
    assert names == [
        f"compare/{compare_id}/metadata.json",
        f"compare/{compare_id}/page_1.json",
        f"compare/{compare_id}/page_2.json",
    ]
    # Nothing of the comparison is in the bucket whose objects anyone may read.
    assert minio_storage.upload_bucket not in found
    assert list(found) == [minio_storage.compare_bucket]
    for name in names:
        assert _anonymous_get(minio_storage.compare_bucket, name) in (401, 403)
    with pytest.raises(Exception) as no_policy:
        minio_storage.client.get_bucket_policy(minio_storage.compare_bucket)
    assert getattr(no_policy.value, "code", None) == "NoSuchBucketPolicy"

    # The backend still serves the comparison from any replica.
    _replica(monkeypatch, tmp_path, "replica-b")
    second = _result(_post(auth_client, left.id, [right.id], 1, compare_id))
    returned = [
        row[str(left_input.id)]["cell_value"]
        for row in first["table"] + second["table"]
    ]
    assert sorted(returned) == shared
    detail = auth_client.get(
        ROW_URL.format(compare_id=compare_id, row_id=_row_ids(first)[0])
    )
    assert detail.status_code == status.HTTP_200_OK, detail.content[:500]

    # Exit removes it from the private bucket.
    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no worker")),
    )
    deleted = auth_client.delete(DELETE_URL.format(compare_id=compare_id))
    assert deleted.status_code == status.HTTP_200_OK, deleted.content[:500]
    assert _compare_objects(minio_storage, compare_id) == {}


@pytest.mark.django_db
def test_a_public_or_upload_bucket_is_refused_for_comparison_data(
    minio_storage, settings
):
    from model_hub.utils.compare_storage import write_compare_json
    from tfc.utils.storage_client import PrivateBucketRefused, ensure_bucket

    compare_id = str(uuid.uuid4())
    settings.COMPARE_BUCKET_NAME = minio_storage.upload_bucket
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})

    # A bucket an operator made readable by URL is not used either.
    settings.COMPARE_BUCKET_NAME = minio_storage.compare_bucket
    ensure_bucket(minio_storage.client, minio_storage.compare_bucket)
    with pytest.raises(PrivateBucketRefused):
        write_compare_json(compare_id, "metadata.json", {"status": "completed"})
    assert _compare_objects(minio_storage, compare_id) == {}


class _FakeBucketClient:
    """Object storage holding objects with their modification times."""

    def __init__(self, objects, fail_remove=()):
        self.objects = dict(objects)
        self.fail_remove = set(fail_remove)

    def bucket_exists(self, bucket):
        return True

    def get_bucket_policy(self, bucket):
        error = Exception("no policy")
        error.code = "NoSuchBucketPolicy"
        raise error

    def list_objects(self, bucket, prefix="", recursive=False):
        return [
            SimpleNamespace(object_name=key.split("/", 1)[1], last_modified=modified)
            for key, modified in sorted(self.objects.items())
            if key.startswith(f"{bucket}/{prefix}")
        ]

    def remove_object(self, bucket, name):
        if name.split("/")[1] in self.fail_remove:
            raise RuntimeError("storage unavailable")
        self.objects.pop(f"{bucket}/{name}", None)


@pytest.mark.django_db
def test_scheduled_cleanup_removes_expired_comparisons_and_keeps_fresh_ones(
    monkeypatch, tmp_path, settings
):
    from model_hub.tasks.develop_dataset import delete_unused_compare_folder

    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = "test-upload-compare"
    root = tmp_path / "compare-root"
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(root))
    now = datetime.now(UTC)
    expired_id, fresh_id, stuck_id, remote_only_id, legacy_id = (
        str(uuid.uuid4()) for _ in range(5)
    )
    old, recent = now - timedelta(hours=3), now - timedelta(minutes=10)
    client = _FakeBucketClient(
        {
            f"test-upload-compare/compare/{expired_id}/metadata.json": old,
            f"test-upload-compare/compare/{expired_id}/page_1.json": old,
            f"test-upload-compare/compare/{fresh_id}/metadata.json": recent,
            f"test-upload-compare/compare/{stuck_id}/page_1.json": old,
            f"test-upload-compare/compare/{remote_only_id}/page_1.json": old,
            f"test-upload/compare/{legacy_id}/page_1.json": old,
            "test-upload/images/kept.png": old,
        },
        fail_remove={stuck_id},
    )
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    for compare_id in (expired_id, fresh_id, stuck_id):
        (root / compare_id).mkdir(parents=True)
    hours_ago = time.time() - 3 * 60 * 60
    real_getctime = os.path.getctime
    monkeypatch.setattr(
        "os.path.getctime",
        lambda path: (
            hours_ago
            if os.path.basename(path) in (expired_id, stuck_id)
            else real_getctime(path)
        ),
    )

    delete_unused_compare_folder()

    assert sorted(p.name for p in root.iterdir()) == sorted([fresh_id, stuck_id])
    assert sorted(client.objects) == sorted(
        [
            f"test-upload-compare/compare/{fresh_id}/metadata.json",
            # Remote deletion failed, so its local folder stays for a retry.
            f"test-upload-compare/compare/{stuck_id}/page_1.json",
            "test-upload/images/kept.png",
        ]
    )
