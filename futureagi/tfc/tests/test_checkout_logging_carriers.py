"""Late native carriers never reach a configured sink; objects stay unchanged.

Each scenario is observed in what the configured sinks emit (checkout_sinks),
not in the in-memory record.
"""

import logging
from collections import Counter
from contextlib import contextmanager

import httpx
import pytest
from django.http import HttpResponsePermanentRedirect, StreamingHttpResponse
from django.test import Client, override_settings
from django.urls import resolve

from tfc.logging.config import configure_structlog
from tfc.settings.settings import MIDDLEWARE as DEPLOYED_MIDDLEWARE
from tfc.tests.checkout_sinks import configured_sinks, native_emit

pytestmark = pytest.mark.integration
BOUNDARIES = ["logger", "handler", "formatter"]


class SnapshotFormatter(logging.Formatter):
    """A sink rendering the metadata surfaces the former snapshot handler read.

    Native carrier fields (response headers, a response's stored attributes,
    exception fields outside args) are written through the same StreamHandler
    emit boundary as every configured sink.
    """

    def format(self, record):
        surfaces = [str(vars(record))]
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            payload = payload["nested"][0]
        if hasattr(payload, "headers"):
            surfaces.append(str(payload.headers))
            # A 404 response's repr omits its attached original request. A
            # metadata consumer can still inspect that stored request directly.
            surfaces.append(str(vars(payload)))
        if record.exc_info:
            error = record.exc_info[1]
            for field in ("filename", "filename2", "text", "msg", "object", "reason"):
                surfaces.append(str(getattr(error, field, None)))
        surfaces.append(super().format(record))
        if hasattr(record, "payload"):
            surfaces.append(str(record.payload))
        return " ".join(surfaces)


@contextmanager
def capture():
    with configured_sinks(
        "new.checkout.native.carrier.consumer",
        extra={"snapshot": SnapshotFormatter()},
    ) as (logger, sinks):
        yield logger, sinks


def dispatch(logger, sinks, record, boundary):
    if boundary == "logger":
        logger.handle(record)
    elif boundary == "handler":
        sinks.handle(record)
    else:
        logging.Formatter().format(record)
        sinks.emit(record)


def route_for(checkout):
    supplied = "synthetic-native-carrier-681"
    prefix = "/agentcc/managed-credits/checkout" if checkout else "/ordinary"
    return supplied, f"{prefix}/{supplied}/"


def assert_confidential(sinks, supplied, checkout):
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(supplied) == [], "supplied value reached a configured sink"
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert supplied in sinks.text(), "unrelated diagnostic must remain observable"


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("kind", ["url", "redirect", "streaming"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("checkout", [True, False])
def test_late_url_and_response_carriers(boundary, kind, nested, checkout):
    supplied, route = route_for(checkout)
    streamed = []

    def content():
        streamed.append(True)
        yield b"ordinary content"

    if kind == "url":
        carrier = httpx.URL("http://localhost" + route)
    elif kind == "redirect":
        carrier = HttpResponsePermanentRedirect(route)
    else:
        carrier = StreamingHttpResponse(content(), headers={"Location": route})
    original = str(carrier) if kind == "url" else dict(carrier.headers)
    payload = {"nested": [carrier]} if nested else carrier
    with capture() as (logger, sinks):
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "late diagnostic",
            (),
            None,
            extra={"payload": payload},
        )
        dispatch(logger, sinks, record, boundary)
    assert (str(carrier) if kind == "url" else dict(carrier.headers)) == original
    assert not streamed, "logging must never consume a response body"
    assert_confidential(sinks, supplied, checkout)


def native_error(kind, route):
    if kind == "filename":
        return FileNotFoundError(2, "missing", route)
    if kind == "filename2":
        return OSError(2, "missing", "ordinary", None, route)
    if kind == "strerror":
        error = OSError(2, "ordinary")
        error.strerror = route
    elif kind in (
        "syntax-filename",
        "syntax-text",
        "syntax-msg",
        "syntax-lineno",
    ):
        error = SyntaxError("ordinary", ("ordinary", 1, 1, "ordinary"))
        setattr(error, kind.removeprefix("syntax-"), route)
    elif kind == "import-msg":
        error = ImportError("ordinary")
        error.msg = route
    elif kind == "unicode-reason":
        error = UnicodeDecodeError("utf-8", b"x", 0, 1, "ordinary")
        error.reason = route
    elif kind == "unicode-object":
        error = UnicodeDecodeError("utf-8", b"x", 0, 1, "ordinary")
        error.object = route.encode()
    else:
        error = ExceptionGroup("ordinary", [FileNotFoundError(2, "missing", route)])
    assert route not in str(error.args)
    return error


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize(
    "kind",
    [
        "filename",
        "filename2",
        "strerror",
        "syntax-filename",
        "syntax-text",
        "syntax-msg",
        "syntax-lineno",
        "import-msg",
        "unicode-reason",
        "unicode-object",
        "group",
    ],
)
@pytest.mark.parametrize("checkout", [True, False])
def test_native_exception_fields_outside_args(boundary, kind, checkout):
    supplied, route = route_for(checkout)
    error = native_error(kind, route)
    original = str(error)
    with capture() as (logger, sinks):
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "late failure",
            (),
            (type(error), error, None),
        )
        dispatch(logger, sinks, record, boundary)
    assert str(error) == original
    assert_confidential(sinks, supplied, checkout)


