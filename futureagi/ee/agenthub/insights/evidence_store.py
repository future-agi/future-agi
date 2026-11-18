"""Where a run's unit snapshots wait for the Insights agent to read them.

The Temporal worker builds each unit's snapshot (``broker.prebuild``) before
the agent is asked to read the unit and puts it here, once per run; the
evidence broker on every web pod serves the agent from here
(``broker.snapshot``). A unit is one document: its canonical snapshot
(manifest and fields) and where its media lives (``media_sources``, for the
media op), as gzip-compressed canonical JSON named after the run and the
sha256 of the unit's key. Its digest is checked whenever it is read back; a
document that fails the check is never served.

``INSIGHTS_EVIDENCE_STORE`` says where:

- ``local`` (the default): a directory of this host,
  ``INSIGHTS_EVIDENCE_CACHE_DIR`` (0700 directories, 0600 files), the oldest
  files going first past ``INSIGHTS_EVIDENCE_CACHE_MAX_BYTES``. Another host
  never sees it, so the broker builds what it does not find, as each web pod
  did before. One host running both the API and the worker (Standalone)
  serves what the worker built.
- ``object``: the platform's object storage (``tfc.utils.storage_client``),
  in the private bucket ``INSIGHTS_EVIDENCE_BUCKET`` under
  ``insights-evidence/<run>/``. Every web pod reads what the worker put
  there. Never the upload bucket, whose objects anyone may read by their URL:
  that, or no bucket, is refused (``configuration_error``), and so is a
  bucket whose policy lets anyone in (``probe``, when a run is created, and
  on first use). Server-side encryption is asked for on S3; GCS always
  encrypts at rest; MinIO encrypts when its operator set it up. The bucket
  belongs to one deployment: its sweep deletes the documents of every run
  its own database does not know.

A run's documents go when it ends (``delete_run``, from finish and fail, on
the worker) and in a sweep (``sweep``), at most once a minute per process, at
every broker call and every pre-build and, while this process holds anything
(or this host's directory does), on a timer: runs that failed, were deleted
or are gone; runs whose files sat for ``CACHE_TTL_S`` (local) or that were
created that long ago (bucket); completed runs once no question is reading
them (``ENDED_GRACE_S`` after the last write here, or once their ``hold``
lapses in the bucket). The bucket's lifecycle rule (delete after two days)
is the backstop. No customer content is stored in the database.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
import zlib
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

BACKENDS = ("local", "object")
PREFIX = "insights-evidence"
CACHE_TTL_S = 6 * 3600
DEFAULT_CACHE_MAX_BYTES = 2 * 1024**3
# A completed report's documents outlive their last write (local) or its last
# question's hold (object) by this much.
ENDED_GRACE_S = 5 * 60
SWEEP_INTERVAL_S = 60
# Snapshots this process keeps parsed, by size; the newest is always kept.
RECENT_BYTES = 128 * 1024 * 1024
# A unit's document, compressed or not, is at most a 32 MiB snapshot plus its
# media's sources: far below this.
MAX_DOCUMENT_BYTES = 256 * 1024 * 1024
# How long a run's creation waits to learn whether the bucket lets anyone in
# (``probe``); a bucket that has not answered by then is checked on first use.
POLICY_PROBE_S = 5.0
# Statuses of a run whose documents may still be read.
_LIVE = ("pending", "running", "completed")


class EvidenceStoreUnavailable(Exception):
    """The store could not be read or written just now (``code`` is the
    broker's 503 code)."""

    code = "cache_unavailable"


class EvidenceStoreMisconfigured(EvidenceStoreUnavailable):
    """The store refuses to start: ``reason`` says why."""

    code = "store_misconfigured"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Cached:
    """A unit's snapshot document and where its media lives."""

    document: dict[str, Any]
    media_sources: dict[str, Any]


@dataclass(frozen=True)
class RunState:
    """What a sweep needs to know of a run."""

    status: str
    deleted: bool
    created_at: datetime | None = None
    finished_at: datetime | None = None


def _setting(name: str, default: Any = None) -> Any:
    from django.conf import settings

    value = getattr(settings, name, None)
    return default if value in (None, "") else value


def _backend() -> str:
    return str(_setting("INSIGHTS_EVIDENCE_STORE", "local")).strip().lower()


def _bucket() -> str:
    return str(_setting("INSIGHTS_EVIDENCE_BUCKET", "")).strip()


def configuration_error() -> str | None:
    """Why the configured store refuses to start, or None. The object store
    needs its own bucket: never none, never the upload bucket."""
    backend = _backend()
    if backend not in BACKENDS:
        return "unknown_backend"
    if backend == "local":
        return None
    bucket = _bucket()
    if not bucket:
        return "bucket_unset"
    if bucket.lower() == str(_setting("UPLOAD_BUCKET_NAME", "")).strip().lower():
        return "bucket_is_upload_bucket"
    return None


def probe() -> str | None:
    """Why the configured store refuses to start, the bucket asked too: what
    ``configuration_error`` says, or ``bucket_is_public`` for a bucket whose
    policy lets anyone in. A bucket this process has checked is not asked
    again; one that does not answer within ``POLICY_PROBE_S`` is left to its
    first use, which refuses it then."""
    reason = configuration_error()
    if reason or _backend() != "object" or _bucket() in _checked_buckets:
        return reason
    found: list[str] = []

    def check():
        try:
            ObjectStore(_bucket())._client()
        except EvidenceStoreMisconfigured as refused:
            found.append(refused.reason)
        except Exception:
            # Can't tell now: first use decides.
            pass

    # The storage client waits minutes on an endpoint that does not answer.
    checking = threading.Thread(target=check, daemon=True)
    checking.start()
    checking.join(POLICY_PROBE_S)
    return found[0] if found else None


def current():
    """The configured store (``LocalStore`` or ``ObjectStore``). Raises
    EvidenceStoreMisconfigured when it refuses to start."""
    reason = configuration_error()
    if reason:
        raise EvidenceStoreMisconfigured(reason)
    if _backend() == "object":
        return ObjectStore(_bucket())
    return LocalStore(cache_root(), _cache_cap())


def delete_run(run_id, *, budget_s: float | None = None) -> None:
    """Delete a run's documents (a run that ended): from the bucket, or from
    this host's directory. What this process holds of it goes too. Returns
    within ``budget_s`` when given, however slow the store: the deleting goes
    on in the background for a while, and the sweep deletes what is left."""
    _forget(run_id)
    store = current()
    if budget_s is None:
        store.delete_run(run_id)
        return
    # The storage client waits minutes on an endpoint that does not answer,
    # and retries: the deleting runs on a thread of its own so the stage
    # that ends the run does not wait for it.
    deleting = threading.Thread(
        target=store.delete_run,
        args=(run_id,),
        kwargs={"budget_s": budget_s},
        daemon=True,
    )
    deleting.start()
    deleting.join(max(budget_s, 0))
    if deleting.is_alive():
        logger.warning("insight_evidence_store_delete_unfinished")


def release(run_id) -> None:
    """Drop what this process and this host hold of a run that is no longer
    served; a bucket's documents wait for ``delete_run`` or the sweep."""
    _forget(run_id)
    with contextlib.suppress(EvidenceStoreMisconfigured):
        store = current()
        if not store.shared:
            store.delete_run(run_id)


# ─── Documents ──────────────────────────────────────────────────────────────
def _run_name(run_id) -> str:
    return str(uuid.UUID(str(run_id)))


def _unit_name(key: str) -> str:
    return f"{hashlib.sha256(str(key).encode()).hexdigest()}.json.gz"


def _pack(cached: Cached) -> tuple[bytes, int]:
    """The document as stored (gzip of its canonical JSON), and the size of
    that JSON."""
    from ee.agenthub.insights.evidence import canonical_json

    raw = canonical_json(
        {"document": cached.document, "media_sources": cached.media_sources}
    ).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0), len(raw)


