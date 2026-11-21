"""Synthetic transport contracts only; never native GCS qualification."""

import hashlib
import hmac
import io
import json
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4
from xml.sax.saxutils import escape

import pytest
from google.oauth2.credentials import Credentials
from urllib3._collections import HTTPHeaderDict

from tfc.utils.storage_config import (
    AttachmentStorageConfig,
    StorageConfig,
    StorageCredentials,
)
from tracer.services.attachments import storage_gcs as g
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
)

CREATED = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
KMS = "projects/synthetic/locations/us-central1/keyRings/test/cryptoKeys/test"


def binding():
    values = {
        "id": uuid4(),
        "provider": "gcs",
        "endpoint": "https://storage.googleapis.com",
        "account_identity": "123456789",
        "region": "us-central1",
    }
    for purpose in ("quarantine", "serving"):
        bucket = "synthetic-" + purpose
        values.update(
            {
                f"{purpose}_bucket": bucket,
                f"{purpose}_resource_identity": g.GCSPrivateMediaStorage.resource_identity(
                    bucket
                ),
                f"{purpose}_owner_identity": "123456789",
                f"{purpose}_created_at": CREATED,
                f"{purpose}_encryption_mode": "google-managed",
                f"{purpose}_encryption_key_identity": "",
            }
        )
    return SimpleNamespace(**values)


def config(b):
    return AttachmentStorageConfig(
        StorageConfig("gcs", "storage.googleapis.com", True, b.region, ""),
        b.quarantine_bucket,
        b.serving_bucket,
        StorageCredentials("SYNTHETICQ", "synthetic-quarantine-secret"),
        StorageCredentials("SYNTHETICS", "synthetic-serving-secret"),
    )


def metadata(b, purpose):
    return {
        "kind": "storage#bucket",
        "name": getattr(b, f"{purpose}_bucket"),
        "id": getattr(b, f"{purpose}_bucket"),
        "projectNumber": b.account_identity,
        "timeCreated": CREATED.isoformat(),
        "location": b.region.upper(),
        "iamConfiguration": {
            "uniformBucketLevelAccess": {"enabled": True},
            "publicAccessPrevention": "enforced",
        },
        "versioning": {"enabled": True},
        "storageClass": "STANDARD",
        "softDeletePolicy": {
            "retentionDurationSeconds": "604800",
            "effectiveTime": CREATED.isoformat(),
        },
        "lifecycle": {
            "rule": [
                {
                    "action": {"type": "Delete"},
                    "condition": {"isLive": False, "daysSinceNoncurrentTime": 1},
                },
                {
                    "action": {"type": "AbortIncompleteMultipartUpload"},
                    "condition": {"age": 1},
                },
            ]
        },
    }


class Response:
    def __init__(self, status=200, data=b"", headers=None):
        self.status = status
        self.headers = HTTPHeaderDict(headers or {})
        self.body = io.BytesIO(data)
        self.closed = False
        self.reads = []

    def read(self, size, *, decode_content):
        assert decode_content is False
        assert 0 < size <= g.CHUNK
        self.reads.append(size)
        return self.body.read(size)

    def close(self):
        self.closed = True

    def release_conn(self):
        pass


def xml(root, body):
    return Response(
        data=(
            f'<{root} xmlns="http://doc.s3.amazonaws.com/2006-03-01">{body}</{root}>'
        ).encode()
    )


