"""Storage core contract tests. Synthetic peers/stubs are NOT native proof.

The isolated runner imports the actual schema-owned model/types read-only, and
blocks database connections. Normal integrated runs need no import overlay.
"""

import base64
import hashlib
import io
import json
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryFile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from minio.credentials import StaticProvider
from minio.minioadmin import MinioAdmin

from tfc.utils import storage_config as sc
from tracer.models.attachment_storage import (
    BINDING_IDENTITY_FIELDS,
    AttachmentStorageBinding,
    binding_identity_sha256,
)
from tracer.services.attachments import readiness, storage
from tracer.services.attachments import storage_s3 as s3
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    LeaseFence,
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
    SourceRef,
)
from tracer.services.attachments.storage_minio import (
    MinIOPostureCredentials,
    MinIOPrivateMediaStorage,
)

DATA = b"synthetic bounded media"
HASH = hashlib.sha256(DATA).hexdigest()
CHECKSUM = base64.b64encode(hashlib.sha256(DATA).digest()).decode()
CREATED = datetime(2025, 1, 1, tzinfo=UTC)


def record(provider="s3", **overrides):
    values = {
        "provider": provider,
        "endpoint": "https://s3.us-east-1.amazonaws.com",
        "account_identity": "owner-a",
        "region": "us-east-1",
        "quarantine_bucket": "private-quarantine",
        "serving_bucket": "private-serving",
    }
    if provider == "minio":
        values.update(
            endpoint="https://private-minio.test:9443",
            account_identity="installation-a",
        )
    for purpose in ("quarantine", "serving"):
        bucket = values[purpose + "_bucket"]
        values.update(
            {
                purpose + "_" + key: value
                for key, value in {
                    "resource_identity": (
                        f"arn:aws:s3:::{bucket}"
                        if provider == "s3"
                        else f"minio://installation-a/{bucket}"
                    ),
                    "owner_identity": "owner-a",
                    "created_at": CREATED,
                    "encryption_mode": "AES256",
                    "encryption_key_identity": "",
                }.items()
            }
        )
    values.update(overrides)
    return AttachmentStorageBinding(
        id=uuid4(), identity_sha256=binding_identity_sha256(values), **values
    )


def config(row):
    return sc.AttachmentStorageConfig(
        sc.StorageConfig(
            row.provider,
            row.endpoint.split("://", 1)[1],
            row.endpoint.startswith("https:"),
            row.region,
            "/synthetic/ca.pem",
        ),
        row.quarantine_bucket,
        row.serving_bucket,
        sc.StorageCredentials("synthetic-q-access", "synthetic-q-secret"),
        sc.StorageCredentials("synthetic-s-access", "synthetic-s-secret"),
    )


@pytest.fixture
def adapter():
    row = record()
    return s3.S3PrivateMediaStorage(binding=row, config=config(row))


def locator(adapter, purpose="serving", key="original/asset"):
    return ObjectLocator(adapter.binding_id, purpose, key)


def identity(adapter, purpose="serving", version="version-a"):
    return ObjectIdentity(
        adapter.binding_id,
        purpose,
        "original/asset" if purpose == "serving" else "quarantine/asset",
        version,
    )


def permit(adapter, purpose="serving", seconds=25):
    now, owner = datetime.now(UTC), uuid4()
    return PutPermit(
        "derivative",
        owner,
        locator(adapter, purpose),
        LeaseFence("derivative", owner, uuid4(), 1, now + timedelta(minutes=1)),
        1,
        now + timedelta(seconds=seconds),
    )


def missing(operation="HeadObject", code="NoSuchVersion", status=404):
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


def no_posture(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "verify_identity", Mock())
    monkeypatch.setattr(adapter, "verify_readiness", Mock())


