"""Final rendered diagnostics preserve normal evaluation and redact paths."""

import inspect
import io
import logging
import logging.config
from collections import UserString
from email.header import Header
from email.headerregistry import Address
from enum import Enum, IntEnum
from functools import partial
from pathlib import PurePosixPath

import httpx
import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import SpanContext, TraceFlags, TraceState

from tfc.logging.checkout_output import scrub_rendered
from tfc.logging.config import configure_structlog, get_logging_config

pytestmark = pytest.mark.integration
VALUE = "synthetic-output-session"
PATH = "/agentcc/managed-credits/checkout/" + VALUE + "/"
FACTORIES = {
    "partial": lambda path: partial(str, path),
    "bound-arguments": lambda path: inspect.signature(lambda target: None).bind(path),
    "parameter": lambda path: inspect.Parameter(
        "target", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=path
    ),
    "signature": lambda path: inspect.Signature(return_annotation=path),
    "address": lambda path: Address(
        display_name=path, username="reader", domain="localhost"
    ),
    "enum": lambda path: Enum("StoredTarget", {"ITEM": path}).ITEM,
    "logger": logging.getLogger,
}


def stream_handler(formatter=None):
    configure_structlog(cache_logger_on_first_use=False)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(formatter or logging.Formatter("%(message)s %(payload)s"))
    return handler, output


@pytest.mark.parametrize("kind", sorted(FACTORIES))
@pytest.mark.parametrize("placement", ["msg", "args", "extra"])
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("checkout", [True, False])
def test_diagnostic_carriers_at_output(kind, placement, boundary, checkout):
    path = PATH if checkout else "/ordinary/" + VALUE + "/"
    carrier = FACTORIES[kind](path)
    original = repr(carrier)
    record = logging.LogRecord("output", 40, __file__, 1, "metadata", (), None)
    record.payload = ""
    if placement == "msg":
        record.msg = carrier
    elif placement == "args":
        record.msg, record.args = "metadata %s", (carrier,)
    else:
        record.payload = carrier
    handler, output = stream_handler()
    if boundary == "formatter":
        # A caller's own formatting (not a sink), then the handler's write.
        handler.format(record)
        handler.emit(record)
    elif boundary == "handler":
        handler.emit(record)
    else:
        logger = logging.Logger("output", level=1)
        logger.addHandler(handler)
        logger.handle(record)
    rendered = output.getvalue()
    assert rendered.strip(), "the sink wrote nothing"
    if checkout:
        assert VALUE not in rendered
    else:
        assert str(carrier) in rendered
    assert repr(carrier) == original
    assert (record.msg is carrier) if placement == "msg" else True


@pytest.mark.parametrize("factory", [UserString, httpx.URL, Header, PurePosixPath])
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
def test_assembled_path_at_output(factory, boundary):
    record = logging.LogRecord(
        "output",
        40,
        __file__,
        1,
        "%s%s/",
        ("/agentcc/managed-credits/checkout/", factory(VALUE)),
        None,
    )
    handler, output = stream_handler(logging.Formatter())
    if boundary == "formatter":
        # A caller's own formatting (not a sink), then the handler's write.
        handler.format(record)
        handler.emit(record)
    elif boundary == "handler":
        handler.emit(record)
    else:
        logger = logging.Logger("output", level=1)
        logger.addHandler(handler)
        logger.handle(record)
    rendered = output.getvalue()
    assert "/agentcc/managed-credits/checkout/" in rendered
    assert VALUE not in rendered


@pytest.mark.parametrize(
    "wire", [PATH, PATH.encode(), PATH.replace("/", r"\/"), PATH.replace("/", "%2F")]
)
def test_final_text_and_bytes(wire):
    result = scrub_rendered(wire)
    assert VALUE not in (result.decode() if isinstance(result, bytes) else result)
    assert type(result) is type(wire)
    assert scrub_rendered(result) == result


@pytest.mark.parametrize("environment", ["local", "production"])
def test_every_configured_handler_after_final_renderer(
    tmp_path, monkeypatch, environment
):
    monkeypatch.setenv("ENV_TYPE", environment)
    config = get_logging_config(str(tmp_path))
    # Configure each app handler without replacing the test runner's loggers.
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
        output = io.StringIO()
        if name != "file":
            handler.setStream(output)
        try:
            record = logging.LogRecord("output", 40, __file__, 1, PATH, (), None)
            # emit is deliberately direct: no logger or handler filter pass.
            handler.emit(record)
            handler.flush()
            rendered = (
                (tmp_path / "logs/wsgi.log").read_text()
                if name == "file"
                else output.getvalue()
            )
            assert VALUE not in rendered
            assert "[redacted]" in rendered
        finally:
            handler.close()


