"""Read the values a prompt's "use in code" example writes into its source."""

import json

_DECODER = json.JSONDecoder()
_GO_MAP = "map[string]interface{}{"
_GO_SLICE = "[]interface{}{"


def json_after(code, marker):
    """The JSON value that starts right after ``marker`` in ``code``."""
    value, _ = _DECODER.raw_decode(code, code.index(marker) + len(marker))
    return value


def go_literal_after(code, marker):
    """The Go composite literal that starts right after ``marker``, as Python values.

    Reads what a ``map[string]interface{}`` value can hold in Go source:
    ``map[string]interface{}{...}`` and ``[]interface{}{...}`` literals,
    double-quoted strings, numbers, ``true``, ``false`` and ``nil``. A JSON
    object or array is not a Go literal, and every element needs its comma
    because the closing brace sits on its own line.
    """
    value, _ = _go_value(code, code.index(marker) + len(marker))
    return value


def _skip_space(code, pos):
    while code[pos] in " \t\n":
        pos += 1
    return pos


def _go_value(code, pos):
    pos = _skip_space(code, pos)
    for opener, container in ((_GO_MAP, dict), (_GO_SLICE, list)):
        if code.startswith(opener, pos):
            return _go_container(code, pos + len(opener), container)
    if code.startswith("nil", pos):
        return None, pos + len("nil")
    if code[pos] in "{[":
        raise ValueError(f"not a Go literal: {code[pos:pos + 40]!r}")
    return _DECODER.raw_decode(code, pos)


def _go_container(code, pos, container):
    items = container()
    while True:
        pos = _skip_space(code, pos)
        if code[pos] == "}":
            return items, pos + 1
        if container is dict:
            key, pos = _DECODER.raw_decode(code, pos)
            pos = _skip_space(code, pos)
            if code[pos] != ":":
                raise ValueError(f"expected ':' at {code[pos:pos + 40]!r}")
            items[key], pos = _go_value(code, pos + 1)
        else:
            value, pos = _go_value(code, pos)
            items.append(value)
        if code[pos] != ",":
            raise ValueError(f"expected ',' at {code[pos:pos + 40]!r}")
        pos += 1
