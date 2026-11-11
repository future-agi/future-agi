"""Policy/identity component tests; these doubles do not qualify native S3."""

import io
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.storage import (
    ObjectIdentity,
    PrivateMediaStorage,
    from_settings,
)

pytestmark = pytest.mark.unit


def private_client():
    client = Mock()
    client.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": dict.fromkeys(
            (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            ),
            True,
        )
    }
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.get_bucket_ownership_controls.return_value = {
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
    }
    client.get_bucket_acl.return_value = {
        "Owner": {"ID": "owner"},
        "Grants": [{"Grantee": {"ID": "owner"}}],
    }
    client.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        }
    }
    client.get_bucket_cors.side_effect = ClientError(
        {"Error": {"Code": "NoSuchCORSConfiguration"}}, "GetBucketCors"
    )
    client.get_bucket_policy.side_effect = ClientError(
        {"Error": {"Code": "NoSuchBucketPolicy"}}, "GetBucketPolicy"
    )
    client.get_bucket_lifecycle_configuration.return_value = {
        "Rules": [
            {
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
            }
        ]
    }
    client.put_object.return_value = {"VersionId": "immutable-v1"}
    return client


def storage(client):
    return PrivateMediaStorage(
        quarantine_client=client,
        serving_client=client,
        quarantine_bucket="private-quarantine",
        serving_bucket="private-serving",
    )


def test_private_capabilities_positive_and_no_direct_mode():
    service = storage(private_client())
    service.verify_readiness()
    assert service.direct_upload_enabled is False
    assert not hasattr(service, "get_object_url")


@pytest.mark.parametrize(
    "method,result",
    [
        ("get_public_access_block", {"PublicAccessBlockConfiguration": {}}),
        ("get_bucket_versioning", {"Status": "Suspended"}),
        ("get_bucket_ownership_controls", {"OwnershipControls": {"Rules": []}}),
        (
            "get_bucket_acl",
            {"Owner": {"ID": "owner"}, "Grants": [{"Grantee": {"URI": "AllUsers"}}]},
        ),
        ("get_bucket_encryption", {"ServerSideEncryptionConfiguration": {"Rules": []}}),
        ("get_bucket_lifecycle_configuration", {"Rules": []}),
    ],
)
def test_policy_drift_fails_readiness(method, result):
    client = private_client()
    storage(client).verify_readiness()
    getattr(client, method).return_value = deepcopy(result)
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage(client).verify_readiness()


def test_wildcard_policy_or_cors_refused():
    client = private_client()
    client.get_bucket_policy.side_effect = None
    client.get_bucket_policy.return_value = {
        "Policy": '{"Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject"}]}'
    }
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage(client).verify_readiness()
    client = private_client()
    client.get_bucket_cors.side_effect = None
    client.get_bucket_cors.return_value = {"CORSRules": [{"AllowedOrigins": ["*"]}]}
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage(client).verify_readiness()


def test_storage_error_details_never_escape():
    client = private_client()
    client.get_bucket_versioning.side_effect = RuntimeError(
        "https://signed.invalid/private?canary=SECRET"
    )
    with pytest.raises(AttachmentError) as error:
        storage(client).verify_readiness()
    assert str(error.value) == "storage_unverified"


def test_create_once_quarantine_and_no_null_version():
    client = private_client()
    result = storage(client).put_quarantine("upload", io.BytesIO(b"abc"), 3)
    assert result == ObjectIdentity("quarantine/upload", "immutable-v1")
    assert client.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    client.put_object.return_value = {"VersionId": "null"}
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage(client).put_quarantine("upload", io.BytesIO(b"abc"), 3)


def test_snapshot_never_reads_latest_or_excess_bytes():
    client = private_client()
    client.get_object.return_value = {
        "Body": io.BytesIO(b"abcd"),
        "VersionId": "v1",
        "ContentLength": 3,
    }
    with pytest.raises(AttachmentError, match="length_mismatch"):
        storage(client).snapshot(
            ObjectIdentity("key", "v1"), io.BytesIO(), expected_length=3
        )
    assert client.get_object.call_args.kwargs["VersionId"] == "v1"


def test_unknown_backend_never_uses_legacy_credentials():
    for backend in ("disabled", "minio", "gcs"):
        with pytest.raises(AttachmentError, match="storage_backend_unqualified"):
            from_settings(SimpleNamespace(FI_ATTACHMENTS_STORAGE={"backend": backend}))


def test_promotion_requires_exact_snapshot():
    client = private_client()
    with pytest.raises(AttachmentError, match="snapshot_changed"):
        storage(client).promote(
            io.BytesIO(b"mutated"),
            upload_id="00112233-4455-6677-8899-aabbccddeeff",
            expected_length=3,
            expected_sha256="0" * 64,
        )
    client.put_object.assert_not_called()