class HTTP:
    def __init__(self, b):
        self.b = b
        self.calls = []
        self.puts = []
        self.responses = []
        self.meta = {
            purpose: metadata(b, purpose) for purpose in ("quarantine", "serving")
        }
        self.objects = {"101": b"abcdef"}
        self.next_generation = "102"
        self.put_status = 200
        self.delete_status = 204
        self.delete_error = "NoSuchKey"
        self.bucket_status = 200
        self.version_pages = []
        self.multipart_pages = []
        self.soft_pages = []
        self.mutate_object_response = lambda r: r
        self.mutate_put_response = lambda r: r
        self.error = None

    def bounded_put(self, request, data):
        assert request["deadline"] > time.time()
        self.puts.append((request, data))
        if self.put_status == 200:
            self.objects[self.next_generation] = data
        headers = {"x-goog-generation": self.next_generation, "content-length": "0"}
        if "x-goog-encryption-kms-key-name" in request["headers"]:
            headers["x-goog-encryption-kms-key-name"] = (
                request["headers"]["x-goog-encryption-kms-key-name"]
                + "/cryptoKeyVersions/4"
            )
        return self.mutate_put_response({"status": self.put_status, "headers": headers})

    def urlopen(self, method, url, **kwargs):
        if self.error:
            raise RuntimeError(self.error)
        assert kwargs["redirect"] is False
        assert kwargs["retries"] is False
        assert kwargs["preload_content"] is False
        assert kwargs["decode_content"] is False
        assert kwargs["timeout"].connect_timeout <= 3
        assert kwargs["timeout"].read_timeout <= 10
        self.calls.append((method, url, kwargs))
        parsed, query = (
            urlsplit(url),
            parse_qs(urlsplit(url).query, keep_blank_values=True),
        )
        purpose = "quarantine" if "synthetic-quarantine" in parsed.path else "serving"
        bucket = getattr(self.b, f"{purpose}_bucket")
        if parsed.path.startswith("/storage/v1/b/"):
            assert method == "GET"
            assert kwargs["headers"]["authorization"] == "Bearer synthetic-token"
            if "/o/" in parsed.path:
                assert query["softDeleted"] == ["true"]
                assert query["generation"] == ["101"]
                status, data = (
                    404,
                    {"error": {"code": 404, "errors": [{"reason": "notFound"}]}},
                )
            elif parsed.path.endswith("/o"):
                assert query["softDeleted"] == ["true"]
                assert "versions" not in query
                status, data = (
                    self.soft_pages.pop(0)
                    if self.soft_pages
                    else (200, {"kind": "storage#objects"})
                )
            else:
                assert parsed.path == f"/storage/v1/b/{bucket}"
                status, data = self.bucket_status, self.meta[purpose]
            response = Response(status, json.dumps(data).encode())
        else:
            assert kwargs["headers"]["authorization"].startswith(
                "GOOG4-HMAC-SHA256 Credential=SYNTHETIC"
            )
            assert parsed.path.split("/")[1] == bucket
            if "uploads" in query:
                response = (
                    self.multipart_pages.pop(0)
                    if self.multipart_pages
                    else self.upload_page(bucket)
                )
            elif "uploadId" in query:
                assert method == "DELETE"
                response = Response(204)
            elif "versions" in query:
                response = (
                    self.version_pages.pop(0)
                    if self.version_pages
                    else self.version_page(bucket, query["prefix"][0])
                )
            elif method == "DELETE":
                assert (
                    query["generation"][0]
                    == kwargs["headers"]["x-goog-if-generation-match"]
                )
                assert "VersionId" not in query and "versionId" not in query
                if self.delete_status == 204:
                    self.objects.pop(query["generation"][0], None)
                    response = Response(204)
                else:
                    response = xml("Error", f"<Code>{self.delete_error}</Code>")
                    response.status = self.delete_status
            else:
                assert method in ("GET", "HEAD")
                generation = query["generation"][0]
                assert kwargs["headers"]["x-goog-if-generation-match"] == generation
                data = self.objects[generation]
                headers = {
                    "x-goog-generation": generation,
                    "content-length": str(len(data)),
                }
                kms = self.meta[purpose].get("encryption", {}).get("defaultKmsKeyName")
                if kms:
                    headers["x-goog-encryption-kms-key-name"] = (
                        kms + "/cryptoKeyVersions/4"
                    )
                status = 200
                if "range" in kwargs["headers"]:
                    start, end = map(
                        int, kwargs["headers"]["range"].split("=")[1].split("-")
                    )
                    headers["content-range"] = f"bytes {start}-{end}/{len(data)}"
                    data, status = data[start : end + 1], 206
                    headers["content-length"] = str(len(data))
                response = self.mutate_object_response(
                    Response(status, b"" if method == "HEAD" else data, headers)
                )
        self.responses.append(response)
        return response

    def version_page(
        self, bucket, key, versions=None, truncated=False, marker=None, generation=None
    ):
        if versions is None:
            versions = [(key, value) for value in self.objects]
        body = f"<Name>{bucket}</Name><EncodingType>url</EncodingType><IsTruncated>{str(truncated).lower()}</IsTruncated>"
        body += "".join(
            f"<Version><Key>{quote(k, safe='')}</Key><Generation>{v}</Generation></Version>"
            for k, v in versions
        )
        if marker is not None:
            body += f"<NextMarker>{quote(marker, safe='')}</NextMarker><NextGenerationMarker>{generation}</NextGenerationMarker>"
        return xml("ListBucketResult", body)

    def upload_page(
        self, bucket, uploads=(), truncated=False, marker=None, upload_id=None
    ):
        body = f"<Bucket>{bucket}</Bucket><EncodingType>url</EncodingType><IsTruncated>{str(truncated).lower()}</IsTruncated>"
        body += "".join(
            f"<Upload><Key>{quote(k, safe='')}</Key><UploadId>{escape(v)}</UploadId></Upload>"
            for k, v in uploads
        )
        if marker is not None:
            body += f"<NextKeyMarker>{quote(marker, safe='')}</NextKeyMarker><NextUploadIdMarker>{escape(upload_id)}</NextUploadIdMarker>"
        return xml("ListMultipartUploadsResult", body)