def _inflate(data: bytes) -> bytes | None:
    """``data`` decompressed, unless it is not one whole gzip member or
    inflates past MAX_DOCUMENT_BYTES."""
    inflater = zlib.decompressobj(wbits=31)
    try:
        raw = inflater.decompress(data, MAX_DOCUMENT_BYTES + 1)
    except zlib.error:
        return None
    if len(raw) > MAX_DOCUMENT_BYTES or not inflater.eof or inflater.unused_data:
        return None
    return raw


def _unpack(data: bytes, key: str) -> tuple[Cached, int] | None:
    """The stored document of unit ``key`` and its size, or None when it is
    not one: unreadable, another unit's, or its digest does not match."""
    from ee.agenthub.insights.evidence import verify_snapshot

    raw = _inflate(data)
    if raw is None:
        return None
    try:
        stored = json.loads(raw)
        document, sources = stored["document"], stored["media_sources"]
        if not (
            isinstance(sources, dict)
            and isinstance(document["fields"], dict)
            and document["manifest"]["unit"]["key"] == str(key)
            and verify_snapshot(document)
        ):
            return None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return Cached(document, sources), len(raw)


# ─── What this process holds ────────────────────────────────────────────────
@dataclass
class _Recent:
    """A snapshot this process holds parsed: ``stamp`` is the (mtime, size)
    of the local file it came from, None otherwise; ``at`` when it was
    written, built or fetched, which its age counts from."""

    stamp: tuple[int, int] | None
    cached: Cached
    size: int
    at: float


