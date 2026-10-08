"""Durable, private JSON outbox for product analytics events."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from tfc.deployment_telemetry.buffer import _trusted, _refuse
from tfc.deployment_telemetry.config import get_telemetry_buffer_dir
from tfc.deployment_telemetry.config import BUFFER_RETENTION_DAYS

logger = structlog.get_logger(__name__)

EVENT_BUFFER_PREFIX = "event-"


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
    """Atomically persist one event, using event_id as the dedupe key."""
    event_id = UUID(str(event["event_id"]))
    directory = _ensure_event_dir()
    destination = directory / f"{EVENT_BUFFER_PREFIX}{event_id}.json"
    if destination.exists():
        return destination

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


def pending_events(limit: int = 50) -> list[Path]:
    directory = get_telemetry_buffer_dir() / "events"
    if not _trusted(directory):
        _refuse(directory)
        return []
    try:
        return sorted(directory.glob(f"{EVENT_BUFFER_PREFIX}*.json"))[:limit]
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


def clear_events() -> int:
    removed = 0
    for path in pending_events(limit=10_000):
        delete_event(path)
        removed += 1
    return removed


def prune_events(now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=BUFFER_RETENTION_DAYS)
    removed = 0
    for path in pending_events(limit=10_000):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                delete_event(path)
                removed += 1
        except OSError:
            continue
    return removed
