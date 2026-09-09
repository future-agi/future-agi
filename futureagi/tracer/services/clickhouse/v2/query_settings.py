"""Per-context ClickHouse query settings for the v2 readers.

``ch_query_settings(**settings)`` layers settings (``log_comment``,
``max_memory_usage``, …) onto a ContextVar; every CH client the v2 readers
construct while the context is active merges them into its client-level
settings. Nested contexts merge, inner keys win.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from tracer.services.clickhouse.application_read_policy import (
    application_read_settings as _application_read_settings,
)

_settings: ContextVar[dict | None] = ContextVar("ch_query_settings", default=None)


def application_read_settings(
    settings: dict | None = None,
    *,
    timeout_ms: int | None = None,
) -> dict:
    """Apply the same analytics policy as the native query-service boundary.

    Legacy timeout arguments no longer impose per-statement execution limits.
    """
    return _application_read_settings(settings)


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
