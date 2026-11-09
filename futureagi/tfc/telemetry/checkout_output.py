"""Checkout redaction on the final OTLP request, before transport encoding."""

import logging
from contextvars import ContextVar
from functools import wraps
from threading import Lock
from weakref import WeakKeyDictionary

from tfc.logging.checkout import _scan_text, _values, find_checkout_values
from tfc.logging.checkout_carriers import instance_storage
from tfc.logging.checkout_output import scrub_rendered

# Request-scope values captured where telemetry is produced (inside the
# checkout request), keyed by the SDK's own exported objects. Batch export
# runs later on another thread, after the request scope has been reset.
_SCOPES = WeakKeyDictionary()
_SCOPES_LOCK = Lock()
_BATCH_VALUES = ContextVar("checkout_export_values", default=())
# Session forms named by the stdlib record the OTLP LoggingHandler is
# translating, available while the SDK creates the record it will export.
_RECORD_VALUES = ContextVar("checkout_record_values", default=())


def _record_values(record):
    """Session forms named anywhere in a stdlib record the OTLP handler exports.

    The SDK drops attribute values it cannot encode (for example a URL-bearing
    object) while exporting others, such as a bare session in another extra.
    Values found here keep that correlation for the final payload. Trusted
    storage only: no hook, property or rendering of the record's values runs.
    """
    try:
        return find_checkout_values(dict(instance_storage(record, logging.LogRecord)))
    except Exception:
        return ()


def remember_scope(item, values):
    """Associate request values with an exportable SDK object (not exported)."""
    if not values:
        return
    with _SCOPES_LOCK:
        previous = _SCOPES.get(item, ())
        _SCOPES[item] = tuple({*previous, *values})


def scope_values(item):
    with _SCOPES_LOCK:
        return _SCOPES.get(item, ())


def _batch_values(batch):
    values = set()
    for item in batch:
        # Logs export ReadableLogRecord wrappers around the record we keyed.
        values.update(scope_values(getattr(item, "log_record", item)))
    return tuple(values)


def install_export_scope():
    """Capture the active checkout scope synchronously at SDK production time.

    Log records: ReadWriteLogRecord is constructed inside Logger.emit, on the
    emitting thread, for every SDK log (LoggingHandler or the logs API). For
    LoggingHandler, session forms named anywhere in the stdlib record being
    exported are carried too, so the payload is scrubbed even when the SDK
    drops the attribute that named the checkout path.
    Spans: the live span captures scope at start, and every readable snapshot
    handed to processors inherits it (plus the scope active at end). Neither
    the application's records nor exported attributes are changed.
    """
    from opentelemetry.sdk._logs import LoggingHandler, ReadWriteLogRecord
    from opentelemetry.sdk.trace import Span

    post_init = ReadWriteLogRecord.__post_init__
    if not getattr(post_init, "_checkout_scope", False):

        @wraps(post_init)
        def created(record):
            post_init(record)
            remember_scope(record.log_record, (*_values.get(), *_RECORD_VALUES.get()))

        created._checkout_scope = True
        ReadWriteLogRecord.__post_init__ = created

    handler_emit = LoggingHandler.emit
    if not getattr(handler_emit, "_checkout_scope", False):

        @wraps(handler_emit)
        def emit(handler, record):
            token = _RECORD_VALUES.set(_record_values(record))
            try:
                return handler_emit(handler, record)
            finally:
                _RECORD_VALUES.reset(token)

        emit._checkout_scope = True
        LoggingHandler.emit = emit

    start = Span.start
    if not getattr(start, "_checkout_scope", False):

        @wraps(start)
        def started(span, *args, **kwargs):
            remember_scope(span, _values.get())
            return start(span, *args, **kwargs)

        snapshot = Span._readable_span

        @wraps(snapshot)
        def readable(span):
            result = snapshot(span)
            remember_scope(result, (*scope_values(span), *_values.get()))
            return result

        started._checkout_scope = True
        Span.start = started
        Span._readable_span = readable


def scrub_otlp_request(request, values=()):
    """Return a protobuf copy with all textual fields sanitized.

    Walking the encoded message descriptor covers resource/scope envelopes,
    schema URLs, arbitrary AnyValue nesting, span/log bodies, events, links,
    status and trace state. New SDK string fields are covered automatically.
    Neither application spans nor the supplied encoded request are mutated.
    values are request-scope session forms carried from production time.
    """
    result = type(request)()
    result.CopyFrom(request)
    pending, fields, found = [result], [], set(values)
    while pending:
        message = pending.pop()
        for field, value in message.ListFields():
            if field.type == field.TYPE_MESSAGE:
                if field.is_repeated:
                    pending.extend(value)
                else:
                    pending.append(value)
            elif field.type == field.TYPE_STRING or (
                field.type == field.TYPE_BYTES
                and field.full_name
                == "opentelemetry.proto.common.v1.AnyValue.bytes_value"
            ):
                fields.append((message, field, value))
                for item in value if field.is_repeated else (value,):
                    text = (
                        item.decode("utf-8", errors="surrogateescape")
                        if type(item) is bytes
                        else item
                    )
                    _scan_text(text, found)
    # Only encoded scalar text is inspected, not spans/carriers. Discover over
    # the complete payload so a bare session in one field is covered when its
    # checkout path is in another (including another resource or scope).
    for message, field, value in fields:
        if field.is_repeated:
            for index, item in enumerate(value):
                value[index] = scrub_rendered(item, values=found)
        else:
            setattr(message, field.name, scrub_rendered(value, values=found))
    return result


class _GrpcOutput:
    def _translate_data(self, data):
        return scrub_otlp_request(super()._translate_data(data), _batch_values(data))


class _HttpOutput:
    def export(self, batch, *args, **kwargs):
        token = _BATCH_VALUES.set(_batch_values(batch))
        try:
            return super().export(batch, *args, **kwargs)
        finally:
            _BATCH_VALUES.reset(token)

    def _export(self, serialized_data, *args, **kwargs):
        request = self._checkout_request_type()
        request.ParseFromString(serialized_data)
        safe = scrub_otlp_request(request, _BATCH_VALUES.get()).SerializeToString()
        # The SDK performs compression/retry/transport after this boundary.
        return super()._export(safe, *args, **kwargs)


def span_exporter(protocol, **kwargs):
    """Construct the normal SDK exporter with its final payload protected."""
    install_export_scope()
    if protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        class CheckoutSpanExporter(_HttpOutput, OTLPSpanExporter):
            _checkout_request_type = ExportTraceServiceRequest
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        class CheckoutSpanExporter(_GrpcOutput, OTLPSpanExporter):
            pass

    return CheckoutSpanExporter(**kwargs)


def log_exporter(protocol, **kwargs):
    """Apply the same final request boundary to the configured OTLP log sink."""
    install_export_scope()
    if protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter,
        )
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceRequest,
        )

        class CheckoutLogExporter(_HttpOutput, OTLPLogExporter):
            _checkout_request_type = ExportLogsServiceRequest
    else:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )

        class CheckoutLogExporter(_GrpcOutput, OTLPLogExporter):
            pass

    return CheckoutLogExporter(**kwargs)
