from unittest.mock import Mock

import pytest

from model_hub.tests.test_dataset_compare_api import _compare_request, _new_compare_pair
from model_hub.utils import compare_storage


@pytest.mark.django_db(transaction=True)
def test_missing_private_bucket_returns_configuration_guidance(
    auth_client, organization, workspace, monkeypatch, tmp_path, settings
):
    base, peer = _new_compare_pair(organization, workspace, "Missing compare bucket")
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.start_local_sweeper", lambda: None
    )
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "s3")
    settings.COMPARE_BUCKET_NAME = "missing-private-comparison-bucket"
    storage = Mock()
    storage.bucket_exists.return_value = False
    monkeypatch.setattr(compare_storage, "get_storage_client", lambda: storage)

    response = _compare_request(auth_client, base, peer)

    assert response.status_code == 503
    assert response.json()["code"] == "compare_bucket_missing"
    assert "COMPARE_BUCKET_NAME" in response.json()["detail"]
    assert "objectStorage.compareBucket" in response.json()["detail"]
    storage.bucket_exists.assert_called_once_with(settings.COMPARE_BUCKET_NAME)
    storage.put_object.assert_not_called()
