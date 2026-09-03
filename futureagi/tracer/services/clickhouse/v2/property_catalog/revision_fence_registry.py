"""Durable multi-tenant revision-fence registry for the Go hot producer.

The registry is a single canonical version-2 JSON document shared by the
control plane and ``fi-collector``.  Every read/modify/write is serialized by
an advisory lock in the same directory, validates the complete existing
inventory before mutation, and replaces the document atomically.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .codec import canonical_uuid, framed_sha256
from .coordinator import ProducerRevisionAssignment

REVISION_FENCE_FORMAT = "futureagi.property-catalog-revision-fence"
REVISION_FENCE_VERSION = 2
MAX_REVISION_FENCE_BYTES = 64 << 20
MAX_REVISION_FENCE_PROJECTS = 256

_FENCE_SHA_DOMAIN = "futureagi.property-catalog.revision-fence.v2"
# Keep the structural protocol bound aligned with Go's maxRevisionLease and the
# reviewed PROPERTY_CATALOG_MAX_REVISION_LEASE_SECONDS setting ceiling. Runtime
# admission may choose a shorter configured lease, but either implementation
# must be able to decode every valid cross-language fence.
_MAX_DRAIN_LEASE = timedelta(minutes=60)
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
_DOCUMENT_FIELDS = ("format", "version", "fences")
_FENCE_FIELDS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "projection_version",
    "build_lease_sha256",
    "build_token",
    "project_ids",
    "span_since_us",
    "span_until_us",
    "issued_at",
    "expires_at",
    "drain_deadline",
    "fenced_sequence",
    "status",
    "fence_sha256",
)


class RevisionFenceRegistryError(RuntimeError):
    """The shared fence inventory is unsafe, corrupt, or out of bounds."""


def encode_revision_fence_registry(
    assignments: Sequence[ProducerRevisionAssignment],
    *,
    now: datetime | None = None,
) -> bytes:
    """Encode assignments exactly as Go ``EncodeRevisionFenceFile`` does."""

    observed_at = _validated_now(now or datetime.now(UTC))
    documents: list[Mapping[str, Any]] = []
    for assignment in assignments:
        if not isinstance(assignment, ProducerRevisionAssignment):
            raise TypeError("revision fence entries must be producer assignments")
        documents.append(assignment.document)
    return _encode_documents(documents, now=observed_at)


def decode_revision_fence_registry(
    raw: bytes,
    *,
    now: datetime | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Decode one complete Go-compatible registry or fail closed."""

    observed_at = _validated_now(now or datetime.now(UTC))
    return _decode_revision_fence_registry(
        raw,
        now=observed_at,
        allow_expired=False,
    )


