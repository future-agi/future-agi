"""Shared, storage-independent semantics for source and UI-assigned tags.

Source names are ingestion-owned: an assignment adds names and/or colors, never
suppresses a source name. ``None`` means no assignment exists; ``[]`` means a
saved clear, which must suppress the *known legacy manual* fallback. Telemetry
columns are conservatively source-owned because their provenance is unknown.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from tracer.types.entity_tags import EffectiveTags, TagItem, TagSourceRow

MAX_MANUAL_TAGS = 100
MAX_TAG_NAME_LENGTH = 256
MAX_MANUAL_TAG_BYTES = 64 * 1024
MAX_SOURCE_TAG_BYTES = 1024 * 1024
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


class TagInputError(ValueError):
    """Invalid user input, safe to report as a validation error."""


class TagReadUnavailable(RuntimeError):
    """Incomplete/over-budget source or assignment input; never an empty result."""

    @property
    def code(self) -> str:
        return str(self)


def _encoded_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise TagInputError("Tags must contain valid JSON values.") from None


def normalize_manual_tags(value: object) -> list[TagItem]:
    """Accept strings and rich items without truncation, coercion or data loss.

    Repeated identical names are one assignment. A rich item can add color to a
    string item, but conflicting colors for the same name are ambiguous input.
    """
    if not isinstance(value, list) or len(value) > MAX_MANUAL_TAGS:
        raise TagInputError(
            f"tags must be an array of at most {MAX_MANUAL_TAGS} items."
        )
    if _encoded_size(value) > MAX_MANUAL_TAG_BYTES:
        raise TagInputError("Tag assignment exceeds the byte limit.")
    normalized: dict[str, TagItem] = {}
    for item in value:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or set(item) - {"name", "color"}:
            raise TagInputError("Each tag must be a name or a {name, color} object.")
        name = item.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > MAX_TAG_NAME_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise TagInputError(
                "Tag names must be nonblank, at most 256 characters, without control characters."
            )
        color = item.get("color")
        if "color" in item and (
            not isinstance(color, str) or not _COLOR.fullmatch(color)
        ):
            raise TagInputError("Tag colors must be six-digit hexadecimal colors.")
        previous = normalized.setdefault(name, {"name": name})
        if color is not None:
            if "color" in previous and previous["color"] != color:
                raise TagInputError("Conflicting colors for the same tag name.")
            previous["color"] = color
    return list(normalized.values())


def _decode(value: object) -> object:
    # The collector may preserve an already JSON-encoded attribute string.
    # Bound decoding depth and bytes of one tag value; over-budget is
    # unavailable, not absence. Never call this on a whole attribute map.
    try:
        if _encoded_size(value) > MAX_SOURCE_TAG_BYTES:
            raise TagReadUnavailable("tag_source_budget_exceeded")
    except TagInputError:
        return None
    for _ in range(3):
        if not isinstance(value, str):
            return value
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    return value if not isinstance(value, str) else None


def _source_items(value: object, *, skip_oversized: bool = False) -> list[TagItem]:
    try:
        decoded = _decode(value)
    except TagReadUnavailable:
        if not skip_oversized:
            raise
        return []
    if not isinstance(decoded, list):
        return []
    result = []
    for item in decoded:
        name = (
            item
            if isinstance(item, str)
            else item.get("name")
            if isinstance(item, dict)
            else None
        )
        if not isinstance(name, str) or not name.strip():
            continue
        tag: TagItem = {"name": name}
        color = item.get("color") if isinstance(item, dict) else None
        if isinstance(color, str) and _COLOR.fullmatch(color):
            tag["color"] = color
        result.append(tag)
    return result


def _attribute_map(value: object) -> Mapping | None:
    """Decode one attribute container without budgeting its non-tag payload.

    Read surfaces already fetched the whole map (LLM input/output, retrieval
    documents, images). Its size must never make the read unavailable; only the
    extracted ``tag.tags`` value is bounded by ``_decode``. A JSON string that
    cannot spell a tag key, even through an escape, is not parsed at all.
    """
    for _ in range(3):
        if isinstance(value, Mapping):
            return value
        if not isinstance(value, str) or ("tag" not in value and "\\u" not in value):
            return None
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    return value if isinstance(value, Mapping) else None


def _source_tags(row: TagSourceRow, *, skip_oversized: bool = False) -> list[TagItem]:
    inputs = [row.get("tags")]
    for key in ("attrs_string", "attributes_extra", "span_attributes"):
        attributes = _attribute_map(row.get(key))
        if attributes is None:
            continue
        inputs.append(attributes.get("tag.tags"))
        nested = attributes.get("tag")
        if isinstance(nested, Mapping):
            inputs.append(nested.get("tags"))
    tags: dict[str, TagItem] = {}
    for value in inputs:
        for tag in _source_items(value, skip_oversized=skip_oversized):
            existing = tags.setdefault(tag["name"], tag)
            if "color" not in existing and "color" in tag:
                existing["color"] = tag["color"]
    return list(tags.values())


def source_tag_names(row: TagSourceRow) -> tuple[str, ...]:
    """Union the dedicated column and SDK attributes, including an empty column."""
    return tuple(tag["name"] for tag in _source_tags(row))


def effective_tags(
    row: TagSourceRow,
    *,
    manual_tags: object,
    legacy_tags: object = None,
    skip_oversized: bool = False,
) -> EffectiveTags:
    """Union source and assigned names, suppressing legacy manual tags after a save.

    ``skip_oversized`` omits a single tag value above the source budget instead
    of failing; callers must then make that row read-only.
    """
    source = _source_tags(row, skip_oversized=skip_oversized)
    tags: dict[str, TagItem] = {tag["name"]: tag.copy() for tag in source}
    if manual_tags is None:
        manual = _source_items(legacy_tags, skip_oversized=skip_oversized)
    else:
        try:
            manual = normalize_manual_tags(manual_tags)
        except TagInputError:
            # Stored invalid data is an unavailable read, not caller input and
            # never permission to silently omit saved assignments.
            raise TagReadUnavailable("tag_assignment_invalid") from None
    for tag in manual:
        tags.setdefault(tag["name"], {"name": tag["name"]}).update(tag)
    return {
        "tags": list(tags.values()),
        "source_tag_names": [tag["name"] for tag in source],
    }