_recent: OrderedDict[str, _Recent] = OrderedDict()
_recent_lock = threading.Lock()


def _memory_key(run_id, key: str) -> str:
    return f"{_run_name(run_id)}/{_unit_name(key)}"


def _held(run_id, key: str, now: float) -> _Recent | None:
    name = _memory_key(run_id, key)
    with _recent_lock:
        held = _recent.get(name)
        if held is not None and now - held.at > CACHE_TTL_S:
            _recent.pop(name, None)
            held = None
        if held is not None:
            _recent.move_to_end(name)
    return held


def recall(run_id, key: str) -> Cached | None:
    """The unit's document as this process holds it, whatever the store
    holds now."""
    held = _held(run_id, key, time.time())
    return held.cached if held is not None else None


def _remember(
    run_id, key: str, stamp, cached: Cached, size: int, at: float | None = None
) -> None:
    name = _memory_key(run_id, key)
    with _recent_lock:
        _recent.pop(name, None)
        _recent[name] = _Recent(stamp, cached, size, at or time.time())
        total = sum(entry.size for entry in _recent.values())
        while len(_recent) > 1 and total > RECENT_BYTES:
            _, dropped = _recent.popitem(last=False)
            total -= dropped.size
    _arm_sweeper()


def _forget(run_id) -> None:
    try:
        prefix = f"{_run_name(run_id)}/"
    except ValueError:
        return
    with _recent_lock:
        for name in [k for k in _recent if k.startswith(prefix)]:
            _recent.pop(name, None)


def _held_runs() -> dict[str, float]:
    """The runs this process holds snapshots of, with when it last took one."""
    runs: dict[str, float] = {}
    with _recent_lock:
        for name, held in _recent.items():
            run_id = name.split("/", 1)[0]
            runs[run_id] = max(runs.get(run_id, 0.0), held.at)
    return runs


# ─── Local: a directory of this host ────────────────────────────────────────
def cache_root() -> Path:
    root = _setting("INSIGHTS_EVIDENCE_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "insights-evidence"
    )
    return Path(root)


def _cache_cap() -> int:
    return int(_setting("INSIGHTS_EVIDENCE_CACHE_MAX_BYTES", DEFAULT_CACHE_MAX_BYTES))


def _private_dir(path: Path) -> Path:
    """``path`` as a directory only this process's user can enter: created
    0700, or refused when it is a link or someone else's."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _owned(path):
        raise EvidenceStoreUnavailable()
    if stat.S_IMODE(os.lstat(path).st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def _owned(path: Path) -> bool:
    """A real directory of this process's user, not a link to one."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()


