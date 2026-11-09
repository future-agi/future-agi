"""Checkout session identifiers are redacted by shape in every configured output.

A Stripe Checkout Session identifier (cs_test_ or cs_live_ followed by at
least 24 ASCII letters or digits) is replaced wherever it appears in what a
configured output emits: the console and rotating-file handlers, the logging
error path on stderr, OTLP log and span exports, and the Sentry hooks. This
holds with or without a checkout request in scope and with no checkout path
anywhere in the output, for the canonical, single and double percent-encoded
and JSON \\u-escaped spellings. Every production line still parses. Values of
another shape are left unchanged. All values are synthetic.
"""

import io
import json
import logging
import re
import sys
import time
from urllib.parse import unquote

import pytest
import structlog
from django.http import HttpResponse
from django.test import RequestFactory

from tfc.logging.checkout import CheckoutLoggingMiddleware, _values
from tfc.logging.config import configure_structlog
from tfc.tests.checkout_sentry import sentry_capture
from tfc.tests.checkout_sinks import configured_sinks
from tfc.tests.test_checkout_output_edges import configured_handlers
from tfc.tests.test_checkout_request_path_source import delivered

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"
TAIL = "SyntheticShapeSession0428AbCdXyZ"
SESSION = "cs_test_" + TAIL
LIVE = "cs_live_" + TAIL
TEXT_SINKS = ("record", "console-production", "file-production", "console-local")
_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")


def percent(text, *, double=False):
    lead = "%25" if double else "%"
    return "".join(f"{lead}{byte:02X}" for byte in text.encode())


def escaped(text, *, upper=False):
    return "".join(
        (f"\\u{ord(char):04X}" if upper else f"\\u{ord(char):04x}") for char in text
    )


FORMS = {
    "canonical": lambda value: value,
    "percent": percent,
    "percent-lower-hex": lambda value: percent(value).lower(),
    "percent-separators": lambda value: value.replace("_", "%5F"),
    "percent-double": lambda value: percent(value, double=True),
    "percent-double-separators": lambda value: value.replace("_", "%255F"),
    "json": escaped,
    "json-upper-hex": lambda value: escaped(value, upper=True),
    "json-separators": lambda value: value.replace("_", "\\u005f"),
    # A literal identifier whose 24th tail character is encoded.
    "mixed-tail": lambda value: value[:31] + percent(value[31]) + value[32:],
    "mixed-first": lambda value: percent(value[0]) + value[1:],
}
CARRIERS = ("message", "argument", "extra", "exception", "structured")
NEAR_MISSES = {
    "tail-23": "cs_test_" + TAIL[:23],
    "other-mode": "cs_prod_" + TAIL,
    "other-object": "sk_test_" + TAIL,
    "upper-case-prefix": "CS_TEST_" + TAIL,
}


def decoded(text):
    """Every in-scope spelling, decoded for the assertion (never for output)."""
    for _ in range(4):
        text = text.replace("\\\\", "\\")
        text = unquote(_UNICODE.sub(lambda m: chr(int(m[1], 16)), text))
    return text


def log(logger, carrier, value):
    if carrier == "message":
        logger.error("session " + value + " settled")
    elif carrier == "argument":
        logger.error("session %s settled", value)
    elif carrier == "extra":
        logger.error("session settled", extra={"payload": value})
    elif carrier == "exception":
        try:
            raise ValueError("session " + value)
        except ValueError:
            logger.exception("session failed")
    else:
        structlog.get_logger(logger.name).error("session_settled", session=value)


def emitted(carrier, value, scope):
    configure_structlog(cache_logger_on_first_use=False)
    with configured_sinks("checkout.session.shape") as (logger, sinks):
        if scope == "none":
            token = _values.set(())
            try:
                log(logger, carrier, value)
            finally:
                _values.reset(token)
        else:
            path = PREFIX + SESSION + "/" if scope == "checkout" else "/ordinary/x/"

            def respond(request):
                log(logger, carrier, value)
                return HttpResponse("ok")

            CheckoutLoggingMiddleware(respond)(RequestFactory().get(path))
    return sinks


def assert_absent(sinks, *sessions):
    outputs = sinks.outputs()
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.structured(), "a production line is not one JSON document"
    for name, text in outputs.items():
        for session in sessions:
            assert session not in text, name
            assert session not in decoded(text), name


