"""Opt-in, native attachment storage qualification on disposable GCS buckets.

Use the normal attachment settings and dedicated purpose-specific HMAC references.
Supply ATTQUAL_PROJECT_ID, ATTQUAL_PROJECT_NUMBER, ATTQUAL_{QUARANTINE,SERVING}_CREATED_AT
from enrollment, ATTQUAL_POSTURE_TOKEN and ATTQUAL_POSTURE_EXPIRES from the read-only
identity, and a private ATTQUAL_MANIFEST_DIR for recovery after runner loss. Enable
the feature with a real shared Redis cache and a disposable migrated PostgreSQL DB. No ADC,
credential minting, bucket mutations, or test doubles on the positive path.
"""

import hashlib
import json
import os
import time
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from uuid import uuid4

import pytest
from google.oauth2.credentials import Credentials

from tfc.utils.storage_config import attachment_binding_access_settings
from tracer.models.attachment_storage import (
    AttachmentStorageBinding,
    binding_identity_sha256,
)
from tracer.services.attachments import storage_gcs as gcs
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    ObjectLocator,
    PutPermit,
    StoragePurpose,
)
from tracer.services.attachments.storage import bind_for_admission, for_binding

_PURPOSES: tuple[StoragePurpose, ...] = ("quarantine", "serving")
_CREDENTIAL_REFS = {
    "quarantine": {
        "access_key_ref": "ATTQ_GCS_ID",
        "secret_key_ref": "ATTQ_GCS_SECRET",
    },
    "serving": {"access_key_ref": "ATTS_GCS_ID", "secret_key_ref": "ATTS_GCS_SECRET"},
}
_REFS = {**_CREDENTIAL_REFS, "posture_credential_ref": "gcs.qual.ro"}
_REQUIRED = (
    "ATTQUAL_PROJECT_ID",
    "ATTQUAL_PROJECT_NUMBER",
    "ATTQUAL_POSTURE_TOKEN",
    "ATTQUAL_POSTURE_EXPIRES",
    "ATTQUAL_MANIFEST_DIR",
    "FI_ATTACHMENTS_ENABLED",
    "STORAGE_BACKEND",
    "FI_ATTACHMENTS_STORAGE_REGION",
    "FI_ATTACHMENTS_POSTURE_CREDENTIAL_REF",
    *(f"ATTQUAL_{p.upper()}_CREATED_AT" for p in _PURPOSES),
    *(
        f"FI_ATTACHMENTS_{p.upper()}_{k}"
        for p in _PURPOSES
        for k in ("BUCKET", "ACCESS_KEY_ENV", "SECRET_KEY_ENV")
    ),
    *(name for p in _PURPOSES for name in _CREDENTIAL_REFS[p].values()),
)
pytestmark = [
    pytest.mark.external,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.getenv("FI_ATTACHMENTS_LIVE_GCS") != "1"
        or not all(os.getenv(k) for k in _REQUIRED),
        reason="requires explicitly configured live GCS qualification",
    ),
]


@pytest.fixture
def enrollment(settings, monkeypatch):
    """Pin discovery to the two enrolled incarnations before installing references."""
    assert os.environ["STORAGE_BACKEND"] == "gcs" and settings.FI_ATTACHMENTS_ENABLED
    assert (
        os.environ["FI_ATTACHMENTS_POSTURE_CREDENTIAL_REF"]
        == _REFS["posture_credential_ref"]
    )
    project, number = (
        os.environ["ATTQUAL_PROJECT_ID"],
        os.environ["ATTQUAL_PROJECT_NUMBER"],
    )
    expected: dict[str, object] = {
        "provider": "gcs",
        "endpoint": "https://storage.googleapis.com",
        "region": "us-central1",
        "account_identity": number,
    }
    for purpose in _PURPOSES:
        prefix = f"FI_ATTACHMENTS_{purpose.upper()}_"
        bucket = f"{project}-attqual-{purpose[0]}-{number}"
        assert os.environ[prefix + "BUCKET"] == bucket
        for key in ("access_key", "secret_key"):
            # Direct secret values must not select or override one of the two purposes.
            if (
                os.getenv(prefix + key.upper())
                or os.environ[prefix + key.upper() + "_ENV"]
                != _CREDENTIAL_REFS[purpose][key + "_ref"]
            ):
                pytest.fail(
                    "expected dedicated qualification credential reference",
                    pytrace=False,
                )
        expected.update(
            {
                f"{purpose}_bucket": bucket,
                f"{purpose}_resource_identity": f"//storage.googleapis.com/projects/_/buckets/{bucket}",
                f"{purpose}_owner_identity": number,
                f"{purpose}_created_at": datetime.fromisoformat(
                    os.environ[f"ATTQUAL_{purpose.upper()}_CREATED_AT"]
                ),
                f"{purpose}_encryption_mode": "google-managed",
                f"{purpose}_encryption_key_identity": "",
            }
        )
    expiry = datetime.fromisoformat(os.environ["ATTQUAL_POSTURE_EXPIRES"])
    if expiry.utcoffset() is None or expiry < datetime.now(UTC) + timedelta(minutes=10):
        pytest.fail(
            "posture credential needs at least ten minutes for qualification and cleanup",
            pytrace=False,
        )
    cred = Credentials(
        token=os.environ["ATTQUAL_POSTURE_TOKEN"],
        expiry=expiry.astimezone(UTC).replace(tzinfo=None),
    )
    settings.FI_ATTACHMENTS_POSTURE_CREDENTIALS = {"gcs.qual.ro": cred}
    observed = gcs.GCSPrivateMediaStorage.discover_identity(
        config=settings.FI_ATTACHMENTS_STORAGE["resolved"],
        posture_credentials=cred,
    )
    assert observed == expected
    fingerprint = binding_identity_sha256(observed)
    monkeypatch.setenv(
        "FI_ATTACHMENTS_BINDING_ACCESS", json.dumps({fingerprint: _REFS})
    )
    settings.FI_ATTACHMENTS_BINDING_ACCESS = attachment_binding_access_settings()
    return fingerprint


