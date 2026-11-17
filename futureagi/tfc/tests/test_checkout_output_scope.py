"""Final outputs keep checkout scope across export queues and escape spellings."""

import inspect
import io
import json
import logging
import logging.config
import re
from collections import UserString
from email.headerregistry import Address
from enum import Enum
from functools import partial
from pathlib import PurePosixPath
from urllib.parse import unquote

import pytest
import structlog
from django.http import HttpRequest

from tfc.logging.checkout import CheckoutLoggingMiddleware
from tfc.logging.config import configure_structlog, get_logging_config
from tfc.tests.checkout_sinks import configured_sinks, dispatch, native_emit

pytestmark = pytest.mark.integration
VALUE = "synthetic-scope-segment-0428"
PREFIX = "/agentcc/managed-credits/checkout/"
SLASH = chr(92)


def capture_exporter(exporter, protocol, captured):
    if protocol == "grpc":

        class Client:
            def Export(self, request, **kwargs):
                captured.append(request.SerializeToString())

        exporter._client = Client()
    else:

        class Response:
            status_code = 200
            reason = "OK"
            text = ""
            ok = True
            content = b""

        def post(*args, **kwargs):
            captured.append(kwargs["data"])
            return Response()

        exporter._session.post = post


def request_for(checkout):
    request = HttpRequest()
    request.path_info = (PREFIX if checkout else "/ordinary/") + VALUE + "/"
    request.path = request.path_info
    return request


@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
def test_queued_log_export_keeps_request_scope(monkeypatch, protocol, checkout):
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    from tfc.telemetry.logs import _create_log_exporter

    configure_structlog(cache_logger_on_first_use=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    exporter = _create_log_exporter()
    captured = []
    capture_exporter(exporter, protocol, captured)
    provider = LoggerProvider(resource=Resource({"service.name": "scope-queue"}))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(exporter, schedule_delay_millis=600000)
    )
    source = logging.Logger("scope.queue", level=1)
    console = io.StringIO()
    source.handlers = [
        logging.StreamHandler(console),
        LoggingHandler(logger_provider=provider),
    ]
    arguments = ("session %s status processed", VALUE)
    request = request_for(checkout)

    def view(_):
        source.error(*arguments)
        return "complete"

    try:
        assert CheckoutLoggingMiddleware(view)(request) == "complete"
        assert not captured, "export must happen after the request scope ends"
        provider.force_flush()
        wire = b"".join(captured)
        assert wire
        if checkout:
            assert VALUE not in console.getvalue()
            assert VALUE.encode() not in wire
        else:
            assert VALUE in console.getvalue()
            assert VALUE.encode() in wire
        assert request.path_info.endswith(VALUE + "/")
    finally:
        provider.shutdown()


@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
def test_queued_span_export_keeps_request_scope(monkeypatch, protocol, checkout):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from tfc.telemetry.config import _create_exporter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    exporter = _create_exporter()
    captured = []
    capture_exporter(exporter, protocol, captured)
    # The SDK processor alone: no span-level redaction before the queue.
    provider = TracerProvider(resource=Resource({"service.name": "scope-queue"}))
    provider.add_span_processor(
        BatchSpanProcessor(exporter, schedule_delay_millis=600000)
    )
    tracer = provider.get_tracer("scope.queue")

    def view(_):
        with tracer.start_as_current_span("work", attributes={"session": VALUE}):
            pass
        return "complete"

    try:
        assert CheckoutLoggingMiddleware(view)(request_for(checkout)) == "complete"
        assert not captured
        provider.force_flush()
        wire = b"".join(captured)
        assert wire
        assert (VALUE.encode() not in wire) if checkout else (VALUE.encode() in wire)
    finally:
        provider.shutdown()