@pytest.fixture
def setup():
    b = binding()
    http = HTTP(b)
    storage = g.GCSPrivateMediaStorage(
        binding=b,
        config=config(b),
        posture_credentials=Credentials("synthetic-token"),
        http=http,
    )
    return storage, http, b


def identity(b, purpose="serving", key="original/test", version="101"):
    return ObjectIdentity(b.id, purpose, key, version)


def permit(b, purpose="quarantine", deadline=None):
    return PutPermit(
        "upload",
        uuid4(),
        ObjectLocator(
            b.id,
            purpose,
            "quarantine/test" if purpose == "quarantine" else "original/test",
        ),
        None,
        1,
        deadline or g._now() + timedelta(seconds=25),
    )


def assert_error(code, function, *args, **kwargs):
    with pytest.raises(AttachmentError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert str(caught.value) == code


def test_signing_matches_independent_canonical_oracle():
    creds = StorageCredentials("SYNTHETIC", "not-base64+/=")
    now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    query = g._query({"generation": "123", "prefix": "a b/+é", "versions": ""})
    assert query == "generation=123&prefix=a%20b%2F%2B%C3%A9&versions="
    digest = hashlib.sha256(b"").hexdigest()
    headers = g._signed_headers(
        "GET",
        "/test/a%20b/%C3%A9",
        query,
        {"Host": "storage.googleapis.com", "Range": "bytes=0-2"},
        creds,
        region="auto",
        now=now,
        digest=digest,
    )
    canonical = (
        "GET\n/test/a%20b/%C3%A9\n" + query + "\n"
        "host:storage.googleapis.com\nrange:bytes=0-2\n"
        "x-goog-content-sha256:" + digest + "\nx-goog-date:20250102T030405Z\n\n"
        "host;range;x-goog-content-sha256;x-goog-date\n" + digest
    )
    date_key = hmac.digest(b"GOOG4not-base64+/=", b"20250102", "sha256")
    location_key = hmac.digest(date_key, b"auto", "sha256")
    service_key = hmac.digest(location_key, b"storage", "sha256")
    request_key = hmac.digest(service_key, b"goog4_request", "sha256")
    string = (
        "GOOG4-HMAC-SHA256\n20250102T030405Z\n20250102/auto/storage/goog4_request\n"
        + hashlib.sha256(canonical.encode()).hexdigest()
    )
    expected = hmac.digest(request_key, string.encode(), "sha256").hex()
    assert headers["authorization"] == (
        "GOOG4-HMAC-SHA256 Credential=SYNTHETIC/20250102/auto/storage/goog4_request, SignedHeaders=host;range;x-goog-content-sha256;x-goog-date, Signature="
        + expected
    )


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "s3"},
        {"endpoint": "https://other.test"},
        {"region": "auto"},
        {"serving_bucket": "synthetic-quarantine"},
        {"account_identity": "project-name"},
        {"serving_resource_identity": "wrong"},
        {"serving_owner_identity": "999"},
        {"serving_created_at": datetime(2025, 1, 1)},
        {"serving_encryption_mode": "AES256"},
        {"serving_encryption_key_identity": KMS},
    ],
)
def test_binding_config_mismatch_fails_closed(change):
    b = binding()
    c = config(b)
    for key, value in change.items():
        setattr(b, key, value)
    assert_error("storage_unverified", g.GCSPrivateMediaStorage, binding=b, config=c)


def test_supplied_posture_must_be_typed():
    b = binding()
    assert_error(
        "storage_unverified",
        g.GCSPrivateMediaStorage,
        binding=b,
        config=config(b),
        posture_credentials=object(),
    )


def test_session_token_never_treated_as_gcs_hmac():
    b = binding()
    c = replace(
        config(b),
        serving_credentials=StorageCredentials("SYNTHETICS", "secret", "token"),
    )
    assert_error("storage_unverified", g.GCSPrivateMediaStorage, binding=b, config=c)


