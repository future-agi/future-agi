"""HubStore (SPEC §11): one small interface over object storage (S3/GCS/MinIO) or a local folder.

The bucket is private: unlike `tfc.utils.storage_client.ensure_bucket`, which gives a new bucket an
anonymous-read policy, the Hub creates its bucket with no policy. Every byte reaches a client through
a Django view, so counting and the approved-only rule cannot be skipped.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

import structlog
from django.core.exceptions import ImproperlyConfigured

from omega_hub import conf

logger = structlog.get_logger(__name__)


class HubStore(Protocol):
    def put(self, key: str, fileobj: BinaryIO, sha256: str | None = None) -> str | None: ...
    def open(self, key: str, version_id: str | None = None) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def purge(self, key: str) -> int: ...
    def versions(self, key: str) -> list[str]: ...
    def restore(self, key: str, version_id: str) -> None: ...
    def extend_retention(self, key: str, until: datetime) -> int: ...


def _check_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"bad storage key {key!r}")
    return key


def _verify(data: bytes, sha256: str | None) -> None:
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("stored bytes do not match their sha256")


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, target)


class FilesystemStore:
    """Filesystem equivalent of a versioned private bucket for tests and local development.

    As on a versioned bucket, every put is a new retained version (newest first in `versions`) and
    `delete` removes only the current object, so a deleted pin can be restored from its version:
    the restore drill runs the same on this backend as on S3 or MinIO.
    """

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def _version_dir(self, key: str) -> Path:
        return self.root / ".versions" / _check_key(key)

    def put(self, key, fileobj, sha256=None):
        data = fileobj.read()
        _verify(data, sha256)
        target = self._path(key)
        # Time-ordered ids, so `versions` lists newest first like a bucket's version listing.
        version_id = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"
        _write_atomic(self._version_dir(key) / version_id, data)
        _write_atomic(target, data)
        return version_id

    def open(self, key, version_id=None):
        return open((self._version_dir(key) / version_id) if version_id else self._path(key), "rb")

    def exists(self, key):
        return self._path(key).is_file()

    def delete(self, key):
        """Like a delete marker: the current object goes, its versions stay restorable."""
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def purge(self, key):
        """Remove the current object and every retained local version (nothing is retention-locked)."""
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass
        version_dir = self._version_dir(key)
        if version_dir.is_dir():
            for version in version_dir.iterdir():
                if version.is_file():
                    version.unlink()
            try:
                version_dir.rmdir()
            except OSError:
                pass
        return 0

    def versions(self, key):
        directory = self._version_dir(key)
        return sorted((path.name for path in directory.iterdir() if path.is_file()), reverse=True) if directory.is_dir() else []

    def restore(self, key, version_id):
        with self.open(key, version_id) as handle:
            self.put(key, handle)

    def extend_retention(self, key, until):
        """The development store has no object lock, so there is nothing to extend."""
        _check_key(key)
        return 0


class ObjectStore:
    """Private object storage. Object operations only: the bucket itself is provisioned and
    verified by `storage_provision` (bootstrap, the operations check, or an operator job), so Hub
    object credentials never need bucket-admin rights (ops/iam/app-policy.json)."""

    BUNDLE_RETENTION_DAYS = 90

    def __init__(self, bucket: str):
        from tfc.utils.storage_client import get_storage_client

        self.bucket = bucket
        self.client = get_storage_client()

    def provision(self) -> dict:
        from omega_hub.services import storage_provision

        return storage_provision.provision(self.client, self.bucket)

    def status(self) -> dict:
        from omega_hub.services import storage_provision

        return storage_provision.status(self.client, self.bucket)

    def put(self, key, fileobj, sha256=None):
        from minio.error import S3Error

        from omega_hub.services import storage_provision

        data = fileobj.read()
        _verify(data, sha256)
        object_name = _check_key(key)
        retention = None
        if object_name.startswith("bundles/") and storage_provision.object_lock_supported():
            from minio.retention import GOVERNANCE, Retention

            retention = Retention(GOVERNANCE, datetime.now(UTC) + timedelta(days=self.BUNDLE_RETENTION_DAYS))
        try:
            result = self.client.put_object(self.bucket, object_name, io.BytesIO(data), len(data), retention=retention)
        except S3Error as exc:
            raise _storage_error(exc, self.bucket) from exc
        return getattr(result, "version_id", None)

    def open(self, key, version_id=None):
        response = self.client.get_object(self.bucket, _check_key(key), version_id=version_id)
        try:
            return io.BytesIO(response.read())
        finally:
            response.close()
            response.release_conn()

    def exists(self, key):
        from minio.error import S3Error

        try:
            self.client.stat_object(self.bucket, _check_key(key))
            return True
        except S3Error:
            return False

    def delete(self, key):
        """Create a versioned delete marker; use ``purge`` for erasure."""
        self.client.remove_object(self.bucket, _check_key(key))

    def _retain_until(self, object_name: str, version_id: str):
        """A version's GOVERNANCE retain-until date, or None when it was written without one (an
        operator's copy, or a put from before bundles were locked)."""
        from minio.error import S3Error

        try:
            retention = self.client.get_object_retention(self.bucket, object_name, version_id=version_id)
        except S3Error as exc:
            if getattr(exc, "code", "") in ("NoSuchObjectLockConfiguration", "ObjectLockConfigurationNotFoundError"):
                return None
            raise
        return getattr(retention, "retain_until_date", None)

    def _locked(self, object_name: str, version_id: str | None, now) -> bool:
        """Whether a bundle version is still inside its GOVERNANCE retention. GCS interop has no
        object retention (put sends no lock headers there)."""
        from omega_hub.services import storage_provision

        if not object_name.startswith("bundles/") or not version_id or not storage_provision.object_lock_supported():
            return False
        until = self._retain_until(object_name, version_id)
        return until is not None and until > now

    def extend_retention(self, key, until):
        """Extend the GOVERNANCE retention of every retained version of a bundle to `until`
        (services/bundle_locks.py renews live entries' bundles daily). A version already locked
        past `until` is left alone: shortening a lock needs a bypass right the app never holds.
        Returns how many versions were extended; a bundle with no retained version is an incident
        (the restore drill has nothing to restore), so it raises."""
        from minio.retention import GOVERNANCE, Retention

        object_name = _check_key(key)
        if not object_name.startswith("bundles/"):
            raise ValueError(f"only bundles are retention-locked, not {key!r}")
        versions = [item.version_id for item in self.client.list_objects(self.bucket, prefix=object_name, recursive=True,
                                                                          include_version=True)
                    if item.object_name == object_name and item.version_id and not getattr(item, "is_delete_marker", False)]
        if not versions:
            raise FileNotFoundError(f"no retained version of {object_name}")
        extended = 0
        for version_id in versions:
            current = self._retain_until(object_name, version_id)
            if current is not None and current >= until:
                continue
            self.client.set_object_retention(self.bucket, object_name, Retention(GOVERNANCE, until), version_id=version_id)
            extended += 1
        return extended

    def purge(self, key):
        """Permanently erase every version and delete marker of a key, except bundle versions still
        inside their GOVERNANCE retention: those are left for a later run, and their count is
        returned (retention defers them; it is not an error)."""
        object_name = _check_key(key)
        now = datetime.now(UTC)
        deferred = 0
        for item in list(self.client.list_objects(self.bucket, prefix=object_name, recursive=True,
                                                  include_version=True)):
            if item.object_name != object_name:
                continue
            version_id = getattr(item, "version_id", None)
            if not getattr(item, "is_delete_marker", False) and self._locked(object_name, version_id, now):
                deferred += 1
                continue
            self.client.remove_object(self.bucket, object_name, version_id=version_id)
        return deferred

    def versions(self, key):
        return [
            item.version_id
            for item in self.client.list_objects(self.bucket, prefix=_check_key(key), recursive=True,
                                                 include_version=True)
            if item.object_name == key and item.version_id and not getattr(item, "is_delete_marker", False)
        ]

    def restore(self, key, version_id):
        with self.open(key, version_id) as handle:
            self.put(key, handle)