def _require_empty(erase, locator):
    result = erase.erase_all_versions(locator)
    assert (result.empty, result.retryable, result.reason) == (True, False, None)


def _retained(erase, locator):
    # Independent JSON evidence, not the adapter's boolean absence result.
    copies = {}
    tokens: set[str] = set()
    query = {"prefix": locator.key, "maxResults": "1", "softDeleted": "true"}
    while True:
        status, page = erase._json_get(locator.purpose, "/o", query)
        assert status == 200 and page["kind"] == "storage#objects"
        for item in page.get("items", []):
            assert item["name"].startswith(locator.key)
            if item["name"] != locator.key:
                continue
            version = item["generation"]
            assert version not in copies
            hard_delete = datetime.fromisoformat(item["hardDeleteTime"])
            assert (
                datetime.fromisoformat(item["softDeleteTime"])
                <= datetime.now(UTC)
                < hard_delete
            )
            copies[version] = item["hardDeleteTime"]
        if "nextPageToken" not in page:
            return copies
        token = page["nextPageToken"]
        assert token and token not in tokens and len(tokens) < 10
        tokens.add(token)
        query["pageToken"] = token


def _versions(erase, locator, *, limit=100, live_only=False):
    versions, cursors, cursor = [], set(), None
    # Every exact test key has at most two versions and one neighbor.
    for _ in range(10):
        list_page = erase._list_live_versions if live_only else erase.list_versions
        page = list_page(locator, cursor=cursor, limit=limit)
        assert not page.delete_markers and len(page.versions) <= limit
        for identity in page.versions:
            _check_identity(locator, identity)
            assert identity not in versions
            versions.append(identity)
        cursor = page.next_cursor
        if cursor is None:
            return versions
        assert cursor not in cursors
        cursors.add(cursor)
    pytest.fail("version enumeration failed to terminate")


def _check_identity(locator, identity):
    assert ObjectLocator(identity.binding_id, identity.purpose, identity.key) == locator
    assert identity.version.isascii() and identity.version.isdecimal()
    assert 0 < int(identity.version) < 2**64 and identity.version == str(
        int(identity.version)
    )