def test_constructor_lazy_and_no_ambient_credentials(monkeypatch):
    import google.auth

    monkeypatch.setattr(
        google.auth, "default", lambda *a, **k: pytest.fail("ADC invoked")
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/must-not-open")
    b = binding()
    http = HTTP(b)
    s = g.GCSPrivateMediaStorage(binding=b, config=config(b), http=http)
    assert http.calls == []
    assert_error("storage_unverified", s.verify_readiness)
    assert http.calls == []


def test_historical_cleanup_without_posture_or_default(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "invalid-default-b")
    monkeypatch.setenv("S3_ENDPOINT", "https://synthetic-secret@[")
    monkeypatch.delenv("FI_ATTACHMENTS_SOURCE_KEY", raising=False)
    b = binding()
    http = HTTP(b)
    s = g.GCSPrivateMediaStorage(binding=b, config=config(b), http=http)
    assert_error("cleanup_unavailable", s.erase_version, identity(b))
    assert_error("cleanup_unavailable", s.verify_identity, operation="erase")
    result = s.erase_all_versions(ObjectLocator(b.id, "serving", "original/test"))
    assert (
        not result.empty and result.retryable and result.reason == "cleanup_unavailable"
    )
    assert "101" in http.objects and http.calls == []


def test_binding_instance_mutation_cannot_redirect_adapter(setup):
    s, http, b = setup
    b.serving_bucket = "replacement-bucket"
    assert s._binding.serving_bucket == "synthetic-serving"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "different"),
        ("id", "different"),
        ("projectNumber", "999"),
        ("timeCreated", "2026-01-01T00:00:00Z"),
        ("location", "EU"),
        ("hierarchicalNamespace", {"enabled": True}),
        (
            "iamConfiguration",
            {
                "publicAccessPrevention": "inherited",
                "uniformBucketLevelAccess": {"enabled": True},
            },
        ),
        (
            "iamConfiguration",
            {
                "publicAccessPrevention": "enforced",
                "uniformBucketLevelAccess": {"enabled": False},
            },
        ),
    ],
)
def test_read_identity_and_privacy_change_refused(setup, field, value):
    s, http, b = setup
    http.meta["serving"][field] = value
    assert_error(
        "media_unavailable",
        s.open_range,
        identity(b),
        start=0,
        end=1,
        expected_length=6,
    )
    assert not any(
        method == "GET" and "/original/" in url for method, url, _ in http.calls
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("versioning", {"enabled": False}),
        ("cors", [{"origin": ["*"]}]),
        ("retentionPolicy", {"retentionPeriod": "1"}),
        ("defaultEventBasedHold", True),
        ("objectRetention", {"mode": "Enabled"}),
        ("softDeletePolicy", {"retentionDurationSeconds": "0"}),
        ("softDeletePolicy", {}),
        ("softDeletePolicy", None),
        ("softDeletePolicy", {"retentionDurationSeconds": 604799}),
        ("softDeletePolicy", {"retentionDurationSeconds": "7776001"}),
        ("softDeletePolicy", {"retentionDurationSeconds": 604800.0}),
        ("softDeletePolicy", {"retentionDurationSeconds": True}),
        ("lifecycle", {"rule": []}),
        (
            "lifecycle",
            {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 1}}]},
        ),
        ("encryption", {"defaultKmsKeyName": KMS}),
        ("storageClass", "UNKNOWN"),
    ],
)
def test_write_posture_rejects_unsafe_settings_but_cleanup_survives(
    setup, field, value
):
    s, http, b = setup
    http.meta["quarantine"][field] = value
    assert_error("storage_unverified", s.verify_readiness)
    s.erase_version(identity(b, "quarantine"))


def test_readiness_valid_and_no_posture_writes(setup):
    s, http, b = setup
    s.verify_readiness()
    for purpose in ("quarantine", "serving"):
        path = f"/storage/v1/b/{getattr(b, f'{purpose}_bucket')}"
        assert any(urlsplit(url).path == path for _, url, _ in http.calls)
        assert any(
            urlsplit(url).path == path + "/o"
            and parse_qs(urlsplit(url).query).get("softDeleted") == ["true"]
            for _, url, _ in http.calls
        )
    assert all(method == "GET" for method, _, _ in http.calls)


@pytest.mark.parametrize("duration", [604800, "7776000"])
def test_enabled_policy_evidence_is_not_a_history_test(setup, duration):
    adapter, http, _ = setup
    policy = http.meta["quarantine"]["softDeletePolicy"]
    policy["retentionDurationSeconds"] = duration
    for present in (True, False):
        if not present:
            del policy["effectiveTime"]
        assert adapter._write_posture("quarantine") == {
            "retention_seconds": int(duration),
            "has_effective_time": present,
        }
        adapter.verify_readiness()
    del http.meta["quarantine"]["softDeletePolicy"]
    assert_error("storage_unverified", adapter.verify_readiness)


def test_expired_posture_never_refreshes(setup, monkeypatch):
    s, http, b = setup
    s._posture = Credentials(
        "synthetic-token",
        expiry=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5),
    )
    monkeypatch.setattr(s._posture, "refresh", lambda *a: pytest.fail("minted token"))
    assert_error("storage_unverified", s.verify_readiness)
    assert http.calls == []
    assert_error("cleanup_unavailable", s.erase_version, identity(b))
    assert "101" in http.objects and http.calls == []