def posture_client(**overrides):
    client = Mock()
    client.list_buckets.return_value = {
        "Owner": {"ID": "owner-a"},
        "Buckets": [
            {"Name": "private-quarantine", "CreationDate": CREATED},
            {"Name": "private-serving", "CreationDate": CREATED},
        ],
    }
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    client.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        }
    }
    client.get_bucket_acl.return_value = {
        "Owner": {"ID": "owner-a"},
        "Grants": [
            {
                "Grantee": {"ID": "owner-a", "Type": "CanonicalUser"},
                "Permission": "FULL_CONTROL",
            }
        ],
    }
    client.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }
    }
    client.get_bucket_ownership_controls.return_value = {
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
    }
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.get_bucket_cors.return_value = {"CORSRules": []}
    client.get_bucket_policy.side_effect = missing(
        "GetBucketPolicy", "NoSuchBucketPolicy"
    )
    client.get_object_lock_configuration.side_effect = missing(
        "GetObjectLockConfiguration", "ObjectLockConfigurationNotFoundError"
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
    for method, value in overrides.items():
        getattr(client, method).side_effect = None
        getattr(client, method).return_value = value
    return client


def test_real_frozen_protocol(adapter):
    assert adapter.binding_id
    assert tuple(adapter.identity) == BINDING_IDENTITY_FIELDS
    with pytest.raises(TypeError):
        adapter.identity["region"] = "changed"
    adapter.posture = posture_client()
    adapter.verify_readiness()
    for operation in ("admit", "write", "read", "erase"):
        adapter.verify_identity(operation=operation)


@pytest.mark.parametrize("field", BINDING_IDENTITY_FIELDS)
def test_each_persisted_identity_field_compared(adapter, monkeypatch, field):
    observed = dict(adapter.identity)
    observed[field] = (
        CREATED + timedelta(days=1) if field.endswith("created_at") else "changed"
    )
    monkeypatch.setattr(adapter, "_observe", Mock(return_value=observed))
    with pytest.raises(AttachmentError):
        adapter.verify_identity(operation="erase")


@pytest.mark.parametrize("operation", ["read", "erase"])
def test_historical_ignores_admission_lifecycle(adapter, operation):
    adapter.posture = posture_client(
        get_bucket_lifecycle_configuration={"Rules": []},
        get_bucket_versioning={"Status": "Suspended"},
    )
    adapter.verify_identity(operation=operation)
    adapter.posture.get_bucket_lifecycle_configuration.assert_not_called()
    adapter.posture.get_bucket_versioning.assert_not_called()
    with pytest.raises(AttachmentError):
        adapter.verify_readiness()


@pytest.mark.parametrize(
    "method,value",
    [
        ("get_public_access_block", {"PublicAccessBlockConfiguration": {}}),
        ("get_bucket_ownership_controls", {"OwnershipControls": {"Rules": []}}),
        ("get_bucket_acl", {"Owner": {"ID": "foreign"}, "Grants": []}),
        ("get_bucket_encryption", {"ServerSideEncryptionConfiguration": {"Rules": []}}),
        ("get_bucket_versioning", {"Status": "Suspended"}),
        ("get_bucket_versioning", {"Status": "Enabled", "MFADelete": "Enabled"}),
        ("get_bucket_cors", {"CORSRules": [{"AllowedOrigins": ["*"]}]}),
        (
            "get_bucket_policy",
            {
                "Policy": json.dumps(
                    {"Statement": [{"Effect": "Allow", "Principal": "*"}]}
                )
            },
        ),
        (
            "get_object_lock_configuration",
            {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}},
        ),
        ("get_bucket_lifecycle_configuration", {"Rules": []}),
    ],
)
def test_posture_negatives_fail_closed(adapter, method, value):
    adapter.posture = posture_client(**{method: value})
    with pytest.raises(AttachmentError):
        adapter.verify_readiness()


@pytest.mark.parametrize(
    "mutation",
    [
        {"Expiration": {"Days": 1}},
        {"Transitions": [{"Days": 1, "StorageClass": "GLACIER"}]},
        {
            "NoncurrentVersionTransitions": [
                {"NoncurrentDays": 1, "StorageClass": "GLACIER"}
            ]
        },
        {"Filter": {"Tag": {"Key": "x", "Value": "y"}}},
        {
            "NoncurrentVersionExpiration": {
                "NoncurrentDays": 1,
                "NewerNoncurrentVersions": 3,
            }
        },
        {"AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}},
    ],
)
def test_lifecycle_not_waived(adapter, mutation):
    adapter.posture = posture_client()
    adapter.posture.get_bucket_lifecycle_configuration.return_value["Rules"][0].update(
        mutation
    )
    with pytest.raises(AttachmentError):
        adapter.verify_readiness()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://minio.test",
        "http://s3.us-east-1.amazonaws.com",
        "https://s3.us-east-1.amazonaws.com.evil",
        "https://s3.us-west-2.amazonaws.com",
    ],
)
def test_native_s3_cannot_masquerade(endpoint):
    row = record(endpoint=endpoint)
    with pytest.raises(ValueError):
        s3.S3PrivateMediaStorage(binding=row, config=config(row))


def test_ambient_sdk_inputs_ignored(monkeypatch, tmp_path):
    forbidden = tmp_path / "must-not-open"
    forbidden.write_text("[profile evil]\nregion=us-west-2\n")
    for name in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.setenv(name, str(forbidden))
    monkeypatch.setenv("AWS_PROFILE", "evil")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://evil.invalid")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://evil.invalid")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-secret")
    row = record()
    client = s3._client(config(row), config(row).quarantine_credentials)
    assert client.meta.endpoint_url == row.endpoint
    assert client.meta.region_name == row.region
    assert client._request_signer._credentials.access_key == "synthetic-q-access"
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client.meta.config.connect_timeout == 3
    assert client.meta.config.read_timeout == 10


def put_request(adapter, client, permit, stream, length, digest):
    # Substitute the in-process SDK transport only; use the real request,
    # response, exact-version HEAD verification and deadline logic.
    with patch.object(adapter, "_supervised_put", partial(s3._put_and_head, client)):
        return adapter._put_request(permit, stream, length, digest)


