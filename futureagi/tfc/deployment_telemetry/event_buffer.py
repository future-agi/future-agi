"""Durable, private JSON outbox for product analytics events."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from tfc.deployment_telemetry.buffer import _refuse, _trusted
from tfc.deployment_telemetry.config import (
    BUFFER_RETENTION_DAYS,
    get_telemetry_buffer_dir,
)

logger = structlog.get_logger(__name__)

EVENT_BUFFER_PREFIX = "event-"


@contextmanager
def event_flush_lock():
    """Try to acquire an inter-process lock for event delivery.

    The API process and a distributed Temporal worker share the durable event
    volume.  A Python ``threading.Lock`` cannot coordinate those processes, so
    use an advisory filesystem lock around the network POST and deletes.  If a
    platform has no ``fcntl`` support, retain the in-process behavior rather
    than making telemetry prevent application startup.
    """
    directory = _ensure_event_dir()
    lock_path = directory / ".flush.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        os.chmod(lock_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    try:
        try:
            import fcntl
        except ImportError:
            # Windows has no fcntl; retain best-effort behavior there.
            yield True
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another process owns the lock; the next scheduled flush retries.
            yield False
            return
        except OSError:
            # An unsupported filesystem should not prevent application startup.
            yield True
            return
        yield True
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def _ensure_event_dir() -> Path:
    directory = get_telemetry_buffer_dir() / "events"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _trusted(directory):
        _refuse(directory)
        raise PermissionError("deployment telemetry buffer directory is not private")
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory


def store_event(event: dict) -> Path:
    """Atomically persist one event, using event_id as the dedupe key.

    Include the install id in the filename.  A bare-metal development install
    defaults to a shared temp directory, and two installs on one host must not
    consume or delete one another's outbox files.
    """
    event_id = UUID(str(event["event_id"]))
    directory = _ensure_event_dir()
    try:
        instance_id = str(UUID(str(event.get("instance_id"))))
    except (TypeError, ValueError, AttributeError):
        instance_id = "legacy"
    destination = directory / (f"{EVENT_BUFFER_PREFIX}{instance_id}-{event_id}.json")
    legacy_destination = directory / f"{EVENT_BUFFER_PREFIX}{event_id}.json"
    if destination.exists():
        if legacy_destination.exists() and instance_id != "legacy":
            try:
                legacy_payload = json.loads(
                    legacy_destination.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                legacy_payload = None
            if (
                isinstance(legacy_payload, dict)
                and str(legacy_payload.get("instance_id")) == instance_id
            ):
                try:
                    legacy_destination.unlink(missing_ok=True)
                except OSError:
                    pass
        return destination

    # Migrate an unscoped event from an earlier release when it belongs to this
    # install, so an outage-buffered event is not stranded after an upgrade.
    if instance_id != "legacy" and legacy_destination.exists():
        try:
            legacy_payload = json.loads(legacy_destination.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            legacy_payload = None
        if (
            isinstance(legacy_payload, dict)
            and str(legacy_payload.get("instance_id")) == instance_id
        ):
            try:
                os.replace(legacy_destination, destination)
                return destination
            except OSError:
                pass

    # Include a random suffix so concurrent request threads in one process
    # cannot collide while atomically writing different events.
    temporary = directory / f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
    body = json.dumps(event, separators=(",", ":"), ensure_ascii=True)
    try:
        with temporary.open("x", encoding="utf-8") as file:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            file.write(body)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def pending_events(
    limit: int = 50, instance_id: UUID | str | None = None
) -> list[Path]:
    directory = get_telemetry_buffer_dir() / "events"
    if not _trusted(directory):
        _refuse(directory)
        return []
    try:
        if instance_id is None:
            return sorted(directory.glob(f"{EVENT_BUFFER_PREFIX}*.json"))[:limit]

        scoped_prefix = f"{EVENT_BUFFER_PREFIX}{instance_id}-"
        scoped = list(directory.glob(f"{scoped_prefix}*.json"))
        scoped_event_ids = {
            path.name[len(scoped_prefix) : -len(".json")] for path in scoped
        }
        # Legacy files used ``event-<event_id>.json``. Read only legacy
        # payloads belonging to this install so a shared temp directory stays
        # isolated while upgrades can still drain an outage buffer.
        legacy = []
        for path in directory.glob(f"{EVENT_BUFFER_PREFIX}*.json"):
            if path.name.startswith(scoped_prefix):
                continue
            if path.name.endswith(".json"):
                legacy_event_id = path.name[
                    len(EVENT_BUFFER_PREFIX) : -len(".json")
                ]
                if legacy_event_id in scoped_event_ids:
                    continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and str(payload.get("instance_id")) == str(
                instance_id
            ):
                legacy.append(path)
        return sorted(scoped + legacy)[:limit]
    except OSError:
        logger.warning("deployment_telemetry_event_buffer_list_failed")
        return []


def load_event(path: Path) -> dict | None:
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("deployment_telemetry_event_buffer_read_failed")
        return None
    return event if isinstance(event, dict) else None


def delete_event(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("deployment_telemetry_event_buffer_delete_failed")


def clear_events(instance_id: UUID | str | None = None) -> int:
    removed = 0
    for path in pending_events(limit=10_000, instance_id=instance_id):
        delete_event(path)
        removed += 1
    return removed


def prune_events(
    now: datetime | None = None,
    instance_id: UUID | str | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=BUFFER_RETENTION_DAYS)
    removed = 0
    for path in pending_events(limit=10_000, instance_id=instance_id):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                delete_event(path)
                removed += 1
        except OSError:
            continue
    return removed