def spelled(checkout, form):
    prefix = PREFIX if checkout else "/ordinary/"
    if form == "unicode-slash":
        prefix = prefix.replace("/", SLASH + "u002f")
    elif form == "unicode-letter":
        prefix = prefix.replace("checkout", "checkou" + SLASH + "u0074")
    elif form == "hex-slash":
        prefix = prefix.replace("/", SLASH + "x2f")
    elif form == "wide-slash":
        prefix = prefix.replace("/", SLASH + "U0000002f")
    elif form == "percent-unicode":
        prefix = prefix.replace("/", "%2f").replace(
            "checkout", "checkou" + SLASH + "u0074"
        )
    elif form == "escaped-backslash":
        prefix = prefix.replace("/", SLASH + SLASH + "u002f")
    return prefix + VALUE + "/"


def decoded(text):
    for _ in range(6):
        text = unquote(text)
        text = re.sub(
            r"\\+(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|U([0-9a-fA-F]{8}))",
            lambda m: chr(int(m[1] or m[2] or m[3], 16)),
            text,
        )
    return text


def json_strings(text):
    fields, pending = [], [json.loads(line) for line in text.splitlines()]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            fields.append(value)
    return "\n".join(fields)


FORMS = [
    "unicode-slash",
    "unicode-letter",
    "hex-slash",
    "wide-slash",
    "percent-unicode",
    "escaped-backslash",
]


@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("form", FORMS)
def test_escaped_paths_at_configured_outputs(
    tmp_path, monkeypatch, form, environment, checkout
):
    monkeypatch.setenv("ENV_TYPE", environment)
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))
    configurator = logging.config.DictConfigurator(config)
    for name in config["formatters"]:
        configurator.config["formatters"][name] = configurator.configure_formatter(
            configurator.config["formatters"][name]
        )
    for name in config["filters"]:
        configurator.config["filters"][name] = configurator.configure_filter(
            configurator.config["filters"][name]
        )
    for name in config["handlers"]:
        handler = configurator.configure_handler(configurator.config["handlers"][name])
        stream = io.StringIO()
        handler.setStream(stream)
        try:
            record = logging.LogRecord(
                "scope.escaped", 40, __file__, 1, spelled(checkout, form), (), None
            )
            handler.emit(record)
            text = stream.getvalue()
            observed = decoded(
                json_strings(text) if environment == "production" else text
            )
            assert (VALUE not in observed) if checkout else (VALUE in observed), (
                name,
                text,
            )
        finally:
            handler.close()