def test_put_request_uses_create_only_checksum_revision_and_exact_head(adapter):
    p = permit(adapter)
    with Stubber(adapter.serving) as stub:
        stub.add_response(
            "put_object",
            {
                "VersionId": "new",
                "ServerSideEncryption": "AES256",
                "ChecksumSHA256": CHECKSUM,
            },
            {
                "Bucket": adapter.serving_bucket,
                "Key": p.locator.key,
                "Body": ANY,
                "ContentLength": len(DATA),
                "ContentType": "application/octet-stream",
                "IfNoneMatch": "*",
                "ChecksumSHA256": CHECKSUM,
                "ServerSideEncryption": "AES256",
            },
        )
        stub.add_response(
            "head_object",
            {
                "VersionId": "new",
                "ContentLength": len(DATA),
                "ServerSideEncryption": "AES256",
            },
            {
                "Bucket": adapter.serving_bucket,
                "Key": p.locator.key,
                "VersionId": "new",
            },
        )
        result = put_request(
            adapter, adapter.serving, p, io.BytesIO(DATA), len(DATA), HASH
        )
        assert result == ObjectIdentity(
            adapter.binding_id, "serving", p.locator.key, "new"
        )
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "change",
    [
        {"VersionId": "null"},
        {"VersionId": ""},
        {"ChecksumSHA256": "wrong"},
        {"ServerSideEncryption": "aws:kms"},
    ],
)
def test_put_response_never_adopts_ambiguous_revision(adapter, change):
    response = {
        "VersionId": "new",
        "ChecksumSHA256": CHECKSUM,
        "ServerSideEncryption": "AES256",
        **change,
    }
    client = Mock()
    client.put_object.return_value = response
    with pytest.raises(ValueError):
        put_request(adapter, client, permit(adapter), io.BytesIO(DATA), len(DATA), HASH)
    client.head_object.assert_not_called()


@pytest.mark.parametrize(
    "body,length,digest",
    [
        (DATA, len(DATA) - 1, HASH),
        (DATA, len(DATA) + 1, HASH),
        (DATA, len(DATA), "0" * 64),
    ],
)
def test_put_hash_length_preflight_never_dispatches(adapter, body, length, digest):
    client = Mock()
    with pytest.raises(ValueError):
        put_request(adapter, client, permit(adapter), io.BytesIO(body), length, digest)
    client.put_object.assert_not_called()


@pytest.mark.parametrize("seconds", [-1, 31])
def test_put_expired_or_unbounded_permit_never_dispatches(adapter, seconds):
    client = Mock()
    with pytest.raises(ValueError):
        put_request(
            adapter,
            client,
            permit(adapter, seconds=seconds),
            io.BytesIO(DATA),
            len(DATA),
            HASH,
        )
    client.put_object.assert_not_called()


@pytest.mark.parametrize("operation", ["snapshot", "range", "delete", "list", "put"])
def test_cross_binding_rejected_before_io(adapter, monkeypatch, operation):
    no_posture(adapter, monkeypatch)
    obj = replace(identity(adapter), binding_id=uuid4())
    loc = ObjectLocator(obj.binding_id, obj.purpose, obj.key)
    with pytest.raises(AttachmentError):
        if operation == "snapshot":
            adapter.snapshot(obj, io.BytesIO(), expected_length=1)
        elif operation == "range":
            adapter.open_range(obj, start=0, end=0, expected_length=1)
        elif operation == "delete":
            adapter.erase_version(obj)
        elif operation == "list":
            adapter.list_versions(loc)
        else:
            adapter.promote(
                io.BytesIO(DATA),
                permit=replace(permit(adapter), locator=loc),
                expected_length=len(DATA),
                expected_sha256=HASH,
            )
    adapter.verify_identity.assert_not_called()
    adapter.verify_readiness.assert_not_called()