def _is_uuid(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


class LocalStore:
    """Unit documents in a directory of this host. A cache: a document it
    cannot take is not stored, and nothing here raises for want of room."""

    shared = False

    def __init__(self, root: Path, cap: int):
        self.root = root
        self.cap = cap

    def run_dir(self, run_id) -> Path:
        return self.root / _run_name(run_id)

    def path(self, run_id, key: str) -> Path:
        return self.run_dir(run_id) / _unit_name(key)

    def put(self, run_id, key: str, cached: Cached, *, remember=False) -> int:
        """Write a unit's document, the oldest files of any run making room
        for it; the bytes written, 0 when the directory cannot take it."""
        data, size = _pack(cached)
        path = self.path(run_id, key)
        stamp = None
        try:
            stamp = self._write(path, data)
        except (OSError, EvidenceStoreUnavailable):
            logger.warning("insight_evidence_cache_write_failed", exc_info=True)
        if remember:
            # Held in memory too, so a unit the disk could not take is not
            # built again.
            _remember(run_id, key, stamp, cached, size)
        if stamp:
            # A worker writes without holding anything: its files expire too.
            _arm_sweeper()
        return len(data) if stamp else 0

    def _write(self, path: Path, data: bytes) -> tuple[int, int] | None:
        _private_dir(self.root)
        _private_dir(path.parent)
        if not self._make_room(len(data)):
            return None
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        info = os.lstat(path)
        return info.st_mtime_ns, info.st_size

    def get(self, run_id, key: str) -> Cached | None:
        """The unit's document as this process holds it or this host's
        directory does, unless older than ``CACHE_TTL_S``."""
        now = time.time()
        held = _held(run_id, key, now)
        path = self.path(run_id, key)
        # Only what this user wrote: on a shared temp directory someone else
        # could have planted the directories first.
        if not (_owned(self.root) and _owned(path.parent)):
            return held.cached if held is not None else None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            # Never written, or swept since: what this process holds is the
            # same.
            return held.cached if held is not None else None
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if now - info.st_mtime > CACHE_TTL_S:
                return None
            stamp = (info.st_mtime_ns, info.st_size)
            if held is not None and held.stamp == stamp:
                return held.cached
            unpacked = _unpack(handle.read(MAX_DOCUMENT_BYTES + 1), key)
        if unpacked is None:
            logger.warning("insight_evidence_document_invalid", backend="local")
            path.unlink(missing_ok=True)
            return None
        cached, size = unpacked
        _remember(run_id, key, stamp, cached, size, at=info.st_mtime)
        return cached

    def has(self, run_id, key: str) -> bool:
        if _held(run_id, key, time.time()) is not None:
            return True
        path = self.path(run_id, key)
        if not (_owned(self.root) and _owned(path.parent)):
            return False
        try:
            info = os.lstat(path)
        except OSError:
            return False
        fresh = time.time() - info.st_mtime <= CACHE_TTL_S
        return stat.S_ISREG(info.st_mode) and fresh

    def delete_run(self, run_id, *, budget_s: float | None = None) -> None:
        _forget(run_id)
        try:
            path = self.run_dir(run_id)
        except ValueError:
            return
        shutil.rmtree(path, ignore_errors=True)

    def _make_room(self, need: int) -> bool:
        """Delete the oldest unit files, of whichever run (the one being read
        too), until ``need`` more bytes fit the cap. Files still being
        written count but stay. False when it cannot fit."""
        if need > self.cap:
            return False
        files, total = [], 0
        for name, info in self._unit_files():
            total += info.st_size
            if not os.path.basename(name).startswith("."):
                files.append((info.st_mtime, name, info.st_size))
        for _, path, size in sorted(files):
            if total + need <= self.cap:
                break
            with contextlib.suppress(OSError):
                os.unlink(path)
                total -= size
        return total + need <= self.cap

    def _unit_files(self):
        """(path, stat) of every file in a run directory; one deleted
        meanwhile (another process's sweep) is skipped."""
        for run_dir in os.scandir(self.root):
            if not run_dir.is_dir(follow_symlinks=False):
                continue
            try:
                items = list(os.scandir(run_dir.path))
            except OSError:
                continue
            for item in items:
                try:
                    if item.is_file(follow_symlinks=False):
                        yield item.path, item.stat(follow_symlinks=False)
                except OSError:
                    continue

    def run_dirs(self) -> list[os.DirEntry]:
        """The run directories (named by run id), when the root is ours."""
        if not _owned(self.root):
            return []
        with os.scandir(self.root) as entries:
            return [
                entry
                for entry in entries
                if entry.is_dir(follow_symlinks=False) and _is_uuid(entry.name)
            ]

    def sweep(self, now: float, states: Callable) -> None:
        """Forget every run whose files are older than ``CACHE_TTL_S`` or that
        has ended: failed, deleted or gone, or completed with nothing written
        for ``ENDED_GRACE_S``. Then hold the directory to its cap."""
        written = {
            entry.name: entry.stat(follow_symlinks=False).st_mtime
            for entry in self.run_dirs()
        }
        for run_id, at in _held_runs().items():
            written[run_id] = max(written.get(run_id, 0.0), at)
        if not written:
            return
        known = _known(states, list(written))
        for run_id, at in written.items():
            if _idle_ended(known, run_id, now - at):
                self.delete_run(run_id)
        if _owned(self.root):
            self._make_room(0)

    def holds_anything(self) -> bool:
        try:
            return bool(self.run_dirs())
        except OSError:
            # Can't tell: keep sweeping.
            return True


def _idle_ended(known: dict | None, run_id: str, idle_s: float) -> bool:
    if idle_s > CACHE_TTL_S:
        return True
    if known is None:
        # The database did not answer: age alone decides.
        return False
    state = known.get(run_id)
    if state is None or state.deleted or state.status not in _LIVE:
        return True
    return state.status == "completed" and idle_s > ENDED_GRACE_S


# ─── Object: the platform's object storage ──────────────────────────────────
# Buckets this process has looked at the policy of.
_checked_buckets: set[str] = set()
_NOT_FOUND = ("NoSuchKey", "NoSuchObject", "NotFound")
# A policy read's answer that there is none to read: none set, not these
# credentials' to read, or none on this backend (GCS keeps access in IAM).
_NO_POLICY = (
    "NoSuchBucketPolicy",
    "AccessDenied",
    "NotImplemented",
    "MethodNotAllowed",
)
# Client errors that say nothing of the policy: a bucket not found, a request
# timeout, throttling.
_RETRY_4XX = (404, 408, 429)


def _sse():
    """Server-side encryption for an upload, where the backend takes the
    header: S3's own keys. GCS encrypts every object at rest by itself, and a
    MinIO without a KMS refuses the header."""
    from tfc.utils import storage_client

    if storage_client.STORAGE_BACKEND != "s3":
        return None
    from minio.sse import SseS3

    return SseS3()


def _anyone(principal) -> bool:
    if principal == "*":
        return True
    if isinstance(principal, dict):
        aws = principal.get("AWS")
        return aws == "*" or (isinstance(aws, list) and "*" in aws)
    return False


def _public_policy(policy: str) -> bool:
    """Whether a bucket policy allows anyone at all to do anything."""
    try:
        statements = json.loads(policy).get("Statement") or []
    except (TypeError, ValueError, AttributeError):
        return False
    if isinstance(statements, dict):
        statements = [statements]
    return any(
        isinstance(s, dict)
        and s.get("Effect") == "Allow"
        and _anyone(s.get("Principal"))
        for s in statements
    )


def _no_policy(exc: Exception) -> bool:
    """Whether a failed policy read is the server saying there is no policy
    for these credentials to read, not a failure to ask (a timeout, a 5xx,
    throttling, a bucket that is not there)."""
    if getattr(exc, "code", None) in _NO_POLICY:
        return True
    status = getattr(getattr(exc, "response", None), "status", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in _RETRY_4XX


class ObjectStore:
    """Unit documents in a private bucket that every pod reads, under
    ``insights-evidence/<run>/``."""

    shared = True

    def __init__(self, bucket: str):
        self.bucket = bucket

    def _client(self):
        from tfc.utils.storage_client import get_storage_client

        client = get_storage_client()
        if self.bucket not in _checked_buckets:
            self._check_policy(client)
        return client

    def _check_policy(self, client) -> None:
        """Refuse a bucket whose policy lets anyone in. A backend that keeps
        access elsewhere (GCS IAM), or credentials that may not read the
        policy, leave it to the operator. A read that fails otherwise leaves
        the bucket unchecked and the store unavailable: nothing is stored or
        read unchecked, and the next call asks again."""
        try:
            policy = client.get_bucket_policy(self.bucket)
        except Exception as exc:
            if not _no_policy(exc):
                logger.warning(
                    "insight_evidence_bucket_policy_unavailable", **_failure(exc)
                )
                raise EvidenceStoreUnavailable() from None
            logger.debug(
                "insight_evidence_bucket_policy_unread", error=type(exc).__name__
            )
            policy = None
        if policy and _public_policy(policy):
            logger.error("insight_evidence_bucket_public", bucket=self.bucket)
            raise EvidenceStoreMisconfigured("bucket_is_public")
        _checked_buckets.add(self.bucket)

    @staticmethod
    def prefix(run_id) -> str:
        return f"{PREFIX}/{_run_name(run_id)}/"

    def name(self, run_id, key: str) -> str:
        return self.prefix(run_id) + _unit_name(key)

    def put(self, run_id, key: str, cached: Cached, *, remember=False) -> int:
        """Upload a unit's document; the bytes uploaded. Raises
        EvidenceStoreUnavailable when the bucket did not take it (what is
        remembered stays remembered)."""
        data, size = _pack(cached)
        if remember:
            _remember(run_id, key, None, cached, size)
        name = self.name(run_id, key)
        try:
            self._client().put_object(
                self.bucket,
                name,
                io.BytesIO(data),
                len(data),
                content_type="application/gzip",
                sse=_sse(),
            )
        except EvidenceStoreUnavailable:
            raise
        except Exception as exc:
            logger.warning("insight_evidence_store_put_failed", **_failure(exc))
            raise EvidenceStoreUnavailable() from None
        return len(data)

    def get(self, run_id, key: str) -> Cached | None:
        """The unit's document as this process holds it or the bucket does;
        None when the bucket has none (or one that fails its digest)."""
        now = time.time()
        held = _held(run_id, key, now)
        if held is not None:
            return held.cached
        name = self.name(run_id, key)
        try:
            response = self._client().get_object(self.bucket, name)
        except EvidenceStoreUnavailable:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) in _NOT_FOUND:
                return None
            logger.warning("insight_evidence_store_get_failed", **_failure(exc))
            raise EvidenceStoreUnavailable() from None
        try:
            data = _read_capped(response)
        except Exception as exc:
            logger.warning("insight_evidence_store_get_failed", **_failure(exc))
            raise EvidenceStoreUnavailable() from None
        finally:
            response.close()
            release_conn = getattr(response, "release_conn", None)
            if callable(release_conn):
                release_conn()
        unpacked = _unpack(data, key) if data is not None else None
        if unpacked is None:
            logger.warning("insight_evidence_document_invalid", backend="object")
            with contextlib.suppress(Exception):
                self._client().remove_object(self.bucket, name)
            return None
        cached, size = unpacked
        _remember(run_id, key, None, cached, size, at=now)
        return cached

    def has(self, run_id, key: str) -> bool:
        """Whether the bucket holds the unit's document, whatever this process
        holds: another pod reads the bucket, never this one's memory."""
        try:
            self._client().stat_object(self.bucket, self.name(run_id, key))
        except EvidenceStoreUnavailable:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) in _NOT_FOUND:
                return False
            logger.warning("insight_evidence_store_stat_failed", **_failure(exc))
            raise EvidenceStoreUnavailable() from None
        return True

    def delete_run(self, run_id, *, budget_s: float | None = None) -> None:
        """Delete every document of the run, one object at a time (GCS takes
        no batch delete), for ``budget_s`` at most. Best effort: the sweep
        and the bucket's lifecycle rule catch what is left."""
        _forget(run_id)
        try:
            prefix = self.prefix(run_id)
        except ValueError:
            return
        until = None if budget_s is None else time.monotonic() + budget_s
        try:
            client = self._client()
            for item in client.list_objects(self.bucket, prefix=prefix, recursive=True):
                if until is not None and time.monotonic() > until:
                    logger.warning("insight_evidence_store_delete_unfinished")
                    return
                client.remove_object(self.bucket, item.object_name)
        except Exception as exc:
            logger.warning("insight_evidence_store_delete_failed", **_failure(exc))

    def run_ids(self) -> list[str]:
        """The runs the bucket holds documents of."""
        client = self._client()
        runs = []
        for item in client.list_objects(self.bucket, prefix=f"{PREFIX}/"):
            name = item.object_name[len(PREFIX) + 1 :].strip("/")
            if item.is_dir and _is_uuid(name):
                runs.append(name)
        return runs

    def sweep(self, now: float, states: Callable) -> None:
        """Delete the documents of every run that failed, was deleted or is
        gone, has run for longer than ``CACHE_TTL_S``, or completed and no
        question holds. Nothing when the database can't say: the bucket's
        lifecycle rule is the backstop."""
        runs = set(self.run_ids()) | set(_held_runs())
        if not runs:
            return
        known = _known(states, sorted(runs))
        if known is None:
            return
        for run_id in sorted(runs):
            if _object_ended(known.get(run_id), run_id, now):
                self.delete_run(run_id)

    def holds_anything(self) -> bool:
        return False


