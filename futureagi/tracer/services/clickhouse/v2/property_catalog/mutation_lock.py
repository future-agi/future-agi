"""Explicit serialization boundary for DEV control-plane state mutations."""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

T = TypeVar("T")


class CatalogMutationSerializer(Protocol):
    def serialize(self, key: str, operation: Callable[[], T]) -> T: ...


class FileCatalogMutationSerializer:
    """Serialize management-command writers through one explicit lock root.

    The DEV command must mount the same lock directory for every possible
    writer.  A missing/non-absolute directory fails before a state read.  Unit
    tests may inject an in-memory serializer; production use is intentionally
    unsupported by the surrounding command guard.
    """

    def __init__(self, lock_directory: str) -> None:
        path = Path(lock_directory)
        if not path.is_absolute() or not path.exists() or not path.is_dir():
            raise ValueError(
                "catalog mutation lock_directory must be an existing absolute directory"
            )
        self._directory = path

    def serialize(self, key: str, operation: Callable[[], T]) -> T:
        if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 4096:
            raise ValueError("catalog mutation lock key is invalid")
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock"
        path = self._directory / filename
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return operation()
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


class InProcessCatalogMutationSerializer:
    """Small mock/local serializer; the executable command uses file locking."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.RLock()

    def serialize(self, key: str, operation: Callable[[], T]) -> T:
        _ = key
        with self._lock:
            return operation()


__all__ = [
    "CatalogMutationSerializer",
    "FileCatalogMutationSerializer",
    "InProcessCatalogMutationSerializer",
]