class TrackedBody(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requests = []

    def read(self, size=-1):
        self.requests.append(size)
        return super().read(size)


@pytest.mark.parametrize("purpose", ["quarantine", "serving"])
def test_snapshot_exact_version_and_bounded_reads(adapter, monkeypatch, purpose):
    no_posture(adapter, monkeypatch)
    obj = identity(adapter, purpose)
    body = TrackedBody(DATA)
    client = getattr(adapter, purpose)
    with Stubber(client) as stub:
        stub.add_response(
            "get_object",
            {
                "Body": body,
                "VersionId": obj.version,
                "ContentLength": len(DATA),
                "ServerSideEncryption": "AES256",
            },
            {
                "Bucket": getattr(adapter, purpose + "_bucket"),
                "Key": obj.key,
                "VersionId": obj.version,
            },
        )
        output = io.BytesIO()
        assert adapter.snapshot(obj, output, expected_length=len(DATA)) == HASH
        assert output.getvalue() == DATA
        assert body.closed and max(body.requests) <= s3.BUFFER


@pytest.mark.parametrize(
    "change,data",
    [
        ({"VersionId": "wrong"}, DATA),
        ({"ContentLength": 99}, DATA),
        ({"ServerSideEncryption": "none"}, DATA),
        ({}, DATA[:-1]),
        ({}, DATA + b"x"),
    ],
)
def test_snapshot_integrity_failure_closes_body(adapter, monkeypatch, change, data):
    no_posture(adapter, monkeypatch)
    obj, body = identity(adapter), TrackedBody(data)
    client = Mock()
    client.get_object.return_value = {
        "Body": body,
        "VersionId": obj.version,
        "ContentLength": len(DATA),
        "ServerSideEncryption": "AES256",
        **change,
    }
    adapter.serving = client
    with pytest.raises(AttachmentError):
        adapter.snapshot(obj, io.BytesIO(), expected_length=len(DATA))
    assert body.closed


@pytest.mark.parametrize("start,end", [(0, len(DATA) - 1), (2, 5)])
def test_range_actual_request_contract(adapter, monkeypatch, start, end):
    no_posture(adapter, monkeypatch)
    obj, body = identity(adapter), TrackedBody(DATA[start : end + 1])
    with Stubber(adapter.serving) as stub:
        stub.add_response(
            "get_object",
            {
                "Body": body,
                "VersionId": obj.version,
                "ContentLength": end - start + 1,
                "ContentRange": f"bytes {start}-{end}/{len(DATA)}",
                "ServerSideEncryption": "AES256",
            },
            {
                "Bucket": adapter.serving_bucket,
                "Key": obj.key,
                "VersionId": obj.version,
                "Range": f"bytes={start}-{end}",
            },
        )
        assert (
            adapter.open_range(
                obj, start=start, end=end, expected_length=len(DATA)
            ).read()
            == DATA[start : end + 1]
        )
        assert body.closed


@pytest.mark.parametrize(
    "start,end,total",
    [
        (-1, 1, 2),
        (1, 0, 2),
        (0, 2, 2),
        (0, s3.RANGE_MAX, s3.RANGE_MAX + 1),
        (True, 1, 2),
    ],
)
def test_invalid_range_never_requests_body(adapter, monkeypatch, start, end, total):
    no_posture(adapter, monkeypatch)
    adapter.serving = Mock()
    with pytest.raises(AttachmentError):
        adapter.open_range(
            identity(adapter), start=start, end=end, expected_length=total
        )
    adapter.serving.get_object.assert_not_called()


@pytest.mark.parametrize(
    "change,data",
    [
        ({"ContentRange": "bytes 0-4/100"}, DATA[:5]),
        ({"VersionId": "null"}, DATA[:5]),
        ({}, DATA[:4]),
        ({}, DATA[:6]),
    ],
)
def test_range_wrong_revision_shape_or_body_closed(adapter, monkeypatch, change, data):
    no_posture(adapter, monkeypatch)
    body = TrackedBody(data)
    adapter.serving = Mock()
    adapter.serving.get_object.return_value = {
        "Body": body,
        "VersionId": "version-a",
        "ContentLength": 5,
        "ContentRange": f"bytes 0-4/{len(DATA)}",
        "ServerSideEncryption": "AES256",
        **change,
    }
    with pytest.raises(AttachmentError):
        adapter.open_range(identity(adapter), start=0, end=4, expected_length=len(DATA))
    assert body.closed


def test_list_exact_filter_markers_and_cursor_binding(adapter, monkeypatch):
    no_posture(adapter, monkeypatch)
    loc = locator(adapter)
    with Stubber(adapter.serving) as stub:
        stub.add_response(
            "list_object_versions",
            {
                "IsTruncated": True,
                "NextKeyMarker": loc.key,
                "NextVersionIdMarker": "marker",
                "Versions": [
                    {"Key": loc.key, "VersionId": "a"},
                    {"Key": loc.key + "-neighbour", "VersionId": "b"},
                ],
                "DeleteMarkers": [{"Key": loc.key, "VersionId": "marker"}],
            },
            {"Bucket": adapter.serving_bucket, "Prefix": loc.key, "MaxKeys": 100},
        )
        first = adapter.list_versions(loc)
        assert [v.version for v in first.versions] == ["a"]
        assert [v.version for v in first.delete_markers] == ["marker"]
        stub.add_response(
            "list_object_versions",
            {"IsTruncated": False},
            {
                "Bucket": adapter.serving_bucket,
                "Prefix": loc.key,
                "MaxKeys": 100,
                "KeyMarker": loc.key,
                "VersionIdMarker": "marker",
            },
        )
        assert adapter.list_versions(loc, cursor=first.next_cursor).next_cursor is None
        with pytest.raises(AttachmentError):
            adapter.list_versions(replace(loc, key="other"), cursor=first.next_cursor)


@pytest.mark.parametrize("limit", [0, 101, -1, True])
def test_list_bounds(adapter, monkeypatch, limit):
    no_posture(adapter, monkeypatch)
    adapter.serving = Mock()
    with pytest.raises(AttachmentError):
        adapter.list_versions(locator(adapter), limit=limit)
    adapter.serving.list_object_versions.assert_not_called()


def test_erase_exact_version_request_and_absence_confirmation(adapter, monkeypatch):
    no_posture(adapter, monkeypatch)
    obj = identity(adapter)
    args = {"Bucket": adapter.serving_bucket, "Key": obj.key, "VersionId": obj.version}
    with Stubber(adapter.serving) as stub:
        stub.add_response("delete_object", {"VersionId": obj.version}, args)
        stub.add_client_error(
            "head_object", "NoSuchVersion", http_status_code=404, expected_params=args
        )
        adapter.erase_version(obj)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("failure", ["denied", "retained", "wrong-version", "network"])
def test_ambiguous_erase_never_success(adapter, monkeypatch, failure):
    no_posture(adapter, monkeypatch)
    adapter.serving = Mock()
    adapter.serving.delete_object.return_value = {"VersionId": "version-a"}
    adapter.serving.head_object.side_effect = missing()
    if failure == "denied":
        adapter.serving.delete_object.side_effect = missing(
            "DeleteObject", "AccessDenied", 403
        )
    elif failure == "retained":
        adapter.serving.head_object.side_effect = None
    elif failure == "wrong-version":
        adapter.serving.delete_object.return_value = {"VersionId": "foreign"}
    else:
        adapter.serving.delete_object.side_effect = OSError(
            "sensitive-synthetic-endpoint"
        )
    with pytest.raises(AttachmentError) as exc:
        adapter.erase_version(identity(adapter))
    assert "sensitive" not in str(exc.value)


def test_all_versions_exact_keys_markers_multipart_and_final_empty(
    adapter, monkeypatch
):
    no_posture(adapter, monkeypatch)
    client, loc = Mock(), locator(adapter)
    adapter.serving = client
    client.list_object_versions.side_effect = [
        {
            "IsTruncated": True,
            "NextKeyMarker": loc.key,
            "NextVersionIdMarker": "marker",
            "Versions": [
                {"Key": loc.key, "VersionId": "a"},
                {"Key": loc.key + "-neighbour", "VersionId": "untouched"},
            ],
        },
        {
            "IsTruncated": False,
            "DeleteMarkers": [{"Key": loc.key, "VersionId": "marker"}],
        },
        {"IsTruncated": False},
    ]
    client.delete_object.return_value = {}
    client.head_object.side_effect = missing()
    client.list_multipart_uploads.side_effect = [
        {
            "IsTruncated": True,
            "NextKeyMarker": loc.key,
            "NextUploadIdMarker": "part-a",
            "Uploads": [
                {"Key": loc.key, "UploadId": "part-a"},
                {"Key": loc.key + "x", "UploadId": "untouched"},
            ],
        },
        {"IsTruncated": False, "Uploads": [{"Key": loc.key, "UploadId": "part-b"}]},
        {"IsTruncated": False},
    ]
    result = adapter.erase_all_versions(loc)
    assert result.empty and not result.retryable
    assert [c.kwargs["VersionId"] for c in client.delete_object.call_args_list] == [
        "a",
        "marker",
    ]
    assert [
        c.kwargs["UploadId"] for c in client.abort_multipart_upload.call_args_list
    ] == ["part-a", "part-b"]
    assert (
        client.list_object_versions.call_args_list[1].kwargs["VersionIdMarker"]
        == "marker"
    )
    assert (
        client.list_multipart_uploads.call_args_list[1].kwargs["UploadIdMarker"]
        == "part-a"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "partial",
        "cycle",
        "pages",
        "late-version",
        "late-part",
        "permission",
        "null-version",
    ],
)
def test_all_versions_ambiguous_stays_pending(adapter, monkeypatch, failure):
    no_posture(adapter, monkeypatch)
    client, loc = Mock(), locator(adapter)
    adapter.serving = client
    client.list_object_versions.return_value = {"IsTruncated": False}
    client.list_multipart_uploads.return_value = {"IsTruncated": False}
    client.delete_object.return_value = {}
    client.head_object.side_effect = missing()
    if failure in ("partial", "null-version"):
        client.list_object_versions.return_value = {
            "IsTruncated": False,
            "Versions": [
                {
                    "Key": loc.key,
                    "VersionId": "null" if failure == "null-version" else "a",
                }
            ],
        }
        client.delete_object.side_effect = missing("DeleteObject", "AccessDenied", 403)
    elif failure == "cycle":
        client.list_object_versions.return_value = {
            "IsTruncated": True,
            "NextKeyMarker": loc.key,
            "NextVersionIdMarker": "a",
        }
    elif failure == "pages":
        counter = iter(range(200))
        client.list_object_versions.side_effect = lambda **kw: {
            "IsTruncated": True,
            "NextKeyMarker": loc.key,
            "NextVersionIdMarker": str(next(counter)),
        }
    elif failure == "late-version":
        client.list_object_versions.side_effect = [
            {"IsTruncated": False},
            {"IsTruncated": False, "Versions": [{"Key": loc.key, "VersionId": "late"}]},
        ]
    elif failure == "late-part":
        client.list_multipart_uploads.side_effect = [
            {"IsTruncated": False},
            {"IsTruncated": False, "Uploads": [{"Key": loc.key, "UploadId": "late"}]},
        ]
    else:
        client.list_object_versions.side_effect = missing(
            "ListObjectVersions", "AccessDenied", 403
        )
    result = adapter.erase_all_versions(loc)
    assert not result.empty and result.retryable
    assert client.list_object_versions.call_count <= 100


