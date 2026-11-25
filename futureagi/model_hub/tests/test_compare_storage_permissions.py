from unittest.mock import Mock

import pytest
from minio.error import S3Error

from model_hub.tests.test_dataset_compare_api import _compare_request, _new_compare_pair
from model_hub.utils import compare_storage


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "method,permission",
    [("_execute", "s3:GetBucketAcl"), ("get_bucket_policy", "s3:GetBucketPolicy")],
)
def test_compare_reports_denied_privacy_read(
    auth_client,
    organization,
    workspace,
    monkeypatch,
    tmp_path,
    settings,
    method,
    permission,
):
    base, peer = _new_compare_pair(organization, workspace, "Compare permissions")
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.start_local_sweeper", lambda: None
    )
    monkeypatch.setattr("tfc.utils.storage_client.STORAGE_BACKEND", "s3")
    settings.COMPARE_BUCKET_NAME = "comparison-permissions-test"
    storage = Mock()
    storage.bucket_exists.return_value = True
    storage.get_bucket_policy.return_value = None
    getattr(storage, method).side_effect = S3Error(
        None, "AccessDenied", "denied", "/", "request", "host"
    )
    monkeypatch.setattr(compare_storage, "get_storage_client", lambda: storage)

    response = _compare_request(auth_client, base, peer)

    assert response.status_code == 503
    assert response.json()["code"] == "compare_storage_permission_missing"
    assert permission in response.json()["detail"]
    storage.put_object.assert_not_called()
