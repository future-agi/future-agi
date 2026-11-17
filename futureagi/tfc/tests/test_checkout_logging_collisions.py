"""Literal marker collisions and late exception notes must not bypass redaction.

Logging scenarios are observed in what the configured sinks emit
(checkout_sinks), not in the in-memory record.
"""

import logging
import sys
from contextlib import contextmanager
from urllib.parse import quote

import pytest
import structlog
from django.test import Client, override_settings
from django.urls import resolve

from tfc.logging.config import configure_structlog
from tfc.settings.settings import MIDDLEWARE as DEPLOYED_MIDDLEWARE
from tfc.tests.checkout_sinks import configured_sinks

pytestmark = pytest.mark.integration


class Snapshot(logging.Handler):
    """Delivery control only: notes on unrelated errors arrive unchanged."""

    def __init__(self):
        super().__init__()
        self.surfaces = []

    def emit(self, record):
        self.surfaces.extend([str(vars(record)), logging.Formatter().format(record)])


@contextmanager
def first_consumer():
    configure_structlog(cache_logger_on_first_use=False)
    logger = logging.getLogger("new.checkout.collision.consumer")
    previous = logger.propagate
    logger.propagate = False
    handler = Snapshot()
    logger.handlers.insert(0, handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous


def capture():
    return configured_sinks("new.checkout.collision.consumer")


def assert_safe(surfaces, supplied):
    assert surfaces
    observed = supplied in str(surfaces) or quote(supplied, safe="") in str(surfaces)
    assert not observed, "supplied value reached an observable surface"


def assert_sinks_safe(sinks, supplied):
    assert sinks.emitted(), "a configured sink wrote nothing"
    for form in (supplied, quote(supplied, safe="")):
        assert sinks.leaks(form) == [], "supplied value reached a configured sink"
    assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.parametrize("kind", ["stdlib", "structlog", "bytes", "sentry"])
@pytest.mark.parametrize("position", ["prefix", "middle", "suffix", "repeated"])
def test_literal_marker_is_untrusted_input(kind, position):
    # Synthetic values only, including markers supplied as part of the session.
    fragments = {
        "prefix": ["[redacted]", "synthetic-tail-681"],
        "middle": ["synthetic-prefix-", "[redacted]", "-tail-681"],
        "suffix": ["synthetic-prefix-681", "[redacted]"],
        "repeated": ["synthetic-", "[redacted]", "-681-", "[redacted]"],
    }
    supplied = "".join(fragments[position])
    route = f"/agentcc/managed-credits/checkout/{supplied}/"
    if kind == "sentry":
        from tfc.logging.sentry import _get_before_send

        event = {"request": {"url": route}, "message": supplied}
        result = _get_before_send()(event, {})
        assert result is not None
        assert_safe([result], supplied)
        assert event["message"] == supplied
        return
    with capture() as (logger, sinks):
        if kind == "structlog":
            structlog.get_logger(logger.name).error("late", url=route, echo=supplied)
        elif kind == "bytes":
            logger.error(
                "late", extra={"url": route.encode(), "echo": supplied.encode()}
            )
        else:
            logger.error("GET %s", route, extra={"echo": supplied})
    assert_sinks_safe(sinks, supplied)


@pytest.mark.django_db
@pytest.mark.parametrize("suffix, status", [("", 301), ("/unresolved", 404)])
def test_literal_marker_actual_route_full_middleware(suffix, status):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-prefix" + "[redacted]" + "private-tail-681"
    route = f"/agentcc/managed-credits/checkout/{supplied}"
    assert resolve(route + "/").url_name == "agentcc-managed-credit-checkout-status"
    with configured_sinks("django.request") as (_, framework):
        with configured_sinks("django_structlog.middlewares.request") as (_, requests):
            with override_settings(
                MIDDLEWARE=DEPLOYED_MIDDLEWARE,
                DEBUG=False,
                APPEND_SLASH=True,
                ALLOWED_HOSTS=["testserver"],
                SECURE_SSL_REDIRECT=False,
            ):
                response = Client().get(route + suffix)
    assert response.status_code == status
    assert response.wsgi_request.path == route + suffix
    if status == 301:
        assert response["Location"] == quote(route, safe="/") + "/"
    assert requests.emitted(), "the request logger's sinks wrote nothing"
    for sinks in (framework, requests):
        for form in (supplied, quote(supplied, safe="")):
            assert sinks.leaks(form) == [], "supplied value reached a configured sink"
        assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.parametrize("dispatch", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("chain", ["direct", "cause", "context"])
def test_exception_notes_never_reach_a_configured_sink(dispatch, chain):
    supplied = "synthetic-exception-note-681"
    route = f"/agentcc/managed-credits/checkout/{supplied}/"
    noted = ValueError("value-free ordinary message")
    noted.add_note(route)
    with capture() as (logger, sinks):
        try:
            try:
                raise noted
            except ValueError as cause:
                if chain == "cause":
                    raise RuntimeError("value-free wrapper") from cause
                if chain == "context":
                    # Intentionally exercise implicit exception context.
                    raise RuntimeError("value-free wrapper")  # noqa: B904
                raise
        except (ValueError, RuntimeError):
            if dispatch == "logger":
                logger.exception("late failure")
            else:
                record = logging.LogRecord(
                    logger.name,
                    logging.ERROR,
                    __file__,
                    1,
                    "late failure",
                    (),
                    sys.exc_info(),
                )
                if dispatch == "handler":
                    sinks.handle(record)
                else:
                    # A caller's own formatting first (not a sink), then each sink.
                    logging.Formatter().format(record)
                    sinks.emit(record)
    assert_sinks_safe(sinks, supplied)
    assert noted.__notes__ == [route]
    assert noted.args == ("value-free ordinary message",)


@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize(
    "supplied", ["a", "redacted]private-tail-681", "[redacted]tail"]
)
def test_overlapping_markers_never_hide_sensitive_matches(as_bytes, supplied):
    from tfc.logging.checkout import _scrub

    original = "[" + supplied + " | " + supplied
    if as_bytes:
        original = original.encode()
    sanitized = _scrub(original, (supplied,))
    if len(supplied) > 1:
        assert_safe([sanitized], supplied)
    # Repeat source/handler/formatter passes without recursively expanding markers.
    assert _scrub(sanitized, (supplied,)) == sanitized
    assert _scrub(sanitized, ("a", "r", "redacted")) == sanitized


@pytest.mark.parametrize("as_bytes", [False, True])
def test_replacement_cannot_reconstruct_a_marker_bearing_value(as_bytes):
    from tfc.logging.checkout import _scrub

    supplied = "[redacted]" + "private-tail-681"
    original = "synthetic-private-tail-681"
    if as_bytes:
        original = original.encode()
    values = (supplied, "synthetic-")
    sanitized = _scrub(original, values)
    assert_safe([sanitized], supplied)
    assert _scrub(sanitized, values) == sanitized


def test_exception_note_discovery_does_not_call_user_properties():
    from tfc.logging.checkout import _find_values

    class UntrustedException(ValueError):
        @property
        def __notes__(self):
            raise AssertionError("note property must not run during discovery")

        @property
        def __dict__(self):
            raise AssertionError("dict property must not run during discovery")

    assert _find_values(UntrustedException("ordinary")) == ()


def test_unrelated_exception_notes_remain_unchanged():
    note = "ordinary note with [redacted] text"
    with first_consumer() as (logger, handler):
        try:
            error = ValueError("ordinary")
            error.add_note(note)
            raise error
        except ValueError:
            logger.exception("ordinary failure")
    assert note in handler.surfaces[-1]
