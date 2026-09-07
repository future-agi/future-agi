"""Bounded durable local repair records shared by independent repair signals."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MAX_RECORD_BYTES = 4096


@contextmanager
def locked_file(path: Path):
    fd = os.open(
        str(path) + ".lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("repair lock must be a regular file")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def publish_file(path: Path, raw: bytes) -> None:
    if not raw or len(raw) > MAX_RECORD_BYTES:
        raise ValueError("repair record is empty or oversized")
    fd, temporary = tempfile.mkstemp(prefix=".catalog-repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as destination:
            destination.write(raw)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def utc_micros(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("repair observation requires UTC")
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def read_file(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise ValueError("repair must be a bounded regular file")
        raw = source.read(MAX_RECORD_BYTES + 1)
    if not raw or len(raw) > MAX_RECORD_BYTES:
        raise ValueError("repair is empty or oversized")
    return raw
