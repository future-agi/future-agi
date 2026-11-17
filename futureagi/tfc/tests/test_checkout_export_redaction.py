"""Checkout sessions stay out of exported span names, span attributes and analytics.

Actual routes go through the deployed middleware stack. Unrelated routes must
export exactly what they exported before.
"""

from contextlib import contextmanager

import pytest
from django.test import Client, override_settings
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind
from rest_framework.test import APIClient

from tfc.settings.settings import MIDDLEWARE as DEPLOYED
from tfc.telemetry.naming import django_request_hook, django_response_hook

pytestmark = pytest.mark.integration
SUPPLIED = "synthetic-export-session-value"
CHECKOUT = "/agentcc/managed-credits/checkout/" + SUPPLIED
# (path_info, SCRIPT_NAME): resolved view, APPEND_SLASH redirect before the
# view, unresolved suffix, and a mounted deployment.
CHECKOUT_SHAPES = [
    (CHECKOUT + "/", ""),
    (CHECKOUT, ""),
    (CHECKOUT + "/missing", ""),
    (CHECKOUT + "/", "/mounted"),
]
UNRELATED = ["/agentcc/managed-credits/", "/ordinary-unrelated/" + SUPPLIED + "/"]


@contextmanager
def deployed():
    # A copy: OpenTelemetry's instrumentor edits the MIDDLEWARE list in place.
    with override_settings(
        MIDDLEWARE=list(DEPLOYED),
        DEBUG=False,
        APPEND_SLASH=True,
        ALLOWED_HOSTS=["testserver"],
        SECURE_SSL_REDIRECT=False,
    ):
        yield


def rendered(spans):
    return str(
        [
            (
                span.name,
                dict(span.attributes or {}),
                [(event.name, dict(event.attributes or {})) for event in span.events],
                span.status.description,
            )
            for span in spans
        ]
    )


def local_provider(processor_for):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(processor_for(exporter))
    return provider, exporter


