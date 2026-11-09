"""Redact Stripe Checkout Session identifiers by shape in emitted output.

A Checkout Session identifier is cs_test_ or cs_live_ followed by at least 24
ASCII letters or digits. The checkout status route accepts only that shape
(agentcc.views.managed_credits), so any value the route can carry is
recognised here wherever it appears in final text, with or without a checkout
path or request scope beside it.

Spellings covered: canonical, percent-encoded (single and double, either hex
case) and JSON/JavaScript \\u-escaped, including mixtures within one
identifier. Decoding is for matching only: each match is mapped back to its
original span, widened to whole escapes, and replaced with the marker, so the
surrounding text (JSON syntax included) is unchanged. Text with no "cs_" and
no decodable escape returns at once, as the same object.
"""

import re
from bisect import bisect_right
from itertools import islice

from .checkout_encoding import (
    _ESCAPE,
    _decode_with_offsets,
    decoded_levels,
    may_be_escaped,
)

SESSION_ID = re.compile(r"cs_(?:test|live)_[A-Za-z0-9]{24,}")
# An occurrence in text. An identifier that directly follows a longer
# identifier-shaped run shares that run's last two letters ("cs"), so a plain
# SESSION_ID match ends just before its "_test_"/"_live_" and would leave its
# tail; such a chain is matched as one span.
_OCCURRENCE = re.compile(
    SESSION_ID.pattern + r"(?:(?<=cs)_(?:test|live)_[A-Za-z0-9]{24,})*"
)
_MARKER = "[redacted]"
_LEVELS = 6


def is_session_id(value):
    """True when value is exactly one Checkout Session identifier."""
    return type(value) is str and SESSION_ID.fullmatch(value) is not None


def _encoded_spans(text):
    # Decoded levels, each mapped back to offsets in the original text. A
    # cheap string-only decode first: most escaped text spells no identifier.
    if not any(
        "cs_" in level and SESSION_ID.search(level)
        for level in islice(decoded_levels(text), 1, None)
    ):
        return []
    decoded, spans = text, []
    offsets = [(index, index + 1) for index in range(len(text))]
    for _ in range(_LEVELS):
        level = _decode_with_offsets(decoded, offsets)
        if level is None:
            break
        decoded, offsets = level
        for match in _OCCURRENCE.finditer(decoded):
            start, end = match.span()
            spans.append((offsets[start][0], offsets[end - 1][1]))
    return spans


def _widened(spans, text):
    # A match may begin or end inside an escape of the original text (the
    # final hex digit of \u004c is "c"); never cut an escape in two.
    escapes = [match.span() for match in _ESCAPE.finditer(text)]
    if not escapes:
        return spans
    starts = [start for start, _ in escapes]
    result = []
    for start, end in spans:
        index = bisect_right(starts, start) - 1
        if index >= 0 and escapes[index][0] < start < escapes[index][1]:
            start = escapes[index][0]
        index = bisect_right(starts, end - 1) - 1
        if index >= 0 and escapes[index][1] > end:
            end = escapes[index][1]
        result.append((start, end))
    return result


def redact_session_ids(text):
    """text with every identifier spelling replaced; the same object if none."""
    escaped = may_be_escaped(text)
    if not escaped:
        if "cs_" not in text:
            return text
        result, count = _OCCURRENCE.subn(_MARKER, text)
        return result if count else text
    spans = [match.span() for match in _OCCURRENCE.finditer(text)]
    spans += _encoded_spans(text)
    if not spans:
        return text
    pieces, cursor, start, end = [], 0, None, None
    for span_start, span_end in sorted(_widened(spans, text)):
        if start is not None and span_start <= end:
            end = max(end, span_end)
            continue
        if start is not None:
            pieces.extend((text[cursor:start], _MARKER))
            cursor = end
        start, end = span_start, span_end
    pieces.extend((text[cursor:start], _MARKER, text[end:]))
    return "".join(pieces)


def redact_session_bytes(data):
    """The bytes form of redact_session_ids (UTF-8 with surrogateescape)."""
    if b"cs_" not in data and b"%" not in data and b"\\" not in data:
        return data
    text = data.decode("utf-8", errors="surrogateescape")
    result = redact_session_ids(text)
    if result is text:
        return data
    return result.encode("utf-8", errors="surrogateescape")


def redact_session_payload(value, _active=None):
    """A copy of a serialized payload with identifiers redacted in its text.

    Only exact built-in containers (dict, list, tuple) and text (str, bytes)
    are visited; any other object is returned as is and never rendered.
    Unchanged containers are returned as the same object. Cycles are visited
    once.
    """
    kind = type(value)
    if kind is str:
        return redact_session_ids(value)
    if kind is bytes:
        return redact_session_bytes(value)
    if kind is not dict and kind is not list and kind is not tuple:
        return value
    active = set() if _active is None else _active
    if id(value) in active:
        return value
    active.add(id(value))
    try:
        if kind is dict:
            items = [
                (
                    redact_session_payload(key, active),
                    redact_session_payload(item, active),
                )
                for key, item in value.items()
            ]
            if all(
                new_key is key and new_item is item
                for (new_key, new_item), (key, item) in zip(
                    items, value.items(), strict=True
                )
            ):
                return value
            return dict(items)
        items = [redact_session_payload(item, active) for item in value]
        if all(new is old for new, old in zip(items, value, strict=True)):
            return value
        return items if kind is list else tuple(items)
    finally:
        active.discard(id(value))


__all__ = [
    "SESSION_ID",
    "is_session_id",
    "redact_session_bytes",
    "redact_session_ids",
    "redact_session_payload",
]
