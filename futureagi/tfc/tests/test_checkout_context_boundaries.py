"""Independent synthetic carrier, formatter and trace-context controls.

Logging scenarios are observed in what the configured sinks emit
(checkout_sinks; ruling 681 addendum 5), not in the in-memory record.
"""

import io
import json
import logging
from argparse import Namespace
from contextlib import contextmanager
from email.header import Header

import httpx
import pytest
import structlog
from django.core.files.base import File
from django.test import Client, override_settings
from django.utils.functional import SimpleLazyObject
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, TraceFlags, TraceState
from starlette.datastructures import UploadFile

from tfc.logging.config import configure_structlog, get_logging_config
from tfc.settings.settings import MIDDLEWARE as DEPLOYED
from tfc.telemetry.config import build_span_processor
from tfc.telemetry.naming import django_request_hook, django_response_hook
from tfc.tests.checkout_sinks import configured_sinks, dispatch, native_emit

pytestmark = pytest.mark.integration
VALUE = "synthetic-further-checkout-681"
PREFIX = "/agentcc/managed-credits/checkout/"


def path(checkout):
    return (PREFIX if checkout else "/ordinary/") + VALUE + "/"


@contextmanager
def capture(**options):
    with configured_sinks("independent.further.boundaries", **options) as (
        source,
        sinks,
    ):
        yield source, sinks


CARRIERS = {
    "namespace": lambda p: Namespace(target=p),
    "email-header": Header,
    "django-file": lambda p: File(io.BytesIO(b"unrelated body"), name=p),
    "httpx-proxy": lambda p: httpx.Proxy("http://localhost" + p),
    "starlette-upload-file": lambda p: UploadFile(
        io.BytesIO(b"unrelated body"), filename=p
    ),
}