class _Run:
    def __init__(self, binding_id, prefix, manifest, cleanup):
        self.binding_id = binding_id
        self.prefix = prefix
        self.manifest = manifest
        self.cleanup = cleanup
        self.expected: dict[ObjectLocator, set[str]] = {}
        self.horizon = 0.0
        write = for_binding(binding_id, operation="write")
        assert isinstance(write, gcs.GCSPrivateMediaStorage)
        self.write = write
        self.read = for_binding(binding_id, operation="read")
        self.erase = for_binding(binding_id, operation="erase")

    def locator(self, purpose, name):
        locator = ObjectLocator(self.binding_id, purpose, self.prefix + name)
        # Register all exact keys before dispatch, including failed and ambiguous PUTs.
        self.record(purpose=purpose, key=locator.key)
        self.expected[locator] = set()
        self.cleanup.callback(self.cleanup_key, locator)
        return locator

    def remember(self, locator, identity):
        _check_identity(locator, identity)
        self.expected[locator].add(identity.version)
        self.record(
            purpose=locator.purpose, key=locator.key, generation=identity.version
        )
        return identity

    def cleanup_key(self, locator):
        result = self.erase.erase_all_versions(locator)
        copies = _retained(self.erase, locator)
        # Record actuals even on a failed run (e.g. a write with a lost reply).
        self.record(purpose=locator.purpose, key=locator.key, retained=copies)
        assert set(copies) == self.expected[locator]
        assert _versions(self.erase, locator, live_only=True) == []
        expected = (
            (False, True, "soft_deleted_pending") if copies else (True, False, None)
        )
        assert (result.empty, result.retryable, result.reason) == expected

    def record(self, **fields):
        self.manifest.write(
            json.dumps({"binding": str(self.binding_id), **fields}) + "\n"
        )
        self.manifest.flush()
        os.fsync(self.manifest.fileno())

    def permit(self, locator):
        deadline = datetime.now(UTC) + timedelta(seconds=30)
        self.record(
            key=locator.key, purpose=locator.purpose, deadline=deadline.isoformat()
        )
        self.horizon = max(self.horizon, deadline.timestamp())
        return PutPermit("upload", uuid4(), locator, None, 1, deadline)

    def put(self, locator, data):
        permit = self.permit(locator)
        if locator.purpose == "quarantine":
            identity = self.write.put_quarantine(permit, BytesIO(data), len(data))
        else:
            identity = self.write.promote(
                BytesIO(data),
                permit=permit,
                expected_length=len(data),
                expected_sha256=_digest(data),
            )
        return self.remember(locator, identity)

    def seed_successor(self, locator, old, data):
        """Fixture-only conditional replacement; the production writer stays create-only."""
        _check_identity(locator, old)
        permit = self.permit(locator)
        config = self.write.config
        bucket = getattr(config, locator.purpose + "_bucket")
        path = f"/{bucket}/{quote(locator.key, safe='/~')}"
        headers = gcs._signed_headers(
            "PUT",
            path,
            "",
            {
                "host": "storage.googleapis.com",
                "content-type": "application/octet-stream",
                "content-length": str(len(data)),
                "x-goog-if-generation-match": old.version,
            },
            getattr(config, locator.purpose + "_credentials"),
            region=config.storage.region,
            now=datetime.now(UTC),
            digest=_digest(data),
        )
        # Same bounded, non-retrying GCS transport. Never put a signed request in an assertion.
        response = gcs._bounded_put(
            {
                "url": "https://storage.googleapis.com" + path,
                "headers": headers,
                "length": len(data),
                "ca_certs": config.storage.ca_certs,
                "deadline": permit.deadline_at.timestamp(),
            },
            data,
        )
        assert response["status"] == 200
        successor = replace(old, version=response["headers"]["x-goog-generation"])
        _check_identity(locator, successor)
        assert successor != old
        return self.remember(locator, successor)


@pytest.fixture(scope="session")
def run_id():
    return uuid4().hex


@pytest.fixture
def live(enrollment, settings, run_id):
    manifest_dir = Path(os.environ["ATTQUAL_MANIFEST_DIR"])
    assert (
        manifest_dir.is_absolute()
        and manifest_dir.is_dir()
        and not manifest_dir.stat().st_mode & 0o077
    )
    binding_id = bind_for_admission(settings=settings)
    assert (
        AttachmentStorageBinding.objects.get(pk=binding_id).identity_sha256
        == enrollment
    )
    prefix = f"qual/{run_id}/{uuid4().hex}/"
    with NamedTemporaryFile(
        mode="w", prefix="gcs-", suffix=".jsonl", dir=manifest_dir, delete=False
    ) as manifest:
        with ExitStack() as cleanup:
            run = _Run(binding_id, prefix, manifest, cleanup)
            # Preflight both identities before ANY data write. Do not issue a delete if
            # an allegedly fresh canary turns out to contain a generation.
            for purpose in _PURPOSES:
                run.record(purpose=purpose, posture=run.write._write_posture(purpose))
                probe = ObjectLocator(binding_id, purpose, prefix + "empty-canary")
                assert _versions(run.erase, probe) == []
                _require_empty(run.erase, probe)
            try:
                yield run
            finally:
                # An ambiguous PUT may have outlived its response. No retry and no
                # cleanup until all registered writer deadlines have passed.
                time.sleep(max(0, run.horizon - time.time()))
        run.record(
            cleanup="verified",
            settlement="pending" if any(run.expected.values()) else "empty",
        )  # Not written if ANY cleanup callback failed.


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _snapshot(run, identity, data):
    output = BytesIO()
    digest = run.read.snapshot(identity, output, expected_length=len(data))
    assert output.getvalue() == data and digest == _digest(data)
    return output, digest


def test_bind_for_admission_and_cleanup_preflight(live, settings):
    assert bind_for_admission(settings=settings) == live.binding_id
    assert all(
        type(adapter) is gcs.GCSPrivateMediaStorage
        for adapter in (live.read, live.write, live.erase)
    )


