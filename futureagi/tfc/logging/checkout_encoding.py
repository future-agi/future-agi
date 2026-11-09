"""Match encoded checkout paths while retaining their original text offsets."""

import re

_PREFIX = "/agentcc/managed-credits/checkout/"
_MARKER = "[redacted]"
# One escape level of the spellings a diagnostic renderer can produce: URL
# percent octets, JSON/JavaScript \uXXXX (a surrogate pair is one character)
# and \/, Python \xXX and \UXXXXXXXX, and an escaped backslash (so a rendered
# "\\u002f" decodes in two levels).
_ESCAPE = re.compile(
    r"%(?P<pct>[0-9a-fA-F]{2})"
    r"|\\(?:u(?P<high>[dD][89abAB][0-9a-fA-F]{2})\\u(?P<low>[dD][c-fC-F][0-9a-fA-F]{2})"
    r"|u(?P<u>[0-9a-fA-F]{4})|x(?P<x>[0-9a-fA-F]{2})|U(?P<U>[0-9a-fA-F]{8})"
    r"|(?P<lit>[/\\]))"
)
_SEGMENT_END = re.compile(r"[/\s?#\"'<>\\]|%(?:25)*2[fF]")
_LEVELS = 6


def may_be_escaped(text):
    """Whether text contains a decodable escape; a cheap necessary test.

    Substring checks first, so ordinary text never runs a pattern. A
    printf-style template such as "%s" or "%d ", or a JSON control escape
    such as a quoted newline, is not a decodable escape.
    """
    if "\\" not in text and "%" not in text:
        return False
    return _ESCAPE.search(text) is not None


def _character(match):
    high = match["high"]
    if high is not None:
        # A JSON surrogate pair spells one non-BMP character (e.g. U+1F600).
        code = 0x10000 + ((int(high, 16) - 0xD800) << 10) + int(match["low"], 16)
        return chr(code - 0xDC00)
    hexadecimal = match["pct"] or match["u"] or match["x"] or match["U"]
    if hexadecimal is None:
        return match["lit"]
    code = int(hexadecimal, 16)
    if code >= 0x110000 or 0xD800 <= code <= 0xDFFF:
        # Not a character (a lone surrogate or out of range): keep it as
        # written, so no decoded level holds text that cannot be encoded.
        return match[0]
    return chr(code)


def decode_level(text):
    """Decode one escape level, for matching only (never for output)."""
    return _ESCAPE.sub(_character, text)


def decoded_levels(text):
    """The rendered text followed by each successively decoded level."""
    yield text
    for _ in range(_LEVELS):
        decoded = decode_level(text)
        if decoded == text:
            return
        yield decoded
        text = decoded


def contains_encoded_prefix(text):
    """True when some decoded level spells the checkout prefix."""
    return any(_PREFIX in level for level in decoded_levels(text))


def _decode_with_offsets(text, offsets):
    pieces, mapped, cursor = [], [], 0
    for match in _ESCAPE.finditer(text):
        replacement = _character(match)
        if replacement == match[0]:
            continue
        start, end = match.span()
        pieces.extend((text[cursor:start], replacement))
        mapped.extend(offsets[cursor:start])
        mapped.append((offsets[start][0], offsets[end - 1][1]))
        cursor = end
    if not pieces:
        return None
    pieces.append(text[cursor:])
    mapped.extend(offsets[cursor:])
    return "".join(pieces), mapped


def scrub_encoded_paths(text):
    """Replace raw session segments, including escaped and mixed spellings.

    Decoding is for matching only. Offsets point back into the rendered input,
    so JSON syntax, escape spelling and everything outside the segment remain
    untouched. A segment ends at its first delimiter in the decoded level that
    revealed the prefix; the whole original span of that segment is replaced.
    Percent octets need not be valid UTF-8 to locate ASCII prefixes.
    """
    if not contains_encoded_prefix(text):
        return text
    decoded = text
    offsets = [(index, index + 1) for index in range(len(text))]
    spans = []
    for _ in range(_LEVELS + 1):
        cursor = decoded.find(_PREFIX)
        while cursor >= 0:
            segment = cursor + len(_PREFIX)
            delimiter = _SEGMENT_END.search(decoded, segment)
            end = delimiter.start() if delimiter else len(decoded)
            if end > segment:
                spans.append((offsets[segment][0], offsets[end - 1][1]))
            cursor = decoded.find(_PREFIX, segment)
        level = _decode_with_offsets(decoded, offsets)
        if level is None:
            break
        decoded, offsets = level
    # Union overlapping spans: a literal prefix can end early at an escaped
    # character that a later decoded level shows is part of the same segment.
    pieces, cursor, start, end = [], 0, None, None
    for span_start, span_end in sorted(spans):
        if start is not None and span_start <= end:
            end = max(end, span_end)
            continue
        if start is not None:
            pieces.extend((text[cursor:start], _MARKER))
            cursor = end
        start, end = span_start, span_end
    if start is not None:
        pieces.extend((text[cursor:start], _MARKER))
        cursor = end
    return "".join((*pieces, text[cursor:]))