@pytest.mark.parametrize("scope", ["none", "unrelated-request", "checkout"])
@pytest.mark.parametrize("carrier", CARRIERS)
@pytest.mark.parametrize("form", sorted(FORMS))
def test_identifier_spellings_at_every_sink(form, carrier, scope):
    sinks = emitted(carrier, FORMS[form](SESSION), scope)
    assert_absent(sinks, SESSION)


@pytest.mark.parametrize("carrier", ["message", "argument"])
def test_live_mode_and_repeated_identifiers(carrier):
    value = LIVE + " then " + SESSION + "," + LIVE
    sinks = emitted(carrier, value, "none")
    assert_absent(sinks, SESSION, LIVE)
    production = sinks.outputs()["console-production"]
    assert json.loads(production.splitlines()[-1])["event"].count("[redacted]") == 3


@pytest.mark.parametrize("carrier", ["message", "argument"])
@pytest.mark.parametrize("label", sorted(NEAR_MISSES))
def test_other_shapes_are_unchanged(label, carrier):
    value = NEAR_MISSES[label]
    sinks = emitted(carrier, value, "none")
    outputs = sinks.outputs()
    assert sinks.structured(), "a production line is not one JSON document"
    for name in TEXT_SINKS:
        assert value in outputs[name], name
        assert "[redacted]" not in outputs[name], name


@pytest.mark.parametrize(
    "value",
    [
        # Renders as \\u00ec in a JSON line: the escape's last digit is "c".
        "ì" + SESSION[1:],
        "Č" + SESSION[1:],
        "\U0001f600" + SESSION,
        '"' + SESSION + "\\",
        "\\" + SESSION + '"',
        "{" + SESSION + "}",
        SESSION + "\udcff",
    ],
    ids=[
        "escape-ends-c",
        "escape-ends-upper-c",
        "non-bmp",
        "quote",
        "backslash",
        "braces",
        "lone-surrogate",
    ],
)
@pytest.mark.parametrize("carrier", ["message", "argument", "structured"])
def test_identifiers_beside_escapes_keep_every_line_parseable(value, carrier):
    sinks = emitted(carrier, value, "none")
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.structured(), "a production line is not one JSON document"
    for name in ("console-production", "file-production"):
        assert SESSION not in sinks.outputs()[name], name


class FailFormat(logging.Formatter):
    def format(self, record):
        raise ValueError("synthetic formatter failure")


class FailWrite(io.StringIO):
    def write(self, text):
        raise OSError("synthetic stream failure")


def handled(tmp_path, monkeypatch, environment, failure, make_record):
    """Each configured handler's stream text plus its stderr diagnostic.

    Every handler gets its own record: a handler filter may rewrite a record.
    """
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    texts = []
    token = _values.set(())
    handlers = configured_handlers(tmp_path, monkeypatch, environment)
    try:
        for handler in handlers:
            output = io.StringIO()
            handler.setStream(FailWrite() if failure == "write" else output)
            if failure == "format":
                handler.setFormatter(FailFormat())
            errors.seek(0)
            errors.truncate()
            handler.handle(make_record())
            texts.append(output.getvalue() + errors.getvalue())
            if failure != "none":
                assert "--- Logging error ---" in errors.getvalue()
    finally:
        for handler in handlers:
            handler.close()
        _values.reset(token)
    return texts


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("failure", ["none", "format", "write"])
@pytest.mark.parametrize("form", ["canonical", "percent", "percent-double", "json"])
def test_logging_error_path_redacts_identifiers(
    tmp_path, monkeypatch, environment, failure, form
):
    value = FORMS[form](SESSION)

    def make_record():
        return logging.LogRecord(
            "checkout.session.errors",
            logging.ERROR,
            __file__,
            1,
            "session %s settled",
            (value,),
            None,
        )

    for text in handled(tmp_path, monkeypatch, environment, failure, make_record):
        assert text
        assert SESSION not in text and SESSION not in decoded(text)


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("failure", ["none", "format", "write"])
def test_identifier_without_its_rendered_path_field(
    tmp_path, monkeypatch, environment, failure
):
    # The checkout path is only in an extra field that neither the configured
    # renderer nor the error diagnostic writes; the bare identifier is in the
    # message. No request is in scope.
    def make_record():
        record = logging.LogRecord(
            "checkout.session.errors",
            logging.ERROR,
            __file__,
            1,
            "settled " + SESSION,
            (),
            None,
        )
        record.url = PREFIX + SESSION + "/"
        return record

    for text in handled(tmp_path, monkeypatch, environment, failure, make_record):
        assert text
        assert SESSION not in text and SESSION not in decoded(text)


