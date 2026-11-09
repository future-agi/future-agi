"""Independent standard-carrier and deployed-export controls, synthetic values only.

Logging scenarios are observed in what the configured sinks emit
(checkout_sinks; ruling 681 addendum 5), not in the in-memory record.
"""

import io
import logging
from contextlib import contextmanager
from http.cookiejar import Cookie, CookieJar
from pathlib import PurePosixPath

import httpx
import pytest
import requests
import structlog
from django.template import Context, RequestContext
from django.test import Client, RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, TraceFlags
from starlette.datastructures import State

from tfc.logging.config import configure_structlog, get_logging_config
from tfc.settings.settings import MIDDLEWARE as DEPLOYED
from tfc.telemetry.config import build_span_processor
from tfc.telemetry.naming import django_request_hook, django_response_hook
from tfc.tests.checkout_sinks import configured_sinks, dispatch, native_emit

pytestmark = pytest.mark.integration
SUPPLIED = "synthetic-independent-additional-session-681"
CHECKOUT = "/agentcc/managed-credits/checkout/" + SUPPLIED + "/"


def cookie(path):
    return Cookie(
        0,
        "next",
        path,
        None,
        False,
        "localhost",
        False,
        False,
        "/",
        True,
        False,
        None,
        True,
        None,
        None,
        {},
        False,
    )


def jar(kind, path):
    value = kind()
    value.set_cookie(cookie(path))
    return value


def response_with_cookie(path):
    result = requests.Response()
    result.url = "http://localhost/ordinary/"
    result.cookies = jar(requests.cookies.RequestsCookieJar, path)
    return result


CARRIERS = {
    "registered-requests-response-cookies": response_with_cookie,
    "stdlib-cookiejar": lambda p: jar(CookieJar, p),
    "requests-cookiejar": lambda p: jar(requests.cookies.RequestsCookieJar, p),
    "httpx-cookies": lambda p: httpx.Cookies({"next": p}),
    "stdlib-cookie": cookie,
    "django-context": lambda p: Context({"target": p}),
    "django-request-context": lambda p: RequestContext(
        RequestFactory().get("/ordinary/"), {"target": p}
    ),
    "starlette-state": lambda p: State({"target": p}),
    "stdlib-path": PurePosixPath,
}


class PayloadState(logging.Formatter):
    """A sink whose formatter also renders a carrier's stored state.

    The former first consumer rendered these internals; this sink writes them
    through the same StreamHandler emit boundary as every configured sink.
    """

    def format(self, record):
        rows = [super().format(record)]
        payload = vars(record).get("payload")
        if isinstance(payload, State):
            rows.append(str(payload._state))
        if isinstance(payload, requests.Response):
            rows.append(str(vars(payload)))
        return " ".join(rows)


@contextmanager
def capture(otlp=True):
    with configured_sinks(
        "independent.additional.first.consumer",
        otlp=otlp,
        extra={"payload-state": PayloadState()},
    ) as (source, sinks):
        yield source, sinks


@pytest.mark.parametrize("kind", sorted(CARRIERS))
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("checkout", [True, False])
def test_additional_standard_carrier(kind, boundary, checkout):
    path = CHECKOUT if checkout else "/ordinary/" + SUPPLIED + "/"
    carrier = CARRIERS[kind](path)
    before = str(carrier)
    with capture() as (source, sinks):
        record = source.makeRecord(
            source.name,
            40,
            __file__,
            1,
            "late metadata",
            (),
            None,
            extra={"payload": carrier},
        )
        dispatch(source, sinks, record, boundary)
    assert str(carrier) == before, "application carrier was changed"
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(SUPPLIED) == [], (
            "standard carrier reached a configured sink with session"
        )
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert record.payload is carrier, "unrelated carrier was replaced"
        assert SUPPLIED in sinks.text(), "unrelated carrier was hidden"


@pytest.mark.parametrize(
    "kind", ["stdlib-cookiejar", "requests-cookiejar", "httpx-cookies"]
)
@pytest.mark.parametrize("checkout", [True, False])
def test_cookie_metadata_through_configured_production_renderer(
    tmp_path, monkeypatch, kind, checkout
):
    path = CHECKOUT if checkout else "/ordinary/" + SUPPLIED + "/"
    carrier = CARRIERS[kind](path)
    monkeypatch.setenv("ENV_TYPE", "production")
    configure_structlog(cache_logger_on_first_use=False)
    configuration = get_logging_config(str(tmp_path))["formatters"]["structured"]
    output = io.StringIO()
    rendered = logging.StreamHandler(output)
    rendered.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=configuration["processor"],
            foreign_pre_chain=configuration["foreign_pre_chain"],
        )
    )
    with capture() as (source, sinks):
        source.handlers.append(rendered)
        try:
            structlog.get_logger(source.name).error(
                "late cookie metadata", payload=carrier
            )
        finally:
            source.removeHandler(rendered)
    assert sinks.emitted() and output.getvalue(), "both consumers must execute"
    exposed = {
        "configured_sinks": SUPPLIED in sinks.text(),
        "production_json": SUPPLIED in output.getvalue(),
    }
    if checkout:
        assert not any(exposed.values()), "checkout cookie value exported: " + str(
            exposed
        )
    else:
        assert all(exposed.values()), "unrelated cookie metadata must remain"


