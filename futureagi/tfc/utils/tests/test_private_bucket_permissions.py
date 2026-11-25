from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from minio.error import S3Error

from tfc.utils import storage_client


@pytest.mark.parametrize(
    "method,permission,code,status",
    [
        ("_execute", "s3:GetBucketAcl", "AccessDenied", 403),
        ("get_bucket_policy", "s3:GetBucketPolicy", "Forbidden", 403),
        ("_execute", "s3:GetBucketAcl", "InvalidAccessKeyId", 401),
    ],
)
def test_denied_privacy_read_names_required_permission(
    monkeypatch, method, permission, code, status
):
    client = Mock()
    client.get_bucket_policy.return_value = None
    bucket = "comparison-permission-test"
    getattr(client, method).side_effect = S3Error(
        SimpleNamespace(status=status), code, "denied", "/", "request", "host"
    )
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "s3")
    with pytest.raises(storage_client.PrivateBucketRefused, match=permission):
        storage_client.ensure_private_bucket(client, bucket)
    assert bucket not in storage_client._checked_private_buckets