def test_exact_range_inclusive_checked_and_detached(setup):
    s, http, b = setup
    with s.open_range(
        identity(b, key="original/space + é"), start=1, end=3, expected_length=6
    ) as body:
        assert body.read() == b"bcd"
    assert "/original/space%20%2B%20%C3%A9?generation=101" in http.calls[-1][1]
    assert all(response.closed for response in http.responses)


@pytest.mark.parametrize(
    "start,end,length",
    [
        (True, 2, 6),
        (-1, 2, 6),
        (0, 6, 6),
        (3, 2, 6),
        (0, 1, True),
        (0, g.MAX_BYTES, g.MAX_BYTES + 1),
        (None, 2, 6),
    ],
)
def test_bad_range_never_dispatches_data(setup, start, end, length):
    s, http, b = setup
    assert_error(
        "media_unavailable",
        s.open_range,
        identity(b),
        start=start,
        end=end,
        expected_length=length,
    )
    assert not any("/original/" in url for _, url, _ in http.calls)


@pytest.mark.parametrize(
    "header,value",
    [
        ("x-goog-generation", "102"),
        ("content-length", "2"),
        ("content-range", "bytes 1-3/7"),
        ("content-encoding", "gzip"),
        ("x-goog-stored-content-encoding", "gzip"),
    ],
)
def test_changed_range_headers_close_body(setup, header, value):
    s, http, b = setup

    def mutate(r):
        r.headers[header] = value
        return r

    http.mutate_object_response = mutate
    assert_error(
        "media_unavailable",
        s.open_range,
        identity(b),
        start=1,
        end=3,
        expected_length=6,
    )
    assert all(r.closed for r in http.responses)


@pytest.mark.parametrize("payload", [b"bc", b"bcde"])
def test_short_or_excess_range_body_not_exposed(setup, payload):
    s, http, b = setup

    def mutate(r):
        r.body = io.BytesIO(payload)
        return r

    http.mutate_object_response = mutate
    assert_error(
        "media_unavailable",
        s.open_range,
        identity(b),
        start=1,
        end=3,
        expected_length=6,
    )
    assert all(r.closed for r in http.responses)


@pytest.mark.parametrize("failure", ["read", "write"])
def test_snapshot_io_failure_remains_retryable(setup, failure):
    s, http, b = setup

    class Output(io.BytesIO):
        def write(self, data):
            raise OSError("scratch unavailable")

    if failure == "read":

        def fail_get(_response):
            raise OSError("transient object GET failure")

        http.mutate_object_response = fail_get
    assert_error(
        "storage_unverified",
        s.snapshot,
        identity(b),
        io.BytesIO() if failure == "read" else Output(),
        expected_length=6,
    )


def test_snapshot_rehash_and_buffer_bound(setup):
    s, http, b = setup
    data = b"x" * (g.CHUNK + 123)
    http.objects["101"] = data
    out = io.BytesIO()
    assert (
        s.snapshot(identity(b, "quarantine"), out, expected_length=len(data))
        == hashlib.sha256(data).hexdigest()
    )
    assert out.getvalue() == data
    assert max(http.responses[-1].reads) <= g.CHUNK


@pytest.mark.parametrize(
    "version", ["0", "01", "null", "-1", "1.0", "1e2", str(2**64), "a"]
)
def test_invalid_generation_rejected_before_network(setup, version):
    s, http, b = setup
    if version == "null":
        with pytest.raises(ValueError):
            identity(b, version=version)
        return
    assert_error("cleanup_unavailable", s.erase_version, identity(b, version=version))
    assert http.calls == []


def test_foreign_binding_and_dot_paths_never_dispatch(setup):
    s, http, b = setup
    for obj in (
        ObjectIdentity(uuid4(), "serving", "original/test", "101"),
        identity(b, key="original/../test"),
    ):
        assert_error("cleanup_unavailable", s.erase_version, obj)
    assert http.calls == []


def test_create_only_native_generation_and_head_length(setup):
    s, http, b = setup
    value = s.put_quarantine(permit(b), io.BytesIO(b"abc"), 3)
    assert value == identity(b, "quarantine", "quarantine/test", "102")
    request, payload = http.puts[0]
    assert payload == b"abc"
    assert request["headers"]["x-goog-if-generation-match"] == "0"
    assert (
        request["headers"]["x-goog-content-sha256"]
        == hashlib.sha256(b"abc").hexdigest()
    )
    assert "SYNTHETICQ/" in request["headers"]["authorization"]
    assert http.calls[-1][0] == "HEAD"
    assert "generation=102" in http.calls[-1][1]


