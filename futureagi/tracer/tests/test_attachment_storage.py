"""Policy/identity component tests; these doubles do not qualify native S3."""

import base64
import hashlib
import io
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from django.utils import timezone

from tfc.utils.storage_config import (
    AttachmentStorageConfig,
    StorageConfig,
    StorageCredentials,
)
from tracer.models.attachment_storage import (
    AttachmentStorageBinding,
    binding_identity_sha256,
)
from tracer.services.attachments import storage_s3
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
)
from tracer.services.attachments.storage import bind_for_admission
from tracer.tests.attachment_fixtures import BINDING_ID, binding_values

pytestmark = pytest.mark.unit


def private_client():
    client = Mock()
    values = binding_values()
    client.list_buckets.return_value = {
        "Owner": {"ID": "owner"},
        "Buckets": [
            {
                "Name": values[f"{purpose}_bucket"],
                "CreationDate": values[f"{purpose}_created_at"],
            }
            for purpose in ("quarantine", "serving")
        ],
    }
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    client.get_object_lock_configuration.side_effect = ClientError(
        {"Error": {"Code": "ObjectLockConfigurationNotFoundError"}},
        "GetObjectLockConfiguration",
    )
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
        "Grants": [
            {
                "Grantee": {"ID": "owner", "Type": "CanonicalUser"},
                "Permission": "FULL_CONTROL",
            }
        ],
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
    client.put_object.return_value = {
        "VersionId": "immutable-v1",
        "ServerSideEncryption": "AES256",
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(b"abc").digest()).decode(),
    }
    client.head_object.return_value = {
        "VersionId": "immutable-v1",
        "ContentLength": 3,
        "ServerSideEncryption": "AES256",
    }
    return client


def storage(client):
    values = binding_values()
    binding = AttachmentStorageBinding(
        id=BINDING_ID, identity_sha256=binding_identity_sha256(values), **values
    )
    config = AttachmentStorageConfig(
        StorageConfig("s3", "s3.us-east-1.amazonaws.com", True, "us-east-1", ""),
        "private-quarantine",
        "private-serving",
        StorageCredentials("synthetic-quarantine", "synthetic-quarantine-secret"),
        StorageCredentials("synthetic-serving", "synthetic-serving-secret"),
    )
    with patch.object(storage_s3, "_client", return_value=client):
        service = storage_s3.S3PrivateMediaStorage(binding=binding, config=config)
    # Request-level component seam only: the real request/hash/privacy code runs.
    # Process supervision has separate tests; this double makes calls observable.
    service._supervised_put = (
        lambda permit, stream, length, digest: service._put_request(
            client, permit, stream, length, digest
        )
    )
    return service


