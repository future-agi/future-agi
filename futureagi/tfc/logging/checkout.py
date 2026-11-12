"""Value-free diagnostics for the checkout URL family, even before URL resolution.

Acceptance rests on the configured sinks: every
stream and file handler sanitizes its final rendered text immediately before
the write (checkout_output), the logging error path sanitizes its stderr text,
OTLP exports sanitize their final payload (tfc.telemetry.checkout_output) and
Sentry sanitizes in its before-send hooks. Raw in-memory LogRecords are not a
sink and are never rewritten here.

This module holds the shared matcher, the request scope and the source fix:
- CheckoutLoggingMiddleware keeps the active request's session forms in a
  private ContextVar, so a bare session in any line of that request is also
  replaced at output;
- the request-logging path field (django_structlog request events and the
  Granian / runserver access logs) is redacted by route match before logging
  (checkout_requests);
- discovery helpers (find/scrub) for telemetry and Sentry payloads read
  trusted storage only and never render application objects.
The request and resolver kwargs remain untouched.
"""

import json
import re
from contextvars import ContextVar
from functools import lru_cache
from heapq import merge
from urllib.parse import quote, quote_plus, unquote

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest
from django.urls import ResolverMatch
from django.utils.encoding import escape_uri_path
from django.utils.functional import Promise

from .checkout_carriers import (
    checkout_carrier_parts,
    derives,
    instance_storage,
    stored_text,
)
from .checkout_encoding import decoded_levels, may_be_escaped
from .checkout_format import redact_session_payload

# Match path_info (without a deployment's SCRIPT_NAME), not resolver state.
# CommonMiddleware may append a slash only AFTER request logs were emitted.
# Malformed suffixes still carry the same sensitive session segment.
_CHECKOUT_PATH = re.compile(r"\A/agentcc/managed-credits/checkout/([^/]+)(?:/|\Z)")
# Access loggers run after the middleware context has unwound and often carry
# only a request-line or URL, not an HttpRequest. Also match mounted/absolute URLs.
_CHECKOUT_URL = re.compile(
    r"/agentcc/managed-credits/checkout/"
    r"((?:[^/\s?#\"'<>\\]+|\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2})+)"
)
_CHECKOUT_ROUTE_NAME = "agentcc-managed-credit-checkout-status"
_PREFIX = "/agentcc/managed-credits/checkout/"
_REDACTED = "[redacted]"
_values = ContextVar("checkout_log_values", default=())


def _request_values(request):
    # Read the stored path, not a (possibly overridden) attribute.
    if not derives(type(request), HttpRequest):
        return ()
    path = stored_text(instance_storage(request, HttpRequest).get("path_info"))
    match = None if path is None else _CHECKOUT_PATH.match(path)
    if match is None:
        return ()
    return _variants(match.group(1))


def _is_checkout_route(name):
    text = stored_text(name)
    return text is not None and text.rpartition(":")[2] == _CHECKOUT_ROUTE_NAME


def _argument_values(*arguments):
    # Route arguments (positional, keyword or nested lists of them) are bare
    # session values rather than URL-shaped text. Mapping keys are names.
    values, pending = set(), list(arguments)
    for _ in range(64):
        if not pending:
            break
        value = pending.pop()
        text = stored_text(value)
        if text:
            values.update(_variants(text))
        elif derives(type(value), dict):
            pending.extend(dict.values(value))
        else:
            pending.extend(checkout_carrier_parts(value) or ())
    return values


def _match_values(match):
    # A resolved checkout route stores the bare session in its captured
    # arguments; the match's repr renders them.
    stored = instance_storage(match, ResolverMatch)
    if not _is_checkout_route(stored.get("url_name")):
        return ()
    return _argument_values(
        stored.get("args"), stored.get("kwargs"), stored.get("captured_kwargs")
    )


def _lazy_values(promise):
    # reverse_lazy(<checkout route>, ...) stores the session in its arguments
    # and renders the checkout URL only when evaluated by a consumer.
    stored = instance_storage(promise, Promise)
    arguments = checkout_carrier_parts(stored.get("_args")) or ()
    if not arguments or not _is_checkout_route(arguments[0]):
        return ()
    return _argument_values(arguments[1:], stored.get("_kw"))


def _spellings(value):
    # URI/JSON/repr forms. Text that cannot be UTF-8 encoded (a lone
    # surrogate) has no percent spelling; its other spellings still apply.
    forms = [value, json.dumps(value)[1:-1], repr(value)[1:-1]]
    for encode in (
        lambda text: quote(text, safe=""),
        quote_plus,
        escape_uri_path,
    ):
        try:
            forms.append(encode(value))
        except UnicodeError:
            pass
    return forms