@pytest.mark.parametrize("failure", ["none", "format", "write"])
@pytest.mark.parametrize("form", ["canonical", "percent-separators", "json"])
def test_server_access_query_after_path_redaction(tmp_path, monkeypatch, failure, form):
    from granian.log import DEFAULT_ACCESSLOG_FMT, log_request_builder

    configure_structlog(cache_logger_on_first_use=False)
    request = {
        "addr_remote": "127.0.0.1",
        "path": PREFIX + SESSION + "/",
        "qs": "echo=" + FORMS[form](SESSION),
        "method": "GET",
        "scheme": "http",
        "protocol": "HTTP/1.1",
        "headers": lambda name: None,
    }
    logger = logging.getLogger("granian.access")
    disabled, logger.disabled = logger.disabled, False
    token = _values.set(())

    def make_record():
        (record,) = delivered(
            "granian.access",
            lambda: log_request_builder(DEFAULT_ACCESSLOG_FMT)(
                time.time(), time.perf_counter(), request, 200
            ),
        )
        return record

    try:
        texts = handled(tmp_path, monkeypatch, "production", failure, make_record)
    finally:
        _values.reset(token)
        logger.disabled = disabled
    for text in texts:
        assert text
        assert SESSION not in text and SESSION not in decoded(text)


def _span_exporter(protocol, monkeypatch, signal):
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
    return exporter, captured


@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
@pytest.mark.parametrize("signal", ["trace", "log"])
@pytest.mark.parametrize("form", ["canonical", "percent", "percent-double", "json"])
def test_otlp_exports_redact_identifiers(protocol, signal, form, monkeypatch):
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    value = FORMS[form](SESSION)
    exporter, captured = _span_exporter(protocol, monkeypatch, signal)
    token = _values.set(())
    try:
        if signal == "trace":
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            tracer = provider.get_tracer("checkout.session.shape")
            with tracer.start_as_current_span(
                "settle", attributes={"session": value, "note": "session " + value}
            ) as span:
                span.add_event("settled", {"session": value})
        else:
            provider = LoggerProvider()
            provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
            handler = LoggingHandler(logger_provider=provider)
            record = logging.LogRecord(
                "checkout.session.shape", 40, __file__, 1, "session %s", (value,), None
            )
            record.session = value
            handler.emit(record)
        provider.shutdown()
    finally:
        _values.reset(token)
    assert captured, "the exporter sent nothing"
    for wire in captured:
        text = wire.decode("utf-8", "replace")
        assert SESSION not in text and SESSION not in decoded(text)


@pytest.fixture
def sentry_client(monkeypatch):
    with sentry_capture(monkeypatch) as captured:
        yield captured


@pytest.mark.parametrize("kind", ["logging", "message", "exception"])
@pytest.mark.parametrize("form", ["canonical", "percent", "percent-double", "json"])
def test_sentry_events_redact_identifiers(sentry_client, kind, form):
    import sentry_sdk

    client, envelopes = sentry_client
    value = FORMS[form](SESSION)
    token = _values.set(())
    try:
        if kind == "logging":
            logging.getLogger("checkout.session.sentry").error("session %s", value)
        elif kind == "message":
            sentry_sdk.capture_message("session " + value)
        else:
            try:
                raise RuntimeError("session " + value)
            except RuntimeError:
                sentry_sdk.capture_exception()
        sentry_sdk.flush()
    finally:
        _values.reset(token)
    wire = b"".join(envelopes).decode("utf-8", "replace")
    assert wire, "the integration sent no event"
    assert SESSION not in wire and SESSION not in decoded(wire)


@pytest.mark.parametrize("form", ["canonical", "percent", "percent-double", "json"])
def test_sentry_breadcrumb_and_transaction_hooks_redact_identifiers(
    sentry_client, form
):
    client, _ = sentry_client
    value = FORMS[form](SESSION)
    crumb = client.options["before_breadcrumb"](
        {"message": "session " + value, "data": {"session": value, "list": [value]}},
        {},
    )
    transaction = client.options["before_send_transaction"](
        {
            "type": "transaction",
            "transaction": "GET " + value,
            "spans": [{"description": value, "data": {"session": (value,)}}],
            "breadcrumbs": {"values": [{"message": value}]},
        },
        {},
    )
    for result in (crumb, transaction):
        text = json.dumps(result)
        assert SESSION not in text and SESSION not in decoded(text)
