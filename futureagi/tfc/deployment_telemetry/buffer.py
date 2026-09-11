from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import structlog

from tfc.deployment_telemetry.config import (
    BUFFER_FLUSH_BATCH_SIZE,
    BUFFER_RETENTION_DAYS,
    get_telemetry_buffer_dir,
)

logger = structlog.get_logger(__name__)


def _window_filename(window_start: datetime, window_end: datetime) -> str:
    start = window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    end = window_end.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{start}_{end}.json"


def _ensure_buffer_dir() -> Path:
    buffer_dir = get_telemetry_buffer_dir()
    buffer_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        buffer_dir.chmod(0o700)
    except OSError:
        pass
    return buffer_dir


def store_window(
    window_start: datetime,
    window_end: datetime,
    payload: dict,
) -> Path:
    buffer_dir = _ensure_buffer_dir()
    destination = buffer_dir / _window_filename(window_start, window_end)
    if destination.exists():
        return destination

    temporary = buffer_dir / (f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    try:
        with temporary.open("x", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
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


def pending_windows(limit: int = BUFFER_FLUSH_BATCH_SIZE) -> list[Path]:
    try:
        return sorted(get_telemetry_buffer_dir().glob("*.json"))[:limit]
    except OSError:
        logger.warning("deployment_telemetry_buffer_list_failed")
        return []


def load_window(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("deployment_telemetry_buffer_read_failed")
        return None
    return payload if isinstance(payload, dict) else None


def delete_window(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("deployment_telemetry_buffer_delete_failed")


def clear_buffer() -> int:
    removed = 0
    for path in pending_windows(limit=10_000):
        delete_window(path)
        removed += 1
    return removed


def prune_expired_windows(now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=BUFFER_RETENTION_DAYS)
    removed = 0
    try:
        paths = get_telemetry_buffer_dir().glob("*.json")
        for path in paths:
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            except OSError:
                continue
            if modified_at < cutoff:
                delete_window(path)
                removed += 1
    except OSError:
        logger.warning("deployment_telemetry_buffer_prune_failed")
    return removed
