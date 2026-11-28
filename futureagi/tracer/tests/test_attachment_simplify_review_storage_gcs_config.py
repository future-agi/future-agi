"""Request-layer regressions; synthetic documented responses, NOT native proof.

Contracts: cloud.google.com/storage/docs/{xml-api/get-bucket-list,
xml-api/get-bucket-uploads,json_api/v1/objects/list,json_api/v1/objects/get,
bucket-metadata,disable-soft-delete}. No policy changes, credentials or cloud IO.
"""

import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4
from xml.sax.saxutils import escape

import pytest
from google.oauth2.credentials import Credentials

from tfc.utils import storage_config as sc
from tracer.models.attachment_storage import (
    AttachmentStorageBinding,
    binding_identity_sha256,
)
from tracer.services.attachments import storage
from tracer.services.attachments import storage_gcs as g
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import ObjectIdentity, ObjectLocator

CREATED = datetime(2025, 1, 2, tzinfo=UTC)
KEY = "original/space + %2F é"


class Response:
    def __init__(self, status=200, body=b""):
        self.status, self.body, self.headers = status, io.BytesIO(body), {}
        self.closed = False

    def read(self, size, *, decode_content):
        assert 0 < size <= g.CHUNK and decode_content is False
        return self.body.read(size)

    def close(self):
        self.closed = True

    def release_conn(self):
        pass


def xml(root, body):
    return Response(
        body=f'<{root} xmlns="http://doc.s3.amazonaws.com/2006-03-01">{body}</{root}>'.encode()
    )


def error(code, reason):
    return {"error": {"code": code, "errors": [{"domain": "global", "reason": reason}]}}