def test_no_stale_key_only_erase_wrapper(adapter):
    assert not adapter.erase_upload(uuid4()).empty


def minio_posture(endpoint="private-minio.test:9443"):
    http = sc.storage_http_client(connect_timeout=3, read_timeout=10, retries=0)
    admin = MinioAdmin(
        endpoint=endpoint,
        credentials=StaticProvider("synthetic-meta-access", "synthetic-meta-secret"),
        region="us-east-1",
        http_client=http,
    )
    admin.info = Mock(
        return_value=json.dumps(
            {"deploymentID": "installation-a", "servers": [{"state": "ok", "env": {}}]}
        )
    )
    admin.config_get = Mock(return_value='api cors_allow_origin="" requests_max=0')
    admin.kms_key_status = Mock(
        return_value=json.dumps(
            {"key-id": "real-kms-id", "encryptionErr": "", "decryptionErr": ""}
        )
    )
    return MinIOPostureCredentials(
        admin,
        s3.S3PostureCredentials(
            sc.StorageCredentials("synthetic-meta-access", "synthetic-meta-secret")
        ),
    )


def test_minio_separate_posture_not_aws_checks():
    row, posture = record("minio"), minio_posture()
    adapter = MinIOPrivateMediaStorage(
        binding=row, config=config(row), posture_credentials=posture
    )
    adapter.posture = posture_client()
    adapter.verify_readiness()
    adapter.posture.get_public_access_block.assert_not_called()
    adapter.posture.get_bucket_ownership_controls.assert_not_called()
    assert posture.admin.config_get.call_count
    assert posture.admin.kms_key_status.call_count