def provider():
    exporter = InMemorySpanExporter()
    result = TracerProvider()
    result.add_span_processor(build_span_processor(exporter, use_batch_processor=False))
    return result, exporter


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["query", "request-id", "referer"])
@pytest.mark.parametrize("checkout", [True, False])
def test_instrumented_deployed_route_bare_session_exports(field, checkout):
    path = CHECKOUT if checkout else "/ordinary/" + SUPPLIED + "/"
    data = {"next": SUPPLIED} if field == "query" else None
    headers = (
        {"HTTP_X_REQUEST_ID": SUPPLIED}
        if field == "request-id"
        else {"HTTP_REFERER": SUPPLIED}
        if field == "referer"
        else {}
    )
    tracer_provider, exporter = provider()
    try:
        with override_settings(
            MIDDLEWARE=list(DEPLOYED),
            DEBUG=False,
            ALLOWED_HOSTS=["testserver"],
            APPEND_SLASH=True,
            SECURE_SSL_REDIRECT=False,
        ):
            DjangoInstrumentor().instrument(
                tracer_provider=tracer_provider,
                request_hook=django_request_hook,
                response_hook=django_response_hook,
            )
            try:
                response = Client().get(path, data=data, **headers)
            finally:
                DjangoInstrumentor().uninstrument()
        spans = exporter.get_finished_spans()
        assert spans, "no instrumented spans were exported"
        assert response.wsgi_request.path_info == path
        rendered = str(
            [
                (
                    s.name,
                    dict(s.attributes or {}),
                    [(e.name, dict(e.attributes or {})) for e in s.events],
                )
                for s in spans
            ]
        )
        if checkout:
            leaked_fields = [
                (s.name, key)
                for s in spans
                for key, value in dict(s.attributes or {}).items()
                if SUPPLIED in str(key) or SUPPLIED in str(value)
            ]
            assert not leaked_fields, "bare session exported in fields: " + str(
                leaked_fields
            )
            assert SUPPLIED not in rendered, (
                "bare session survived the deployed span export path"
            )
        else:
            assert SUPPLIED in rendered, "unrelated export was removed"
    finally:
        tracer_provider.shutdown()


@pytest.mark.parametrize("placement", ["span-key", "event-key", "link-key"])
def test_span_attribute_names_cannot_export_checkout_value(placement):
    tracer_provider, exporter = provider()
    tracer = tracer_provider.get_tracer("independent-additional")
    context = SpanContext(1, 1, False, TraceFlags(1))
    links = [Link(context, {SUPPLIED: "ordinary"})] if placement == "link-key" else []
    try:
        with tracer.start_as_current_span("work", links=links) as span:
            span.set_attribute("http.url", "http://localhost" + CHECKOUT)
            if placement == "span-key":
                span.set_attribute(SUPPLIED, "ordinary")
            if placement == "event-key":
                span.add_event("event", {SUPPLIED: "ordinary"})
        (exported,) = exporter.get_finished_spans()
        rendered = str(
            (
                dict(exported.attributes),
                [dict(e.attributes) for e in exported.events],
                [dict(link.attributes) for link in exported.links],
            )
        )
        assert SUPPLIED not in rendered, "attribute name exported the checkout value"
    finally:
        tracer_provider.shutdown()


@pytest.mark.parametrize("placement", ["msg", "args"])
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
def test_logging_boundary_does_not_evaluate_unevaluated_lazy_carrier(
    placement, boundary
):
    consumed = []
    stream = io.BytesIO(CHECKOUT.encode())

    def evaluate():
        consumed.append("evaluated")
        return stream.read().decode()

    def run():
        stream.seek(0)
        value = SimpleLazyObject(evaluate)
        with capture() as (source, sinks):
            record = source.makeRecord(
                source.name,
                40,
                __file__,
                1,
                "late metadata",
                (),
                None,
                extra={"url": CHECKOUT},
            )
            if placement == "msg":
                record.msg = value
            else:
                record.msg, record.args = "late metadata %s", (value,)
            dispatch(source, sinks, record, boundary)
        observed = (list(consumed), stream.tell())
        consumed.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert with_boundary == natively, (
        "the sink boundary evaluated an application lazy value or read its"
        " source beyond what logging itself does"
    )
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"
