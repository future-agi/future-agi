"""Bounded Discovery reserved expansion, deliberately narrower than RFC 6570."""

import re
import unicodedata
from urllib.parse import quote


def resource_pattern(schema):
    pattern = schema.get("pattern")
    if (
        schema.get("type") != "string"
        or not isinstance(pattern, str)
        or not 2 <= len(pattern) <= 512
        or not pattern.startswith("^")
        or not pattern.endswith("$")
        or "(?" in pattern
        or "\\" in pattern
        or any(unicodedata.category(c) == "Cc" for c in pattern)
    ):
        raise ValueError("reserved expansion needs a portable anchored string pattern")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError("reserved expansion has an invalid pattern") from exc


def expand_resource(value, schema):
    pattern = resource_pattern(schema)
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not pattern.fullmatch(value)
        or any(c in value for c in "%?#\\")
        or any(unicodedata.category(c) == "Cc" for c in value)
    ):
        raise ValueError("reserved expansion value is unsafe or does not match its declared pattern")
    segments = value.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError("reserved expansion contains an empty or dot segment")
    return "/".join(quote(segment, safe="") for segment in segments)