@lru_cache(maxsize=1024)
def _variants(value):
    # Include URI/JSON/repr forms, including an already encoded request target.
    variants = set()
    for _ in range(3):
        if value and value != _REDACTED:
            variants.update(_spellings(value))
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    variants |= {re.sub(r"%[0-9A-F]{2}", lambda m: m[0].lower(), v) for v in variants}
    return tuple(sorted(variants, key=len, reverse=True))


def _scan_text(text, values):
    escaped = may_be_escaped(text)
    if not escaped and _PREFIX not in text:
        return  # No literal prefix and no escape that could spell one.
    original = text
    for _ in range(3):
        for match in _CHECKOUT_URL.finditer(text):
            values.update(_variants(match.group(1)))
        decoded = unquote(text.replace(r"\/", "/"))
        if decoded == text:
            break
        text = decoded
    if escaped:
        # Unicode/hex escapes and escaped backslashes, level by level.
        for level in decoded_levels(original):
            for match in _CHECKOUT_URL.finditer(level):
                values.update(_variants(match.group(1)))


def _find_values(data):
    """Discover checkout values in a telemetry or Sentry payload, without hooks.

    Iterative traversal handles cycles and deeply nested metadata; the scrubber
    bounds the resulting output. No logger-name or metadata-key allowlist.
    Every container is read through its trusted base storage (for example all
    of a MultiValueDict's stored values, or an HTTP client request's URL).
    """
    values = set(_values.get())
    pending, seen = [data], set()
    while pending:
        value = pending.pop()
        kind = type(value)
        if kind is str:
            _scan_text(value, values)
            continue
        if value is None or kind is int or kind is float or kind is bool:
            continue
        text = stored_text(value)
        if text is not None:
            _scan_text(text, values)
            continue
        if id(value) in seen:
            continue
        parts = checkout_carrier_parts(value)
        if parts is None:
            continue
        seen.add(id(value))
        # The real type, never isinstance: a spoofed __class__ must not run.
        if derives(kind, HttpRequest):
            values.update(_request_values(value))
        elif derives(kind, ResolverMatch):
            values.update(_match_values(value))
        elif derives(kind, Promise):
            values.update(_lazy_values(value))
        pending.extend(parts)
    values.discard(_REDACTED)
    return tuple(sorted(values, key=len, reverse=True))


def scrub_checkout_payload(payload, *, values=None):
    """Sanitize a telemetry payload with the same rules as logging output.

    Correlated forms are replaced first; then any session identifier left in
    the payload's text is replaced by its shape. A caller that removes carriers
    first can pass the values discovered in the original payload.
    """
    if values is None:
        values = _find_values(payload)
    return redact_session_payload(_scrub(payload, values) if values else payload)


def find_checkout_values(payload):
    """Every checkout session form present in payload or the request scope."""
    return _find_values(payload)


def scrub_checkout_values(value, values):
    """A sanitized copy of value with every discovered session form replaced."""
    return _scrub(value, values)


def _scrub_text(value, values, marker):
    # Find every sensitive occurrence in the ORIGINAL input, including matches
    # across or inside literal markers. Markers are not trusted exemptions.
    # Union their spans with sensitive spans before replacement: short values
    # inside our output marker then replace the whole marker, not its letters.
    # This is idempotent across repeated passes without ignoring user-supplied
    # marker text or rescanning newly inserted replacements.
    if len(values) == 1:
        sensitive = next(iter(values))
        first = value.find(sensitive)
        if first < 0:
            return value  # Only marker spans: replacing them changes nothing.
        if (
            value.find(sensitive, first + 1) < 0
            and value.find(marker) < 0
            and sensitive not in marker
        ):
            # One occurrence and no marker to merge with: a plain replacement.
            result = value[:first] + marker + value[first + len(sensitive) :]
            return marker if sensitive in result else result
    elif not any(sensitive in value for sensitive in values):
        return value  # Only marker spans: replacing them changes nothing.

    def matches(sensitive):
        start = value.find(sensitive)
        while start >= 0:
            yield start, start + len(sensitive)
            start = value.find(sensitive, start + 1)

    # Merge ordered iterators rather than allocating every matching span for
    # a long input containing many repetitions of a short invalid session.
    spans = merge(*(matches(sensitive) for sensitive in (*values, marker)))
    first = next(spans, None)
    if first is None:
        return value
    pieces, cursor = [], 0
    start, end = first
    for next_start, next_end in spans:
        if next_start <= end:
            end = max(end, next_end)
        else:
            pieces.extend((value[cursor:start], marker))
            cursor, start, end = end, next_start, next_end
    pieces.extend((value[cursor:start], marker, value[end:]))
    result = value[:0].join(pieces)
    # A replacement plus a neighboring literal could itself spell another
    # sensitive value. Fail closed for that field instead of emitting it or
    # repeatedly rescanning (an attacker could force quadratic rescans).
    if any(sensitive not in marker and sensitive in result for sensitive in values):
        return marker
    return result


