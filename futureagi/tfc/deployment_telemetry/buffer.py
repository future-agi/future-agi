from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

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


def _trusted(buffer_dir: Path) -> bool:
    """Whether ``buffer_dir`` is a real directory owned by this process's user.

    The default location is under the shared temp directory, where another
    local user (the standalone install's code-eval sandbox, for one) can create
    it first, then read the windows or plant its own for the sender to sign
    and send. A missing directory is fine: it is created with mode 0700.
    """
    try:
        info = os.lstat(buffer_dir)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    geteuid = getattr(os, "geteuid", None)
    return stat.S_ISDIR(info.st_mode) and (geteuid is None or info.st_uid == geteuid())


def _refuse(buffer_dir: Path) -> None:
    logger.warning(
        "deployment_telemetry_buffer_untrusted",
        path=str(buffer_dir),
        hint="not a directory owned by this user; set "
        "FUTURE_AGI_TELEMETRY_BUFFER_DIR to a private directory",
    )


def _ensure_buffer_dir() -> Path:
    buffer_dir = get_telemetry_buffer_dir()
    buffer_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _trusted(buffer_dir):
        _refuse(buffer_dir)
        raise PermissionError("deployment telemetry buffer directory is not private")
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
    # Include the install id in the filename.  The default bare-metal buffer
    # lives under the shared temp directory, so two local installs must not
    # overwrite or flush one another's heartbeat window.
    try:
        instance_id = str(UUID(str(payload.get("instance_id"))))
    except (TypeError, ValueError, AttributeError):
        instance_id = "legacy"
    destination = (
        buffer_dir / f"{instance_id}_{_window_filename(window_start, window_end)}"
    )
    # Migrate an unscoped window from an earlier release when it belongs to
    # this install.  This avoids sending the same fixed window twice after an
    # upgrade while leaving another install's legacy file untouched.
    legacy_destination = buffer_dir / _window_filename(window_start, window_end)
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


def pending_windows(
    limit: int = BUFFER_FLUSH_BATCH_SIZE,
    instance_id: str | None = None,
) -> list[Path]:
    buffer_dir = get_telemetry_buffer_dir()
    if not _trusted(buffer_dir):
        _refuse(buffer_dir)
        return []
    try:
        if instance_id is None:
            return sorted(buffer_dir.glob("*.json"))[:limit]

        scoped_prefix = f"{instance_id}_"
        scoped = list(buffer_dir.glob(f"{scoped_prefix}*.json"))
        scoped_names = {path.name[len(scoped_prefix) :] for path in scoped}
        # Before instance-scoped filenames were introduced, heartbeat windows
        # were flat timestamp files.  Keep retrying those legacy files only
        # when their payload belongs to this install; a second install sharing
        # the old default temp directory must never claim them.
        legacy = []
        for path in buffer_dir.glob("*.json"):
            if path.name.startswith(scoped_prefix):
                continue
            # If migration could not rename a legacy file, prefer the scoped
            # copy so a fixed window is never delivered twice.
            if path.name in scoped_names:
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


def clear_buffer(instance_id: str | None = None) -> int:
    removed = 0
    for path in pending_windows(limit=10_000, instance_id=instance_id):
        delete_window(path)
        removed += 1
    return removed


def prune_expired_windows(
    now: datetime | None = None,
    instance_id: str | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=BUFFER_RETENTION_DAYS)
    removed = 0
    buffer_dir = get_telemetry_buffer_dir()
    if not _trusted(buffer_dir):
        _refuse(buffer_dir)
        return removed
    try:
        paths = (
            pending_windows(limit=10_000, instance_id=instance_id)
            if instance_id is not None
            else buffer_dir.glob("*.json")
        )
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
