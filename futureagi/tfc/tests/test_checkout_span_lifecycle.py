"""Span-local lifetime, independent exporters and fixed-schema controls."""

import asyncio
import gc
from weakref import ref

import pytest
from django.test import RequestFactory
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.trace import Link, SpanContext, TraceFlags

from tfc.logging.checkout import CheckoutLoggingMiddleware
from tfc.telemetry.config import build_span_processor
from tfc.telemetry.naming import django_request_hook

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"
VALUES = ["synthetic-scope-alpha", "synthetic-scope-bravo"]


def configured():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        build_span_processor(exporter, use_batch_processor=False)
    )
    return provider, exporter


def test_independent_exporters_share_values_until_completion():
    provider, first = configured()
    second = InMemorySpanExporter()
    provider.add_span_processor(build_span_processor(second, use_batch_processor=False))
    try:
        span = provider.get_tracer("multi-export").start_span("server")
        django_request_hook(span, RequestFactory().get(PREFIX + VALUES[0] + "/"))
        span.set_attribute("request.id", VALUES[0])
        span.end()
        for exporter in (first, second):
            (finished,) = exporter.get_finished_spans()
            assert VALUES[0] not in str(dict(finished.attributes))
    finally:
        provider.shutdown()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_overlapping_requests_keep_separate_span_values(asynchronous):
    provider, exporter = configured()
    tracer = provider.get_tracer("overlapping-requests")
    spans = []

    async def create(request):
        await asyncio.sleep(0)
        span = tracer.start_span("work")
        await asyncio.sleep(0)
        return span

    async def requests():
        middleware = CheckoutLoggingMiddleware(create)
        spans.extend(
            await asyncio.gather(
                *(
                    middleware(RequestFactory().get(PREFIX + value + "/"))
                    for value in VALUES
                )
            )
        )

    try:
        if asynchronous:
            asyncio.run(requests())
        else:
            for value in VALUES:
                span = tracer.start_span("work")
                django_request_hook(span, RequestFactory().get(PREFIX + value + "/"))
                spans.append(span)
        for i, span in enumerate(spans):
            span.set_attribute("own", VALUES[i])
            span.set_attribute("other", VALUES[1 - i])
        for span in reversed(spans):
            span.end()
        finished = list(reversed(exporter.get_finished_spans()))
        for i, span in enumerate(finished):
            assert span.attributes["own"] == "[redacted]"
            assert span.attributes["other"] == VALUES[1 - i]
    finally:
        provider.shutdown()


def test_completed_and_abandoned_spans_do_not_retain_registry_entries():
    from tfc.telemetry.redaction import CheckoutRedactingSpanProcessor

    exporter = InMemorySpanExporter()
    processor = CheckoutRedactingSpanProcessor(SimpleSpanProcessor(exporter))
    provider = TracerProvider()
    provider.add_span_processor(processor)
    try:
        tracer = provider.get_tracer("span-lifetime")
        span = tracer.start_span(
            "abandoned", attributes={"http.url": PREFIX + VALUES[0]}
        )
        reference = ref(span)
        del span
        gc.collect()
        assert reference() is None
        assert not getattr(processor, "_active", {})
        with tracer.start_as_current_span("completed"):
            pass
        assert not getattr(processor, "_active", {})
    finally:
        provider.shutdown()


@pytest.mark.parametrize("supplied", ["http", "opentelemetry.semconv.trace"])
def test_only_fixed_schema_keys_are_exempt_from_dynamic_key_redaction(supplied):
    provider, exporter = configured()
    url = PREFIX + supplied + "/"
    dynamic = supplied + ".user-field"
    # A class's metadata is not a semantic-convention attribute name.
    module_key = SpanAttributes.__module__
    attrs = {"http.url": url, dynamic: "ordinary", module_key: "ordinary"}
    try:
        with provider.get_tracer("schema-keys").start_as_current_span(
            "work",
            attributes=attrs,
            links=[Link(SpanContext(1, 1, False, TraceFlags(1)), attrs)],
        ) as span:
            span.add_event("event", attrs)
        (finished,) = exporter.get_finished_spans()
        for fields in (
            finished.attributes,
            finished.events[0].attributes,
            finished.links[0].attributes,
        ):
            assert "http.url" in fields
            assert fields["http.url"].endswith("/[redacted]/")
            assert dynamic not in fields
            if supplied == module_key:
                assert module_key not in fields
    finally:
        provider.shutdown()