@pytest.mark.django_db
@pytest.mark.parametrize("path,script_name", CHECKOUT_SHAPES)
def test_context_middleware_attributes_redact_checkout_session(path, script_name):
    # No export boundary here: the middleware's own attributes must be safe.
    provider, exporter = local_provider(SimpleSpanProcessor)
    with (
        deployed(),
        provider.get_tracer("checkout-export").start_as_current_span("server"),
    ):
        response = Client().get(path, SCRIPT_NAME=script_name)
    assert response.wsgi_request.path_info == path
    (server,) = [s for s in exporter.get_finished_spans() if s.name == "server"]
    assert "http.route" in server.attributes and "http.url" in server.attributes
    assert SUPPLIED not in rendered([server]), (
        "checkout session value exported in span attributes"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("path", UNRELATED)
def test_context_middleware_attributes_unchanged_for_unrelated_routes(path):
    provider, exporter = local_provider(SimpleSpanProcessor)
    with (
        deployed(),
        provider.get_tracer("checkout-export").start_as_current_span("server"),
    ):
        response = Client().get(path)
    request = response.wsgi_request
    (server,) = [s for s in exporter.get_finished_spans() if s.name == "server"]
    assert server.attributes["http.route"] == request.path
    assert server.attributes["http.url"] == request.build_absolute_uri()


@pytest.mark.django_db
@pytest.mark.parametrize("path", [CHECKOUT + "/", *UNRELATED])
def test_span_naming_hooks_redact_only_checkout_paths(path):
    with deployed():
        response = Client().get(path)
    request = response.wsgi_request
    provider, exporter = local_provider(SimpleSpanProcessor)
    span = provider.get_tracer("checkout-export").start_span(request.method)
    django_request_hook(span, request)
    django_response_hook(span, request, response)
    span.end()
    (exported,) = exporter.get_finished_spans()
    resolved = request.resolver_match is not None
    if path.startswith(CHECKOUT):
        assert resolved, "the checkout status view must resolve"
        assert SUPPLIED not in rendered([exported]), (
            "checkout session value exported in a span name or attribute"
        )
        # Redacted before truncation: no partial session survives in the name.
        assert exported.name.startswith(
            "GET /agentcc/managed-credits/checkout/[redacted]/ → "
        )
    else:
        assert exported.attributes["http.target"] == request.path
        if resolved:
            assert exported.name.startswith(f"GET {request.path} → ")


def instrumented_request(path, script_name=""):
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    from tfc.telemetry.config import build_span_processor

    provider, exporter = local_provider(
        lambda exporter: build_span_processor(exporter, use_batch_processor=False)
    )
    with deployed():
        DjangoInstrumentor().instrument(
            tracer_provider=provider,
            request_hook=django_request_hook,
            response_hook=django_response_hook,
        )
        try:
            response = Client().get(path, SCRIPT_NAME=script_name)
        finally:
            DjangoInstrumentor().uninstrument()
    spans = exporter.get_finished_spans()
    servers = [span for span in spans if span.kind == SpanKind.SERVER]
    assert servers, "the instrumented request exported no server span"
    return response, spans, servers[-1]


@pytest.mark.django_db
@pytest.mark.parametrize("path,script_name", CHECKOUT_SHAPES)
def test_instrumented_server_spans_export_no_checkout_session(path, script_name):
    response, spans, server = instrumented_request(path, script_name)
    assert response.wsgi_request.path_info == path
    assert "http.url" in server.attributes
    assert SUPPLIED not in rendered(spans), (
        "checkout session value reached the span exporter"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("path", UNRELATED)
def test_instrumented_server_spans_unchanged_for_unrelated_routes(path):
    response, _, server = instrumented_request(path)
    request = response.wsgi_request
    assert server.attributes["http.url"] == request.build_absolute_uri()
    assert server.attributes["http.target"] == request.path
    assert server.attributes["http.route"] in (
        request.path,
        getattr(request.resolver_match, "route", None),
    )


def test_init_telemetry_installs_the_export_boundary(monkeypatch):
    from opentelemetry import trace

    from tfc.telemetry import config

    exporter = InMemorySpanExporter()
    installed = []
    monkeypatch.setattr(config, "OTEL_ENABLED", True)
    monkeypatch.setattr(config, "_initialized", False)
    monkeypatch.setattr(config, "_tracer_provider", None)
    monkeypatch.setattr(config, "_create_exporter", lambda: exporter)
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    provider = config.init_telemetry(
        component="checkout-export-test", use_batch_processor=False
    )
    assert installed == [provider]
    with provider.get_tracer("checkout-export").start_as_current_span("work") as span:
        span.set_attribute("http.url", "http://testserver" + CHECKOUT + "/")
    (exported,) = exporter.get_finished_spans()
    assert SUPPLIED not in rendered([exported])


def test_batch_export_path_is_behind_the_boundary():
    from tfc.telemetry.config import build_span_processor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        build_span_processor(exporter, use_batch_processor=True)
    )
    try:
        tracer = provider.get_tracer("checkout-export")
        with tracer.start_as_current_span("work") as span:
            span.set_attribute("http.url", "http://testserver" + CHECKOUT + "/")
        assert provider.force_flush()
        (exported,) = exporter.get_finished_spans()
    finally:
        provider.shutdown()
    assert SUPPLIED not in rendered([exported])


def test_exception_events_and_status_are_redacted_before_export():
    from tfc.telemetry.config import build_span_processor

    provider, exporter = local_provider(
        lambda exporter: build_span_processor(exporter, use_batch_processor=False)
    )
    url = "http://testserver" + CHECKOUT + "/"
    with pytest.raises(ValueError):
        with provider.get_tracer("checkout-export").start_as_current_span("work"):
            raise ValueError("lookup failed for " + url)
    (span,) = exporter.get_finished_spans()
    assert span.events and "exception.stacktrace" in span.events[0].attributes
    assert SUPPLIED not in rendered([span])


def test_span_redaction_failure_drops_the_span(monkeypatch):
    from tfc.logging import checkout
    from tfc.telemetry.config import build_span_processor

    def broken(payload):
        raise RuntimeError("redaction unavailable")

    monkeypatch.setattr(checkout, "find_checkout_values", broken)
    provider, exporter = local_provider(
        lambda exporter: build_span_processor(exporter, use_batch_processor=False)
    )
    with provider.get_tracer("checkout-export").start_as_current_span("work") as span:
        span.set_attribute("http.url", "http://testserver" + CHECKOUT + "/")
    assert exporter.get_finished_spans() == ()


class AnalyticsRecorder:
    is_enabled = True

    def __init__(self):
        self.events = []

    def capture(self, user_id, event_name, properties=None, groups=None):
        self.events.append((event_name, dict(properties or {})))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path,script_name",
    [*CHECKOUT_SHAPES, *((path, "") for path in UNRELATED)],
)
def test_analytics_request_path_through_deployed_stack(
    path, script_name, user, monkeypatch
):
    recorder = AnalyticsRecorder()
    monkeypatch.setattr("analytics.posthog_util.posthog_tracker", recorder)
    # Session login covers responses produced before a view runs (redirects,
    # 404s); DRF views authenticate the request themselves.
    client = APIClient()
    client.force_login(user)
    client.force_authenticate(user=user)
    with deployed():
        response = client.get(path, SCRIPT_NAME=script_name)
    request = response.wsgi_request
    assert recorder.events, "the analytics middleware captured no request"
    event_name, properties = recorder.events[-1]
    assert event_name == "api_request"
    if path.startswith(CHECKOUT):
        assert SUPPLIED not in str(recorder.events), (
            "checkout session value exported to analytics"
        )
        assert properties["path"].startswith(
            script_name + "/agentcc/managed-credits/checkout/"
        )
    else:
        assert properties["path"] == request.path