@pytest.mark.parametrize("kind", sorted(CARRIERS))
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("checkout", [True, False])
def test_independent_further_standard_carriers(kind, boundary, checkout):
    carrier = CARRIERS[kind](path(checkout))
    before = str(carrier)
    assert VALUE in before, "candidate must actually render the supplied value"
    buffer = carrier.file if isinstance(carrier, (UploadFile, File)) else None
    position = buffer.tell() if buffer is not None else None
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
    assert str(carrier) == before, "application carrier was mutated"
    if buffer is not None:
        assert buffer.tell() == position, "carrier stream was consumed"
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(VALUE) == [], (
            "additional standard carrier reached a configured sink"
        )
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert record.payload is carrier
        assert VALUE in sinks.outputs()["record"]


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("placement", ["_name-text", "_name-container", "levelname"])
@pytest.mark.parametrize("checkout", [True, False])
def test_dynamic_logging_schema_values(boundary, placement, checkout):
    carried = path(checkout)
    with capture() as (source, sinks):
        record = source.makeRecord(source.name, 40, __file__, 1, "metadata", (), None)
        if placement == "levelname":
            record.levelname = carried
        else:
            record._name = (
                {"target": carried} if placement.endswith("container") else carried
            )
        dispatch(source, sinks, record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(VALUE) == [], "schema exemption retained request data"
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert VALUE in sinks.outputs()["record"]


@pytest.mark.parametrize("placement", ["msg", "args"])
@pytest.mark.parametrize("formatter_kind", ["base", "production"])
def test_direct_production_formatter_uses_no_application_lazy_hooks(
    tmp_path, monkeypatch, placement, formatter_kind
):
    calls = []
    stream = io.BytesIO(path(True).encode())

    def evaluate():
        calls.append("evaluated")
        return stream.read().decode()

    monkeypatch.setenv("ENV_TYPE", "production")
    configure_structlog(cache_logger_on_first_use=False)
    configuration = get_logging_config(str(tmp_path))["formatters"]["structured"]
    formatter = (
        logging.Formatter()
        if formatter_kind == "base"
        else structlog.stdlib.ProcessorFormatter(
            processor=configuration["processor"],
            foreign_pre_chain=configuration["foreign_pre_chain"],
        )
    )

    def run():
        # The formatter is the sink's: its output is what a StreamHandler writes.
        stream.seek(0)
        lazy = SimpleLazyObject(evaluate)
        record = logging.LogRecord(
            "further.formatter", 40, __file__, 1, "metadata", (), None
        )
        record.url = path(True)
        if placement == "msg":
            record.msg = lazy
        else:
            record.msg, record.args = "metadata %s", (lazy,)
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(formatter)
        handler.emit(record)
        observed = {"factory_calls": len(calls), "stream_position": stream.tell()}
        calls.clear()
        return output.getvalue(), observed

    written, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert with_boundary == natively, (
        "the sink boundary evaluated or consumed the original beyond the"
        " formatter's own rendering: " + str((with_boundary, natively))
    )
    assert written.strip(), "the sink wrote nothing"
    assert VALUE not in written, "checkout session value reached the sink"
    if formatter_kind == "production":
        json.loads(written)


@pytest.mark.parametrize("placement", ["msg", "args", "extra"])
@pytest.mark.parametrize("engine", ["stdlib", "structlog"])
def test_configured_structured_logger_does_not_evaluate_lazy_sources(placement, engine):
    calls = []
    stream = io.BytesIO(path(True).encode())

    def evaluate():
        calls.append("evaluated")
        return stream.read().decode()

    def run():
        stream.seek(0)
        value = SimpleLazyObject(evaluate)
        with capture() as (source, sinks):
            logger = (
                structlog.get_logger(source.name) if engine == "structlog" else source
            )
            if engine == "structlog":
                if placement == "msg":
                    logger.error(value, url=path(True))
                elif placement == "args":
                    logger.error("metadata %s", value, url=path(True))
                else:
                    logger.error("metadata", payload=value, url=path(True))
            elif placement == "msg":
                logger.error(value, extra={"url": path(True)})
            elif placement == "args":
                # Construct first so the stdlib LogRecord constructor's own
                # Mapping check is outside this probe (it evaluates proxies).
                record = source.makeRecord(
                    source.name,
                    40,
                    __file__,
                    1,
                    "metadata",
                    (),
                    None,
                    extra={"url": path(True)},
                )
                record.msg, record.args = "metadata %s", (value,)
                source.handle(record)
            else:
                logger.error("metadata", extra={"payload": value, "url": path(True)})
        observed = {"factory_calls": len(calls), "stream_position": stream.tell()}
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    assert with_boundary == natively, (
        "the sink boundary evaluated or consumed the original beyond the"
        " configured logger's own rendering: " + str((with_boundary, natively))
    )


def test_standard_logger_extra_name_uses_the_same_boundary():
    with capture() as (source, sinks):
        source.error("metadata", extra={"_name": path(True)})
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "ordinary logging extra bypassed redaction"
    assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.django_db
@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("field", ["context", "parent"])
def test_deployed_exported_trace_state(field, checkout):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(
        build_span_processor(exporter, use_batch_processor=False)
    )
    try:
        with override_settings(
            MIDDLEWARE=list(DEPLOYED),
            DEBUG=False,
            ALLOWED_HOSTS=["testserver"],
            APPEND_SLASH=True,
            SECURE_SSL_REDIRECT=False,
        ):
            DjangoInstrumentor().instrument(
                tracer_provider=provider,
                request_hook=django_request_hook,
                response_hook=django_response_hook,
            )
            try:
                response = Client().get(
                    path(checkout).rstrip("/"),
                    HTTP_TRACEPARENT="00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
                    HTTP_TRACESTATE="vendor=" + VALUE,
                )
            finally:
                DjangoInstrumentor().uninstrument()
        assert response.wsgi_request.path_info == path(checkout).rstrip("/")
        spans = exporter.get_finished_spans()
        assert spans
        states = [
            getattr(s, field).trace_state.to_header()
            for s in spans
            if getattr(s, field) is not None
        ]
        assert states, "no exported tracing contexts were observed"
        wire = encode_spans(spans).SerializeToString()
        if checkout:
            exposed = {
                "span_context": VALUE in str(states),
                "otlp_bytes": VALUE.encode() in wire,
            }
            assert not any(exposed.values()), (
                "checkout request metadata survived exported trace state: "
                + str(exposed)
            )
        else:
            assert VALUE in str(states), "unrelated trace state must stay unchanged"
            assert VALUE.encode() in wire, (
                "unrelated state must remain in the actual OTLP serialization"
            )
    finally:
        provider.shutdown()


@pytest.mark.parametrize("checkout", [True, False])
def test_exported_link_trace_state(checkout):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(
        build_span_processor(exporter, use_batch_processor=False)
    )
    linked = SpanContext(1, 1, False, TraceFlags(1), TraceState([("vendor", VALUE)]))
    try:
        with provider.get_tracer("further.link").start_as_current_span(
            "work", attributes={"http.url": path(checkout)}, links=[Link(linked)]
        ):
            pass
        (span,) = exporter.get_finished_spans()
        state = span.links[0].context.trace_state.to_header()
        # The first exporting processor receives this ReadableSpan context.
        # This SDK's current OTLP encoder omits link trace_state; do not claim
        # that this field is present on its wire from a substring search.
        if checkout:
            assert VALUE not in state, (
                "checkout session retained in the first exported link context"
            )
        else:
            assert VALUE in state
    finally:
        provider.shutdown()