def permit(purpose="quarantine"):
    upload_id = uuid4()
    return PutPermit(
        "upload",
        upload_id,
        ObjectLocator(
            BINDING_ID,
            purpose,
            f"{'quarantine' if purpose == 'quarantine' else 'original'}/{upload_id}",
        ),
        None,
        1,
        timezone.now() + timedelta(seconds=25),
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
    allocation = permit()
    result = storage(client).put_quarantine(allocation, io.BytesIO(b"abc"), 3)
    assert result == ObjectIdentity(
        BINDING_ID, "quarantine", allocation.locator.key, "immutable-v1"
    )
    assert client.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    # Only the revision is invalid; encryption, digest, size and HEAD agree.
    client.put_object.return_value["VersionId"] = "null"
    client.head_object.return_value["VersionId"] = "null"
    client.head_object.reset_mock()
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage(client).put_quarantine(allocation, io.BytesIO(b"abc"), 3)
    # Reject the invalid PUT revision before dispatching any revision-bound HEAD.
    client.head_object.assert_not_called()


def test_null_version_negative_control_detects_missing_revision_guard(monkeypatch):
    # One-sided mutation, scoped to this test. No persisted production change:
    # accept only the forbidden null revision, leaving every other guard real.
    revision = storage_s3._revision
    monkeypatch.setattr(
        storage_s3,
        "_revision",
        lambda value: value if value == "null" else revision(value),
    )
    # ObjectIdentity independently rejects null too. Assert that this test also
    # catches removal of the adapter guard, rather than passing via that fallback.
    with pytest.raises(
        AssertionError, match="Expected 'head_object' to not have been called"
    ):
        test_create_once_quarantine_and_no_null_version()


def test_snapshot_never_reads_latest_or_excess_bytes():
    client = private_client()
    client.get_object.return_value = {
        "Body": io.BytesIO(b"abcd"),
        "VersionId": "v1",
        "ContentLength": 3,
        "ServerSideEncryption": "AES256",
    }
    with pytest.raises(AttachmentError, match="snapshot_changed"):
        storage(client).snapshot(
            ObjectIdentity(BINDING_ID, "quarantine", "key", "v1"),
            io.BytesIO(),
            expected_length=3,
        )
    assert client.get_object.call_args.kwargs["VersionId"] == "v1"


def test_unknown_backend_never_uses_legacy_credentials():
    for backend in ("disabled", "minio", "gcs"):
        with pytest.raises(AttachmentError, match="^storage_unverified$"):
            bind_for_admission(
                settings=SimpleNamespace(
                    FI_ATTACHMENTS_ENABLED=True,
                    FI_ATTACHMENTS_STORAGE={"backend": backend},
                )
            )


@pytest.mark.parametrize(
    "data,expected_length,bad_digest,provider_error,code,status",
    [
        (b"ab", 3, False, False, "snapshot_changed", 409),
        (b"abcd", 3, False, False, "snapshot_changed", 409),
        (b"abc", 3, True, False, "snapshot_changed", 409),
        (b"abc", 3, False, True, "storage_unverified", 503),
    ],
)
def test_supervised_promotion_classifies_failures(
    data, expected_length, bad_digest, provider_error, code, status
):
    client = private_client()
    service = storage(client)
    del service._supervised_put  # exercise the real supervised child and its pipe
    if provider_error:
        client.put_object.side_effect = RuntimeError("provider failure")
    with patch.object(storage_s3, "_client", return_value=client):
        with pytest.raises(AttachmentError) as error:
            service.promote(
                io.BytesIO(data), permit=permit("serving"), expected_length=expected_length,
                expected_sha256="0" * 64 if bad_digest else hashlib.sha256(data).hexdigest(),
            )
    assert (error.value.code, error.value.status) == (code, status)


def test_promotion_requires_exact_snapshot():
    client = private_client()
    with pytest.raises(AttachmentError, match="snapshot_changed"):
        storage(client).promote(
            io.BytesIO(b"mutated"),
            permit=permit("serving"),
            expected_length=3,
            expected_sha256="0" * 64,
        )
    client.put_object.assert_not_called()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"binding_id": uuid4()}, "storage_unverified"),
        ({"purpose": "serving"}, "media_unavailable"),
        ({"version": "different-version"}, "media_unavailable"),
    ],
    ids=["foreign-binding", "wrong-purpose", "wrong-version"],
)
def test_component_store_keeps_identity_negative_controls(change, reason):
    from dataclasses import replace

    from tracer.tests.attachment_fixtures import MemoryObjectStore

    store = MemoryObjectStore()
    identity = store.put_quarantine(permit(), io.BytesIO(b"abc"), 3)
    output = io.BytesIO()
    with pytest.raises(AttachmentError, match=f"^{reason}$"):
        store.snapshot(replace(identity, **change), output, expected_length=3)
    assert output.getvalue() == b""
    assert store.objects[identity.key] == b"abc"


def test_component_store_retains_exact_bytes_digest_and_range():
    from tracer.tests.attachment_fixtures import MemoryObjectStore

    store = MemoryObjectStore()
    identity = store.put_quarantine(permit(), io.BytesIO(b"abc"), 3)
    output = io.BytesIO()
    assert store.snapshot(identity, output, expected_length=3) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert output.getvalue() == b"abc"
    assert store.open_range(identity, start=1, end=2, expected_length=3).read() == b"bc"


def test_component_store_never_erases_another_revision():
    from dataclasses import replace

    from tracer.tests.attachment_fixtures import MemoryObjectStore

    store = MemoryObjectStore()
    identity = store.put_quarantine(permit(), io.BytesIO(b"abc"), 3)
    store.erase_version(replace(identity, version="foreign-version"))
    assert store.objects[identity.key] == b"abc"
    store.erase_version(identity)
    assert store.objects == {}
