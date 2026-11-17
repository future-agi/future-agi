"""Every key a cassette recorded must appear in the live answer (Zapier T004,
scaling.md §5 L4).

A response becomes a set of key paths. Both sides are read the same way:
- an array is one segment, "[]", whatever its length;
- a member name the cassette scrubber would have replaced (a placeholder, an
  id of digits, a uuid or an email) is a wildcard, "*": it names a record,
  not a field;
- a string that holds a JSON object or array (the usual MCP text content) is
  read as JSON, under a "<json>" segment;
- at the protocol level, _meta and isError are not data.

A cassette path is present when the live answer has it, unverified when the
live answer holds null or an empty array on the way, or an empty object
where the cassette has a record name (the test account has no such record
today), and missing otherwise. A named field under an empty live object is
missing: the object is there and the field is not. Array items merge, so a
key is unverified only when no live item holds a value there: when one item
has null or [] and another holds an object, a key that object lacks is
missing.
"""

from __future__ import annotations

import json
import re

from .placeholders import PLACEHOLDER as _PLACEHOLDER
from .toolshash import Number

WILDCARD = "*"
ARRAY = "[]"
JSON_TEXT = "<json>"
MAX_DEPTH = 64
MAX_PATHS = 20000

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DIGITS = re.compile(r"^[0-9]+$")
_PROTOCOL_KEYS = ("_meta", "isError")


def is_record_name(name: str) -> bool:
    """A member name that names a record, not a field."""
    return bool(
        _PLACEHOLDER.match(name)
        or _DIGITS.match(name)
        or _UUID.match(name)
        or _EMAIL.match(name)
    )


def _json_text(value: str):
    text = value.strip()
    if not text or text[0] not in "{[":
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


class Node:
    __slots__ = ("children", "opaque", "empty_object")

    def __init__(self):
        self.children = {}
        self.opaque = False  # null or an empty array: nothing below is known
        self.empty_object = False  # an empty object: it holds no records

    def child(self, seg: str) -> Node:
        node = self.children.get(seg)
        if node is None:
            node = self.children[seg] = Node()
        return node


def tree(value, protocol: bool = True) -> Node:
    """The key-path tree of a value. protocol drops _meta and isError at the
    top and _meta in each content item (MCP structure, not data)."""
    root = Node()
    _walk(value, root, 0, protocol, top=True)
    return root


def _walk(value, node: Node, depth: int, protocol: bool, top: bool = False) -> None:
    if depth > MAX_DEPTH:
        return
    if value is None:
        node.opaque = True
        return
    if isinstance(value, dict):
        if not value:
            node.empty_object = True
            return
        for name, item in value.items():
            if protocol and top and name in _PROTOCOL_KEYS:
                continue
            child = node.child(WILDCARD if is_record_name(name) else name)
            if protocol and top and name == "content":
                _walk_content(item, child, depth + 1)
            else:
                _walk(item, child, depth + 1, False)
        return
    if isinstance(value, list):
        if not value:
            node.opaque = True
            return
        arr = node.child(ARRAY)
        for item in value:
            _walk(item, arr, depth + 1, False)
        return
    if isinstance(value, str) and not isinstance(value, Number):
        parsed = _json_text(value)
        if parsed is not None:
            _walk(parsed, node.child(JSON_TEXT), depth + 1, False)


def _walk_content(value, node: Node, depth: int) -> None:
    """An MCP content array: each item's _meta is protocol, not data."""
    if not isinstance(value, list) or not value:
        _walk(value, node, depth, False)
        return
    arr = node.child(ARRAY)
    for item in value:
        if isinstance(item, dict):
            item = {k: v for k, v in item.items() if k != "_meta"}
        _walk(item, arr, depth + 1, False)


def paths(node: Node, prefix=()) -> list:
    """Every path of a tree, as tuples of segments, in order."""
    out = []
    for seg in sorted(node.children):
        child = node.children[seg]
        path = prefix + (seg,)
        out.append(path)
        if len(out) > MAX_PATHS:
            break
        out.extend(paths(child, path))
    return out[:MAX_PATHS]


def render(path) -> str:
    out = ["$"]
    for seg in path:
        if seg in (ARRAY, JSON_TEXT):
            out.append(seg)
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", seg) or seg == WILDCARD:
            out.append("." + seg)
        else:
            out.append("[" + json.dumps(seg, ensure_ascii=True) + "]")
    return "".join(out)


PRESENT, UNVERIFIED, MISSING = "present", "unverified", "missing"


def _lookup(node: Node, path, i: int = 0) -> str:
    if i == len(path):
        return PRESENT
    if node.opaque and not node.children:
        return UNVERIFIED
    seg = path[i]
    if seg == WILDCARD and node.empty_object and not node.children:
        return UNVERIFIED  # no records today; a named field stays missing
    candidates = (
        list(node.children.values())
        if seg == WILDCARD
        else [node.children[seg]]
        if seg in node.children
        else []
    )
    best = MISSING  # a populated item lacks it; only a child lifts it
    for child in candidates:
        found = _lookup(child, path, i + 1)
        if found == PRESENT:
            return PRESENT
        if found == UNVERIFIED:
            best = UNVERIFIED
    return best


def compare(recorded, live) -> dict:
    """Which of the recorded answer's key paths the live answer lacks."""
    rec_paths = paths(tree(recorded))
    live_tree = tree(live)
    missing, unverified = [], []
    for path in rec_paths:
        found = _lookup(live_tree, path)
        if found == MISSING:
            missing.append(render(path))
        elif found == UNVERIFIED:
            unverified.append(render(path))
    rec_set = set(rec_paths)
    extra = sum(1 for p in paths(live_tree) if p not in rec_set)
    # report only the shortest missing paths: a missing object hides its fields
    shortest = [
        m
        for m in missing
        if not any(m != o and m.startswith(o) and m[len(o)] in ".[<" for o in missing)
    ]
    return {
        "recorded": len(rec_paths),
        "missing": shortest,
        "missingCount": len(missing),
        "unverified": len(unverified),
        "extraCount": extra,
    }