@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
@pytest.mark.parametrize("signal", ["trace", "log"])
def test_actual_exporter_wire_payload(protocol, signal, monkeypatch):
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from tfc.telemetry.config import _create_exporter
    from tfc.telemetry.logs import _create_log_exporter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    exporter = _create_exporter() if signal == "trace" else _create_log_exporter()
    captured = []
    if protocol == "grpc":

        class Client:
            def Export(self, request, **kwargs):
                captured.append(request.SerializeToString())

        exporter._client = Client()
    else:

        class Response:
            ok = True
            status_code = 200
            text = ""

        def post(url, data, **kwargs):
            captured.append(data)
            return Response()

        monkeypatch.setattr(exporter._session, "post", post)
    resource = Resource({PATH: PATH}, schema_url="http://localhost" + PATH)
    if signal == "trace":
        provider = TracerProvider(resource=resource)
        # No defensive checkout processor: this proves the final exporter alone.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer(
            PATH, PATH, "http://localhost" + PATH, {PATH: PATH}
        )
        with tracer.start_as_current_span(PATH, attributes={PATH: PATH}) as span:
            span.add_event(PATH, {PATH: PATH})
    else:
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        handler = LoggingHandler(logger_provider=provider)
        handler.emit(logging.LogRecord(PATH, 40, __file__, 1, PATH, (), None))
    try:
        assert captured
        assert all(VALUE.encode() not in wire for wire in captured)
        assert VALUE in str(resource.attributes)
    finally:
        provider.shutdown()


def test_all_protobuf_string_fields_and_originals():
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.trace import Event
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import Link, Status, StatusCode

    from tfc.telemetry.checkout_output import scrub_otlp_request

    context = SpanContext(1, 2, False, TraceFlags(1), TraceState([("target", PATH)]))
    span = ReadableSpan(
        name=PATH,
        context=context,
        parent=context,
        resource=Resource({PATH: PATH}, schema_url=PATH),
        instrumentation_scope=InstrumentationScope(PATH, PATH, PATH, {PATH: PATH}),
        attributes={PATH: PATH},
        events=[Event(PATH, {PATH: PATH})],
        links=[Link(context, {PATH: PATH})],
        status=Status(StatusCode.ERROR, PATH),
        start_time=1,
        end_time=2,
    )
    request = encode_spans([span])
    original = request.SerializeToString()
    result = scrub_otlp_request(request)
    assert VALUE.encode() not in result.SerializeToString()
    assert request.SerializeToString() == original
    assert VALUE.encode() in original
    exported_span = result.resource_spans[0].scope_spans[0].spans[0]
    assert exported_span.trace_id == context.trace_id.to_bytes(16, "big")


def test_repeated_session_in_final_text():
    output = scrub_rendered(PATH + " session=" + VALUE)
    assert VALUE not in output


def test_final_payload_discovers_values_across_envelope_fields():
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope

    from tfc.telemetry.checkout_output import scrub_otlp_request

    context = SpanContext(1, 2, False)
    span = ReadableSpan(
        name=PATH,
        context=context,
        resource=Resource({"session": VALUE}),
        instrumentation_scope=InstrumentationScope(VALUE, VALUE, VALUE),
        start_time=1,
        end_time=2,
    )
    request = encode_spans([span])
    original = request.SerializeToString()
    result = scrub_otlp_request(request)
    assert VALUE.encode() not in result.SerializeToString()
    assert request.SerializeToString() == original


def test_binary_trace_identifiers_are_not_text():
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

    from tfc.telemetry.checkout_output import scrub_otlp_request

    context = SpanContext(
        int.from_bytes(b"A" * 16, "big"), int.from_bytes(b"A" * 8, "big"), False
    )
    span = ReadableSpan(
        name="/agentcc/managed-credits/checkout/A/",
        context=context,
        resource=Resource({}),
        start_time=1,
        end_time=2,
    )
    request = scrub_otlp_request(encode_spans([span]))
    exported = request.resource_spans[0].scope_spans[0].spans[0]
    assert exported.trace_id == b"A" * 16
    assert exported.span_id == b"A" * 8
    assert exported.name.endswith("/[redacted]/")


def test_nested_logging_during_rendering_is_also_sanitized():
    handler, output = stream_handler(logging.Formatter())
    logger = logging.Logger("output", level=1)
    logger.addHandler(handler)
    calls = []

    class Nested:
        def __str__(self):
            calls.append("str")
            logger.error(PATH)
            return "outer"

    logger.error("%s", Nested())
    assert calls == ["str"]
    assert "outer" in output.getvalue()
    assert VALUE not in output.getvalue()


def test_final_custom_formatter_output_is_sanitized():
    class Formatter(logging.Formatter):
        def format(self, record):
            return PATH

    handler, output = stream_handler(Formatter())
    handler.emit(logging.LogRecord("output", 40, __file__, 1, "ordinary", (), None))
    assert VALUE not in output.getvalue()
    assert "[redacted]" in output.getvalue()


@pytest.mark.parametrize("base", [str, bytes])
def test_rendered_subclass_uses_native_text_without_hooks(base):
    class Rendered(base):
        def __str__(self):
            raise AssertionError("rendered text must not be rendered again")

        def __bytes__(self):
            raise AssertionError("rendered bytes must not be rendered again")

    original = Rendered(PATH if base is str else PATH.encode())
    output = scrub_rendered(original)
    assert VALUE not in (output.decode() if base is bytes else output)


class Severity(IntEnum):
    ERROR = 40


class NumericSeverity(int):
    def __str__(self):
        raise AssertionError("numeric severity must not be stringified")


@pytest.mark.parametrize(
    "level",
    [40, Severity.ERROR, NumericSeverity(40)],
    ids=["int", "int-enum", "int-subclass"],
)
def test_numeric_severity_dispatch(level):
    handler, output = stream_handler(logging.Formatter())
    logger = logging.Logger("output", level=1)
    logger.addHandler(handler)
    logger.log(level, PATH)
    assert VALUE not in output.getvalue()
    assert "[redacted]" in output.getvalue()