def _storage_error(exc, bucket: str) -> Exception:
    """An object write the bucket's configuration or the credential refuses becomes a typed 503
    (the client retries later; the operations check pages the maintainers), never a raw 500."""
    from omega_hub.errors import HubError

    code = getattr(exc, "code", "")
    message = str(getattr(exc, "message", "") or "")
    logger.warning("omega_hub_storage_write_refused", bucket=bucket, code=code, error=message[:300])
    if code == "NoSuchBucket" or (code == "InvalidRequest" and "ObjectLockConfiguration" in message):
        return HubError("OMEGA_HUB_STORAGE_NOT_PROVISIONED", 503,
                        message=f"Hub storage is not provisioned yet (bucket {bucket}: {code}). Try again later.",
                        headers={"Retry-After": "300"})
    if code == "AccessDenied":
        return HubError("OMEGA_HUB_STORAGE_DENIED", 503,
                        message="Hub storage refused the write. Try again later.", headers={"Retry-After": "300"})
    return exc


_store: HubStore | None = None
_store_config: tuple | None = None


def get_store() -> HubStore:
    global _store, _store_config
    config = (conf.storage_kind(), conf.storage_dir(), conf.bucket())
    if _store is None or _store_config != config:
        check_storage_config()
        _store = FilesystemStore(config[1]) if config[0] == "filesystem" else ObjectStore(config[2])
        _store_config = config
    return _store