@pytest.mark.parametrize(
    "length,payload", [(2, b"abc"), (4, b"abc"), (True, b"a"), (g.MAX_BYTES + 1, b"a")]
)
def test_put_length_invalid_never_writes(setup, length, payload):
    s, http, b = setup
    with pytest.raises(AttachmentError):
        s.put_quarantine(permit(b), io.BytesIO(payload), length)
    assert http.puts == []


def test_expired_or_wrong_purpose_permit_never_writes(setup):
    s, http, b = setup
    for p in (
        permit(b, deadline=g._now() - timedelta(seconds=1)),
        permit(b, "serving"),
    ):
        assert_error("storage_unverified", s.put_quarantine, p, io.BytesIO(b"abc"), 3)
    assert http.calls == [] and http.puts == []


def test_create_conflict_not_retried_or_deleted(setup):
    s, http, b = setup
    http.put_status = 412
    assert_error("conflict", s.put_quarantine, permit(b), io.BytesIO(b"abc"), 3)
    assert len(http.puts) == 1
    assert not any(method == "DELETE" for method, _, _ in http.calls)


def test_promote_rehashes_validated_bytes_not_source_copy(setup):
    s, http, b = setup
    snapshot = io.BytesIO(b"abcdef")
    snapshot.seek(5)
    result = s.promote(
        snapshot,
        permit=permit(b, "serving"),
        expected_length=6,
        expected_sha256=hashlib.sha256(b"abcdef").hexdigest(),
    )
    assert result.purpose == "serving"
    assert http.puts[0][1] == b"abcdef"
    assert "SYNTHETICS/" in http.puts[0][0]["headers"]["authorization"]
    assert "copy" not in str(http.puts[0][0]["headers"])


def test_changed_promotion_hash_never_writes(setup):
    s, http, b = setup
    assert_error(
        "snapshot_changed",
        s.promote,
        io.BytesIO(b"abcdef"),
        permit=permit(b, "serving"),
        expected_length=6,
        expected_sha256="0" * 64,
    )
    assert http.puts == []


def test_put_missing_generation_and_mismatched_head_fail(setup):
    s, http, b = setup
    http.mutate_put_response = lambda r: {"status": 200, "headers": {}}
    assert_error(
        "storage_unverified", s.put_quarantine, permit(b), io.BytesIO(b"abc"), 3
    )
    http.mutate_put_response = lambda r: r

    def mutate(r):
        r.headers["content-length"] = "999"
        return r

    http.mutate_object_response = mutate
    assert_error(
        "storage_unverified", s.put_quarantine, permit(b), io.BytesIO(b"abc"), 3
    )
    assert len(http.puts) == 2


def test_kms_key_pinned_and_version_checked():
    b = binding()
    b.serving_encryption_mode, b.serving_encryption_key_identity = "google-kms", KMS
    http = HTTP(b)
    http.meta["serving"]["encryption"] = {"defaultKmsKeyName": KMS}
    s = g.GCSPrivateMediaStorage(
        binding=b,
        config=config(b),
        posture_credentials=Credentials("synthetic-token"),
        http=http,
    )
    result = s.promote(
        io.BytesIO(b"abc"),
        permit=permit(b, "serving"),
        expected_length=3,
        expected_sha256=hashlib.sha256(b"abc").hexdigest(),
    )
    assert result.version == "102"
    assert http.puts[0][0]["headers"]["x-goog-encryption-kms-key-name"] == KMS

    def mutate(r):
        r.headers["x-goog-encryption-kms-key-name"] = KMS + "-other/cryptoKeyVersions/4"
        return r

    http.mutate_object_response = mutate
    assert_error(
        "media_unavailable",
        s.open_range,
        identity(b),
        start=0,
        end=1,
        expected_length=6,
    )


@pytest.mark.parametrize(
    "status,code,success",
    [
        (404, "NoSuchKey", True),
        (404, "NoSuchBucket", False),
        (403, "AccessDenied", False),
        (412, "PreconditionFailed", False),
        (500, "InternalError", False),
    ],
)
def test_exact_delete_absence_vs_ambiguous(setup, status, code, success):
    s, http, b = setup
    http.delete_status, http.delete_error = status, code
    if success:
        del http.objects["101"]  # The exact live generation was already removed.
        s.erase_version(identity(b))
        assert "101" not in http.objects
    else:
        assert_error("cleanup_unavailable", s.erase_version, identity(b))
    assert all(r.closed for r in http.responses)
    (delete,) = [call for call in http.calls if call[0] == "DELETE"]
    assert delete[0] == "DELETE"
    assert "generation=101" in delete[1]
    assert delete[2]["headers"]["x-goog-if-generation-match"] == "101"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "different"),
        ("id", "different"),
        ("projectNumber", "999"),
        ("timeCreated", "2026-01-01T00:00:00Z"),  # Recreated under the same name.
    ],
)
def test_bucket_identity_mismatch_blocks_exact_delete(setup, field, value):
    s, http, b = setup
    http.meta["serving"][field] = value
    assert_error("cleanup_unavailable", s.erase_version, identity(b))
    assert [(m, urlsplit(url).path) for m, url, _ in http.calls] == [
        ("GET", f"/storage/v1/b/{b.serving_bucket}")
    ]
    assert "101" in http.objects


