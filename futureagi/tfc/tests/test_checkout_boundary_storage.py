"""Independent storage, render-hook and completed-request lifecycle coverage.

Logging scenarios are observed in what the configured sinks emit
(checkout_sinks), not in the in-memory record.
"""

import io
from collections import Counter
from pathlib import PurePosixPath

import pytest
from django.test import RequestFactory
from django.utils.functional import SimpleLazyObject
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, TraceFlags

from tfc.logging.checkout import CheckoutLoggingMiddleware
from tfc.telemetry.config import build_span_processor
from tfc.telemetry.naming import django_request_hook
from tfc.tests.checkout_sinks import dispatch, native_emit
from tfc.tests.test_checkout_additional_exports import (
    CARRIERS,
    CHECKOUT,
    SUPPLIED,
    capture,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", sorted(CARRIERS))
@pytest.mark.parametrize("placement", ["msg", "args", "extra", "exc_info"])
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("checkout", [True, False])
def test_independent_carrier_placements(kind, placement, boundary, checkout):
    path = CHECKOUT if checkout else "/ordinary/" + SUPPLIED + "/"
    carrier = CARRIERS[kind](path)
    with capture() as (source, sinks):
        record = source.makeRecord(source.name, 40, __file__, 1, "metadata", (), None)
        if placement == "args":
            record.msg, record.args = "metadata %s", (carrier,)
        elif placement == "exc_info":
            error = ValueError("metadata")
            error.payload = carrier
            record.exc_info = (ValueError, error, None)
        elif placement == "extra":
            record.payload = carrier
        else:
            setattr(record, placement, carrier)
        dispatch(source, sinks, record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"
        assert sinks.structured(), "a production line is not one JSON document"
        # Records are not rewritten: only the sinks' output is sanitized.
        if placement == "exc_info":
            assert record.exc_info[1].payload is carrier
        else:
            assert record.exc_info is None
    else:
        # The boundary must leave unrelated input identities untouched.
        if placement == "args":
            assert record.args[0] is carrier
        elif placement == "exc_info":
            assert record.exc_info[1].payload is carrier
        else:
            assert (
                getattr(record, "payload" if placement == "extra" else placement)
                is carrier
            )


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("placement", ["msg", "args", "mapping-args", "nested-args"])
@pytest.mark.parametrize("kind", ["lazy", "custom"])
def test_rendering_uses_a_copy_without_application_hooks(boundary, placement, kind):
    calls = []
    stream = io.BytesIO(CHECKOUT.encode())

    def evaluate():
        calls.append("render")
        return stream.read().decode()

    class ApplicationValue:
        def __str__(self):
            return evaluate()

        __repr__ = __str__

        @property
        def __class__(self):
            calls.append("class")
            return ApplicationValue

    def run():
        stream.seek(0)
        value = SimpleLazyObject(evaluate) if kind == "lazy" else ApplicationValue()
        with capture() as (source, sinks):
            record = source.makeRecord(
                source.name,
                40,
                __file__,
                1,
                "metadata",
                (),
                None,
                extra={"url": CHECKOUT},
            )
            if placement == "msg":
                record.msg = value
            elif placement == "args":
                record.msg, record.args = "metadata %s", (value,)
            elif placement == "mapping-args":
                record.msg, record.args = "metadata %(payload)s", {"payload": value}
            else:
                record.msg, record.args = "metadata %s", ([value],)
            dispatch(source, sinks, record, boundary)
        observed = (Counter(calls), stream.tell())
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    # The boundary renders nothing itself: the application's hooks run and
    # its source is read exactly as the same sinks do natively.
    assert with_boundary == natively
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
def test_path_built_from_segments_uses_storage_without_path_hooks(boundary):
    calls = []

    class ApplicationPath(PurePosixPath):
        def __str__(self):
            calls.append("str")
            return super().__str__()

        def __fspath__(self):
            calls.append("fspath")
            return super().__fspath__()

    def run():
        carrier = ApplicationPath("/agentcc", "managed-credits", "checkout", SUPPLIED)
        with capture() as (source, sinks):
            record = source.makeRecord(
                source.name,
                40,
                __file__,
                1,
                "metadata",
                (),
                None,
                extra={"payload": carrier},
            )
            dispatch(source, sinks, record, boundary)
        observed = Counter(calls)
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert with_boundary == natively, "the sink boundary called a path hook"
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"


@pytest.mark.parametrize(
    "source", ["initial-attributes", "request-hook", "request-scope"]
)
@pytest.mark.parametrize("batch", [False, True])
def test_span_retains_values_after_request_scope_and_url_are_gone(source, batch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        build_span_processor(exporter, use_batch_processor=batch)
    )
    tracer = provider.get_tracer("completed-request")
    request = RequestFactory().get(CHECKOUT)
    links = [Link(SpanContext(1, 1, False, TraceFlags(1)), {SUPPLIED: "ordinary"})]
    try:
        if source == "initial-attributes":
            span = tracer.start_span(
                "server", attributes={"http.url": CHECKOUT}, links=links
            )
            span.set_attribute(
                "http.url", "/agentcc/managed-credits/checkout/[redacted]/"
            )
        elif source == "request-hook":
            span = tracer.start_span("server", links=links)
            django_request_hook(span, request)
        else:
            span = CheckoutLoggingMiddleware(
                lambda _: tracer.start_span("server", links=links)
            )(request)
        # This work runs after the logging ContextVar has been reset.
        span.set_attribute("request.id", SUPPLIED)
        span.set_attribute(SUPPLIED, "ordinary")
        span.add_event("event", {SUPPLIED: "ordinary", "value": SUPPLIED})
        span.end()
        with tracer.start_as_current_span("unrelated") as unrelated:
            unrelated.set_attribute("request.id", SUPPLIED)
        assert provider.force_flush()
        finished = exporter.get_finished_spans()
        server = next(s for s in finished if s.name == "server")
        ordinary = next(s for s in finished if s.name == "unrelated")
        exported = str(
            (
                dict(server.attributes),
                [dict(e.attributes) for e in server.events],
                [dict(link.attributes) for link in server.links],
            )
        )
        assert SUPPLIED not in exported
        assert ordinary.attributes["request.id"] == SUPPLIED
        assert request.path_info == CHECKOUT
    finally:
        provider.shutdown()