def test_quarantine_snapshot_promote_range(live):
    data = b"\x00qualified\xffbytes"
    quarantine = live.locator("quarantine", "source")
    serving = live.locator("serving", "destination")
    rejected = live.locator("serving", "bad-digest")
    source = live.put(quarantine, data)
    snapshot, digest = _snapshot(live, source, data)
    with pytest.raises(AttachmentError) as error:
        live.write.promote(
            snapshot,
            permit=live.permit(rejected),
            expected_length=len(data),
            expected_sha256=_digest(b"different"),
        )
    assert (error.value.code, error.value.status) == ("snapshot_changed", 409)
    assert _versions(live.erase, rejected) == []
    dest = live.write.promote(
        snapshot,
        permit=live.permit(serving),
        expected_length=len(data),
        expected_sha256=digest,
    )
    live.remember(serving, dest)
    _snapshot(live, dest, data)
    with live.read.open_range(
        dest, start=2, end=6, expected_length=len(data)
    ) as stream:
        assert stream.read() == data[2:7]


def test_generation_pinning_and_create_only(live):
    locator = live.locator("quarantine", "pinned")
    old = live.put(locator, b"old")
    with pytest.raises(AttachmentError) as error:
        live.put(locator, b"new")
    assert (error.value.code, error.value.status) == ("conflict", 409)
    assert _versions(live.erase, locator) == [old]
    _snapshot(live, old, b"old")
    current = live.seed_successor(locator, old, b"new")
    _snapshot(live, old, b"old")
    _snapshot(live, current, b"new")
    assert set(_versions(live.erase, locator, limit=1)) == {old, current}
    missing = next(
        str(n) for n in (1, 2, 3) if str(n) not in {old.version, current.version}
    )
    with pytest.raises(AttachmentError):
        live.read.snapshot(replace(old, version=missing), BytesIO(), expected_length=3)


def test_retained_cleanup_on_both_buckets(live):
    # Qualify settlement independently of the snapshot/read path. A failure in
    # playback must not conceal the provider's physical cleanup disposition.
    for purpose in _PURPOSES:
        locator = live.locator(purpose, "retained")
        old = live.put(locator, b"old")
        current = live.seed_successor(locator, old, b"new")
        assert set(_versions(live.erase, locator, limit=1)) == {old, current}
        for _ in range(2):
            live.cleanup_key(locator)
        assert set(_retained(live.erase, locator)) == {old.version, current.version}


def test_erase_exact_then_all_with_retained_generations(live):
    for purpose in _PURPOSES:
        locator = live.locator(purpose, "erase")
        neighbor = live.locator(purpose, "erase-neighbor")
        old = live.put(locator, b"old")
        current = live.seed_successor(locator, old, b"new")
        sentinel = live.put(neighbor, b"keep")
        with pytest.raises(AttachmentError) as exc:
            live.erase.erase_version(old)
        assert exc.value.code == "cleanup_unavailable"
        assert set(_retained(live.erase, locator)) == {old.version}
        with pytest.raises(AttachmentError) as exc:
            _versions(live.erase, locator)
        assert exc.value.code == "cleanup_unavailable"
        assert _versions(live.erase, locator, live_only=True) == [current]
        with pytest.raises(AttachmentError):
            live.read.snapshot(old, BytesIO(), expected_length=3)
        _snapshot(live, current, b"new")
        _snapshot(live, sentinel, b"keep")
        for _ in range(2):
            live.cleanup_key(locator)
        _snapshot(live, sentinel, b"keep")


@pytest.mark.parametrize("drift", ["pap_inherited", "cors_nonempty"])
def test_refuses_drifted_posture(enrollment, settings, monkeypatch, drift):
    # Wrap only the first successful quarantine metadata response AFTER discovery.
    # Count subsequent calls: an unrelated later readiness failure must not pass.
    original = gcs.GCSPrivateMediaStorage._json_get
    calls: list[tuple[str, str, int]] = []

    def metadata(adapter, purpose, suffix="", query=None):
        status, data = original(adapter, purpose, suffix, query)
        if not calls and (purpose, suffix, status) == ("quarantine", "", 200):
            data = deepcopy(data)
            if drift == "pap_inherited":
                assert data["iamConfiguration"]["publicAccessPrevention"] == "enforced"
                data["iamConfiguration"]["publicAccessPrevention"] = "inherited"
            else:
                assert not data.get("cors")
                data["cors"] = [
                    {"origin": ["https://example.invalid"], "method": ["GET"]}
                ]
        # Append only AFTER a successful observation and mutation. The factory
        # converts exceptions from this wrapper into the same 503 we expect.
        calls.append((purpose, suffix, status))
        return status, data

    monkeypatch.setattr(gcs.GCSPrivateMediaStorage, "_json_get", metadata)
    with pytest.raises(AttachmentError) as error:
        bind_for_admission(settings=settings)
    assert calls == [("quarantine", "", 200)]
    assert (error.value.code, error.value.status) == ("storage_unverified", 503)