@pytest.mark.parametrize(
    "failure",
    [
        "wildcard-config",
        "env-override",
        "missing-env",
        "wrong-installation",
        "kms-encrypt",
        "kms-decrypt",
        "missing-kms",
    ],
)
def test_minio_encryption_and_global_cors_never_waived(failure):
    row, posture = record("minio"), minio_posture()
    if failure == "wildcard-config":
        posture.admin.config_get.return_value = "api cors_allow_origin=*"
    elif failure == "env-override":
        posture.admin.info.return_value = json.dumps(
            {
                "deploymentID": "installation-a",
                "servers": [
                    {"state": "ok", "env": {"MINIO_API_CORS_ALLOW_ORIGIN": "*"}}
                ],
            }
        )
    elif failure == "missing-env":
        posture.admin.info.return_value = json.dumps(
            {"deploymentID": "installation-a", "servers": [{"state": "ok"}]}
        )
    elif failure == "wrong-installation":
        posture.admin.info.return_value = json.dumps(
            {"deploymentID": "installation-b", "servers": [{"state": "ok", "env": {}}]}
        )
    else:
        posture.admin.kms_key_status.return_value = json.dumps(
            {
                "key-id": "id",
                "encryptionErr": "denied" if failure == "kms-encrypt" else "",
                "decryptionErr": "denied" if failure == "kms-decrypt" else "",
            }
            if failure != "missing-kms"
            else {}
        )
    adapter = MinIOPrivateMediaStorage(
        binding=row, config=config(row), posture_credentials=posture
    )
    adapter.posture = posture_client()
    with pytest.raises(AttachmentError):
        adapter.verify_readiness()


def access_settings(row):
    mapping = {}
    for purpose, prefix in (("quarantine", "Q"), ("serving", "S")):
        mapping[purpose] = {
            "access_key_ref": f"HISTORY_{prefix}_ACCESS",
            "secret_key_ref": f"HISTORY_{prefix}_SECRET",
        }
    return SimpleNamespace(
        FI_ATTACHMENTS_BINDING_ACCESS={row.identity_sha256: mapping},
        FI_ATTACHMENTS_STORAGE={"backend": "invalid-current-b"},
        FI_ATTACHMENTS_ENABLED=True,
    )


@pytest.mark.parametrize("operation", ["read", "write", "erase", "admit"])
def test_factory_real_config_historical_a_invalid_default_b(monkeypatch, operation):
    row = record()
    for name in ("Q_ACCESS", "Q_SECRET", "S_ACCESS", "S_SECRET"):
        monkeypatch.setenv("HISTORY_" + name, "synthetic-" + name)
    manager = AttachmentStorageBinding.objects
    adapter = Mock()
    constructor = Mock(return_value=adapter)
    monkeypatch.setattr(storage, "_provider", lambda p: constructor)
    with patch.object(manager, "get", return_value=row):
        assert (
            storage.for_binding(
                row.id, operation=operation, settings=access_settings(row)
            )
            is adapter
        )
    kwargs = constructor.call_args.kwargs
    assert set(kwargs) == {"binding", "config", "posture_credentials"}
    assert kwargs["binding"] is row
    assert kwargs["config"].storage.endpoint_url == row.endpoint
    adapter.verify_identity.assert_called_once_with(operation=operation)
    assert adapter.verify_readiness.call_count == int(operation in ("admit", "write"))


def test_admission_observation_precedes_persistence_and_compares_full_tuple(
    monkeypatch,
):
    row, config_value = record(), None
    config_value = config(row)
    events = []
    constructor = Mock(return_value=Mock())
    constructor.discover_identity.side_effect = lambda **kw: (
        events.append("observe")
        or {n: getattr(row, n) for n in BINDING_IDENTITY_FIELDS}
    )
    constructor.return_value.verify_readiness.side_effect = lambda: events.append(
        "verify"
    )
    monkeypatch.setattr(storage, "_provider", lambda p: constructor)
    monkeypatch.setattr(
        storage, "resolve_attachment_storage_config", lambda c: config_value
    )
    monkeypatch.setattr(AttachmentStorageBinding.objects, "get", lambda **kw: row)
    monkeypatch.setattr(storage, "materialize_attachment_binding_config", Mock())

    def create(**kw):
        events.append("persist")
        return row, False

    with patch.object(
        AttachmentStorageBinding.objects, "get_or_create", side_effect=create
    ):
        assert storage.bind_for_admission(settings=access_settings(row)) == row.id
    assert events == ["observe", "verify", "persist", "verify"]
    observed = {n: getattr(row, n) for n in BINDING_IDENTITY_FIELDS}
    row.serving_created_at = CREATED + timedelta(days=1)
    with patch.object(
        AttachmentStorageBinding.objects, "get_or_create", return_value=(row, False)
    ):
        # Return a candidate matching the old tuple but a corrupt persisted row.
        constructor.discover_identity.side_effect = None
        constructor.discover_identity.return_value = observed
        with pytest.raises(AttachmentError):
            storage.bind_for_admission(settings=access_settings(row))