def _read_capped(response) -> bytes | None:
    """An object's bytes, or None past MAX_DOCUMENT_BYTES (never one of
    ours)."""
    chunks, total = [], 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_DOCUMENT_BYTES:
            return None
        chunks.append(chunk)


def _failure(exc: Exception) -> dict:
    """What a log line says of a storage failure: never its message, which
    can carry a URL."""
    return {"error": type(exc).__name__, "code": getattr(exc, "code", None)}


def _object_ended(state: RunState | None, run_id: str, now: float) -> bool:
    if state is None or state.deleted or state.status not in _LIVE:
        return True
    if state.status == "completed":
        if held(run_id):
            return False
        finished = state.finished_at
        return finished is None or now - finished.timestamp() > ENDED_GRACE_S
    created = state.created_at
    return created is not None and now - created.timestamp() > CACHE_TTL_S


# ─── Questions on a completed report ────────────────────────────────────────
def _hold_key(run_id) -> str:
    return f"insight-evidence-hold:{_run_name(run_id)}"


def hold(run_id, seconds: float) -> None:
    """Keep a completed run's documents in the bucket ``seconds`` longer: a
    question is about to read them."""
    from django.core.cache import cache

    try:
        cache.set(_hold_key(run_id), 1, timeout=max(1, int(seconds) + 1))
    except Exception:
        logger.warning("insight_evidence_hold_failed", exc_info=True)