def _decode_revision_fence_registry(
    raw: bytes,
    *,
    now: datetime,
    allow_expired: bool,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, bytes):
        raise TypeError("revision fence payload must be bytes")
    if not 2 <= len(raw) <= MAX_REVISION_FENCE_BYTES:
        raise RevisionFenceRegistryError(
            "revision fence payload is outside its byte limit"
        )
    if raw[-1:] != b"\n" or b"\n" in raw[:-1]:
        raise RevisionFenceRegistryError(
            "revision fence must be one canonical JSON line"
        )
    try:
        decoded = json.loads(
            raw[:-1],
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except RevisionFenceRegistryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevisionFenceRegistryError("revision fence JSON is invalid") from exc
    if type(decoded) is not dict or set(decoded) != set(_DOCUMENT_FIELDS):
        raise RevisionFenceRegistryError("revision fence document fields are invalid")
    if (
        decoded.get("format") != REVISION_FENCE_FORMAT
        or type(decoded.get("version")) is not int
        or decoded.get("version") != REVISION_FENCE_VERSION
        or type(decoded.get("fences")) is not list
    ):
        raise RevisionFenceRegistryError("revision fence format or version is invalid")
    documents = tuple(
        _validated_fence_document(
            value,
            now=now,
            allow_expired=allow_expired,
        )
        for value in decoded["fences"]
    )
    canonical = _encode_documents(
        documents,
        now=now,
        allow_expired=allow_expired,
    )
    if canonical != raw:
        raise RevisionFenceRegistryError(
            "revision fence document is not canonical version-2 JSON"
        )
    return documents


class AtomicMultiTenantFenceFile:
    """Publish one tenant assignment without clobbering other tenants."""

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError("revision fence registry path must be absolute")
        try:
            parent = os.lstat(candidate.parent)
        except OSError as exc:
            raise ValueError("revision fence registry parent is unavailable") from exc
        if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
            raise ValueError(
                "revision fence registry parent must be a physical directory"
            )
        if not callable(now):
            raise TypeError("revision fence registry clock must be callable")
        self._path = candidate
        self._lock_path = candidate.parent / f".{candidate.name}.lock"
        self._now = now

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def publish(self, assignment: ProducerRevisionAssignment) -> None:
        if not isinstance(assignment, ProducerRevisionAssignment):
            raise TypeError("assignment must be a ProducerRevisionAssignment")
        observed_at = _validated_now(self._now())
        incoming = _validated_fence_document(
            assignment.document,
            now=observed_at,
        )
        descriptor = self._open_lock()
        locked = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            # An interrupted controller can leave a once-valid building or
            # draining lease on disk past its deadline.  The Go reader rejects
            # the complete registry while such an entry remains, so a restart
            # must be able to recover it without weakening corruption checks.
            # Decode the old bytes structurally, discard only authorization
            # entries whose canonical deadline is provably elapsed, and retain
            # every fenced or still-live tenant verbatim.
            current = {
                _tenant_key(document): document
                for document in self._read_unlocked(
                    now=observed_at,
                    allow_expired=True,
                )
                if not _is_expired_live_fence(document, now=observed_at)
            }
            current[_tenant_key(incoming)] = incoming
            raw = _encode_documents(tuple(current.values()), now=observed_at)
            self._write_unlocked(raw)
        except RevisionFenceRegistryError:
            raise
        except OSError as exc:
            raise RevisionFenceRegistryError(
                "revision fence registry update failed"
            ) from exc
        finally:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            else:
                os.close(descriptor)

    def reconcile_authorized_workspaces(
        self,
        workspace_ids: Sequence[str],
    ) -> int:
        """Remove stale authorization entries under the shared writer lock.

        Authorized live assignments and terminal ``fenced`` assignments are
        retained.  Expired building/draining assignments and every assignment
        outside the current workspace inventory are removed.  The complete
        existing document is still validated before any replacement or
        unlink, so malformed state can never be repaired by overwriting it.
        """

        authorized = _validated_workspace_inventory(workspace_ids)
        observed_at = _validated_now(self._now())
        descriptor = self._open_lock()
        locked = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            current = self._read_unlocked(
                now=observed_at,
                allow_expired=True,
            )
            retained = tuple(
                document
                for document in current
                if document["workspace_id"] in authorized
                and not _is_expired_live_fence(document, now=observed_at)
            )
            removed = len(current) - len(retained)
            if removed == 0:
                return 0
            if retained:
                self._write_unlocked(_encode_documents(retained, now=observed_at))
            else:
                self._remove_unlocked()
            return removed
        except RevisionFenceRegistryError:
            raise
        except OSError as exc:
            raise RevisionFenceRegistryError(
                "revision fence registry reconciliation failed"
            ) from exc
        finally:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            else:
                os.close(descriptor)

    def _open_lock(self) -> int:
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
        except OSError as exc:
            raise RevisionFenceRegistryError(
                "revision fence registry lock is unavailable"
            ) from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_mode & 0o022
            ):
                raise RevisionFenceRegistryError(
                    "revision fence registry lock is unsafe"
                )
            os.fchmod(descriptor, 0o600)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _read_unlocked(
        self,
        *,
        now: datetime,
        allow_expired: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            return ()
        except OSError as exc:
            raise RevisionFenceRegistryError(
                "revision fence registry is not a safe regular file"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or before.st_mode & 0o022
                or not 2 <= before.st_size <= MAX_REVISION_FENCE_BYTES
            ):
                raise RevisionFenceRegistryError(
                    "revision fence registry type, mode, or size is unsafe"
                )
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            raw = b"".join(chunks)
            try:
                current = os.lstat(self._path)
            except OSError as exc:
                raise RevisionFenceRegistryError(
                    "revision fence registry changed while being read"
                ) from exc
            identity = (before.st_dev, before.st_ino)
            if (
                identity != (after.st_dev, after.st_ino)
                or identity != (current.st_dev, current.st_ino)
                or stat.S_ISLNK(current.st_mode)
                or before.st_size != after.st_size
                or before.st_size != current.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_mtime_ns != current.st_mtime_ns
                or len(raw) != before.st_size
            ):
                raise RevisionFenceRegistryError(
                    "revision fence registry changed while being read"
                )
        finally:
            os.close(descriptor)
        return _decode_revision_fence_registry(
            raw,
            now=now,
            allow_expired=allow_expired,
        )

    def _write_unlocked(self, raw: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".property-catalog-fence-registry-",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        keep = True
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise RevisionFenceRegistryError(
                        "revision fence registry write was incomplete"
                    )
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self._path)
            keep = False
            self._fsync_parent_directory()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if keep:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _remove_unlocked(self) -> None:
        try:
            os.unlink(self._path)
        except FileNotFoundError as exc:
            raise RevisionFenceRegistryError(
                "revision fence registry changed before removal"
            ) from exc
        self._fsync_parent_directory()

    def _fsync_parent_directory(self) -> None:
        directory_flags = os.O_RDONLY
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(self._path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _encode_documents(
    documents: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    allow_expired: bool = False,
) -> bytes:
    if not documents:
        raise RevisionFenceRegistryError(
            "revision fence registry must contain at least one entry"
        )
    validated = tuple(
        sorted(
            (
                _validated_fence_document(
                    value,
                    now=now,
                    allow_expired=allow_expired,
                )
                for value in documents
            ),
            key=_tenant_key,
        )
    )
    keys = tuple(_tenant_key(document) for document in validated)
    if len(set(keys)) != len(keys):
        raise RevisionFenceRegistryError(
            "revision fence registry contains duplicate tenant scopes"
        )
    document = {
        "format": REVISION_FENCE_FORMAT,
        "version": REVISION_FENCE_VERSION,
        "fences": list(validated),
    }
    raw = (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    if len(raw) > MAX_REVISION_FENCE_BYTES:
        raise RevisionFenceRegistryError(
            "revision fence registry exceeds its byte limit"
        )
    return raw


def _validated_fence_document(
    value: Mapping[str, Any],
    *,
    now: datetime,
    allow_expired: bool = False,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(_FENCE_FIELDS):
        raise RevisionFenceRegistryError("revision fence fields are invalid")
    document = {field: value[field] for field in _FENCE_FIELDS}
    for field in ("organization_id", "workspace_id", "build_token"):
        raw_uuid = document[field]
        if type(raw_uuid) is not str:
            raise RevisionFenceRegistryError(f"revision fence {field} is invalid")
        try:
            canonical = canonical_uuid(raw_uuid, field=f"revision fence {field}")
        except ValueError as exc:
            raise RevisionFenceRegistryError(
                f"revision fence {field} is invalid"
            ) from exc
        if canonical != raw_uuid:
            raise RevisionFenceRegistryError(f"revision fence {field} is not canonical")
    _positive_uint(document["catalog_epoch"], 16, "catalog_epoch")
    _positive_uint(document["catalog_revision"], 64, "catalog_revision")
    _positive_uint(document["projection_version"], 16, "projection_version")
    _lower_sha256(document["build_lease_sha256"], "build_lease_sha256")
    project_ids = document["project_ids"]
    if type(project_ids) is not list or not 1 <= len(project_ids) <= (
        MAX_REVISION_FENCE_PROJECTS
    ):
        raise RevisionFenceRegistryError(
            "revision fence project inventory must contain 1..256 entries"
        )
    if any(type(project_id) is not str for project_id in project_ids):
        raise RevisionFenceRegistryError("revision fence project inventory is invalid")
    canonical_projects: list[str] = []
    for project_id in project_ids:
        try:
            canonical_project = canonical_uuid(
                project_id,
                field="revision fence project_id",
            )
        except ValueError as exc:
            raise RevisionFenceRegistryError(
                "revision fence project inventory is invalid"
            ) from exc
        if canonical_project != project_id:
            raise RevisionFenceRegistryError(
                "revision fence project inventory is not canonical"
            )
        canonical_projects.append(canonical_project)
    if canonical_projects != sorted(canonical_projects) or len(
        set(canonical_projects)
    ) != len(canonical_projects):
        raise RevisionFenceRegistryError(
            "revision fence project inventory is not sorted and unique"
        )
    for field in ("span_since_us", "span_until_us"):
        _positive_uint(document[field], 64, field)
    if document["span_since_us"] >= document["span_until_us"]:
        raise RevisionFenceRegistryError("revision fence span window is not increasing")
    issued_at = _canonical_time(document["issued_at"], "issued_at")
    expires_at = _canonical_time(document["expires_at"], "expires_at")
    if expires_at <= issued_at:
        raise RevisionFenceRegistryError(
            "revision fence expiry must follow its issue time"
        )
    _uint(document["fenced_sequence"], 64, "fenced_sequence")
    status = document["status"]
    if type(status) is not str or status not in {
        "building",
        "draining",
        "fenced",
    }:
        raise RevisionFenceRegistryError("revision fence status is invalid")
    drain_text = document["drain_deadline"]
    if type(drain_text) is not str:
        raise RevisionFenceRegistryError("revision fence drain_deadline is invalid")
    if status == "building":
        if not allow_expired and expires_at <= now:
            raise RevisionFenceRegistryError("building revision fence is expired")
        if drain_text or document["fenced_sequence"] != 0:
            raise RevisionFenceRegistryError(
                "building revision fence carries a drain boundary"
            )
    elif status == "draining":
        drain_deadline = _canonical_time(drain_text, "drain_deadline")
        if (
            (not allow_expired and drain_deadline <= now)
            or drain_deadline <= issued_at
            or drain_deadline - issued_at > _MAX_DRAIN_LEASE
        ):
            raise RevisionFenceRegistryError(
                "draining revision fence deadline is expired or too wide"
            )
    elif drain_text:
        if _canonical_time(drain_text, "drain_deadline") <= issued_at:
            raise RevisionFenceRegistryError(
                "fenced revision deadline does not follow its issue time"
            )
    elif document["fenced_sequence"] != 0:
        raise RevisionFenceRegistryError("fenced sequence requires a drain deadline")
    _lower_sha256(document["fence_sha256"], "fence_sha256")
    if document["fence_sha256"] != _fence_sha256(document):
        raise RevisionFenceRegistryError(
            "revision fence digest does not match its assignment"
        )
    return document


def _is_expired_live_fence(
    document: Mapping[str, Any],
    *,
    now: datetime,
) -> bool:
    status = document["status"]
    if status == "building":
        return _canonical_time(document["expires_at"], "expires_at") <= now
    if status == "draining":
        return _canonical_time(document["drain_deadline"], "drain_deadline") <= now
    return False


def _fence_sha256(document: Mapping[str, Any]) -> str:
    project_ids = document["project_ids"]
    return framed_sha256(
        _FENCE_SHA_DOMAIN,
        document["organization_id"],
        document["workspace_id"],
        document["catalog_epoch"],
        document["catalog_revision"],
        document["projection_version"],
        document["build_lease_sha256"],
        document["build_token"],
        len(project_ids),
        *project_ids,
        document["span_since_us"],
        document["span_until_us"],
        document["issued_at"],
        document["expires_at"],
        document["drain_deadline"],
        document["fenced_sequence"],
        document["status"],
    )


def _canonical_time(value: Any, field: str) -> datetime:
    if type(value) is not str:
        raise RevisionFenceRegistryError(f"revision fence {field} is invalid")
    try:
        parsed = datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise RevisionFenceRegistryError(f"revision fence {field} is invalid") from exc
    if parsed.strftime(_TIME_FORMAT) != value:
        raise RevisionFenceRegistryError(f"revision fence {field} is not canonical")
    return parsed


def _validated_now(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("revision fence clock must be timezone-aware")
    return value.astimezone(UTC)


def _tenant_key(document: Mapping[str, Any]) -> tuple[str, str]:
    return str(document["organization_id"]), str(document["workspace_id"])


def _validated_workspace_inventory(values: Sequence[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("authorized workspace inventory must be a sequence")
    # The authorization inventory is a membership filter and is never encoded
    # into the fence document. Its size follows the active workspace inventory;
    # the canonical registry remains protected by its independent byte bound.
    canonical_values: list[str] = []
    for value in values:
        if type(value) is not str:
            raise RevisionFenceRegistryError(
                "authorized workspace inventory contains a non-string value"
            )
        try:
            canonical = canonical_uuid(value, field="authorized workspace_id")
        except ValueError as exc:
            raise RevisionFenceRegistryError(
                "authorized workspace inventory contains an invalid UUID"
            ) from exc
        if canonical != value:
            raise RevisionFenceRegistryError(
                "authorized workspace inventory is not canonical"
            )
        canonical_values.append(canonical)
    if len(set(canonical_values)) != len(canonical_values):
        raise RevisionFenceRegistryError(
            "authorized workspace inventory contains duplicates"
        )
    return frozenset(canonical_values)


def _positive_uint(value: Any, bits: int, field: str) -> None:
    if type(value) is not int or not 1 <= value < (1 << bits):
        raise RevisionFenceRegistryError(
            f"revision fence {field} must be a positive UInt{bits}"
        )


def _uint(value: Any, bits: int, field: str) -> None:
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise RevisionFenceRegistryError(f"revision fence {field} must be a UInt{bits}")


def _lower_sha256(value: Any, field: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RevisionFenceRegistryError(
            f"revision fence {field} must be lowercase SHA-256"
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RevisionFenceRegistryError(
                "revision fence JSON contains a duplicate key"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise RevisionFenceRegistryError(
        f"revision fence JSON constant {value!r} is invalid"
    )


__all__ = [
    "AtomicMultiTenantFenceFile",
    "MAX_REVISION_FENCE_BYTES",
    "REVISION_FENCE_FORMAT",
    "REVISION_FENCE_VERSION",
    "RevisionFenceRegistryError",
    "decode_revision_fence_registry",
    "encode_revision_fence_registry",
]
