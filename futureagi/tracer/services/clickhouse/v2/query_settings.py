"""Per-context ClickHouse query settings for the v2 readers.

``ch_query_settings(**settings)`` layers settings (``log_comment``,
``max_memory_usage``, …) onto a ContextVar; every CH client the v2 readers
construct while the context is active merges them into its client-level
settings. Nested contexts merge, inner keys win.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_settings: ContextVar[dict | None] = ContextVar("ch_query_settings", default=None)

_APPLICATION_READ_MAX_MEMORY_USAGE = 36 * 1024 * 1024 * 1024
_APPLICATION_READ_MAX_BYTES_TO_READ = 36 * 1024 * 1024 * 1024
_APPLICATION_READ_MAX_EXECUTION_TIME_SECONDS = 9.5
_APPLICATION_READ_DEFAULT_THREADS = 4
_APPLICATION_READ_MAX_THREADS = 8
_APPLICATION_READ_MAX_RESULT_ROWS = 1_000_000
_APPLICATION_READ_MAX_RESULT_BYTES = 512 * 1024 * 1024


def application_read_settings(
    settings: dict | None = None,
    *,
    timeout_ms: int | None = None,
) -> dict:
    """Normalize one ordinary v2 read to the interactive application policy."""

    settings = dict(settings or {})
    settings.pop("max_rows_to_read", None)

    def finite_ceiling(name: str, ceiling: int) -> int:
        requested = int(settings.get(name, 0) or 0)
        return ceiling if requested <= 0 else min(requested, ceiling)

    settings["max_memory_usage"] = finite_ceiling(
        "max_memory_usage", _APPLICATION_READ_MAX_MEMORY_USAGE
    )
    settings["max_bytes_to_read"] = finite_ceiling(
        "max_bytes_to_read", _APPLICATION_READ_MAX_BYTES_TO_READ
    )
    requested_threads = int(settings.get("max_threads", 0) or 0)
    settings["max_threads"] = (
        _APPLICATION_READ_DEFAULT_THREADS
        if requested_threads <= 0
        else min(requested_threads, _APPLICATION_READ_MAX_THREADS)
    )
    settings["max_result_rows"] = finite_ceiling(
        "max_result_rows", _APPLICATION_READ_MAX_RESULT_ROWS
    )
    settings["max_result_bytes"] = finite_ceiling(
        "max_result_bytes", _APPLICATION_READ_MAX_RESULT_BYTES
    )
    settings["readonly"] = 2
    settings["read_overflow_mode"] = "throw"
    settings["timeout_overflow_mode"] = "throw"
    settings["result_overflow_mode"] = "throw"
    requested_timeout = settings.get("max_execution_time")
    timeout_seconds = (
        max(
            0.001,
            min(
                float(requested_timeout),
                _APPLICATION_READ_MAX_EXECUTION_TIME_SECONDS,
            ),
        )
        if requested_timeout is not None
        else _APPLICATION_READ_MAX_EXECUTION_TIME_SECONDS
    )
    if timeout_ms is not None:
        timeout_seconds = min(
            timeout_seconds,
            max(
                0.001,
                min(
                    timeout_ms / 1000,
                    _APPLICATION_READ_MAX_EXECUTION_TIME_SECONDS,
                ),
            ),
        )
    settings["max_execution_time"] = timeout_seconds
    return settings


def current_settings() -> dict:
    return application_read_settings(_settings.get())


@contextmanager
def ch_query_settings(**settings):
    merged = {**current_settings(), **settings}
    token = _settings.set(merged)
    try:
        yield
    finally:
        _settings.reset(token)