def binding():
    values = {
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
    return AttachmentStorageBinding(
        id=uuid4(), identity_sha256=binding_identity_sha256(values), **values
    )


def config(b):
    return sc.AttachmentStorageConfig(
        sc.StorageConfig(
            "gcs", "storage.googleapis.com", True, b.region, "/synthetic/ca.pem"
        ),
        b.quarantine_bucket,
        b.serving_bucket,
        sc.StorageCredentials("SYNTHETICQ", "synthetic-q"),
        sc.StorageCredentials("SYNTHETICS", "synthetic-s"),
    )


class NativeHTTP:
    """Only transport is simulated; adapter/factory/configuration stay real.

    V1 and multipart omit EncodingType. URL encoding follows the request.
    Disabled soft-delete list returns documented 400, not a fictitious 200.
    Exact softDeleted GET checks retained versions independent of current policy.
    """

    def __init__(self, b):
        self.b, self.calls, self.responses = b, [], []
        self.live = {(KEY, "101"), (KEY, "102"), (KEY + "-neighbor", "103")}
        self.soft, self.uploads = set(), set()
        self.soft_delete_time = datetime.now(UTC)
        self.hard_delete_time = self.soft_delete_time + timedelta(days=7)
        self.version_pages, self.upload_pages, self.soft_pages = [], [], []
        self.list_error = error(400, "invalid")
        self.exact_override = None
        self.retain_deletes = False
        self.after_list = lambda: None
        self.meta = {}
        for purpose in ("quarantine", "serving"):
            bucket = getattr(b, purpose + "_bucket")
            self.meta[purpose] = {
                "kind": "storage#bucket",
                "name": bucket,
                "id": bucket,
                "projectNumber": b.account_identity,
                "timeCreated": CREATED.isoformat(),
                "location": b.region.upper(),
                "metageneration": "1",
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
                            "condition": {
                                "isLive": False,
                                "daysSinceNoncurrentTime": 1,
                            },
                        },
                        {
                            "action": {"type": "AbortIncompleteMultipartUpload"},
                            "condition": {"age": 1},
                        },
                    ]
                },
            }

    def version_page(self, bucket, entries, marker=None):
        body = f"<Name>{bucket}</Name><IsTruncated>{str(marker is not None).lower()}</IsTruncated>"
        body += "".join(
            f"<Version><Key>{quote(k, safe='')}</Key><Generation>{v}</Generation></Version>"
            for k, v in entries
        )
        if marker:
            body += f"<NextMarker>{quote(marker[0], safe='')}</NextMarker><NextGenerationMarker>{marker[1]}</NextGenerationMarker>"
        return xml("ListBucketResult", body)

    def upload_page(self, bucket, entries, marker=None):
        body = f"<Bucket>{bucket}</Bucket><IsTruncated>{str(marker is not None).lower()}</IsTruncated>"
        body += "".join(
            f"<Upload><Key>{quote(k, safe='')}</Key><UploadId>{escape(v)}</UploadId></Upload>"
            for k, v in entries
        )
        if marker:
            body += f"<NextKeyMarker>{quote(marker[0], safe='')}</NextKeyMarker><NextUploadIdMarker>{escape(marker[1])}</NextUploadIdMarker>"
        return xml("ListMultipartUploadsResult", body)

    def urlopen(self, method, url, **kw):
        assert not kw["redirect"] and not kw["retries"] and not kw["preload_content"]
        assert kw["decode_content"] is False
        parsed = urlsplit(url)
        query = {
            k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        self.calls.append((method, parsed.path, query, kw["headers"]))
        purpose = "quarantine" if "synthetic-quarantine" in parsed.path else "serving"
        bucket = getattr(self.b, purpose + "_bucket")
        if parsed.path.startswith("/storage/v1/b/"):
            assert method == "GET"
            assert kw["headers"]["authorization"] == "Bearer synthetic-token"
            if "/o/" in parsed.path:
                assert set(query) == {"softDeleted", "generation"}
                assert query["softDeleted"] == "true"
                key = unquote(parsed.path.split("/o/", 1)[1])
                pair = (key, query["generation"])
                status, data = self.exact_override or (
                    (
                        200,
                        {
                            "kind": "storage#object",
                            "name": key,
                            "bucket": bucket,
                            "generation": pair[1],
                            "softDeleteTime": CREATED.isoformat(),
                        },
                    )
                    if pair in self.soft
                    else (404, error(404, "notFound"))
                )
            elif parsed.path.endswith("/o"):
                assert query["softDeleted"] == "true" and "versions" not in query
                if self.soft_pages:
                    status, data = self.soft_pages.pop(0)
                elif self.meta[purpose]["softDeletePolicy"].get(
                    "retentionDurationSeconds"
                ) in ("0", 0):
                    status, data = 400, self.list_error
                else:
                    status, data = (
                        200,
                        {
                            "kind": "storage#objects",
                            "items": [
                                {
                                    "name": k,
                                    "generation": v,
                                    "softDeleteTime": self.soft_delete_time.isoformat(),
                                    "hardDeleteTime": self.hard_delete_time.isoformat(),
                                }
                                for k, v in sorted(self.soft)
                                if k.startswith(query["prefix"])
                            ],
                        },
                    )
                self.after_list()
            else:
                assert parsed.path == f"/storage/v1/b/{bucket}"
                status, data = 200, deepcopy(self.meta[purpose])
            response = Response(status, json.dumps(data).encode())
        else:
            assert kw["headers"]["authorization"].startswith(
                "GOOG4-HMAC-SHA256 Credential=SYNTHETIC"
            )
            assert parsed.path.split("/")[1] == bucket
            if "versions" in query:
                assert (
                    query.get("list-type", "1") == "1"
                    and query["encoding-type"] == "url"
                )
                assert (
                    "continuation-token" not in query
                    and "version-id-marker" not in query
                )
                response = (
                    self.version_pages.pop(0)
                    if self.version_pages
                    else self.version_page(
                        bucket,
                        sorted(
                            p for p in self.live if p[0].startswith(query["prefix"])
                        ),
                    )
                )
            elif "uploads" in query:
                assert query["encoding-type"] == "url"
                response = (
                    self.upload_pages.pop(0)
                    if self.upload_pages
                    else self.upload_page(bucket, sorted(self.uploads))
                )
            elif "uploadId" in query:
                assert method == "DELETE"
                self.uploads.discard(
                    (unquote(parsed.path.split("/", 2)[2]), query["uploadId"])
                )
                response = Response(204)
            else:
                assert method == "DELETE" and set(query) == {"generation"}
                assert (
                    kw["headers"]["x-goog-if-generation-match"] == query["generation"]
                )
                pair = (unquote(parsed.path.split("/", 2)[2]), query["generation"])
                if pair in self.live:
                    self.live.remove(pair)
                    if self.retain_deletes:
                        self.soft.add(pair)
                    response = Response(204)
                else:
                    response = xml("Error", "<Code>NoSuchKey</Code>")
                    response.status = 404
        self.responses.append(response)
        return response


@pytest.fixture
def native(monkeypatch):
    import google.auth

    monkeypatch.setattr(
        google.auth, "default", lambda *a, **k: pytest.fail("ambient credentials")
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/must-not-read")
    b = binding()
    http = NativeHTTP(b)
    creds = Credentials("synthetic-token")
    monkeypatch.setattr(
        creds, "refresh", lambda *a, **k: pytest.fail("credential refresh")
    )
    adapter = g.GCSPrivateMediaStorage(
        binding=b, config=config(b), posture_credentials=creds, http=http
    )
    return adapter, http, b


def loc(b):
    return ObjectLocator(b.id, "serving", KEY)


def identity(b, generation="101"):
    return ObjectIdentity(b.id, "serving", KEY, generation)


def pending(call, *args, **kwargs):
    with pytest.raises(AttachmentError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == "cleanup_unavailable"
    assert str(caught.value) == "cleanup_unavailable"


def test_v1_empty_without_v2_encoding_field(native):
    adapter, http, b = native
    http.live.clear()
    page = adapter.list_versions(loc(b))
    assert page.versions == () and page.next_cursor is None


def test_v1_encoded_keys_generation_pagination(native):
    adapter, http, b = native
    http.version_pages = [
        http.version_page(b.serving_bucket, [(KEY, "101")], (KEY, "101")),
        http.version_page(b.serving_bucket, [(KEY, "102"), (KEY + "-neighbor", "103")]),
    ]
    first = adapter.list_versions(loc(b), limit=1)
    second = adapter.list_versions(loc(b), cursor=first.next_cursor)
    assert [x.version for x in first.versions + second.versions] == ["101", "102"]
    assert all(x.key == KEY for x in first.versions + second.versions)
    last_xml = [c for c in http.calls if "versions" in c[2]][-1]
    assert last_xml[2]["marker"] == KEY
    assert last_xml[2]["generation-marker"] == "101"
    assert second.next_cursor is None


def test_successful_list_completes_all_and_multipart(native):
    adapter, http, b = native
    http.upload_pages = [
        http.upload_page(b.serving_bucket, [(KEY, "a/+1")], (KEY, "a/+1")),
        http.upload_page(b.serving_bucket, [(KEY, "b2"), (KEY + "-neighbor", "keep")]),
    ]
    result = adapter.erase_all_versions(loc(b))
    assert result.empty and not result.retryable
    assert http.live == {(KEY + "-neighbor", "103")}
    assert [
        q["uploadId"] for m, _, q, _ in http.calls if m == "DELETE" and "uploadId" in q
    ] == ["a/+1", "b2"]
    assert all(r.closed for r in http.responses)


@pytest.mark.parametrize("revision", [None, "1", "2", "999", 1, True, "01"])
def test_disabled_unknown_history_never_complete(native, revision):
    adapter, http, b = native
    http.meta["serving"]["softDeletePolicy"] = {"retentionDurationSeconds": "0"}
    http.meta["serving"]["metageneration"] = revision
    http.soft.add((KEY, "7"))
    result = adapter.erase_all_versions(loc(b))
    assert not result.empty and result.retryable
    assert (KEY, "7") in http.soft and not any(k == KEY for k, _ in http.live)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        error(400, "badRequest"),
        error(400, "invalid"),
        error(400, "invalidArgument"),
        error(403, "invalidArgument"),
        {"error": {"code": 400, "errors": []}},
        {
            "error": {
                "code": 400,
                "errors": [{"reason": "invalidArgument"}, {"reason": "forbidden"}],
            }
        },
    ],
)
def test_arbitrary_400_not_absence(native, payload):
    adapter, http, b = native
    http.meta["serving"]["softDeletePolicy"] = {"retentionDurationSeconds": "0"}
    http.list_error = payload
    assert not adapter.erase_all_versions(loc(b)).empty


def test_policy_revision_race_never_complete(native):
    adapter, http, b = native
    http.meta["serving"]["softDeletePolicy"] = {"retentionDurationSeconds": "0"}
    http.after_list = lambda: http.meta["serving"].update(metageneration="3")
    assert not adapter.erase_all_versions(loc(b)).empty


def test_no_policy_field_is_not_never_enabled_evidence(native):
    adapter, http, b = native
    http.meta["serving"].pop("softDeletePolicy")
    http.soft_pages = [(400, error(400, "invalidArgument"))]
    assert not adapter.erase_all_versions(loc(b)).empty


def test_enabled_admission_refuses_unavailable_probe(native):
    adapter, http, _ = native
    http.soft_pages = [(400, error(400, "invalid"))]
    with pytest.raises(AttachmentError, match="storage_unverified"):
        adapter.verify_readiness()
    assert all(method == "GET" for method, _, _, _ in http.calls)


def test_400_mid_pagination_never_complete(native):
    adapter, http, b = native
    http.soft_pages = [
        (200, {"kind": "storage#objects", "nextPageToken": "next/+="}),
        (400, error(400, "invalidArgument")),
    ]
    assert not adapter.erase_all_versions(loc(b)).empty


def test_soft_listing_pages_preserve_neighbor_and_check_final_page(native):
    adapter, http, b = native
    http.soft_pages = [
        (
            200,
            {
                "kind": "storage#objects",
                "items": [{"name": KEY + "-neighbor", "generation": "90"}],
                "nextPageToken": "next/+=",
            },
        ),
        (
            200,
            {"kind": "storage#objects", "items": [{"name": KEY, "generation": "91"}]},
        ),
    ]
    result = adapter.erase_all_versions(loc(b))
    assert not result.empty and result.reason == "soft_deleted_pending"
    assert http.calls[-1][2]["pageToken"] == "next/+="


def test_exact_delete_retained_pending_on_first_call_and_retry(native):
    adapter, http, b = native
    http.retain_deletes = True
    http.meta["serving"].update(
        metageneration="2", softDeletePolicy={"retentionDurationSeconds": "604800"}
    )
    pending(adapter.erase_version, identity(b))
    assert (KEY, "101") not in http.live and (KEY, "101") in http.soft
    assert (
        KEY,
        "102",
    ) in http.live  # Published generation is never collateral cleanup.
    # Disabling policy later does NOT erase historical retained generations.
    http.meta["serving"].update(
        metageneration="3", softDeletePolicy={"retentionDurationSeconds": "0"}
    )
    pending(adapter.erase_version, identity(b))
    http.soft.remove((KEY, "101"))
    pending(adapter.erase_version, identity(b))  # Disabled list still fails closed.
    http.meta["serving"]["softDeletePolicy"] = {"retentionDurationSeconds": "604800"}
    assert adapter.erase_version(identity(b)) is None
    assert (KEY, "102") in http.live


def test_exact_unknown_history_can_complete_with_native_get_notfound(native):
    adapter, http, b = native
    http.meta["serving"]["metageneration"] = "99"
    http.soft.add((KEY, "9"))
    assert adapter.erase_version(identity(b)) is None
    assert (KEY, "9") in http.soft and (KEY, "102") in http.live
    get = next(c for c in http.calls if "/o/" in c[1])
    assert get[2] == {"softDeleted": "true", "generation": "101"}
    assert unquote(get[1].split("/o/")[1]) == KEY


@pytest.mark.parametrize(
    "status,data",
    [
        (400, error(400, "invalidArgument")),
        (403, error(403, "forbidden")),
        (404, {}),
        (404, error(404, "other")),
        (500, error(500, "internalError")),
    ],
)
def test_exact_ambiguous_proof_never_success(native, status, data):
    adapter, http, b = native
    http.exact_override = (status, data)
    pending(adapter.erase_version, identity(b))
    assert (KEY, "101") not in http.live


def test_without_posture_refuses_hmac_delete(native):
    adapter, http, b = native
    adapter._posture = None
    pending(adapter.erase_version, identity(b))
    assert (KEY, "101") in http.live and (KEY, "102") in http.live
    assert http.calls == []


def test_incarnation_mismatch_blocks_delete(native):
    adapter, http, b = native
    http.meta["serving"]["timeCreated"] = "2026-01-01T00:00:00+00:00"
    pending(adapter.erase_version, identity(b))
    assert not any(m == "DELETE" for m, _, _, _ in http.calls)


def test_admission_refuses_unprovable_cleanup_history(native):
    adapter, http, b = native
    http.meta["serving"]["metageneration"] = "9"
    http.meta["serving"]["softDeletePolicy"] = {"retentionDurationSeconds": "0"}
    with pytest.raises(AttachmentError, match="storage_unverified"):
        adapter.verify_readiness()


def test_admission_enabled_policy_has_real_non_stub_path(native):
    adapter, http, b = native
    adapter.verify_readiness()
    assert all(method == "GET" for method, _, _, _ in http.calls)


def install_settings(monkeypatch, b, posture):
    env = {
        "STORAGE_BACKEND": "gcs",
        "MINIO_REGION": b.region,
        "FI_ATTACHMENTS_QUARANTINE_BUCKET": b.quarantine_bucket,
        "FI_ATTACHMENTS_SERVING_BUCKET": b.serving_bucket,
        "FI_ATTACHMENTS_QUARANTINE_ACCESS_KEY": "SYNTHETICQ",
        "FI_ATTACHMENTS_QUARANTINE_SECRET_KEY": "synthetic-q",
        "FI_ATTACHMENTS_SERVING_ACCESS_KEY": "SYNTHETICS",
        "FI_ATTACHMENTS_SERVING_SECRET_KEY": "synthetic-s",
        "FI_ATTACHMENTS_POSTURE_CREDENTIAL_REF": "gcs.readonly-v1",
        "ATT_Q_ID": "SYNTHETICQ",
        "ATT_Q_SECRET": "synthetic-q",
        "ATT_S_ID": "SYNTHETICS",
        "ATT_S_SECRET": "synthetic-s",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    settings = SimpleNamespace(
        FI_ATTACHMENTS_ENABLED=True,
        FI_ATTACHMENTS_STORAGE=sc.attachment_storage_settings(
            enabled=True, environ=env
        ),
        FI_ATTACHMENTS_POSTURE_CREDENTIALS=posture,
        FI_ATTACHMENTS_BINDING_ACCESS={
            b.identity_sha256: {
                "quarantine": {
                    "access_key_ref": "ATT_Q_ID",
                    "secret_key_ref": "ATT_Q_SECRET",
                },
                "serving": {
                    "access_key_ref": "ATT_S_ID",
                    "secret_key_ref": "ATT_S_SECRET",
                },
                "posture_credential_ref": "gcs.readonly-v1",
            }
        },
    )
    monkeypatch.setattr(AttachmentStorageBinding.objects, "get", lambda **kw: b)

    def create(**kw):
        assert kw["identity_sha256"] == b.identity_sha256
        assert binding_identity_sha256(kw["defaults"]) == b.identity_sha256
        return b, False

    monkeypatch.setattr(AttachmentStorageBinding.objects, "get_or_create", create)
    return settings


def test_real_factory_discovery_via_standard_default_posture_bridge(
    native, monkeypatch
):
    adapter, http, b = native
    settings = install_settings(monkeypatch, b, {"gcs.readonly-v1": adapter._posture})
    monkeypatch.setattr(g, "storage_http_client", lambda **kw: http)
    assert (
        settings.FI_ATTACHMENTS_STORAGE["posture_credential_ref"] == "gcs.readonly-v1"
    )
    assert (
        settings.FI_ATTACHMENTS_STORAGE["resolved"].storage.endpoint_url
        == "https://storage.googleapis.com"
    )
    assert storage._provider("gcs") is g.GCSPrivateMediaStorage
    assert storage.bind_for_admission(settings=settings) == b.id
    assert len(http.calls) > 4 and all(m == "GET" for m, _, _, _ in http.calls)


@pytest.mark.parametrize(
    "historical",
    [{}, {"other.readonly": object()}, {"other.readonly": Credentials(None)}],
    ids=["absent", "wrong-type", "typed-without-valid-token"],
)
def test_admission_checks_named_historical_posture(native, monkeypatch, historical):
    adapter, http, b = native
    settings = install_settings(
        monkeypatch, b, {"gcs.readonly-v1": adapter._posture, **historical}
    )
    settings.FI_ATTACHMENTS_BINDING_ACCESS[b.identity_sha256][
        "posture_credential_ref"
    ] = "other.readonly"
    monkeypatch.setattr(g, "storage_http_client", lambda **kw: http)
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage.bind_for_admission(settings=settings)


def test_factory_erase_reads_only_bound_buckets_with_historical_posture(
    native, monkeypatch
):
    adapter, http, b = native
    settings = install_settings(monkeypatch, b, {"retired.readonly": adapter._posture})
    settings.FI_ATTACHMENTS_BINDING_ACCESS[b.identity_sha256][
        "posture_credential_ref"
    ] = "retired.readonly"
    settings.FI_ATTACHMENTS_STORAGE = {"backend": "broken-default-b"}
    monkeypatch.setenv("STORAGE_BACKEND", "broken-default-b")
    monkeypatch.setattr(g, "storage_http_client", lambda **kw: http)
    resolved = storage.for_binding(b.id, operation="erase", settings=settings)
    assert type(resolved) is g.GCSPrivateMediaStorage
    assert [(m, path, query) for m, path, query, _ in http.calls] == [
        ("GET", f"/storage/v1/b/{b.quarantine_bucket}", {}),
        ("GET", f"/storage/v1/b/{b.serving_bucket}", {}),
    ]
    resolved.erase_version(identity(b))
    assert (KEY, "101") not in http.live and (KEY, "102") in http.live
    assert resolved.erase_all_versions(loc(b)).empty


@pytest.mark.parametrize("operation", ["admit", "write", "read"])
def test_missing_posture_prevents_admission_write_and_read(
    native, monkeypatch, operation
):
    _, http, b = native
    settings = install_settings(monkeypatch, b, {})
    monkeypatch.setattr(g, "storage_http_client", lambda **kw: http)
    with pytest.raises(AttachmentError, match="storage_unverified"):
        storage.for_binding(b.id, operation=operation, settings=settings)
    assert not http.calls


@pytest.mark.parametrize(
    "reference,supplied",
    [
        (None, {}),
        ("retired.readonly", {}),
        ("retired.readonly", {"retired.readonly": "not-a-runtime-object"}),
        ("retired.readonly", {"retired.readonly": Credentials(None)}),
    ],
    ids=["no-ref", "missing", "wrong-type", "invalid-token"],
)
def test_factory_refuses_erase_without_historical_posture(
    native, monkeypatch, reference, supplied
):
    adapter, http, b = native
    # Valid current posture must never substitute for historical credentials.
    settings = install_settings(
        monkeypatch, b, {"gcs.readonly-v1": adapter._posture, **supplied}
    )
    settings.FI_ATTACHMENTS_BINDING_ACCESS[b.identity_sha256][
        "posture_credential_ref"
    ] = reference
    monkeypatch.setattr(g, "storage_http_client", lambda **kw: http)
    pending(storage.for_binding, b.id, operation="erase", settings=settings)
    assert not http.calls


@pytest.mark.parametrize(
    "reference", ["/path/to/key", "Bearer token", "bad\nname", "", "a" * 256, {}, 1]
)
def test_default_posture_reference_validation_matches_historical(native, reference):
    _, _, b = native
    raw = {
        "backend": "gcs",
        "region": b.region,
        "quarantine_bucket": b.quarantine_bucket,
        "serving_bucket": b.serving_bucket,
        "quarantine_access_key": "SYNTHETICQ",
        "quarantine_secret_key": "synthetic-q",
        "serving_access_key": "SYNTHETICS",
        "serving_secret_key": "synthetic-s",
        "posture_credential_ref": reference,
    }
    with pytest.raises(sc.StorageConfigurationError, match="posture_reference_invalid"):
        sc.resolve_attachment_storage_config(raw, environ={"STORAGE_BACKEND": "gcs"})


def test_disabled_standard_bridge_keeps_invalid_reference_lazy():
    result = sc.attachment_storage_settings(
        enabled=False,
        environ={
            "FI_ATTACHMENTS_POSTURE_CREDENTIAL_REF": "/not-read",
            "S3_ENDPOINT": "malformed[",
        },
    )
    assert result["resolved"] is None


def test_orphan_cleanup_retry_cannot_release_after_xml_hides_retained_bytes(native):
    adapter, http, b = native
    http.retain_deletes = True
    http.meta["serving"].update(
        metageneration="2", softDeletePolicy={"retentionDurationSeconds": "604800"}
    )
    # Same enumeration/exact-delete boundary as READY derivative/import cleanup.
    # This is not a database/capacity integration test.
    released = []

    def settle():
        cursor = None
        while True:
            page = adapter.list_versions(loc(b), cursor=cursor)
            for item in page.versions:
                if item.version != "102":  # Preserve referenced publication.
                    adapter.erase_version(item)
            cursor = page.next_cursor
            if cursor is None:
                break
        released.append(True)

    pending(settle)
    assert (KEY, "101") in http.soft
    pending(settle)  # XML no longer shows 101; proof must still block settlement.
    assert released == [] and (KEY, "102") in http.live
    http.meta["serving"].update(
        metageneration="3", softDeletePolicy={"retentionDurationSeconds": "0"}
    )
    pending(settle)  # Disabled-now 400 cannot hide retained historical bytes.
    assert released == [] and (KEY, "102") in http.live
    # Once enumeration is available and confirms absence, settlement can finish.
    http.meta["serving"].update(
        metageneration="4", softDeletePolicy={"retentionDurationSeconds": "604800"}
    )
    http.soft.clear()
    settle()
    assert released == [True] and (KEY, "102") in http.live


def test_full_cleanup_without_posture_preserves_live_versions_and_keeps_pending(native):
    adapter, http, b = native
    adapter._posture = None
    result = adapter.erase_all_versions(loc(b))
    assert not result.empty and result.retryable
    assert result.reason == "cleanup_unavailable" and http.calls == []
    assert http.live == {(KEY, "101"), (KEY, "102"), (KEY + "-neighbor", "103")}


def test_unknown_history_terminal_page_never_certifies_orphan_settlement(native):
    adapter, http, b = native
    http.live = {(KEY, "102")}
    http.meta["serving"]["metageneration"] = "9"
    http.meta["serving"]["softDeletePolicy"] = {"retentionDurationSeconds": "0"}
    pending(adapter.list_versions, loc(b))
    assert http.live == {(KEY, "102")}