def test_no_network_under_any_transaction(monkeypatch):
    monkeypatch.setattr(
        storage.connections,
        "all",
        lambda: [
            SimpleNamespace(in_atomic_block=False),
            SimpleNamespace(in_atomic_block=True),
        ],
    )
    provider = Mock()
    monkeypatch.setattr(storage, "_provider", provider)
    with pytest.raises(AttachmentError):
        storage.for_binding(uuid4(), operation="erase", settings=SimpleNamespace())
    provider.assert_not_called()


def test_split_readiness_no_default_decoder_or_envelope_for_erase(monkeypatch):
    fn = Mock(return_value="historical-adapter")
    monkeypatch.setattr(storage, "for_binding", fn)
    monkeypatch.setattr(storage, "bind_for_admission", Mock(side_effect=AssertionError))
    assert (
        readiness.resolve_historical(uuid4(), operation="erase") == "historical-adapter"
    )
    with pytest.raises(AttachmentError):
        readiness.require_new_admission(
            operation="upload", settings=SimpleNamespace(FI_ATTACHMENTS_ENABLED=False)
        )
    with pytest.raises(AttachmentError):
        readiness.check()
    with pytest.raises(AttachmentError):
        storage.from_settings(SimpleNamespace())


def test_provider_file_does_not_require_envelope_keys():
    readiness.require_source_keys(source=SourceRef("ProviderFile", uuid4()))


def test_scratch_missing_symlink_or_insufficient_rejected(tmp_path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    symlink = tmp_path / "link"
    symlink.symlink_to(folder, target_is_directory=True)
    for path, required in (("", 0), (str(symlink), 0), (str(folder), 2**63 - 1)):
        with pytest.raises(AttachmentError):
            readiness._scratch(SimpleNamespace(FI_ATTACHMENTS_WORK_DIR=path), required)
    readiness._scratch(SimpleNamespace(FI_ATTACHMENTS_WORK_DIR=str(folder)), 1)


class PeerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_PUT(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.calls.append(("PUT", self.path, dict(self.headers), data))
        if self.server.mode == "provider":
            self.send_error(503, "synthetic-provider-details")
            return
        if self.server.mode == "stall":
            self.server.release.wait(10)
        if self.server.mode == "redirect":
            self.send_response(307)
            self.send_header("Location", "http://127.0.0.1:1/escape")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("x-amz-version-id", "peer-version")
        self.send_header("x-amz-server-side-encryption", "AES256")
        self.send_header("x-amz-checksum-sha256", CHECKSUM)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.server.calls.append(("HEAD", self.path, dict(self.headers), b""))
        self.send_response(200)
        self.send_header(
            "x-amz-version-id",
            "wrong" if self.server.mode == "revision" else "peer-version",
        )
        self.send_header(
            "x-amz-server-side-encryption",
            "wrong" if self.server.mode == "encryption" else "AES256",
        )
        self.send_header(
            "Content-Length", str(len(DATA) + int(self.server.mode == "length"))
        )
        self.end_headers()


@pytest.fixture
def peer(tmp_path):
    from tracer.tests.test_attachment_safe_fetch import _certificate

    cert, key = _certificate(tmp_path, "localhost")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), PeerHandler)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.ca = str(cert)
    server.calls, server.mode, server.release = [], "normal", threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def loopback_adapter(adapter, peer, monkeypatch):
    no_posture(adapter, monkeypatch)
    adapter.config = replace(
        adapter.config,
        storage=replace(
            adapter.config.storage,
            endpoint=f"localhost:{peer.server_port}",
            ca_certs=peer.ca,
        ),
    )
    # Synthetic TLS peer is transport testing only; constructor guards are tested above.
    return adapter


