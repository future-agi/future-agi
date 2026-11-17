"""The gateway's span-scalar sanitizer (telemetry.SanitizeText), for SIEM export.

A text value leaves the product only as a bounded, identifier-shaped scalar
that the gateway itself would export unchanged; anything else is exported as
the fixed placeholder, never truncated. This ports internal/mcp/telemetry
(content.go, content_text.go, content_encoding.go) and the shared credential
shapes (internal/privacy/credentials.go) to decide *whether* the gateway's
scrubber would change a value, never how. Where an exact port would need the
gateway's URL parser or JSON re-encoding (a URL, a quote, a bracket or brace),
or a bound is reached, it fails closed. Its redact decisions are a superset of
the gateway's, never a subset. Pure: no Django, no I/O.
"""

import re
import unicodedata
from functools import lru_cache

REDACTED = "[redacted]"
SCALAR_SHAPE = re.compile(r"[A-Za-z0-9_.:/-]{1,256}")

_MAX_TEXT_BYTES = 1 << 20
_MAX_TOKENS = 256
_MAX_DEPTH = 32
_MAX_ENCODING_DEPTH = 4
_CREDENTIAL_TERMS = (
    "cookie",
    "jwt",
    "assertion",
    "bearer",
    "auth",
    "token",
    "key",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "pwd",
    "jwk",
    "signature",
    "session",
    "credential",
    "subscription",
)
# RE2's (?i)[a-z] is the ASCII letters plus the two letters that simple-fold
# into them (U+017F LONG S, U+212A KELVIN SIGN); spelled out so that Python's
# wider IGNORECASE folding cannot move a token boundary.
_LETTER = "A-Za-z\u017f\u212a"
# contentToken's assignment alternative: a name, then ':' or '='. Its URL and
# JSON alternatives are refused before tokenizing (_UNPORTED).
_NAME_TOKEN = re.compile(
    rf"[\"']?([{_LETTER}][{_LETTER}0-9_. /-]*?)[\"']?[ \t]*[:=][ \t]*"
)
_UNPORTED = ("//", "[", "{", '"')
_ENCODED_ATOM = re.compile(r"[A-Za-z0-9+/_%.\-]+={0,2}")
_BASE64_ATOM = re.compile(r"[A-Za-z0-9+/_\-]+={0,2}")
_JWT_SHAPE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
# Go's \b and \s are ASCII-only; these spell them out exactly.
_B = r"(?:(?<![A-Za-z0-9_])(?=[A-Za-z0-9_])|(?<=[A-Za-z0-9_])(?![A-Za-z0-9_]))"
_S = r"[\t\n\f\r ]"
_CREDENTIAL_SHAPES = (
    re.compile(rf"{_B}(?i:bearer){_S}+[A-Za-z0-9._~+/=\u017f\u212a-]+"),
    re.compile(rf"{_B}sk-[A-Za-z0-9_-]{{8,}}"),
    re.compile(rf"{_B}AIza[0-9A-Za-z_-]{{20,}}"),
    re.compile(rf"{_B}(?:AKIA|ASIA)[0-9A-Z]{{16}}{_B}"),
    re.compile(
        rf"{_B}(?i:(?:x-)?api[_-]?key|key|token|access[_-]?token|secret|password)"
        rf"[\"']?{_S}*[:=]{_S}*[\"']?[^\t\n\f\r \"'&,;)}}]+"
    ),
)
_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_URL = _STD[:62] + "-_"
# base64.StdEncoding, RawStdEncoding, URLEncoding, RawURLEncoding, in Go's order.
_ENCODINGS = (
    ({c: i for i, c in enumerate(_STD)}, True),
    ({c: i for i, c in enumerate(_STD)}, False),
    ({c: i for i, c in enumerate(_URL)}, True),
    ({c: i for i, c in enumerate(_URL)}, False),
)


def sanitize(text, redactor=None):
    """``text`` as an exported scalar: itself, "" or REDACTED.

    ``redactor`` is the organization's privacy redactor (siem_redactor), or
    None for none, as the gateway's ``SanitizeText(text, r)``."""
    if not text:
        return ""
    if not _approved(text):
        return REDACTED
    if redactor is not None and redactor.redacts(text):
        return REDACTED
    return text


@lru_cache(maxsize=8192)
def _approved(text):
    # Exports repeat the same tools, servers and outcomes row after row.
    return bool(SCALAR_SHAPE.fullmatch(text)) and not dirty(text)


