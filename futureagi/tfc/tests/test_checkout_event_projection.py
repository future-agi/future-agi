"""Structured events and fixed-schema logging controls at the configured sinks.

Each scenario is observed in what the configured sinks emit (checkout_sinks;
ruling 681 addendum 5), not in the in-memory record or event.
"""

import io
import json
import logging

import pytest
import structlog
from django.utils.functional import SimpleLazyObject

from tfc.logging.config import configure_structlog, get_logging_config
from tfc.tests.checkout_sinks import RecordFormatter, native_emit
from tfc.tests.test_checkout_context_boundaries import VALUE, capture, path

pytestmark = pytest.mark.integration


def written(formatter, record):
    """What a StreamHandler with this formatter writes for record."""
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(formatter)
    handler.emit(record)
    return output.getvalue()


@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize("placement", ["event", "positional", "metadata", "context"])
def test_producer_projects_only_checkout_events(checkout, placement):
    calls = []
    stream = io.BytesIO(b"ordinary lazy source")

    def evaluate():
        calls.append("called")
        return stream.read().decode()

    def run():
        stream.seek(0)
        value = SimpleLazyObject(evaluate)
        with capture() as (source, sinks):
            logger = structlog.get_logger(source.name)
            with structlog.contextvars.bound_contextvars(
                **({"payload": value} if placement == "context" else {})
            ):
                if placement == "event":
                    logger.error(value, url=path(checkout))
                elif placement == "positional":
                    logger.error("metadata %s", value, url=path(checkout))
                elif placement == "metadata":
                    logger.error("metadata", payload=value, url=path(checkout))
                else:
                    logger.error("metadata", url=path(checkout))
        observed = (list(calls), stream.tell())
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        with native_emit():
            _, natively = run()
        # No projection pass: the event is evaluated exactly as logging does.
        assert with_boundary == natively
        assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert with_boundary == (["called"], len(b"ordinary lazy source"))
        assert VALUE in sinks.text()


@pytest.mark.parametrize("session", ["%s", "event", "level", "logger"])
def test_producer_formats_before_replacing_directive_collisions(session):
    with capture() as (source, sinks):
        structlog.get_logger(source.name).error(
            "/agentcc/managed-credits/checkout/%s/",
            session,
        )
    outputs = sinks.outputs()
    assert sinks.structured(), "a production line is not one JSON document"
    for name in ("record", "console-production", "file-production", "console-local"):
        assert "/checkout/[redacted]/" in outputs[name], name
        assert "/checkout/" + session + "/" not in outputs[name], name


@pytest.mark.parametrize("method", ["debug", "info", "warning", "error", "critical"])
def test_fixed_logging_method_values_remain_usable(tmp_path, monkeypatch, method):
    monkeypatch.setenv("ENV_TYPE", "production")
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))["formatters"]["structured"]
    formatter = config["()"](
        processor=config["processor"], foreign_pre_chain=config["foreign_pre_chain"]
    )
    level = getattr(logging, method.upper())
    record = logging.LogRecord(
        "schema", level, __file__, 1, {"event": path(True), "level": method}, (), None
    )
    record._logger = logging.getLogger("schema")
    record._name = method
    rendered = written(formatter, record)
    assert rendered.strip(), "the sink wrote nothing"
    assert VALUE not in rendered
    assert method in rendered
    assert json.loads(rendered)["level"] == method
    assert record._name == method
    assert record.levelname == method.upper()


@pytest.mark.parametrize("field", ["_name", "levelname"])
@pytest.mark.parametrize("payload", ["text", "container", "text-subclass"])
def test_schema_payloads_are_sanitized_in_structured_formatter(
    tmp_path, monkeypatch, field, payload
):
    monkeypatch.setenv("ENV_TYPE", "production")
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))["formatters"]["structured"]
    formatter = config["()"](
        processor=config["processor"], foreign_pre_chain=config["foreign_pre_chain"]
    )
    record = logging.LogRecord(
        "schema", 40, __file__, 1, {"event": "metadata"}, (), None
    )
    record._logger = logging.getLogger("schema")
    record._name = "error"
    if payload == "container":
        value = {"url": path(True)}
    elif payload == "text-subclass":

        class Text(str):
            pass

        value = Text(path(True))
    else:
        value = path(True)
    setattr(record, field, value)
    # The full-record sink renders every attribute, including the schema field.
    record_output = written(RecordFormatter(), record)
    production_output = written(formatter, record)
    assert record_output.strip() and production_output.strip()
    assert VALUE not in record_output
    assert VALUE not in production_output
    json.loads(production_output)
    assert getattr(record, field) is value, "the record was rewritten"


def test_projected_event_keeps_structured_data():
    original = {"url": path(True), "count": 3}
    with capture() as (source, sinks):
        structlog.get_logger(source.name).error(original)
    line = sinks.outputs()["console-production"].strip()
    event = json.loads(line)
    assert type(event["event"]) is dict
    assert event["event"]["count"] == 3
    assert event["event"]["url"] == "/agentcc/managed-credits/checkout/[redacted]/"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    assert original["url"] == path(True)


@pytest.mark.parametrize("placement", ["exception", "exception-tuple", "stack"])
def test_producer_does_not_evaluate_exception_or_stack_sources(placement):
    calls = []
    stream = io.BytesIO(b"ordinary source")

    class Error(Exception):
        def __str__(self):
            calls.append("called")
            return stream.read().decode()

    def evaluate():
        calls.append("called")
        return stream.read().decode()

    def run():
        stream.seek(0)
        if placement == "exception":
            extra = {"exc_info": Error()}
        elif placement == "exception-tuple":
            extra = {"exc_info": (Error, Error(), None)}
        else:
            extra = {"stack_info": SimpleLazyObject(evaluate)}
        with capture() as (source, sinks):
            structlog.get_logger(source.name).error("metadata", url=path(True), **extra)
        observed = (list(calls), stream.tell())
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    # No pre-render pass: sources are evaluated exactly as logging does.
    calls, position = with_boundary
    native_calls, native_position = natively
    assert calls == native_calls, "the sink boundary ran an application hook"
    assert position == native_position, "the sink boundary read a source stream"


def test_foreign_request_scope_preserves_logging_metadata(tmp_path, monkeypatch):
    from django.test import RequestFactory

    from tfc.logging.checkout import CheckoutLoggingMiddleware

    monkeypatch.setenv("ENV_TYPE", "production")
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))["formatters"]["structured"]
    formatter = config["()"](
        processor=config["processor"], foreign_pre_chain=config["foreign_pre_chain"]
    )
    record = logging.LogRecord("ordinary.logger", 40, __file__, 1, path(True), (), None)
    record.otelTraceID = "0123456789abcdef0123456789abcdef"
    record.otelSpanID = "0123456789abcdef"
    rendered = CheckoutLoggingMiddleware(lambda request: written(formatter, record))(
        RequestFactory().get(path(True))
    )
    assert VALUE not in rendered
    json.loads(rendered)
    assert "ordinary.logger" in rendered
    assert record.otelTraceID in rendered
    assert record.otelSpanID in rendered
