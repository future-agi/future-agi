"""Redact checkout paths and session identifiers in a sink's final text.

The boundary is StreamHandler.emit, between formatting and the write. It
covers console and saml_console (StreamHandler), the RotatingFileHandler
(BaseRotatingHandler.emit -> FileHandler.emit -> StreamHandler.emit), the
Granian access log (a plain StreamHandler) and logging's last-resort handler.
RotatingFileHandler's size check formats once more but never writes that
text, so it is not scrubbed: each written line is scrubbed exactly once.
Handler.handleError (the stderr diagnostic) is replaced by checkout_errors.
OTLP exports use the final protobuf boundary in tfc.telemetry.checkout_output.
Correlated forms (the request scope, discovered paths and route arguments)
are replaced first; then any Checkout Session identifier is replaced by its
shape (checkout_format), whether or not anything correlates it. Unescaped
text whose correlated values, if any, are all identifiers needs only string
operations: the shape already replaces each occurrence of such a value.
"""

import json
import logging
import re
from functools import wraps

from .checkout import _CHECKOUT_URL, _scan_text, _scrub_text, _values
from .checkout_carriers import derives
from .checkout_encoding import (
    contains_encoded_prefix,
    may_be_escaped,
    scrub_encoded_paths,
)
from .checkout_format import SESSION_ID, redact_session_ids