@pytest.mark.django_db
@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("suffix,status", [("", 301), ("/missing", 404)])
@pytest.mark.parametrize("kind", ["url", "response"])
def test_actual_route_then_late_native_carrier(boundary, suffix, status, kind):
    supplied, route = route_for(True)
    route = route.rstrip("/")
    assert resolve(route + "/").url_name == "agentcc-managed-credit-checkout-status"
    configure_structlog(cache_logger_on_first_use=False)
    with override_settings(
        MIDDLEWARE=DEPLOYED_MIDDLEWARE,
        DEBUG=False,
        APPEND_SLASH=True,
        SECURE_SSL_REDIRECT=False,
        ALLOWED_HOSTS=["testserver"],
    ):
        response = Client().get(route + suffix)
    assert response.status_code == status
    assert response.wsgi_request.path == route + suffix
    headers = dict(response.headers)
    if status == 301:
        assert headers["Location"] == route + "/"
    carrier = (
        response
        if kind == "response"
        else httpx.URL("http://localhost" + route + suffix)
    )
    with capture() as (logger, sinks):
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "late access",
            (),
            None,
            extra={"payload": carrier},
        )
        dispatch(logger, sinks, record, boundary)
    assert response.wsgi_request.path == route + suffix
    assert dict(response.headers) == headers
    assert_confidential(sinks, supplied, True)


PROPERTIES = (
    "args",
    "filename",
    "filename2",
    "__cause__",
    "__context__",
    "__dict__",
    "__notes__",
    "__class__",
)


def test_native_exception_subclass_properties_run_only_where_logging_does():
    from tfc.logging.checkout import _find_values

    calls = []

    def stored(name):
        # Counts each use, then answers from the base's own storage.
        def get(self):
            calls.append(name)
            if name == "__notes__":
                notes = BaseException.__dict__["__dict__"].__get__(self)
                if "__notes__" not in notes:
                    raise AttributeError(name)
                return notes["__notes__"]
            for owner in type(self).__mro__[1:]:
                if name in vars(owner):
                    return vars(owner)[name].__get__(self)
            raise AttributeError(name)

        return property(get)

    error_type = type(
        "UntrustedOSError",
        (FileNotFoundError,),
        {name: stored(name) for name in PROPERTIES},
    )
    supplied, route = route_for(True)
    error = error_type(2, "ordinary", route)
    # Telemetry and Sentry discovery still reads native storage only.
    assert supplied in _find_values(error)
    assert calls == [], "discovery ran a subclass property"

    def run(boundary):
        with capture() as (logger, sinks):
            record = logger.makeRecord(
                logger.name,
                logging.ERROR,
                __file__,
                1,
                "late failure",
                (),
                (error_type, error, None),
            )
            dispatch(logger, sinks, record, boundary)
        observed = Counter(calls)
        calls.clear()
        return sinks, observed

    for boundary in BOUNDARIES:
        sinks, with_boundary = run(boundary)
        with native_emit():
            _, natively = run(boundary)
        assert with_boundary == natively, "the sink boundary ran a subclass property"
        assert_confidential(sinks, supplied, True)
    assert OSError.filename.__get__(error) == route
