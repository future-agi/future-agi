from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from minio.error import S3Error

from model_hub.utils import compare_storage
from tfc.utils import storage_client


def test_minio_cleanup_never_provisions_missing_bucket(monkeypatch, tmp_path):
    monkeypatch.setattr(
        compare_storage,
        "settings",
        SimpleNamespace(
            COMPARE_BUCKET_NAME="removed-minio-compare", UPLOAD_BUCKET_NAME=""
        ),
    )
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "minio")
    storage = Mock()
    storage.bucket_exists.return_value = False
    storage.get_bucket_policy.return_value = None
    storage.list_objects.return_value = []
    monkeypatch.setattr(compare_storage, "get_storage_client", lambda: storage)
    marker = tmp_path / "marker"
    marker.mkdir()
    monkeypatch.setattr(compare_storage, "get_compare_local_dir", lambda _: marker)

    compare_storage.remove_compare("expired")
    compare_storage.sweep_remote_compare_objects(deadline=float("inf"))

    assert not marker.exists()
    storage.make_bucket.assert_not_called()
    storage.list_objects.assert_not_called()


def test_missing_bucket_on_write_invalidates_cached_preflight(monkeypatch):
    bucket = "removed-after-preflight"
    monkeypatch.setattr(
        compare_storage,
        "settings",
        SimpleNamespace(COMPARE_BUCKET_NAME=bucket, UPLOAD_BUCKET_NAME=""),
    )
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    storage = Mock()
    storage.bucket_exists.side_effect = [True, False]
    storage.get_bucket_policy.return_value = None
    storage._execute.return_value.data = b"<Policy><AccessControlList/></Policy>"
    monkeypatch.setattr(compare_storage, "get_storage_client", lambda: storage)
    storage_client.ensure_private_bucket(storage, bucket)
    storage.put_object.side_effect = S3Error(
        response=None,
        code="NoSuchBucket",
        message="Missing",
        resource=None,
        request_id=None,
        host_id=None,
        bucket_name=bucket,
    )

    with pytest.raises(storage_client.PrivateBucketMissing):
        compare_storage.write_compare_json("example", "page_1.json", [])
    storage.bucket_exists.assert_called_once_with(bucket)
    with pytest.raises(storage_client.PrivateBucketMissing):
        compare_storage.write_compare_json("example", "page_2.json", [])
    assert storage.bucket_exists.call_count == 2
    storage.put_object.assert_called_once()