@pytest.mark.parametrize("status", [403, 404, 500])
def test_unavailable_bucket_blocks_cleanup_before_data_requests(setup, status):
    s, http, b = setup
    # Even an otherwise matching metadata body is not proof on non-200.
    http.bucket_status = status
    result = s.erase_all_versions(ObjectLocator(b.id, "serving", "original/test"))
    assert (
        not result.empty and result.retryable and result.reason == "cleanup_unavailable"
    )
    assert [(m, urlsplit(url).path) for m, url, _ in http.calls] == [
        ("GET", f"/storage/v1/b/{b.serving_bucket}")
    ]
    assert "101" in http.objects


def test_native_generation_pagination_exact_key(setup):
    s, http, b = setup
    key = "original/a + é"
    locator = ObjectLocator(b.id, "serving", key)
    http.version_pages = [
        http.version_page(b.serving_bucket, key, [(key, "101")], True, key, "101"),
        http.version_page(
            b.serving_bucket, key, [(key, "102"), (key + "/neighbor", "103")]
        ),
    ]
    first = s.list_versions(locator, limit=1)
    second = s.list_versions(locator, cursor=first.next_cursor)
    assert [x.version for x in first.versions + second.versions] == ["101", "102"]
    assert second.next_cursor is None and second.delete_markers == ()
    query = next(
        parse_qs(urlsplit(url).query)
        for _, url, _ in http.calls
        if "generation-marker=" in url
    )
    assert query["marker"] == [key] and query["generation-marker"] == ["101"]
    assert "version-id-marker" not in query


def test_cursor_cross_key_binding_and_cycle_rejected(setup):
    s, http, b = setup
    loc = ObjectLocator(b.id, "serving", "original/test")
    cursor = json.dumps(
        [str(b.id), "serving", loc.key, loc.key, "101"], separators=(",", ":")
    )
    http.version_pages = [
        http.version_page(b.serving_bucket, loc.key, [], True, loc.key, "101")
    ]
    assert_error("cleanup_unavailable", s.list_versions, loc, cursor=cursor)
    assert_error(
        "cleanup_unavailable",
        s.list_versions,
        replace(loc, key="original/other"),
        cursor=cursor,
    )
    assert_error("cleanup_unavailable", s.list_versions, loc, limit=101)


def test_xml_entities_and_s3_version_fiction_rejected(setup):
    s, http, b = setup
    loc = ObjectLocator(b.id, "serving", "original/test")
    for response in [
        Response(data=b'<!DOCTYPE a [<!ENTITY x "a">]><ListBucketResult/>'),
        xml("ListVersionsResult", "<Version><VersionId>101</VersionId></Version>"),
        xml(
            "ListBucketResult",
            f"<Name>{b.serving_bucket}</Name><EncodingType>url</EncodingType><IsTruncated>false</IsTruncated><Version><Key>original/test</Key><VersionId>101</VersionId></Version>",
        ),
    ]:
        http.version_pages = [response]
        assert_error("cleanup_unavailable", s.list_versions, loc)
        assert response.closed


def test_erase_all_paginates_multipart_preserves_neighbors(setup):
    s, http, b = setup
    key = "original/test"
    http.multipart_pages = [
        http.upload_page(
            b.serving_bucket, [(key, "upload/+1")], True, key, "upload/+1"
        ),
        http.upload_page(
            b.serving_bucket, [(key, "upload2"), (key + "-neighbor", "must-keep")]
        ),
    ]
    result = s.erase_all_versions(ObjectLocator(b.id, "serving", key))
    assert result.empty and not result.retryable
    aborts = [
        (url, parse_qs(urlsplit(url).query))
        for method, url, _ in http.calls
        if method == "DELETE" and "uploadId" in url
    ]
    assert [q["uploadId"][0] for _, q in aborts] == ["upload/+1", "upload2"]
    assert "must-keep" not in str(aborts)
    assert all(r.closed for r in http.responses)


