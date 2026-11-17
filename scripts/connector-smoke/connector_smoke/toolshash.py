"""The gateway's tools hash (M17), recomputed outside Go.

``tools_hash`` follows ``agentcc-gateway/internal/mcp/probe.go`` ``ToolsHash``
exactly, as its doc comment requires of a recompute outside Go:

- each tool is projected onto those of name, title, description, inputSchema,
  outputSchema and annotations it carries, values as the upstream sent them;
- each projection is JSON with no insignificant whitespace and object keys
  sorted bytewise at every depth;
- number literals are kept byte for byte (1.50 stays 1.50, 1e3 stays 1e3);
- strings use Go's encoding/json escaping with HTML escaping off: \\" \\\\ \\b
  \\f \\n \\r \\t, other C0 controls as \\u00xx (lowercase), U+2028 and U+2029
  escaped, and invalid UTF-8 (one U+FFFD per invalid byte, as Go's decoder
  replaces) or an escaped lone surrogate as U+FFFD; every other character as
  itself;
- the projections are sorted by name, then by their bytes, and joined with
  "," inside "[" and "]".

tests/fixtures/toolshash-vectors.json holds hashes Go's own ToolsHash computed;
tests/test_toolshash.py keeps this port equal to them.
"""

from __future__ import annotations

import hashlib
import json

PROJECTED_FIELDS = (
    "name",
    "title",
    "description",
    "inputSchema",
    "outputSchema",
    "annotations",
)


class ToolsHashError(ValueError):
    """A tool list ToolsHash refuses (Go returns an error for it)."""


class Number(str):
    """A JSON number literal, kept as written."""


def _reject_constant(name: str):
    raise ToolsHashError(f"{name} is not JSON")


def decode_go_utf8(data: bytes) -> str:
    """Decode bytes as Go's encoding/json reads string bytes: each byte that
    does not start a well-formed UTF-8 sequence becomes one U+FFFD."""
    out = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            out.append(chr(b))
            i += 1
            continue
        size, lo, hi = 0, 0x80, 0xBF
        if 0xC2 <= b <= 0xDF:
            size = 2
        elif 0xE0 <= b <= 0xEF:
            size = 3
            if b == 0xE0:
                lo = 0xA0
            elif b == 0xED:
                hi = 0x9F
        elif 0xF0 <= b <= 0xF4:
            size = 4
            if b == 0xF0:
                lo = 0x90
            elif b == 0xF4:
                hi = 0x8F
        ok = size > 0 and i + size <= n
        if ok:
            ok = lo <= data[i + 1] <= hi and all(
                0x80 <= data[j] <= 0xBF for j in range(i + 2, i + size)
            )
        if not ok:
            out.append("�")
            i += 1
            continue
        out.append(data[i : i + size].decode("utf-8"))
        i += size
    return "".join(out)


def _clean(value):
    """Replace lone surrogates (from an escaped \\ud800) with U+FFFD, as Go's
    decoder does, at every depth."""
    if isinstance(value, Number):
        return value
    if isinstance(value, str):
        return "".join("�" if 0xD800 <= ord(c) <= 0xDFFF else c for c in value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[_clean(k)] = _clean(v)
        return out
    return value


def loads_go(data) -> object:
    """Parse JSON the way the gateway reads an upstream's answer: number
    literals kept as text, invalid UTF-8 and lone surrogates replaced, the
    last of duplicate keys kept, NaN and Infinity refused."""
    if isinstance(data, (bytes, bytearray)):
        text = decode_go_utf8(bytes(data))
    else:
        text = data
    try:
        value = json.loads(
            text, parse_int=Number, parse_float=Number, parse_constant=_reject_constant
        )
    except json.JSONDecodeError as exc:
        raise ToolsHashError(f"not JSON: {exc.msg}") from None
    return _clean(value)


_SHORT = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _go_string(s: str) -> str:
    out = ['"']
    for c in s:
        o = ord(c)
        if c in _SHORT:
            out.append(_SHORT[c])
        elif o < 0x20:
            out.append(f"\\u{o:04x}")
        elif o in (0x2028, 0x2029):
            out.append(f"\\u{o:04x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _bytes_key(s: str) -> bytes:
    return s.encode("utf-8")


def canonical(value) -> str:
    """Go's encoding/json output for a decoded value: keys sorted bytewise,
    no whitespace, number literals as written."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Number):
        return str.__str__(value)
    if isinstance(value, str):
        return _go_string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_bytes_key)
        return (
            "{"
            + ",".join(_go_string(k) + ":" + canonical(value[k]) for k in keys)
            + "}"
        )
    raise ToolsHashError(f"unexpected value of type {type(value).__name__}")


def tool_canonical(tool) -> tuple[str, bytes]:
    """One tool's sort name and canonical projection."""
    if tool is None:
        tool = {}  # Go unmarshals JSON null into a nil map: no fields
    if not isinstance(tool, dict):
        raise ToolsHashError("a tool is not a JSON object")
    projection = {k: tool[k] for k in PROJECTED_FIELDS if k in tool}
    name = tool.get("name")
    if not isinstance(name, str) or isinstance(name, Number):
        name = ""
    return name, canonical(projection).encode("utf-8")


def tools_hash(tools) -> str:
    """The tools hash of decoded tools (from loads_go), as the manifest
    writes it: "sha256:" and the hex digest."""
    if tools is None:
        tools = []
    entries = [tool_canonical(t) for t in tools]
    entries.sort(key=lambda e: (_bytes_key(e[0]), e[1]))
    digest = hashlib.sha256(b"[" + b",".join(e[1] for e in entries) + b"]").hexdigest()
    return "sha256:" + digest


def tool_hash(tool) -> str:
    """The hash of a one-tool list: a tool's own fingerprint in reports."""
    return tools_hash([tool])