def held(run_id) -> bool:
    """Whether a question holds a run's documents; True when the cache can't
    say, so nothing is deleted under a question."""
    from django.core.cache import cache

    try:
        return cache.get(_hold_key(run_id)) is not None
    except Exception:
        return True


# ─── Sweeping ───────────────────────────────────────────────────────────────
_sweep_lock = threading.Lock()
_sweeping = threading.Lock()
_swept_at = 0.0
_sweeper: threading.Timer | None = None


def _run_states(run_ids: list[str]) -> dict[str, RunState] | None:
    """Each run's status, whether it was deleted, and when it was created and
    finished; a deleted run reads as gone. None when the database can't
    say."""
    from tracer.models.insights import InsightRun

    try:
        rows = InsightRun.no_workspace_objects.filter(id__in=run_ids).values_list(
            "id", "status", "deleted", "created_at", "finished_at"
        )
        return {
            str(run_id): RunState(status, bool(deleted), created, finished)
            for run_id, status, deleted, created, finished in rows
        }
    except Exception:
        logger.warning("insight_evidence_sweep_states_unavailable", exc_info=True)
        return None


def _known(states: Callable, run_ids: list[str]) -> dict[str, RunState] | None:
    known = states(run_ids)
    if known is None:
        return None
    return {
        run_id: state if isinstance(state, RunState) else RunState(*state)
        for run_id, state in known.items()
    }


