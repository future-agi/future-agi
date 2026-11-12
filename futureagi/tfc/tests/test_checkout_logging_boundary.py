"""Checkout values never reach a configured sink; requests stay unchanged.

The configured sinks' emitted output is the acceptance
surface (checkout_sinks), not the in-memory record.
"""

import asyncio
import io
import logging
from typing import NamedTuple

import pytest
import structlog
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import path

from tfc.logging.config import configure_structlog, get_logging_config
from tfc.tests.checkout_sinks import configured_sinks

pytestmark = pytest.mark.integration


def view(request, **kwargs):
    return HttpResponse("ok")


urlpatterns = [
    path(
        "agentcc/managed-credits/checkout/<str:session_id>/",
        view,
        name="agentcc-managed-credit-checkout-status",
    ),
    path("ordinary/<str:session_id>/", view, name="ordinary"),
]


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        # Snapshot at emission: a later handler cannot repair this capture.
        self.records.append(dict(vars(record)))
        self.records.append(logging.Formatter().format(record))


def boundary(get_response):
    from tfc.logging.checkout import CheckoutLoggingMiddleware

    return CheckoutLoggingMiddleware(get_response)


@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.parametrize(
    "source", ["django.request", "django_structlog.middlewares.request"]
)
def test_sinks_receive_no_surface_and_the_request_is_unchanged(source):
    supplied = "synthetic-session-648"
    request = RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
    original_path = request.path
    logger = logging.getLogger(source)

    def respond(incoming):
        assert incoming is request
        assert incoming.path == original_path
        try:
            raise RuntimeError(supplied)
        except RuntimeError:
            logger.error(
                "failure %s",
                supplied,
                exc_info=True,
                stack_info=True,
                extra={
                    "request": request,
                    "nested": {supplied: [supplied.encode(), request]},
                    "exception_copy": RuntimeError(supplied),
                },
            )
        structlog.get_logger(source).warning("structured", metadata={"value": supplied})
        return HttpResponse("ok")

    with configured_sinks(source) as (_, sinks):
        configure_structlog(cache_logger_on_first_use=False)
        boundary(respond)(request)
        # Django BaseHandler logs non-2xx after the middleware stack unwinds.
        logger.error("late failure %s", original_path, extra={"request": request})
    assert request.path == original_path
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(supplied) == [], "checkout session value reached a sink"
    assert "[redacted]" in sinks.outputs()["record"]
    assert sinks.structured(), "a production line is not one JSON document"


@override_settings(ROOT_URLCONF=__name__)
def test_other_routes_records_and_context_after_checkout_are_unchanged():
    logger = logging.getLogger("django.request")
    supplied = "synthetic-other-648"
    request = RequestFactory().get(f"/ordinary/{supplied}/")
    capture = Records()
    logger.addHandler(capture)
    configure_structlog(cache_logger_on_first_use=False)

    def respond(incoming):
        logger.error("ordinary %s", supplied, extra={"request": incoming})
        return HttpResponse("ok")

    try:
        boundary(lambda req: HttpResponse("ok"))(
            RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
        )
        boundary(respond)(request)
    finally:
        logger.removeHandler(capture)
    record = capture.records[0]
    assert record["msg"] == "ordinary %s"
    assert record["args"] == (supplied,)
    assert record["request"] is request
    assert supplied in capture.records[1]


@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.asyncio
async def test_async_requests_have_isolated_redaction_context():
    configure_structlog(cache_logger_on_first_use=False)
    logger = structlog.get_logger("django_structlog.middlewares.request")
    ready = asyncio.Event()
    done = asyncio.Event()
    supplied = "synthetic-async-648"

    async def protected(request):
        ready.set()
        await done.wait()
        logger.warning("protected", value=supplied)
        return HttpResponse("ok")

    async def ordinary(request):
        await ready.wait()
        logger.warning("ordinary", value="ordinary-visible")
        done.set()
        return HttpResponse("ok")

    with configured_sinks("django_structlog.middlewares.request") as (_, sinks):
        await asyncio.gather(
            boundary(protected)(
                RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
            ),
            boundary(ordinary)(RequestFactory().get("/ordinary/plain/")),
        )
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(supplied) == [], "checkout session value reached a sink"
    assert "ordinary-visible" in sinks.outputs()["console-production"]
    assert "ordinary-visible" in sinks.outputs()["record"]


