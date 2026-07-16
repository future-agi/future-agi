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


def current_settings() -> dict:
    return dict(_settings.get() or {})


@contextmanager
def ch_query_settings(**settings):
    merged = {**current_settings(), **settings}
    token = _settings.set(merged)
    try:
        yield
    finally:
        _settings.reset(token)
