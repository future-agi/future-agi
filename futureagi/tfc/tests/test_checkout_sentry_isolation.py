"""A test's Sentry client does not outlive the test.

Tests that initialize the application's Sentry client run beside tests that
count how often a record is rendered and tests that read Sentry envelopes.
After the capture ends, no client is bound, logging's handler dispatch is the
one the test started with, no breadcrumb is kept, and the next capture still
reports log records. All values are synthetic.
"""

import json
import logging

import pytest

from tfc.tests.checkout_sentry import sentry_capture

pytestmark = pytest.mark.integration


def scopes():
    import sentry_sdk

    return (
        sentry_sdk.get_current_scope(),
        sentry_sdk.get_isolation_scope(),
        sentry_sdk.get_global_scope(),
    )


def capture_one(monkeypatch, message):
    import sentry_sdk

    with sentry_capture(monkeypatch) as (_, envelopes):
        logging.getLogger("checkout.sentry.isolation").error(message)
        sentry_sdk.flush()
    return b"".join(envelopes)


def test_capture_leaves_no_client_hook_or_breadcrumb(monkeypatch):
    import sentry_sdk

    dispatch = logging.Logger.callHandlers
    wire = capture_one(monkeypatch, "synthetic isolation event")
    assert b"synthetic isolation event" in wire
    assert not sentry_sdk.get_client().is_active()
    assert logging.Logger.callHandlers is dispatch
    assert all(len(scope._breadcrumbs) == 0 for scope in scopes())
    # Records the stdlib accepts still pass (no client code runs on them).
    calls = []

    class Counted:
        def __str__(self):
            calls.append("str")
            return "counted"

    logger = logging.getLogger("checkout.sentry.isolation.after")
    stream = logging.StreamHandler(_Sink())
    logger.addHandler(stream)
    logger.propagate = False
    try:
        logger.error("%s", Counted())
        logger.handle(
            logging.LogRecord(None, logging.ERROR, __file__, 1, "x", (), None)
        )
    finally:
        logger.removeHandler(stream)
        logger.propagate = True
    # Rendered once, by the one handler: nothing else reads the record.
    assert calls == ["str"]


def logged(wire):
    """(event messages, breadcrumb messages) in serialized envelopes.

    Read from the envelope items, not the raw bytes: an event's stack frames
    carry the calling test's local variables.
    """
    events, crumbs = [], []
    for line in wire.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if type(item) is not dict:
            continue
        entry = item.get("logentry")
        if type(entry) is dict:
            events.append(entry.get("message"))
        for crumb in (item.get("breadcrumbs") or {}).get("values", ()):
            crumbs.append(crumb.get("message"))
    return events, crumbs


def test_next_capture_still_reports_log_records(monkeypatch):
    first = logged(capture_one(monkeypatch, "synthetic first event"))
    second = logged(capture_one(monkeypatch, "synthetic second event"))
    assert first[0] == ["synthetic first event"]
    assert second[0] == ["synthetic second event"]
    # The first capture's record left no breadcrumb for the second.
    assert "synthetic first event" not in second[1]


class _Sink:
    def write(self, text):
        return len(text)

    def flush(self):
        pass