def _exact_bytes(value, kind):
    # Copies made by the built-in base, never the subclass's own methods.
    if derives(kind, bytes):
        return bytes.__bytes__(value)
    if derives(kind, bytearray):
        return bytes(bytearray.__getitem__(value, slice(None)))
    if kind is memoryview:
        try:
            return memoryview.tobytes(value)
        except ValueError:  # released buffer
            return _REDACTED.encode()
    return None


def _encoded(values):
    # Byte spellings of each value; text with no UTF-8 form has none.
    found = []
    for value in values:
        try:
            found.append(value.encode())
        except UnicodeError:
            pass
    return tuple(found)


def _scrub(value, values, depth=0):
    if depth > 8:
        return _REDACTED
    kind = type(value)
    if kind is str:
        return _scrub_text(value, values, _REDACTED)
    # Identity checks only: comparing a hostile metaclass would run its hook.
    # Numeric metadata (levelno, IntEnum and int subclasses) is never text.
    if value is None or derives(kind, int) or derives(kind, float):
        return value
    if derives(kind, str):
        return _scrub_text(str.__str__(value), values, _REDACTED)
    raw = _exact_bytes(value, kind)
    if raw is not None:
        return _scrub_text(raw, _encoded(values), _REDACTED.encode())
    # A sanitized copy of the base storage (a MultiValueDict keeps every value).
    if derives(kind, dict):
        return {
            _scrub(k, values, depth + 1): _scrub(v, values, depth + 1)
            for k, v in dict.items(value)
        }
    if derives(kind, tuple):
        return tuple(_scrub(v, values, depth + 1) for v in tuple.__iter__(value))
    if derives(kind, list):
        return [_scrub(v, values, depth + 1) for v in list.__iter__(value)]
    if derives(kind, HttpRequest):
        # Do not retain request/response/exception objects whose attributes or
        # traceback frames can still expose the original path.
        return None
    return _REDACTED


# Fixed Sentry event schema keys; every other key is dynamic data.
_EVENT_FIELDS = frozenset({"event", "level", "logger", "_record", "_from_structlog"})


def scrub_checkout_event(logger, method_name, event_dict, *, values=None):
    """Sanitize a serialized event (the Sentry before-send boundary).

    Correlated checkout values are replaced first; then session identifiers
    are replaced by their shape anywhere in the event's text (Sentry
    serializes an event to plain data before its before-send hooks). Returns
    event_dict itself when nothing in it is redacted.
    """
    if values is None:
        values = _find_values(event_dict)
    if values:
        event_dict = {
            key if key in _EVENT_FIELDS else _scrub(key, values): _scrub(value, values)
            for key, value in event_dict.items()
        }
    return redact_session_payload(event_dict)


class CheckoutLoggingMiddleware:
    """Scope redaction outside all request-logging/authentication middleware."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        token = _values.set(_request_values(request))
        try:
            return self.get_response(request)
        finally:
            _values.reset(token)

    async def __acall__(self, request):
        token = _values.set(_request_values(request))
        try:
            return await self.get_response(request)
        finally:
            _values.reset(token)


def install_checkout_filters():
    """Install the configured-sink boundaries and the request-path source fix.

    Idempotent. Called by configure_structlog and get_logging_config, so every
    process that loads this logging configuration (Django, Celery, Temporal,
    Granian workers) is covered. Apart from the access loggers' own path
    arguments, no record is rewritten for other consumers.
    """
    from .checkout_output import install_output_boundaries
    from .checkout_requests import install_request_path_redaction

    install_output_boundaries()
    install_request_path_redaction()


__all__ = [
    "CheckoutLoggingMiddleware",
    "find_checkout_values",
    "install_checkout_filters",
    "scrub_checkout_event",
    "scrub_checkout_payload",
    "scrub_checkout_values",
]