def dirty(text, depth=0):
    """Whether the gateway's ``scrubText`` could change ``text``."""
    if (
        depth >= _MAX_DEPTH
        or len(text.encode("utf-8", "surrogatepass")) > _MAX_TEXT_BYTES
    ):
        return True
    if any(mark in text for mark in _UNPORTED):
        return True
    decoded, ok = _canonical(text)
    if not ok or (decoded != text and dirty(decoded, depth + 1)):
        return True
    # Unlike the gateway, a value that decodes cleanly is also scanned as it
    # stands: a decoding the gateway would refuse cannot hide its raw form.
    pos = tokens = 0
    while pos < len(text):
        if tokens >= _MAX_TOKENS:
            return True
        tokens += 1
        match = _NAME_TOKEN.search(text, pos)
        if match is None:
            if _atoms_dirty(text[pos:], depth):
                return True
            break
        if _atoms_dirty(text[pos : match.start()], depth):
            return True
        if _atoms_dirty(match.group(0), depth) or credential_field(match.group(1)):
            return True
        pos = match.end()
    return _atoms_dirty(text, depth)


def credential_field(name):
    """isCredentialField over the name and every layer it decodes to."""
    layers, ok = _layers(name)
    if not ok:
        return True
    for layer in layers:
        # normalizedField keeps letters, lowercased. Only the ASCII ones can
        # form a term, and dropping the others can only join terms, never
        # split one, so differing Unicode tables cannot hide a field.
        letters = "".join(
            low
            for ch in layer
            if ch.isalpha()
            for low in ch.lower()
            if "a" <= low <= "z"
        )
        if any(term in letters for term in _CREDENTIAL_TERMS):
            return True
    return False


def _atoms_dirty(segment, depth):
    if not segment:
        return False
    for match in _ENCODED_ATOM.finditer(segment):
        atom = match.group(0)
        decoded, ok = _canonical(atom)
        if not ok or (decoded != atom and dirty(decoded, depth + 1)):
            return True
    for match in _JWT_SHAPE.finditer(segment):
        # A JSON-object header marks a JWT (RawURLEncoding, not strict).
        head = match.group(0).split(".", 1)[0]
        raw = _b64_decode(head, _ENCODINGS[3][0], False, strict=False)
        if raw is not None and raw.decode("utf-8", "replace").strip().startswith("{"):
            return True
    return any(shape.search(segment) for shape in _CREDENTIAL_SHAPES)


def _canonical(text):
    """canonicalText: the decoded text and whether it decoded within bounds."""
    layers, ok = _layers(text)
    return (layers[-1] if ok else ""), ok


def _layers(text):
    """``text`` and each layer it decodes to; False when a layer is malformed
    or one remains at the bound."""
    layers = [text]
    for _ in range(_MAX_ENCODING_DEPTH):
        decoded, encoded, valid = _decode_layer(layers[-1])
        if not valid:
            return layers, False
        if not encoded:
            return layers, True
        layers.append(decoded)
    _, encoded, valid = _decode_layer(layers[-1])
    return layers, valid and not encoded


def _decode_layer(text):
    if "%" in text:
        raw = _path_unescape(text)
        if raw is None:
            return "", False, False
        if raw != text.encode("utf-8", "surrogatepass"):
            try:
                return raw.decode("utf-8"), True, True
            except UnicodeDecodeError:
                return "", True, False
    if len(text) < 4 or not _BASE64_ATOM.fullmatch(text):
        return text, False, True
    for alphabet, padded in _ENCODINGS:
        raw = _b64_decode(text, alphabet, padded)
        if not raw:
            continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if all(_printable(ch) for ch in decoded):
            return decoded, True, True
    return text, False, True


def _printable(ch):
    # Go's unicode.IsPrint; a code point this Python's tables leave unassigned
    # may be assigned in Go's, so it counts as printable (more decoding).
    return ch.isprintable() or ch in "\r\n\t" or unicodedata.category(ch) == "Cn"


def _path_unescape(text):
    """url.PathUnescape: the bytes, or None for a malformed escape."""
    data = text.encode("utf-8", "surrogatepass")
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x25:  # '%'
            pair = data[i + 1 : i + 3]
            if len(pair) < 2 or not all(c in b"0123456789abcdefABCDEF" for c in pair):
                return None
            out.append(int(pair, 16))
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def _b64_decode(text, alphabet, padded, strict=True):
    """A base64 decoding as Go's (``Encoding.Strict()`` when strict), or None."""
    body = text
    if padded:
        if len(text) % 4:
            return None
        body = text.rstrip("=")
        pad = len(text) - len(body)
        if pad and (pad > 2 or len(body) % 4 + pad != 4):
            return None
    if len(body) % 4 == 1:
        return None
    bits = nbits = 0
    out = bytearray()
    for ch in body:
        value = alphabet.get(ch)
        if value is None:
            return None
        bits = (bits << 6) | value
        nbits += 6
        if nbits >= 8:
            nbits -= 8
            out.append((bits >> nbits) & 0xFF)
            bits &= (1 << nbits) - 1
    if strict and bits:
        return None
    return bytes(out)