_PREFIX = "/agentcc/managed-credits/checkout/"
_PREFIX_LENGTH = len(_PREFIX)
_MARKER = "[redacted]"
_REDACTED_PATH = _PREFIX + _MARKER + "/"
# Only token-shaped values can be replaced throughout a rendered document.
# Short/structural segments are still removed from paths, never JSON syntax.
# A numeric literal can contain e/E; require another letter or an underscore.
_TOKEN = re.compile(r"(?=[A-Za-z0-9_-]*[A-DF-Za-df-z_])[A-Za-z0-9_-]{8,}\Z")
# An already redacted segment, followed by a delimiter (not more segment).
_MARKER_SEGMENT = re.compile(re.escape(_MARKER) + r"(?=[/\s?#\"'<>\\]|\Z)")
_FAST_SEGMENT = re.compile(r"[A-Za-z0-9_-]+(?=[/\s?#\"'<>]|$)")
# A literal checkout path's token segment, followed by a delimiter.
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+(?=[/\s?#\"'<>]|\Z)")
# Every literal spelling of the route (its URL prefix, its name and its
# pattern) contains this; without it, or an escape, no checkout form exists.
_MARKER_TEXT = "managed-credit"
# A rendered resolver match or route arguments name the session only as the
# route's session_id argument, beside the route's name or pattern.
_ROUTE_MARKERS = ("agentcc-managed-credit-checkout-status", "checkout/<str:session_id>")
_ROUTE_ARGUMENT = re.compile(
    r"""session_id\\?['"]?\s*[:=]\s*\\?['"]?([^'"\\\s,;&)}\]]+)"""
)


def _scrub_json(value, values):
    if type(value) is str:
        return _scrub_output(value, values, structured=False)
    if type(value) is list:
        return [_scrub_json(item, values) for item in value]
    if type(value) is dict:
        # A key equal to a short session is schema (event, level, ...), not
        # the session: keys are redacted only where they hold a checkout path.
        result = {}
        for key, item in value.items():
            safe = _scrub_output(key, values, structured=False, exact=False)
            result[safe] = _scrub_json(item, values)
        return result
    return value


def _scrub_output(text, values, *, structured=True, exact=True):
    structured = structured and text.lstrip().startswith(("{", "[", '"'))
    if structured:
        # Structured means a JSON document, not text that merely starts like
        # one (a Python dict repr, for example): that is treated as text.
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            structured = False
    unsafe = tuple(value for value in values if not _TOKEN.fullmatch(value))
    if structured and unsafe:
        return json.dumps(_scrub_json(parsed, values), ensure_ascii=True)
    for value in unsafe:
        if exact and text == value:
            return _MARKER
        # A scoped short segment is meaningful as part of its checkout path,
        # not as every occurrence of that character in a rendered document.
        text = text.replace(_PREFIX + value, _PREFIX + _MARKER)
    found = set(values)
    if may_be_escaped(text):
        _scan_text(text, found)
        text = scrub_encoded_paths(text)

    def replace(match):
        found.add(match[1])
        return _PREFIX + _MARKER

    result = _CHECKOUT_URL.sub(replace, text)
    # A path may discover a numeric/structural value outside request scope too.
    # Revisit the original JSON document, never replace its number literals.
    if structured and any(
        value != _MARKER and not _TOKEN.fullmatch(value) for value in found
    ):
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            pass
        else:
            return json.dumps(_scrub_json(parsed, found), ensure_ascii=True)
    repeated = tuple(
        value
        for value in found
        if (_TOKEN.fullmatch(value) or (not structured and len(value) >= 8))
        and value in result
    )
    return _scrub_text(result, repeated, _MARKER) if repeated else result


def _literal_paths(text, prefix):
    """The common case: literal paths whose segments recur nowhere else.

    Inspect only the final text. An already redacted path is kept. Any other
    shape (a repeated bare segment, a segment needing the general matcher)
    returns None for the general path.
    """
    pieces, cursor, segments = [], 0, []
    while prefix >= 0:
        start = prefix + len(_PREFIX)
        if _MARKER_SEGMENT.match(text, start):
            prefix = text.find(_PREFIX, start)
            continue
        match = _FAST_SEGMENT.match(text, start)
        if match is None:
            return None
        pieces.extend((text[cursor:start], _MARKER))
        cursor = match.end()
        segments.append(match[0])
        prefix = text.find(_PREFIX, cursor)
    if not segments:
        return text
    pieces.append(text[cursor:])
    result = "".join(pieces)
    if any(segment in result for segment in segments):
        return None
    return result


def _unavailable(text):
    """A structure-preserving stand-in if sanitizing fails: never raw text.

    A JSON line stays one parseable JSON object, so the line is not lost
    and downstream parsers keep working.
    """
    if text.lstrip().startswith("{"):
        return json.dumps({"event": _MARKER, "checkout_redaction": "unavailable"})
    return _MARKER


def _route_arguments(text):
    """Redact session_id arguments in text that renders the checkout route.

    Returns the text and the redacted values (so bare repeats are replaced
    too). The replaced span holds no quote, backslash, whitespace or closing
    bracket, so a JSON line keeps its structure.
    """
    if "session_id" not in text or not any(m in text for m in _ROUTE_MARKERS):
        return text, ()
    named = []

    def replace(match):
        if text.startswith(_MARKER, match.start(1)):
            return match[0]
        named.append(match[1])
        return match[0][: match.start(1) - match.start()] + _MARKER

    return _ROUTE_ARGUMENT.sub(replace, text), tuple(named)


def _sanitized(text, values):
    text, named = _route_arguments(text)
    if named:
        values = (*values, *named)
    if not values:
        prefix = text.find(_PREFIX)
        escaped = may_be_escaped(text)
        if prefix < 0:
            # Ordinary output, or escapes that never decode to the prefix
            # (JSON quotes and newlines, percentages, other escapes).
            if not escaped or not contains_encoded_prefix(text):
                return text
        elif not escaped:
            result = _literal_paths(text, prefix)
            if result is not None:
                return result
    return _scrub_output(text, values)


def scrub_rendered(output, *, values=()):
    """Scrub final text/bytes without invoking any application rendering hook.

    values are extra session forms (an export's request scope); the active
    request's forms are added from the middleware scope.

    Unescaped text whose correlated values, if any, are all identifiers takes
    the string path in this one frame (a configured handler calls it for every
    line); anything else takes the general path (_general).
    """
    if type(output) is not str:
        return _scrub_other(output, values)
    text = output
    scope = _values.get()
    if values or scope:
        correlated = (*values, *scope) if values else scope
        if not _identifiers_only(correlated):
            # A correlated value that is not an identifier.
            return _general(output, text, correlated)
    else:
        correlated = ()
    if "%" in text or "\\" in text:
        # A character that can begin an escape.
        return _general(output, text, correlated)
    # Unescaped text, nothing correlated but identifiers: the identifier
    # shape replaces each occurrence of a correlated value. Operators and a
    # few str methods only; any case they do not settle goes to _literal or
    # _general.
    if _MARKER_TEXT not in text:
        # No spelling of the route. Every identifier contains "_" and "cs_".
        if "_" not in text or "cs_" not in text:
            return output
        result = _shaped(text, correlated)
        return _general(output, text, correlated) if result is None else result
    before, found, tail = text.partition(_PREFIX)
    segment, slash, after = tail.partition("/")
    if (
        not found
        or not slash
        or not segment.isidentifier()
        or not segment.isascii()
        or _MARKER_TEXT in after
    ):
        # No literal path, a segment that is not an ASCII letters, digits and
        # underscores token ending at "/", or another path or route spelling
        # later on.
        return _literal(output, text, correlated)
    result = f"{before}{_REDACTED_PATH}{after}"
    if "_" not in before and "_" not in after:
        # Nothing outside the segment can spell an identifier, a correlated
        # value or session_id; a segment without "_" must still not recur.
        if "_" in segment:
            if "session_id" in segment:
                return _general(output, text, correlated)
            return result
        if segment in result:
            return _general(output, text, correlated)
        return result
    if "cs_" in before or "cs_" in after:
        # An identifier outside the segment: the segment must not recur, then
        # the shape for the rest.
        if segment in before or segment in after:
            return _general(output, text, correlated)
        result = _shaped(result, correlated)
        return _general(output, text, correlated) if result is None else result
    if "session_id" in text:
        # A rendered route argument beside the path.
        return _general(output, text, correlated)
    # No "cs_" outside the segment: an identifier segment cannot recur, any
    # other segment must not.
    if segment[:3] != "cs_" and (segment in before or segment in after):
        return _general(output, text, correlated)
    return result


def _literal(output, text, correlated):
    """One literal checkout path whose segment ends at any delimiter.

    The segment is a token of ASCII letters, digits, "_" or "-" ending at a
    delimiter; it is replaced with a splice. Anything else (no such path, a
    second path, a rendered session_id argument, a segment that recurs) goes
    to the general path.
    """
    if "session_id" in text:
        return _general(output, text, correlated)
    start = text.find(_PREFIX)
    if start < 0:
        return _general(output, text, correlated)
    start += _PREFIX_LENGTH
    match = _SEGMENT.match(text, start)
    if match is None:
        return _general(output, text, correlated)
    end = match.end()
    if text.find(_PREFIX, end) >= 0:
        return _general(output, text, correlated)
    result = f"{text[:start]}{_MARKER}{text[end:]}"
    if "_" in result and "cs_" in result:
        result = None if text[start:end] in result else _shaped(result, correlated)
    elif not text.startswith("cs_", start) and text[start:end] in result:
        # No "cs_" is left, so no identifier (and no correlated value)
        # remains: an identifier segment cannot recur; any other must not.
        result = None
    return _general(output, text, correlated) if result is None else result


def _general(output, text, correlated):
    """Correlated forms first, then any session identifier by its shape."""
    try:
        result = redact_session_ids(_sanitized(text, correlated))
    except Exception:
        result = _unavailable(text)
    return _as_output(output, text, result)


def _scrub_other(output, values):
    """Bytes, and str or bytes subclasses: their exact base value, scrubbed."""
    kind = type(output)
    if kind is bytes:
        text = output.decode("utf-8", errors="surrogateescape")
    elif derives(kind, str):
        return scrub_rendered(str.__str__(output), values=values)
    elif derives(kind, bytes):
        output = bytes.__bytes__(output)
        text = output.decode("utf-8", errors="surrogateescape")
    else:
        raise TypeError("logging output must be rendered text or bytes")
    result = scrub_rendered(text, values=values)
    return _as_output(output, text, result)


# The last correlated tuple checked, and the answer: a request's scope is one
# tuple object for the whole request.
_checked = ((), True)


def _identifiers_only(values):
    """Whether every correlated value is a Checkout Session identifier."""
    global _checked
    last = _checked
    if last[0] is values:
        return last[1]
    answer = all(
        type(value) is str and SESSION_ID.fullmatch(value) is not None
        for value in values
    )
    _checked = (values, answer)
    return answer


def _shaped(text, correlated):
    """The identifier shape pass; None if a correlated value still remains.

    Never expected (each correlated value is an identifier), so correlation
    stays decisive: such a line goes to the general path.
    """
    result = _identifiers(text)
    for value in correlated:
        if value in result:
            return None
    return result


def _identifiers(text):
    try:
        return redact_session_ids(text)
    except Exception:
        return _unavailable(text)


def _as_output(output, text, result):
    if result is text:
        return output
    if type(output) is bytes:
        return result.encode("utf-8", errors="surrogateescape")
    return result


def install_output_boundaries():
    """Sanitize every stream/file handler's final text, and the stderr fallback.

    StreamHandler.emit is replaced by the same steps with one addition: the
    formatted text passes through scrub_rendered before stream.write. The
    native implementation stays reachable as emit.__wrapped__.
    """
    native = logging.StreamHandler.emit
    if getattr(native, "_checkout_output_boundary", False):
        return

    @wraps(native)
    def emit(self, record):
        try:
            # Looked up at call time, so the module function is the boundary.
            msg = scrub_rendered(self.format(record))
            stream = self.stream
            # issue 35046: merged two stream.writes into one.
            stream.write(msg + self.terminator)
            self.flush()
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

    from .checkout_errors import handle_error

    emit._checkout_output_boundary = True
    logging.StreamHandler.emit = emit
    logging.Handler.handleError = handle_error