@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
@pytest.mark.parametrize("signal", ["trace", "log"])
@pytest.mark.parametrize("form", ["unicode-slash", "hex-slash", "percent-unicode"])
def test_escaped_paths_in_final_otlp_bytes(
    monkeypatch, form, signal, protocol, checkout
):
    from opentelemetry.sdk.resources import Resource

    from tfc.telemetry.config import _create_exporter
    from tfc.telemetry.logs import _create_log_exporter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    exporter = _create_exporter() if signal == "trace" else _create_log_exporter()
    captured = []
    capture_exporter(exporter, protocol, captured)
    path = spelled(checkout, form)
    if signal == "trace":
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider = TracerProvider(resource=Resource({"target": path}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("scope.escaped")
        with tracer.start_as_current_span(path, attributes={"target": path}):
            pass
    else:
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

        provider = LoggerProvider(resource=Resource({"target": path}))
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        record = logging.LogRecord("scope.escaped", 40, __file__, 1, path, (), None)
        LoggingHandler(logger_provider=provider).emit(record)
    try:
        provider.force_flush()
        wire = b"".join(captured)
        assert wire
        assert (VALUE.encode() not in wire) if checkout else (VALUE.encode() in wire)
    finally:
        provider.shutdown()


METADATA = {
    "partial": lambda path: partial(str, path),
    "bound-arguments": lambda path: inspect.signature(lambda target: None).bind(path),
    "parameter": lambda path: inspect.Parameter(
        "target", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=path
    ),
    "signature": lambda path: inspect.Signature(return_annotation=path),
    "logger": logging.getLogger,
    "address": lambda path: Address(
        display_name=path, username="reader", domain="localhost"
    ),
    "enum": lambda path: Enum("StoredTarget", {"ITEM": path}).ITEM,
}


def delivered(record, boundary):
    """Deliver record at boundary to the configured sinks; return the sinks."""
    with configured_sinks("scope.raw") as (source, sinks):
        dispatch(source, sinks, record, boundary)
    return sinks


@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("placement", ["msg", "args", "extra"])
@pytest.mark.parametrize("kind", sorted(METADATA))
def test_metadata_carrier_at_configured_sinks(kind, placement, boundary, checkout):
    configure_structlog(cache_logger_on_first_use=False)
    carrier = METADATA[kind]((PREFIX if checkout else "/ordinary/") + VALUE + "/")
    original = repr(carrier)
    record = logging.LogRecord("scope.raw", 40, __file__, 1, "metadata", (), None)
    if placement == "msg":
        record.msg = carrier
    elif placement == "args":
        record.msg, record.args = "metadata %s", (carrier,)
    else:
        record.payload = carrier
    sinks = delivered(record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert VALUE in sinks.outputs()["record"]
    assert repr(carrier) == original


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("factory", [UserString, PurePosixPath])
def test_assembled_object_segment_at_configured_sinks(factory, boundary):
    configure_structlog(cache_logger_on_first_use=False)
    record = logging.LogRecord(
        "scope.raw", 40, __file__, 1, "%s%s/", (PREFIX, factory(VALUE)), None
    )
    sinks = delivered(record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.parametrize("form", ["inferred", "explicit"])
def test_structured_current_exception_renders_once_per_native_use(form):
    configure_structlog(cache_logger_on_first_use=False)
    stream = io.BytesIO((PREFIX + VALUE + "/").encode())
    calls = []

    class SourceError(Exception):
        def __str__(self):
            calls.append("rendered")
            return stream.read().decode()

    def run():
        stream.seek(0)
        with configured_sinks("scope.structured.exception") as (source, sinks):
            try:
                raise SourceError(PREFIX + VALUE + "/")
            except SourceError as error:
                info = True if form == "inferred" else (type(error), error, None)
                structlog.get_logger(source.name).error("metadata", exc_info=info)
        observed = (list(calls), stream.tell())
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    # No pre-render pass: the exception is rendered only where logging does.
    assert with_boundary == natively


@pytest.mark.parametrize("environment", ["production", "local"])
def test_marker_prefixed_segment_at_configured_outputs(
    tmp_path, monkeypatch, environment
):
    monkeypatch.setenv("ENV_TYPE", environment)
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))
    configurator = logging.config.DictConfigurator(config)
    for name in config["formatters"]:
        configurator.config["formatters"][name] = configurator.configure_formatter(
            configurator.config["formatters"][name]
        )
    for name in config["filters"]:
        configurator.config["filters"][name] = configurator.configure_filter(
            configurator.config["filters"][name]
        )
    target = PREFIX + "[redacted]" + VALUE + "/"
    for name in config["handlers"]:
        handler = configurator.configure_handler(configurator.config["handlers"][name])
        stream = io.StringIO()
        handler.setStream(stream)
        try:
            record = logging.LogRecord(
                "scope.marker", 40, __file__, 1, target, (), None
            )
            handler.emit(record)
            assert VALUE not in stream.getvalue(), name
        finally:
            handler.close()


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
def test_marker_prefixed_segment_at_every_sink(boundary):
    configure_structlog(cache_logger_on_first_use=False)
    record = logging.LogRecord(
        "scope.raw", 40, __file__, 1, PREFIX + "[redacted]" + VALUE + "/", (), None
    )
    sinks = delivered(record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("field", ["levelname", "name"])
def test_directly_built_record_text_fields(field, boundary):
    configure_structlog(cache_logger_on_first_use=False)
    record = logging.makeLogRecord({"msg": "metadata", "levelno": 40})
    assert record.funcName is None
    setattr(record, field, PREFIX + VALUE + "/")
    sinks = delivered(record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    assert sinks.structured(), "a production line is not one JSON document"