@pytest.mark.parametrize(
    "soft",
    [
        (
            200,
            {
                "kind": "storage#objects",
                "items": [{"name": "original/test", "generation": "101"}],
            },
        ),
        (400, {"error": {"errors": [{"reason": "invalidArgument"}]}}),
        (403, {"error": {}}),
    ],
)
def test_soft_deleted_or_unverifiable_absence_pending(setup, soft):
    s, http, b = setup
    http.soft_pages = [soft]
    result = s.erase_all_versions(ObjectLocator(b.id, "serving", "original/test"))
    assert not result.empty and result.retryable
    assert result.reason == (
        "soft_deleted_pending" if soft[0] == 200 else "cleanup_unavailable"
    )
    # Physical exact cleanup happened even though completion could not be proven.
    assert not http.objects


def test_soft_deleted_json_pagination_filters_neighbors(setup):
    s, http, b = setup
    http.soft_pages = [
        (
            200,
            {
                "kind": "storage#objects",
                "items": [{"name": "original/test-neighbor", "generation": "103"}],
                "nextPageToken": "token/+ 1",
            },
        ),
        (200, {"kind": "storage#objects"}),
    ]
    assert s.erase_all_versions(ObjectLocator(b.id, "serving", "original/test")).empty
    assert any("pageToken=token%2F%2B%201" in url for _, url, _ in http.calls)


def test_bounded_enumeration_never_false_complete(setup, monkeypatch):
    s, http, b = setup
    monkeypatch.setattr(g, "MAX_PAGES", 2)
    key = "original/test"
    http.version_pages = [
        http.version_page(b.serving_bucket, key, [(key, str(i))], True, key, str(i))
        for i in (101, 102)
    ]
    result = s.erase_all_versions(ObjectLocator(b.id, "serving", key))
    assert not result.empty and result.retryable


def test_late_generation_after_delete_keeps_pending(setup):
    s, http, b = setup
    key = "original/test"
    http.version_pages = [
        http.version_page(b.serving_bucket, key, [(key, "101")]),
        http.version_page(b.serving_bucket, key, [(key, "999")]),
    ]
    assert not s.erase_all_versions(ObjectLocator(b.id, "serving", key)).empty


def test_redacted_provider_failure(setup):
    s, http, b = setup
    http.error = "synthetic-secret-token https://private-endpoint/key"
    for code, method, args, kwargs in [
        ("cleanup_unavailable", s.erase_version, (identity(b),), {}),
        (
            "media_unavailable",
            s.open_range,
            (identity(b),),
            {"start": 0, "end": 1, "expected_length": 6},
        ),
        ("storage_unverified", s.verify_readiness, (), {}),
    ]:
        assert_error(code, method, *args, **kwargs)


class Peer(BaseHTTPRequestHandler):
    calls = []
    delay = 0

    def log_message(self, *args):
        pass

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.observed.append((self.path, body, dict(self.headers)))
        if self.server.delay:
            time.sleep(self.server.delay)
        with contextlib_suppress_broken_pipe():
            self.send_response(200)
            self.send_header("x-goog-generation", "123")
            self.send_header("Content-Length", "0")
            self.end_headers()


def contextlib_suppress_broken_pipe():
    from contextlib import suppress

    return suppress(BrokenPipeError, ConnectionResetError)


@pytest.fixture
def peer():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    server.daemon_threads = True
    server.observed, server.delay = [], 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def worker_request(peer, deadline):
    return {
        "url": f"http://127.0.0.1:{peer.server_port}/synthetic/object?generation=1",
        "headers": {"content-length": "3", "x-goog-if-generation-match": "0"},
        "length": 3,
        "deadline": deadline,
        "ca_certs": "",
    }


def test_real_shared_transport_supervised_put_loopback(peer, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://must-not-use.invalid:1")
    request = worker_request(peer, time.time() + 8)
    result = g._bounded_put(request, b"abc")
    assert result == {
        "status": 200,
        "headers": {"x-goog-generation": "123", "content-length": "0"},
    }
    assert len(peer.observed) == 1
    assert peer.observed[0][:2] == ("/synthetic/object?generation=1", b"abc")


def test_put_watchdog_kills_and_reaps_real_socket_writer(peer, monkeypatch):
    peer.delay = 3
    processes = []
    original = subprocess.Popen

    def record(*args, **kwargs):
        p = original(*args, **kwargs)
        processes.append(p)
        return p

    monkeypatch.setattr(g.subprocess, "Popen", record)
    start = time.monotonic()
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        g._bounded_put(worker_request(peer, time.time() + 0.8), b"abc")
    assert time.monotonic() - start < 2
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].returncode != 0
    assert len(peer.observed) == 1


def test_expired_put_worker_never_dispatches(peer):
    with pytest.raises(ValueError):
        g._bounded_put(worker_request(peer, time.time() - 1), b"abc")
    assert peer.observed == []
