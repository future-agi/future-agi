"""Span export boundary for the checkout URL family.

Instrumentation (for example the WSGI attributes http.url and http.target)
records the raw request target, which for the checkout status route carries
the session value. Every finished span passes through this processor before
any exporting processor sees it: the same rule as logging discovers the
session from URL-shaped text (and from the active checkout request scope)
and replaces it in the span name, attribute values, event attributes (for
example exception.message and exception.stacktrace), link attributes and
status description. Spans without a checkout value pass through unchanged.
"""

import logging
from threading import Lock
from weakref import ref

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.trace import Link, SpanContext, Status, TraceState

from . import context

logger = logging.getLogger(__name__)


# As with fixed LogRecord fields, instrumentation schema is not request data.
# All other attribute names are dynamic and must be scrubbed, including event
# and link keys. Do not exempt arbitrary keys by prefix (http.*, fi.*, ...).
_SCHEMA_KEYS = (
    frozenset(
        value
        for name, value in vars(SpanAttributes).items()
        if name.isupper() and type(value) is str
    )
    | frozenset(
        value for name, value in vars(context).items() if name.startswith("ATTR_")
    )
    | frozenset(
        {
            "http.query_params",
            "http.query_params_count",
            "http.request.headers",
            "http.request.body",
            "http.request.files",
            "http.response.headers",
            "http.response.body",
            "http.duration_ms",
            "http.view_name",
        }
    )
)


def _attributes(attributes, scrub):
    return {
        key if key in _SCHEMA_KEYS else scrub(key): scrub(value)
        for key, value in dict(attributes or {}).items()
    }


def capture_span_values(span, payload):
    """Keep request values on the live SDK span, never in exported attributes.

    The server span ends outside the checkout middleware's ContextVar scope.
    Capture before hooks replace URL attributes; children also capture their
    active request scope on_start. The private tuple is not in ReadableSpan.
    """
    from tfc.logging.checkout import find_checkout_values

    previous = getattr(span, "_checkout_redaction_values", ())
    if previous is None:  # Earlier capture failed: keep the fail-closed state.
        return
    try:
        values = find_checkout_values(payload)
        if values:
            span._checkout_redaction_values = tuple(set(previous) | set(values))
    except Exception:
        span._checkout_redaction_values = None


def _state_entries(span_context):
    return tuple(span_context.trace_state.items()) if span_context is not None else ()


def _redact_context(span_context, scrub):
    entries = _state_entries(span_context)
    # Dropping affected vendor entries keeps TraceState's key/value grammar
    # valid, even if a session appears in the vendor key. Never change IDs,
    # sampling flags, the remote bit, or the original propagation context.
    safe = [
        (key, value)
        for key, value in entries
        if scrub(key) == key and scrub(value) == value
    ]
    if len(safe) == len(entries):
        return span_context
    return SpanContext(
        trace_id=span_context.trace_id,
        span_id=span_context.span_id,
        is_remote=span_context.is_remote,
        trace_flags=span_context.trace_flags,
        trace_state=TraceState(safe),
    )


def redact_span(span, captured=()):
    """Return span, or a copy with every checkout value replaced."""
    # Imported on use: this module loads before Django is set up.
    from tfc.logging.checkout import find_checkout_values, scrub_checkout_values

    description = span.status.description
    scope = span.instrumentation_scope
    scope_fields = (
        (scope.name, scope.version, scope.schema_url, dict(scope.attributes or {}))
        if scope is not None
        else ()
    )
    values = find_checkout_values(
        (
            span.name,
            dict(span.resource.attributes),
            span.resource.schema_url,
            scope_fields,
            dict(span.attributes or {}),
            [(event.name, dict(event.attributes or {})) for event in span.events],
            [dict(link.attributes or {}) for link in span.links],
            _state_entries(span.context),
            _state_entries(span.parent),
            [_state_entries(link.context) for link in span.links],
            description,
        )
    )
    values = tuple(sorted(set(values) | set(captured), key=len, reverse=True))
    if not values:
        return span

    def scrub(value):
        return scrub_checkout_values(value, values)

    status = span.status
    if description:
        status = Status(status.status_code, scrub(description))
    return ReadableSpan(
        name=scrub(span.name),
        context=_redact_context(span.context, scrub),
        parent=_redact_context(span.parent, scrub),
        resource=Resource(
            _attributes(span.resource.attributes, scrub),
            scrub(span.resource.schema_url),
        ),
        attributes=_attributes(span.attributes, scrub),
        events=[
            Event(
                scrub(event.name),
                _attributes(event.attributes, scrub),
                event.timestamp,
            )
            for event in span.events
        ],
        links=[
            Link(
                _redact_context(link.context, scrub),
                _attributes(link.attributes, scrub),
            )
            for link in span.links
        ],
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=(
            InstrumentationScope(
                scrub(scope.name),
                scrub(scope.version),
                scrub(scope.schema_url),
                _attributes(scope.attributes, scrub),
            )
            if scope is not None
            else None
        ),
    )


class CheckoutRedactingSpanProcessor(SpanProcessor):
    """Deliver only redacted spans to the wrapped (exporting) processor."""

    def __init__(self, delegate):
        self._delegate = delegate
        self._active = {}
        self._lock = Lock()

    def on_start(self, span, parent_context=None):
        capture_span_values(span, (span.name, dict(span.attributes or {})))
        key = (span.context.trace_id, span.context.span_id)

        def collected(reference):
            # Abandoned spans must not accumulate entries. Identity protects
            # against an old callback removing a subsequently reused key.
            with self._lock:
                if self._active.get(key) is reference:
                    self._active.pop(key, None)

        with self._lock:
            self._active[key] = ref(span, collected)
        self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span):
        key = (span.context.trace_id, span.context.span_id)
        with self._lock:
            reference = self._active.pop(key, None)
        live = reference() if reference is not None else None
        captured = getattr(live, "_checkout_redaction_values", ())
        try:
            if captured is None:
                raise ValueError("checkout span capture failed")
            redacted = redact_span(span, captured)
            # The final export boundary scrubs with the same request values,
            # whichever thread or batch eventually encodes this span.
            from .checkout_output import remember_scope, scope_values

            remember_scope(redacted, (*captured, *scope_values(span)))
        except Exception:
            # Fail closed: drop the span rather than export it unredacted.
            logger.warning("checkout_span_redaction_failed")
            return
        self._delegate.on_end(redacted)

    def shutdown(self):
        with self._lock:
            self._active.clear()
        self._delegate.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._delegate.force_flush(timeout_millis)
