"""Carry deferred sync outcomes through helpers inside one config write."""

from contextlib import contextmanager
from contextvars import ContextVar

_current_warnings = ContextVar("config_sync_warnings", default=None)


@contextmanager
def collect_sync_warnings():
    """Use one mutable receipt until the outer database transaction commits.

    Deferred work starts with a pending warning, so a nested caller cannot
    claim confirmation before its outer transaction has actually committed.
    Context-local scope keeps unrelated requests and rollback paths separate.
    """
    warnings = []
    token = _current_warnings.set(warnings)
    try:
        yield warnings
    finally:
        _current_warnings.reset(token)


def current_sync_warnings():
    warnings = _current_warnings.get()
    return warnings if warnings is not None else []