@pytest.mark.parametrize(
    "request_thread", [False, True], ids=["main", "request-thread"]
)
def test_actual_loopback_signed_create_only_put_and_version_head(
    adapter, peer, monkeypatch, request_thread
):
    adapter = loopback_adapter(adapter, peer, monkeypatch)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-ambient-credential")
    credentials = replace(
        adapter.config.serving_credentials, session_token="synthetic-session-token"
    )
    adapter.config = replace(adapter.config, serving_credentials=credentials)
    with (
        TemporaryFile() as snapshot,
        patch.object(s3.subprocess, "Popen", wraps=s3.subprocess.Popen) as launch,
    ):
        snapshot.write(DATA)
        put = partial(
            adapter.promote,
            snapshot,
            permit=permit(adapter),
            expected_length=len(DATA),
            expected_sha256=HASH,
        )
        if request_thread:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(put).result(timeout=30)
        else:
            result = put()
        launch.assert_called_once()
        argv, options = launch.call_args.args[0], launch.call_args.kwargs
        assert options["env"] == {}
        assert options["pass_fds"] == (snapshot.fileno(),)
        for value in (
            credentials.access_key,
            credentials.secret_key,
            credentials.session_token,
            "synthetic-ambient-credential",
        ):
            assert value not in repr((argv, options["env"]))
    assert result.version == "peer-version"
    assert [c[0] for c in peer.calls] == ["PUT", "HEAD"]
    put, head = peer.calls
    assert put[2]["If-None-Match"] == "*"
    assert put[2]["x-amz-checksum-sha256"] == CHECKSUM
    assert put[2]["x-amz-server-side-encryption"] == "AES256"
    assert put[2]["Authorization"].startswith(
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/"
    )
    assert put[2]["X-Amz-Security-Token"] == credentials.session_token
    assert put[3] == DATA
    assert "versionId=peer-version" in head[1]


@pytest.mark.parametrize("mode", ["stall", "redirect"])
def test_actual_transport_deadline_or_redirect_never_retry(
    adapter, peer, monkeypatch, mode
):
    adapter = loopback_adapter(adapter, peer, monkeypatch)
    peer.mode = mode
    started = time.monotonic()
    with TemporaryFile() as snapshot, pytest.raises(AttachmentError):
        snapshot.write(DATA)
        adapter.promote(
            snapshot,
            permit=permit(adapter, seconds=3 if mode == "stall" else 10),
            expected_length=len(DATA),
            expected_sha256=HASH,
        )
    assert time.monotonic() - started < 4.5
    assert len(peer.calls) == 1
    assert peer.calls[0][0] == "PUT"


@pytest.mark.parametrize("failure", ["revision", "encryption", "length"])
def test_supervised_put_refuses_wrong_head(adapter, peer, monkeypatch, failure):
    adapter = loopback_adapter(adapter, peer, monkeypatch)
    peer.mode = failure
    with (
        TemporaryFile() as snapshot,
        pytest.raises(AttachmentError, match="^storage_unverified$"),
    ):
        snapshot.write(DATA)
        adapter.put_quarantine(
            permit(adapter, purpose="quarantine"), snapshot, len(DATA)
        )
    assert [op for op, *_ in peer.calls] == ["PUT", "HEAD"]


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
    adapter,
    peer,
    monkeypatch,
    data,
    expected_length,
    bad_digest,
    provider_error,
    code,
    status,
):
    adapter = loopback_adapter(adapter, peer, monkeypatch)
    if provider_error:
        peer.mode = "provider"
    with TemporaryFile() as snapshot, pytest.raises(AttachmentError) as error:
        snapshot.write(data)
        adapter.promote(
            snapshot,
            permit=permit(adapter),
            expected_length=expected_length,
            expected_sha256="0" * 64
            if bad_digest
            else hashlib.sha256(data).hexdigest(),
        )
    assert [op for op, *_ in peer.calls] == (["PUT"] if provider_error else [])
    assert (error.value.code, error.value.status) == (code, status)


def test_minio_quarantine_put_from_request_thread(peer):
    endpoint = f"localhost:{peer.server_port}"
    row = record("minio", endpoint=f"https://{endpoint}")
    config_value = config(row)
    adapter = MinIOPrivateMediaStorage(
        binding=row,
        config=replace(
            config_value, storage=replace(config_value.storage, ca_certs=peer.ca)
        ),
        posture_credentials=minio_posture(endpoint),
    )
    adapter.posture = posture_client()
    allocation = permit(adapter, purpose="quarantine")
    with TemporaryFile() as snapshot, ThreadPoolExecutor(max_workers=1) as pool:
        snapshot.write(DATA)
        result = pool.submit(
            adapter.put_quarantine, allocation, snapshot, len(DATA)
        ).result(timeout=30)
    assert result == ObjectIdentity(
        row.id, "quarantine", allocation.locator.key, "peer-version"
    )
    assert [op for op, *_ in peer.calls] == ["PUT", "HEAD"]
    assert "Credential=synthetic-q-access/" in peer.calls[0][2]["Authorization"]
    assert "versionId=peer-version" in peer.calls[1][1]


@pytest.mark.parametrize("provider", ["minio", "s3"])
def test_native_admission_refuses_plain_stack_endpoint(monkeypatch, provider):
    monkeypatch.setenv("STORAGE_BACKEND", provider)
    monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_SECURE", "false")
    fields = {"backend": provider, "region": "us-east-1"}
    for purpose in ("quarantine", "serving"):
        fields.update(
            {
                f"{purpose}_bucket": f"e2e-{purpose}",
                f"{purpose}_access_key": "fixture",
                f"{purpose}_secret_key": "fixture",
            }
        )
    with patch.object(AttachmentStorageBinding.objects, "get_or_create") as persist:
        with pytest.raises(AttachmentError) as refused:
            storage.bind_for_admission(
                settings=SimpleNamespace(
                    FI_ATTACHMENTS_ENABLED=True, FI_ATTACHMENTS_STORAGE=fields
                )
            )
    assert refused.value.code == "storage_unverified"
    persist.assert_not_called()
