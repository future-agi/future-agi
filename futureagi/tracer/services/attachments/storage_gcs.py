"""Private GCS XML/HMAC data plane; read-only, explicitly supplied posture.

Protocol references (no S3 VersionId translation):
https://cloud.google.com/storage/docs/authentication/signatures
https://cloud.google.com/storage/docs/authentication/canonical-requests
https://cloud.google.com/storage/docs/xml-api/get-bucket-list
https://cloud.google.com/storage/docs/xml-api/get-bucket-uploads
https://cloud.google.com/storage/docs/request-preconditions
https://cloud.google.com/storage/docs/json_api/v1/objects/list

Bindings use //storage.googleapis.com/projects/_/buckets/<name>, project number
as owner, the observed timeCreated, actual lower-case location, and encryption
mode google-managed or google-kms (the latter pins a cryptoKeys resource).
No provisioning, SDK discovery, token refresh, OAuth minting or JSON writes.
Cleanup reads the bound bucket's JSON metadata with supplied read-only OAuth
before using its purpose-specific HMAC credentials. It checks incarnation, not
current admission policy. A DELETE acknowledgement alone never releases cleanup
charges: the retained-generation listing must affirm absence.

PUT runs in a short-lived, supervised interpreter: killing a Python thread does
not stop an accepted socket write. Only the bounded data request runs there;
there is no new service, queue, credential acquisition or durable state. Secrets
are sent through its private stdin, never argv, environment or logs. A timed-out
PUT is ambiguous and MUST retain its caller's durable locator/cleanup charge.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote
from uuid import UUID
from xml.etree import ElementTree

import urllib3

from tfc.utils.storage_config import AttachmentStorageConfig, storage_http_client
from tracer.services.attachments.contract import AttachmentError

# The supervised request worker deliberately does not import ORM/interfaces.
if __name__ != "__main__":
    from tracer.services.attachments.interfaces import (
        EraseResult,
        ObjectIdentity,
        ObjectLocator,
        PutPermit,
        VersionPage,
    )

CHUNK = 256 * 1024
MAX_BYTES = 8 * 1024 * 1024  # Existing upload/segment/range ceiling.
MAX_METADATA = 1024 * 1024
MAX_PAGES = 100
PUT_SECONDS = 30
_ORIGIN = "https://storage.googleapis.com"
_KMS = re.compile(r"projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+")
_RESPONSE_HEADERS = (
    "x-goog-generation",
    "x-goog-encryption-kms-key-name",
    "content-length",
)


def _now():
    return datetime.now(UTC)


def _generation(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError
    if int(value) > 2**64 - 1:
        raise ValueError
    return value


def _length(value):
    if type(value) is not int or not 0 <= value <= MAX_BYTES:
        raise ValueError
    return value


def _query(values):
    return "&".join(
        f"{quote(str(k), safe='~')}={quote(str(v), safe='~')}"
        for k, v in sorted(values.items())
    )


def _signed_headers(method, path, query, headers, credentials, *, region, now, digest):
    """GOOG4 key derivation and canonical request; HMAC secret is NOT base64-decoded."""
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    date = timestamp[:8]
    scope = f"{date}/{region}/storage/goog4_request"
    values = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    values.update({"x-goog-date": timestamp, "x-goog-content-sha256": digest})
    names = ";".join(sorted(values))
    canonical = "\n".join(
        (
            method,
            path,
            query,
            "".join(f"{k}:{values[k]}\n" for k in sorted(values)),
            names,
            digest,
        )
    )
    string = "\n".join(
        (
            "GOOG4-HMAC-SHA256",
            timestamp,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
    )
    key = ("GOOG4" + credentials.secret_key).encode()
    for value in (date, region, "storage", "goog4_request"):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, string.encode(), hashlib.sha256).hexdigest()
    values["authorization"] = (
        f"GOOG4-HMAC-SHA256 Credential={credentials.access_key}/{scope}, "
        f"SignedHeaders={names}, Signature={signature}"
    )
    return values


def _close(response):
    with contextlib.suppress(Exception):
        response.close()
    with contextlib.suppress(Exception):
        response.release_conn()


class _ReadLimitExceeded(ValueError):
    pass


def _read(response, limit=MAX_METADATA):
    """Always bound reads, including untrusted error/metadata responses."""
    chunks: list[bytes] = []
    size = 0
    try:
        while True:
            part = response.read(min(CHUNK, limit - size + 1), decode_content=False)
            if not part:
                return b"".join(chunks)
            size += len(part)
            if size > limit:
                raise _ReadLimitExceeded
            chunks.append(part)
    finally:
        _close(response)


def _xml(data, root_name):
    # Reject declarations/entities, including UTF-16 spellings. Responses are
    # UTF-8 XML; external entities are never fetched and expansions never run.
    if b"\x00" in data or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError
    root = ElementTree.fromstring(data)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    if root.tag != root_name:
        raise ValueError
    return root


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


def _perform_put(http, request, data):
    remaining = request["deadline"] - time.time()
    if remaining <= 0:
        raise ValueError
    response = http.urlopen(
        "PUT",
        request["url"],
        body=data,
        headers=request["headers"],
        preload_content=False,
        decode_content=False,
        redirect=False,
        retries=False,
        timeout=urllib3.Timeout(connect=min(3, remaining), read=min(10, remaining)),
    )
    try:
        headers = {
            k: response.headers[k] for k in _RESPONSE_HEADERS if k in response.headers
        }
        if any(not isinstance(v, str) or len(v) > 4096 for v in headers.values()):
            raise ValueError
        return {"status": response.status, "headers": headers}
    finally:
        _close(response)


def _bounded_put(request, data):
    """One dispatch, no automatic retries; kill AND reap on wall-clock timeout."""
    remaining = request["deadline"] - time.time()
    if remaining <= 0:
        raise ValueError
    worker = (
        "import runpy,sys;sys.path.insert(0,sys.argv[1]);"
        "runpy.run_module('tracer.services.attachments.storage_gcs',run_name='__main__')"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", worker, str(Path(__file__).resolve().parents[3])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        payload = json.dumps(request, separators=(",", ":")).encode() + b"\n" + data
        remaining = request["deadline"] - time.time()
        if remaining <= 0:
            raise ValueError
        output, _ = process.communicate(payload, timeout=remaining)
        if (
            process.returncode != 0
            or len(output) > 16384
            or time.time() > request["deadline"]
        ):
            raise ValueError
        result = json.loads(output)
        if not isinstance(result, dict) or "error" in result:
            raise ValueError
        return result
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def _put_worker():
    try:
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b"\n"):
            raise ValueError
        request = json.loads(raw)
        length = _length(request["length"])
        data = sys.stdin.buffer.read(length + 1)
        if len(data) != length:
            raise ValueError
        http = storage_http_client(
            connect_timeout=3,
            read_timeout=10,
            retries=0,
            ca_certs=request["ca_certs"],
        )
        try:
            result = _perform_put(http, request, data)
        finally:
            http.clear()
        sys.stdout.write(json.dumps(result))
    except Exception:  # noqa: BLE001 - child entry point, fails closed
        # Provider errors/headers, credential values and tracebacks never escape.
        sys.stdout.write('{"error":true}')
        raise SystemExit(1) from None


class GCSPrivateMediaStorage:
    """No presign/public URL surface. HTTP injection is a trusted test seam.

    Injected transports implement urllib3.urlopen; for PUT they additionally
    implement bounded_put(request, data), obeying the absolute deadline without
    surviving socket writers. Production uses the supervised shared transport.
    """

    direct_upload_enabled = False

    def __init__(
        self,
        *,
        binding,
        config: AttachmentStorageConfig,
        posture_credentials=None,
        http=None,
    ):
        try:
            if not isinstance(config, AttachmentStorageConfig):
                raise ValueError
            if (
                not isinstance(binding.id, UUID)
                or binding.provider != "gcs"
                or config.storage.backend != "gcs"
                or binding.endpoint != _ORIGIN
                or config.storage.endpoint_url != _ORIGIN
                or not config.storage.secure
                or binding.region != config.storage.region
                or not re.fullmatch(r"[a-z0-9-]+", binding.region)
                or not re.fullmatch(r"[0-9]+", binding.account_identity)
                or config.quarantine_bucket != binding.quarantine_bucket
                or config.serving_bucket != binding.serving_bucket
                or binding.quarantine_bucket == binding.serving_bucket
            ):
                raise ValueError
            # Copy scalar persisted facts; mutating an ORM instance after factory
            # construction cannot redirect a long-lived adapter.
            names = ["id", "provider", "endpoint", "account_identity", "region"]
            for purpose in ("quarantine", "serving"):
                names.extend(
                    f"{purpose}_{field}"
                    for field in (
                        "bucket",
                        "resource_identity",
                        "owner_identity",
                        "created_at",
                        "encryption_mode",
                        "encryption_key_identity",
                    )
                )
                bucket = getattr(binding, f"{purpose}_bucket")
                mode = getattr(binding, f"{purpose}_encryption_mode")
                key = getattr(binding, f"{purpose}_encryption_key_identity")
                created = getattr(binding, f"{purpose}_created_at")
                credentials = getattr(config, f"{purpose}_credentials")
                if (
                    not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket)
                    or getattr(binding, f"{purpose}_resource_identity")
                    != self.resource_identity(bucket)
                    or getattr(binding, f"{purpose}_owner_identity")
                    != binding.account_identity
                    or not isinstance(created, datetime)
                    or created.tzinfo is None
                    or mode not in ("google-managed", "google-kms")
                    or (mode == "google-managed" and key != "")
                    or (mode == "google-kms" and not _KMS.fullmatch(key))
                    or not re.fullmatch(r"[A-Za-z0-9]+", credentials.access_key)
                    or not credentials.secret_key
                    or credentials.session_token is not None
                ):
                    raise ValueError
            if posture_credentials is not None:
                from google.auth.credentials import Credentials

                if not isinstance(posture_credentials, Credentials):
                    raise ValueError
            self._binding = SimpleNamespace(
                **{name: getattr(binding, name) for name in names}
            )
            self.binding_id = binding.id
            self.config = config
            self._posture = posture_credentials
            self._http = (
                http
                if http is not None
                else storage_http_client(
                    connect_timeout=3,
                    read_timeout=10,
                    retries=0,
                    ca_certs=config.storage.ca_certs,
                )
            )
            self._put_request = (
                _bounded_put if http is None else getattr(http, "bounded_put", None)
            )
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("storage_unverified", 503) from None

    @staticmethod
    def resource_identity(bucket):
        return f"//storage.googleapis.com/projects/_/buckets/{bucket}"

    def _locator(self, value):
        if (
            not isinstance(value, ObjectLocator | ObjectIdentity)
            or value.binding_id != self.binding_id
        ):
            raise ValueError
        if (
            value.purpose not in ("quarantine", "serving")
            or not value.key
            or len(value.key) > 255
        ):
            raise ValueError
        if any(ord(c) < 32 or ord(c) == 127 for c in value.key):
            raise ValueError
        # Prevent URI dot-segment normalization at intermediary transports.
        if any(segment in (".", "..") for segment in value.key.split("/")):
            raise ValueError
        if isinstance(value, ObjectIdentity):
            _generation(value.version)
        return getattr(self._binding, f"{value.purpose}_bucket")

    def _xml_request(self, purpose, method, *, key=None, query=None, headers=None):
        bucket = getattr(self._binding, f"{purpose}_bucket")
        path = "/" + quote(bucket, safe="~")
        if key is not None:
            path += "/" + quote(key, safe="/~")
        query = _query(query or {})
        headers = {
            "host": "storage.googleapis.com",
            "content-length": "0",
            **(headers or {}),
        }
        headers = _signed_headers(
            method,
            path,
            query,
            headers,
            getattr(self.config, f"{purpose}_credentials"),
            region=self._binding.region,
            now=_now(),
            digest=hashlib.sha256(b"").hexdigest(),
        )
        return self._http.urlopen(
            method,
            _ORIGIN + path + ("?" + query if query else ""),
            headers=headers,
            preload_content=False,
            decode_content=False,
            redirect=False,
            retries=False,
            timeout=urllib3.Timeout(connect=3, read=10),
        )

    def _xml_get(self, purpose, root, **kwargs):
        response = self._xml_request(purpose, "GET", **kwargs)
        if response.status != 200:
            _close(response)
            raise ValueError
        return _xml(_read(response), root)

    @classmethod
    def discover_identity(cls, *, config, posture_credentials=None, http=None):
        """Observe new-admission resource facts using explicit read-only OAuth.

        No historical binding or default credentials are invented here. The
        factory fingerprints these facts, verifies full posture and requires a
        matching historical credential-reference mapping before admission.
        """
        try:
            from google.auth.credentials import Credentials

            from tracer.services.attachments.storage import outside_transaction

            outside_transaction()
            if (
                not isinstance(config, AttachmentStorageConfig)
                or config.storage.backend != "gcs"
                or config.storage.endpoint_url != _ORIGIN
                or not config.storage.secure
                or not re.fullmatch(r"[a-z0-9-]+", config.storage.region)
                or config.quarantine_bucket == config.serving_bucket
                or not isinstance(posture_credentials, Credentials)
            ):
                raise ValueError
            transport = (
                http
                if http is not None
                else storage_http_client(
                    connect_timeout=3,
                    read_timeout=10,
                    retries=0,
                    ca_certs=config.storage.ca_certs,
                )
            )
            values = {
                "provider": "gcs",
                "endpoint": _ORIGIN,
                "region": config.storage.region,
            }
            for purpose in ("quarantine", "serving"):
                bucket = getattr(config, f"{purpose}_bucket")
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket):
                    raise ValueError
                status, data = cls._json_get_bucket(
                    bucket, credentials=posture_credentials, http=transport
                )
                account = str(data.get("projectNumber", ""))
                encryption = data.get("encryption", {})
                key = encryption.get("defaultKmsKeyName", "")
                if (
                    status != 200
                    or data.get("kind") != "storage#bucket"
                    or data.get("name") != bucket
                    or data.get("id") != bucket
                    or not re.fullmatch(r"[0-9]+", account)
                    or data.get("location", "").lower() != config.storage.region
                    or data.get("hierarchicalNamespace", {}).get("enabled") is True
                    or (
                        "account_identity" in values
                        and values["account_identity"] != account
                    )
                    or not isinstance(key, str)
                    or (key and not _KMS.fullmatch(key))
                ):
                    raise ValueError
                values["account_identity"] = account
                values.update(
                    {
                        f"{purpose}_bucket": bucket,
                        f"{purpose}_resource_identity": cls.resource_identity(bucket),
                        f"{purpose}_owner_identity": account,
                        f"{purpose}_created_at": _timestamp(data["timeCreated"]),
                        f"{purpose}_encryption_mode": (
                            "google-kms" if key else "google-managed"
                        ),
                        f"{purpose}_encryption_key_identity": key,
                    }
                )
            return values
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("storage_unverified", 503) from None

    def _json_get(self, purpose, suffix="", query=None):
        return self._json_get_bucket(
            getattr(self._binding, f"{purpose}_bucket"),
            credentials=self._posture,
            http=self._http,
            suffix=suffix,
            query=query,
        )

    @staticmethod
    def _json_get_bucket(bucket, *, credentials, http, suffix="", query=None):
        # Credentials.apply/before_request can refresh. Use ONLY the supplied,
        # currently valid token; no refresh, ADC or file-based acquisition.
        if (
            credentials is None
            or not credentials.valid
            or not isinstance(credentials.token, str)
        ):
            raise ValueError
        token = credentials.token
        if not token or any(c.isspace() or ord(c) < 32 for c in token):
            raise ValueError
        url = f"{_ORIGIN}/storage/v1/b/{quote(bucket, safe='~')}{suffix}"
        if query:
            url += "?" + _query(query)
        response = http.urlopen(
            "GET",
            url,
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            preload_content=False,
            decode_content=False,
            redirect=False,
            retries=False,
            timeout=urllib3.Timeout(connect=3, read=10),
        )
        status = response.status
        data = json.loads(_read(response))
        if not isinstance(data, dict):
            raise ValueError
        return status, data

    def _bucket_identity(self, purpose):
        # Historical erasure needs the same bucket incarnation, not admission policy.
        status, data = self._json_get(purpose)
        bucket = getattr(self._binding, f"{purpose}_bucket")
        if (
            status != 200
            or data.get("kind") != "storage#bucket"
            or data.get("name") != bucket
            or data.get("id") != bucket
            or str(data.get("projectNumber")) != self._binding.account_identity
            or _timestamp(data["timeCreated"])
            != getattr(self._binding, f"{purpose}_created_at")
        ):
            raise ValueError
        return data

    def _metadata(self, purpose, *, private):
        data = self._bucket_identity(purpose)
        if (
            data.get("location", "").lower() != self._binding.region
            or data.get("hierarchicalNamespace", {}).get("enabled") is True
        ):
            raise ValueError
        if private:
            iam = data.get("iamConfiguration", {})
            if (
                iam.get("uniformBucketLevelAccess", {}).get("enabled") is not True
                or iam.get("publicAccessPrevention") != "enforced"
            ):
                raise ValueError
        return data

    def verify_identity(self, *, operation):
        try:
            if operation not in ("admit", "write", "read", "erase"):
                raise ValueError
            for purpose in ("quarantine", "serving"):
                if operation == "erase":
                    self._bucket_identity(purpose)
                else:
                    self._metadata(purpose, private=True)
            if operation in ("admit", "write"):
                self.verify_readiness()
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError(
                "cleanup_unavailable" if operation == "erase" else "storage_unverified",
                503,
            ) from None

    def _write_posture(self, purpose):
        data = self._metadata(purpose, private=True)
        key = getattr(self._binding, f"{purpose}_encryption_key_identity")
        policy = data.get("softDeletePolicy")
        if not isinstance(policy, dict):
            raise ValueError
        duration = policy.get("retentionDurationSeconds")
        # The JSON API encodes int64 as decimal strings. Never coerce a bool,
        # float, whitespace, or a missing policy into a valid retention period.
        if isinstance(duration, str) and re.fullmatch(r"[1-9][0-9]{0,6}", duration):
            duration = int(duration)
        if (
            type(duration) is not int
            or not 7 * 86400 <= duration <= 90 * 86400
            or data.get("versioning", {}).get("enabled") is not True
            or data.get("encryption", {}).get("defaultKmsKeyName", "") != key
            or data.get("cors")
            or data.get("retentionPolicy")
            or data.get("defaultEventBasedHold") is True
            or data.get("objectRetention", {}).get("mode") == "Enabled"
            or data.get("storageClass")
            not in ("STANDARD", "NEARLINE", "COLDLINE", "ARCHIVE")
        ):
            raise ValueError
        rules = data.get("lifecycle", {}).get("rule", [])
        noncurrent, multipart = False, False
        for rule in rules:
            action, condition = rule["action"], rule["condition"]
            if action == {"type": "AbortIncompleteMultipartUpload"} and condition == {
                "age": 1
            }:
                multipart = True
            elif action == {"type": "Delete"} and condition == {
                "isLive": False,
                "daysSinceNoncurrentTime": 1,
            }:
                noncurrent = True
            else:
                # Do not guess safety of serving age expiry, prefix filters,
                # storage-class transitions or additional retention clauses.
                raise ValueError
        if not noncurrent or not multipart:
            raise ValueError
        # Observational only: effectiveTime is not proof of a bucket's history.
        return {
            "retention_seconds": duration,
            "has_effective_time": bool(policy.get("effectiveTime")),
        }

    def verify_readiness(self):
        try:
            for purpose in ("quarantine", "serving"):
                self._write_posture(purpose)
                # Prove the read-only identity can enumerate retained copies.
                probe = ObjectLocator(
                    self.binding_id, purpose, "__attachment_posture_probe__"
                )
                if not self._soft_deleted_absent(probe):
                    raise ValueError
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("storage_unverified", 503) from None

    def _encryption(self, purpose, headers):
        expected = getattr(self._binding, f"{purpose}_encryption_key_identity")
        actual = headers.get("x-goog-encryption-kms-key-name", "")
        if expected:
            if not re.fullmatch(
                re.escape(expected) + r"/cryptoKeyVersions/[1-9][0-9]*", actual
            ):
                raise ValueError
        elif actual:
            raise ValueError

    def _read_object(self, identity, *, expected_length, start=None, end=None):
        self._locator(identity)
        _length(expected_length)
        self._metadata(identity.purpose, private=True)
        headers = {
            "x-goog-if-generation-match": identity.version,
            "accept-encoding": "identity",
        }
        wanted = expected_length
        if start is not None:
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start <= end < expected_length
            ):
                raise ValueError
            wanted = end - start + 1
            headers["range"] = f"bytes={start}-{end}"
        response = self._xml_request(
            identity.purpose,
            "GET",
            key=identity.key,
            query={"generation": identity.version},
            headers=headers,
        )
        try:
            if response.status != (200 if start is None else 206):
                raise ValueError
            generation = _generation(response.headers.get("x-goog-generation"))
            length = response.headers.get("content-length")
            if not isinstance(length, str) or not re.fullmatch(
                r"0|[1-9][0-9]{0,19}", length
            ):
                raise ValueError
            if generation != identity.version or length != str(wanted):
                raise AttachmentError("snapshot_changed", 409)
            if (
                response.headers.get("content-encoding", "identity") != "identity"
                or response.headers.get("x-goog-stored-content-encoding", "identity")
                != "identity"
                or (
                    start is not None
                    and response.headers.get("content-range")
                    != f"bytes {start}-{end}/{expected_length}"
                )
            ):
                raise ValueError
            self._encryption(identity.purpose, response.headers)
            try:
                data = _read(response, wanted)
            except _ReadLimitExceeded:
                raise AttachmentError("snapshot_changed", 409) from None
            if len(data) != wanted:
                # A truncated stream is not proof that the immutable object changed.
                raise ValueError
            return data
        finally:
            _close(response)

    def snapshot(self, identity, output, *, expected_length):
        try:
            data = self._read_object(identity, expected_length=expected_length)
            for offset in range(0, len(data), CHUNK):
                part = data[offset : offset + CHUNK]
                if output.write(part) != len(part):
                    raise ValueError
            return hashlib.sha256(data).hexdigest()
        except AttachmentError:
            raise
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("storage_unverified", 503) from None

    def open_range(self, identity, *, start, end, expected_length):
        try:
            if start is None or end is None:
                raise ValueError
            return io.BytesIO(
                self._read_object(
                    identity, expected_length=expected_length, start=start, end=end
                )
            )
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("media_unavailable", 503) from None

    def _put(self, permit, stream, length, *, purpose, expected_sha256=None):
        try:
            if not isinstance(permit, PutPermit) or permit.locator.purpose != purpose:
                raise ValueError
            bucket = self._locator(permit.locator)
            _length(length)
            if permit.deadline_at <= _now() or self._put_request is None:
                raise ValueError
            self._write_posture(purpose)
            chunks, size = [], 0
            while True:
                if _now() >= permit.deadline_at:
                    raise ValueError
                part = stream.read(min(CHUNK, length - size + 1))
                if not part:
                    break
                if not isinstance(part, bytes):
                    raise ValueError
                size += len(part)
                if size > length:
                    raise ValueError
                chunks.append(part)
            data = b"".join(chunks)
            digest = hashlib.sha256(data).hexdigest()
            if size != length or (
                expected_sha256 is not None and digest != expected_sha256
            ):
                raise AttachmentError("snapshot_changed", 409)
            path = (
                "/"
                + quote(bucket, safe="~")
                + "/"
                + quote(permit.locator.key, safe="/~")
            )
            headers = {
                "host": "storage.googleapis.com",
                "content-length": str(length),
                "content-type": "application/octet-stream",
                "x-goog-if-generation-match": "0",
            }
            key = getattr(self._binding, f"{purpose}_encryption_key_identity")
            if key:
                headers["x-goog-encryption-kms-key-name"] = key
            now = _now()
            deadline = min(
                permit.deadline_at.timestamp(), now.timestamp() + PUT_SECONDS
            )
            if deadline <= now.timestamp():
                raise ValueError
            headers = _signed_headers(
                "PUT",
                path,
                "",
                headers,
                getattr(self.config, f"{purpose}_credentials"),
                region=self._binding.region,
                now=now,
                digest=digest,
            )
            result = self._put_request(
                {
                    "url": _ORIGIN + path,
                    "headers": headers,
                    "length": length,
                    "deadline": deadline,
                    "ca_certs": self.config.storage.ca_certs,
                },
                data,
            )
            if time.time() >= deadline:
                raise ValueError
            if result["status"] == 412:
                raise AttachmentError("conflict", 409)
            if result["status"] != 200:
                raise ValueError
            version = _generation(result["headers"].get("x-goog-generation"))
            self._encryption(purpose, result["headers"])
            identity = ObjectIdentity(
                self.binding_id, purpose, permit.locator.key, version
            )
            # PUT Content-Length is the *response body*, not object length.
            # Pin a HEAD to the returned generation to verify stored size.
            response = self._xml_request(
                purpose,
                "HEAD",
                key=identity.key,
                query={"generation": version},
                headers={"x-goog-if-generation-match": version},
            )
            try:
                if (
                    response.status != 200
                    or response.headers.get("x-goog-generation") != version
                    or response.headers.get("content-length") != str(length)
                ):
                    raise ValueError
                self._encryption(purpose, response.headers)
            finally:
                _close(response)
            return identity
        except AttachmentError:
            raise
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            # Includes timeout: no latest-generation recovery or automatic retry.
            raise AttachmentError("storage_unverified", 503) from None

    def put_quarantine(self, permit, stream, length):
        return self._put(permit, stream, length, purpose="quarantine")

    def promote(self, snapshot, *, permit, expected_length, expected_sha256):
        try:
            if not isinstance(expected_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected_sha256
            ):
                raise ValueError
            snapshot.seek(0)
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("snapshot_changed", 409) from None
        return self._put(
            permit,
            snapshot,
            expected_length,
            purpose="serving",
            expected_sha256=expected_sha256,
        )

    def list_versions(self, locator, *, cursor=None, limit=100):
        """Do not let a cleanup retry lose a now-soft-deleted orphan.

        Existing consumers enumerate live/noncurrent versions, preserve the
        published identity, then release work charges after the final page.
        XML hides retained generations: a terminal page is therefore usable
        only with retained absence proof. Unknown history/authority stays
        pending, rather than turning the second cleanup pass into false success.
        """
        try:
            page = self._list_live_versions(locator, cursor=cursor, limit=limit)
            if page.next_cursor is None and not self._soft_deleted_absent(locator):
                raise ValueError
            return page
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("cleanup_unavailable", 503) from None

    def _list_live_versions(self, locator, *, cursor=None, limit=100):
        try:
            bucket = self._locator(locator)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError
            self._bucket_identity(locator.purpose)
            query = {
                # Native V1 uses Generation/NextGenerationMarker. Do not mix
                # this cursor with V2 continuation tokens or interop VersionId.
                "list-type": "1",
                "versions": "true",
                "prefix": locator.key,
                "max-keys": str(limit),
                "encoding-type": "url",
            }
            if cursor is not None:
                if not isinstance(cursor, str) or len(cursor) > 8192:
                    raise ValueError
                value = json.loads(cursor)
                if (
                    not isinstance(value, list)
                    or len(value) != 5
                    or value[:3] != [str(self.binding_id), locator.purpose, locator.key]
                ):
                    raise ValueError
                marker, generation = value[3:]
                if marker != locator.key:
                    raise ValueError
                query.update(
                    {"marker": marker, "generation-marker": _generation(generation)}
                )
            root = self._xml_get(locator.purpose, "ListBucketResult", query=query)
            if (
                root.findtext("Name") != bucket
                # GCS only documents EncodingType in V2 responses. The V1
                # encoding-type=url REQUEST still determines key decoding.
                or root.findtext("EncodingType") not in (None, "url")
                or root.findtext("IsTruncated") not in ("true", "false")
            ):
                raise ValueError
            # Native XML returns Version/Generation, not S3 VersionId.
            if root.findall("DeleteMarker") or root.findall("Contents"):
                raise ValueError
            entries = root.findall("Version")
            if len(entries) > limit:
                raise ValueError
            versions = []
            for item in entries:
                key = unquote(item.findtext("Key"), errors="strict")
                if not key.startswith(locator.key):
                    raise ValueError
                if key == locator.key:
                    versions.append(
                        ObjectIdentity(
                            self.binding_id,
                            locator.purpose,
                            key,
                            _generation(item.findtext("Generation")),
                        )
                    )
            next_cursor = None
            if root.findtext("IsTruncated") == "true":
                marker = unquote(root.findtext("NextMarker"), errors="strict")
                generation = _generation(root.findtext("NextGenerationMarker"))
                if marker == locator.key:
                    next_cursor = json.dumps(
                        [
                            str(self.binding_id),
                            locator.purpose,
                            locator.key,
                            marker,
                            generation,
                        ],
                        separators=(",", ":"),
                    )
                    if next_cursor == cursor:
                        raise ValueError
                elif not marker.startswith(locator.key) or marker < locator.key:
                    raise ValueError
                # A prefix neighbour sorts after the exact key: stop safely.
            return VersionPage(tuple(versions), (), next_cursor)
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("cleanup_unavailable", 503) from None

    def erase_version(self, identity):
        """None means proven exact absence, not merely an accepted DELETE.

        Consumers retain locators/charges on cleanup_unavailable. Checking only
        this generation preserves a different, referenced published version.
        """
        try:
            self._delete_version(identity)
            if not self._soft_deleted_absent(identity, generation=identity.version):
                raise ValueError
            status, data = self._json_get(
                identity.purpose,
                "/o/" + quote(identity.key, safe="~"),
                {"generation": identity.version, "softDeleted": "true"},
            )
            # Keep the independent, exact-generation notFound check as well.
            if not self._json_error(status, data, 404, "notFound"):
                raise ValueError
            self._metadata(identity.purpose, private=False)
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("cleanup_unavailable", 503) from None

    def _delete_version(self, identity):
        """Attempt HMAC deletion; caller must separately prove retained absence."""
        try:
            self._locator(identity)
            if not isinstance(identity, ObjectIdentity):
                raise ValueError
            self._bucket_identity(identity.purpose)
            response = self._xml_request(
                identity.purpose,
                "DELETE",
                key=identity.key,
                query={"generation": identity.version},
                headers={"x-goog-if-generation-match": identity.version},
            )
            status = response.status
            if status == 204:
                _close(response)
                return
            if status == 404:
                error = _xml(_read(response), "Error")
                if error.findtext("Code") == "NoSuchKey":
                    return
            else:
                _close(response)
            raise ValueError
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            raise AttachmentError("cleanup_unavailable", 503) from None

    def _multipart(self, locator, *, delete):
        cursor, seen, found = None, set(), False
        for _ in range(MAX_PAGES):
            query = {
                "uploads": "",
                "prefix": locator.key,
                "max-uploads": "100",
                "encoding-type": "url",
            }
            if cursor is not None:
                query.update({"key-marker": cursor[0], "upload-id-marker": cursor[1]})
            root = self._xml_get(
                locator.purpose, "ListMultipartUploadsResult", query=query
            )
            if (
                root.findtext("Bucket")
                != getattr(self._binding, f"{locator.purpose}_bucket")
                # Multipart's documented response also omits EncodingType;
                # the request selects URL encoding, including continuation keys.
                or root.findtext("EncodingType") not in (None, "url")
                or root.findtext("IsTruncated") not in ("true", "false")
            ):
                raise ValueError
            uploads = root.findall("Upload")
            if len(uploads) > 100:
                raise ValueError
            for item in uploads:
                key = unquote(item.findtext("Key"), errors="strict")
                if not key.startswith(locator.key):
                    raise ValueError
                if key != locator.key:
                    continue
                found = True
                upload_id = item.findtext("UploadId")
                if not upload_id or len(upload_id) > 4096:
                    raise ValueError
                if delete:
                    response = self._xml_request(
                        locator.purpose,
                        "DELETE",
                        key=key,
                        query={"uploadId": upload_id},
                    )
                    if response.status != 204:
                        if (
                            response.status != 404
                            or _xml(_read(response), "Error").findtext("Code")
                            != "NoSuchUpload"
                        ):
                            _close(response)
                            raise ValueError
                    _close(response)
            if root.findtext("IsTruncated") == "false":
                return found
            marker = unquote(root.findtext("NextKeyMarker"), errors="strict")
            upload_id = root.findtext("NextUploadIdMarker")
            if marker != locator.key:
                if marker.startswith(locator.key) and marker > locator.key:
                    return found
                raise ValueError
            cursor = (marker, upload_id)
            if not upload_id or cursor in seen:
                raise ValueError
            seen.add(cursor)
        raise ValueError

    @staticmethod
    def _json_error(status, data, expected_status, reason):
        error = data.get("error")
        if status != expected_status or not isinstance(error, dict):
            return False
        errors = error.get("errors")
        return (
            error.get("code") == expected_status
            and isinstance(errors, list)
            and bool(errors)
            and all(
                isinstance(item, dict) and item.get("reason") == reason
                for item in errors
            )
        )

    def _soft_deleted_absent(self, locator, *, generation=None):
        # Only successful bounded enumeration can prove absence. Neither a 400,
        # bucket metadata nor the passage of hardDeleteTime is deletion proof.
        self._metadata(locator.purpose, private=False)
        token, seen = None, set()
        for _ in range(MAX_PAGES):
            query = {"prefix": locator.key, "maxResults": "100", "softDeleted": "true"}
            if token is not None:
                query["pageToken"] = token
            status, page = self._json_get(locator.purpose, "/o", query)
            if (
                status != 200
                or page.get("kind") != "storage#objects"
                or not isinstance(page.get("items", []), list)
            ):
                raise ValueError
            items = page.get("items", [])
            if len(items) > 100:
                raise ValueError
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not item["name"].startswith(locator.key)
                ):
                    raise ValueError
                if item["name"] == locator.key:
                    retained = _generation(item.get("generation"))
                    if generation is None or generation == retained:
                        return False
            token = page.get("nextPageToken")
            if token is None:
                self._metadata(locator.purpose, private=False)
                return True
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 8192
                or token in seen
            ):
                raise ValueError
            seen.add(token)
        raise ValueError

    def _versions(self, locator, *, delete):
        cursor, seen, found = None, set(), False
        for _ in range(MAX_PAGES):
            # Full erasure checks retained absence after deleting live generations.
            page = self._list_live_versions(locator, cursor=cursor)
            for identity in page.versions:
                found = True
                if delete:
                    # Do not stop other physical cleanup merely because exact
                    # retained absence is pending; prove the whole key below.
                    self._delete_version(identity)
            cursor = page.next_cursor
            if cursor is None:
                return found
            if cursor in seen:
                raise ValueError
            seen.add(cursor)
        raise ValueError

    def erase_all_versions(self, locator):
        try:
            self._locator(locator)
            self._bucket_identity(locator.purpose)
            self._versions(locator, delete=True)
            self._multipart(locator, delete=True)
            # A successful DELETE is not evidence that either enumeration is
            # empty. Recheck both; callers still own write-horizon fencing.
            if self._versions(locator, delete=False) or self._multipart(
                locator, delete=False
            ):
                return EraseResult(False, True, "cleanup_pending", _now())
            if not self._soft_deleted_absent(locator):
                return EraseResult(False, True, "soft_deleted_pending", _now())
            return EraseResult(True, False, None, _now())
        except Exception:  # noqa: BLE001 - provider boundary, fails closed
            return EraseResult(False, True, "cleanup_unavailable", _now())

    def erase_upload(self, upload_id):
        try:
            upload_id = UUID(str(upload_id))
        except ValueError:
            raise AttachmentError("cleanup_unavailable", 503) from None
        results = [
            self.erase_all_versions(
                ObjectLocator(self.binding_id, "quarantine", f"quarantine/{upload_id}")
            ),
            self.erase_all_versions(
                ObjectLocator(self.binding_id, "serving", f"original/{upload_id}")
            ),
        ]
        reasons = {result.reason for result in results if not result.empty}
        if reasons:
            reason = (
                "soft_deleted_pending"
                if reasons == {"soft_deleted_pending"}
                else "cleanup_pending"
            )
            if "cleanup_unavailable" in reasons:
                reason = "cleanup_unavailable"
            return EraseResult(False, True, reason, _now())
        return EraseResult(True, False, None, _now())


if __name__ == "__main__":
    _put_worker()