def check_storage_config() -> None:
    kind = conf.storage_kind()
    if kind not in ("object", "filesystem"):
        raise ImproperlyConfigured("OMEGA_HUB_STORAGE must be 'object' or 'filesystem'")
    if kind == "filesystem":
        if not conf.debug_or_test():
            raise ImproperlyConfigured("OMEGA_HUB_STORAGE=filesystem is for development and tests only (DEBUG)")
        if not conf.storage_dir():
            raise ImproperlyConfigured("OMEGA_HUB_STORAGE=filesystem needs OMEGA_HUB_STORAGE_DIR")


def put_bytes(key: str, data: bytes, sha256: str | None = None) -> None:
    get_store().put(key, io.BytesIO(data), sha256)


def read_bytes(key: str) -> bytes:
    """An object the database names but storage does not hold (deleted, or not yet restored after
    an incident) is a typed 503 with Retry-After and an `omega_hub_object_missing` error log, never
    a raw 500: the restore drill (ops/RUNBOOK.md) brings it back under the same key."""
    from minio.error import S3Error

    try:
        with get_store().open(key) as handle:
            return handle.read()
    except (FileNotFoundError, S3Error) as exc:
        if isinstance(exc, S3Error) and getattr(exc, "code", "") not in ("NoSuchKey", "NoSuchVersion"):
            raise
        from omega_hub.errors import HubError

        logger.error("omega_hub_object_missing", key=key, error=exc.__class__.__name__)
        raise HubError("OMEGA_HUB_OBJECT_MISSING", 503, message="This file is temporarily unavailable. Try again later.",
                       headers={"Retry-After": "300"}) from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def raw_key(submission_id) -> str:
    return f"raw/{submission_id}"


def bundle_key(bundle_sha256: str) -> str:
    return f"bundles/{bundle_sha256}.tar.gz"


def cast_key(version_id) -> str:
    return f"casts/{version_id}.cast"


def job_cast_key(job_id) -> str:
    return f"casts/job-{job_id}.cast"


def log_key(job_id) -> str:
    return f"logs/{job_id}.txt"


def avatar_key(publisher_id, ext: str) -> str:
    return f"avatars/{publisher_id}.{ext}"