def sweep(*, force: bool = False, wait: bool = False) -> None:
    """Drop what the store must not keep (the store's ``sweep``), at most
    once every ``SWEEP_INTERVAL_S`` per process unless ``force``. A bucket is
    swept on a thread of its own unless ``wait``: a listing must not hold up
    the call that triggered it. Never raises."""
    global _swept_at
    now = time.time()
    with _sweep_lock:
        if not force and now - _swept_at < SWEEP_INTERVAL_S:
            return
        _swept_at = now
    try:
        store = current()
    except EvidenceStoreMisconfigured:
        return
    if store.shared and not wait:
        threading.Thread(target=_sweep_apart, args=(store, now), daemon=True).start()
        return
    _sweep_with(store, now)


def _sweep_with(store, now: float) -> None:
    if not _sweeping.acquire(blocking=False):
        return
    try:
        store.sweep(now, _run_states)
    except Exception:
        logger.warning("insight_evidence_sweep_failed", exc_info=True)
    finally:
        _sweeping.release()


def _sweep_apart(store, now: float) -> None:
    from django.db import connections

    try:
        _sweep_with(store, now)
    finally:
        # This thread's own database connection.
        connections.close_all()


def _holds_anything() -> bool:
    with _recent_lock:
        if _recent:
            return True
    try:
        return current().holds_anything()
    except EvidenceStoreMisconfigured:
        return False


def _arm_sweeper() -> None:
    """A timer that sweeps while this process holds anything, so documents
    expire on a pod the agent no longer calls."""
    global _sweeper
    with _sweep_lock:
        if _sweeper is not None:
            return
        _sweeper = threading.Timer(SWEEP_INTERVAL_S, _tick)
        _sweeper.daemon = True
        _sweeper.start()


def _tick() -> None:
    global _sweeper
    from django.db import connections

    try:
        sweep(force=True, wait=True)
    finally:
        # The timer's thread has its own database connection.
        connections.close_all()
        with _sweep_lock:
            _sweeper = None
    if _holds_anything():
        _arm_sweeper()
