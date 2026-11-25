from types import SimpleNamespace
from unittest.mock import Mock

from model_hub.utils import compare_storage


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
