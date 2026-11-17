"""WT-27's suite-wide half (DESIGN.md section 17): ``whsec_`` appears in no log record of the whole run (K2 too: no
``WEBHOOKS_ENCRYPTION_KEYS`` entry in force at the time does either).

Every stdlib log record any logger makes while the run goes on is scanned: its message, its arguments, its extra
fields, and its exception and stack text. That holds whichever handlers, levels or propagation the logger has, and
whichever thread made the record (``Logger.handle`` is wrapped, the one call every record passes through). The sweep
starts when ``conftest`` imports this module, while ``webhooks/tests`` is collected, before any test runs.

``test_log_sweep`` runs last (the ``pytest_collection_modifyitems`` wrapper below moves it there after the other
plugins have selected and ordered the run; the test checks it is last) and fails on any hit. A hit names the test
that was running, the logger and the field, never the value itself. ``webhooks`` loggers make records at every level
while the run goes on (``install``).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import traceback

import pytest

SWEEP_TEST = (
    "webhooks/tests/test_log_sweep.py::test_wt27_no_secret_in_any_log_record_of_the_run"
)
SECRET_SHAPE = re.compile(r"whsec_[A-Za-z0-9+/=_-]{6,}")
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
}
_MAX_TEXT = 200_000


def _render(value, depth: int = 0) -> str:
    """Text for a logged value. Containers are walked; a lazy object (a Django QuerySet) is named, never evaluated,
    so the sweep runs no query of its own inside a test's transaction."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return " ".join(
            f"{_render(k, depth + 1)}={_render(v, depth + 1)}" for k, v in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return " ".join(_render(v, depth + 1) for v in value)
    if hasattr(value, "_result_cache") and hasattr(value, "query"):
        return type(value).__name__
    try:
        return repr(value)
    except Exception:  # noqa: BLE001
        return type(value).__name__


def _texts(record: logging.LogRecord):
    """(field, text) for every part of ``record`` a handler or formatter could write."""
    try:
        yield "message", record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string is still scanned raw
        pass
    yield "msg", _render(record.msg)[:_MAX_TEXT]
    if record.args:
        yield "args", _render(record.args)[:_MAX_TEXT]
    for key, value in vars(record).items():
        if key not in _STANDARD:
            yield f"extra.{key}", _render(value)[:_MAX_TEXT]
    if record.exc_info and record.exc_info[1] is not None:
        yield "exc_info", "".join(traceback.format_exception(*record.exc_info))
    if record.exc_text:
        yield "exc_text", record.exc_text
    if record.stack_info:
        yield "stack_info", record.stack_info


class Sweep:
    def __init__(self):
        self.lock = threading.Lock()
        self.records = 0
        self.started = 0
        self.errors = 0
        self.current = "<collection>"
        self.hits: list[tuple[str, str, str]] = []  # (test, logger, "field:kind")

    def scan(self, record: logging.LogRecord) -> None:
        keys = [
            k.strip()
            for k in os.environ.get("WEBHOOKS_ENCRYPTION_KEYS", "").split(",")
            if len(k.strip()) >= 32
        ]
        found = []
        for field, text in _texts(record):
            if SECRET_SHAPE.search(text):
                found.append(f"{field}:whsec_")
            if any(k in text for k in keys):
                found.append(f"{field}:encryption_key")
        with self.lock:
            self.records += 1
            if found:
                self.hits.append((self.current, record.name, ",".join(found)))


SWEEP = Sweep()
_original_handle = logging.Logger.handle


def _handle(self, record):
    try:
        SWEEP.scan(record)
    except Exception:  # noqa: BLE001 - the sweep never breaks logging; a scan error fails the sweep test instead
        SWEEP.errors += 1
    return _original_handle(self, record)


def install() -> None:
    """Wrap ``Logger.handle``, and let every ``webhooks`` logger make its records at every level: the test settings
    leave the root at WARNING, where the app's info lines (the metric lines among them) would never be made, so never
    scanned. Other loggers keep the levels the test settings give them."""
    if logging.Logger.handle is not _handle:
        logging.Logger.handle = _handle
    logging.getLogger("webhooks").setLevel(logging.DEBUG)


install()


@pytest.hookimpl(wrapper=True)
def pytest_collection_modifyitems(session, config, items):
    result = yield
    last = [i for i in items if i.nodeid.endswith(SWEEP_TEST)]
    if last:
        items[:] = [i for i in items if i not in last] + last
    return result


def pytest_runtest_logstart(nodeid, location):
    with SWEEP.lock:
        SWEEP.started += 1
        SWEEP.current = nodeid


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    terminalreporter.write_line(
        f"WT-27 log sweep: {SWEEP.records} log records over {SWEEP.started} tests; "
        f"{len(SWEEP.hits)} with a secret; {SWEEP.errors} scan errors"
    )