@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.parametrize("environment", ["local", "production"])
def test_late_error_formatter_cannot_reintroduce_request_context(
    tmp_path, monkeypatch, environment
):
    supplied = "synthetic-late-648"
    monkeypatch.setenv("ENV_TYPE", environment)
    config = get_logging_config(str(tmp_path))["formatters"]["structured"]
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=config["processor"], foreign_pre_chain=config["foreign_pre_chain"]
    )
    output = io.StringIO()
    capture = logging.StreamHandler(output)
    capture.setFormatter(formatter)
    logger = logging.getLogger("django.request")
    logger.addHandler(capture)
    configure_structlog(cache_logger_on_first_use=False)
    request = RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
    try:
        boundary(lambda req: HttpResponse("ok"))(request)
        with structlog.contextvars.bound_contextvars(ancillary=supplied):
            logger.error("late %s", request.path, extra={"request": request})
    finally:
        logger.removeHandler(capture)
    assert supplied not in output.getvalue()
    assert "[redacted]" in output.getvalue()


@override_settings(ROOT_URLCONF=__name__)
def test_exception_unwinds_context_and_unknown_routes_keep_their_logs():
    supplied = "synthetic-unwind-648"

    def fail(request):
        raise ValueError("test exception")

    with pytest.raises(ValueError, match="test exception"):
        boundary(fail)(
            RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
        )
    from tfc.logging.checkout import scrub_checkout_event

    event = {"event": "unrelated", "value": supplied}
    assert scrub_checkout_event(None, "info", event) is event
    boundary(lambda req: HttpResponse("missing", status=404))(
        RequestFactory().get("/not-resolved/")
    )
    assert scrub_checkout_event(None, "info", event) is event


@override_settings(ROOT_URLCONF=__name__)
def test_quoted_paths_cycles_and_tuple_subclasses_are_safe():
    from django.utils.encoding import escape_uri_path

    from tfc.logging.checkout import scrub_checkout_event

    supplied = "synthetic: quoted 648"
    request = RequestFactory().get(
        f"/agentcc/managed-credits/checkout/{escape_uri_path(supplied)}/"
    )

    class Diagnostic(NamedTuple):
        value: str
        request: object

    diagnostic = Diagnostic(supplied, request)
    cycle = []
    cycle.append(cycle)
    observed = []

    def respond(incoming):
        event = scrub_checkout_event(
            None,
            "info",
            {
                "event": "test",
                "quoted": incoming.get_full_path(),
                "metadata": diagnostic,
                "cycle": cycle,
            },
        )
        observed.append(event)
        return HttpResponse("ok")

    boundary(respond)(request)
    assert supplied not in str(observed)
    assert escape_uri_path(supplied) not in str(observed)
    assert observed[0]["metadata"] == ("[redacted]", None)
    assert request.path.endswith(supplied + "/")


@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.parametrize("supplied", ["a", "msg", "event", "request", "level"])
def test_invalid_short_session_cannot_corrupt_logging_schema(supplied):
    from tfc.logging.config import get_renderer

    configure_structlog(cache_logger_on_first_use=False)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processor=get_renderer()))
    logger = logging.getLogger("django_structlog.middlewares.request")
    logger.addHandler(handler)

    def respond(request):
        structlog.get_logger(logger.name).warning("event", supplied=supplied)
        return HttpResponse("ok")

    try:
        response = boundary(respond)(
            RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
        )
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 200
    assert "supplied" in output.getvalue()


def test_production_config_sanitizes_at_the_sink_write_without_record_filters(
    tmp_path,
):
    from tfc.logging.checkout_errors import handle_error

    config = get_logging_config(str(tmp_path))
    # Raw records are not a sink: no logger or handler filter rewrites them.
    assert "checkout_redact" not in config["filters"]
    for name in ("django.request", "django_structlog.middlewares.request"):
        assert "filters" not in config["loggers"].get(name, {})
    for name in ("console", "file", "saml_console"):
        assert "checkout_redact" not in config["handlers"][name].get("filters", [])
    # Every stream and file handler sanitizes its final text before writing,
    # and logging's stderr fallback sanitizes the diagnostic it writes.
    emit = logging.StreamHandler.emit
    assert getattr(emit, "_checkout_output_boundary", False) is True
    assert emit.__wrapped__ is not emit
    assert logging.Handler.handleError is handle_error
    for name in ("console", "file", "saml_console"):
        handler_class = config["handlers"][name]["class"]
        assert handler_class in (
            "logging.StreamHandler",
            "logging.handlers.RotatingFileHandler",
        )
