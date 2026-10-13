"""Bounded, storage-free output comparison for one persisted run case."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

_MAX_OUTPUT_BYTES = 1 * 1024 * 1024
_MAX_PREVIEW_BYTES = 16 * 1024
_MAX_CHANGED_PATHS = 100
_MAX_DEPTH = 100


class OutputDiffError(ValueError):
    """The selected output cannot be represented as strict JSON."""


@dataclass(frozen=True)
class OutputSide:
    item_id: str | None
    status: str | None
    output_state: str
    output_digest: str | None
    output_bytes: int | None
    output_preview: Any
    preview_omitted: bool
    _value: Any = None


@dataclass(frozen=True)
class OutputComparison:
    status: str
    missing_side: str | None
    changed_paths: tuple[str, ...]
    changed_path_count: int | None
    truncated: bool


@dataclass(frozen=True)
class OutputDiff:
    baseline: OutputSide
    candidate: OutputSide
    comparison: OutputComparison


def _normalise(value: Any, *, path: str = "output", depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        raise OutputDiffError("output JSON nesting exceeds 100 levels")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise OutputDiffError(f"{path} contains an invalid JSON string")
        return value
    if type(value) is int:
        try:
            if len(str(abs(value))) > 4300:
                raise OutputDiffError(f"{path} integer is too large")
        except ValueError as exc:
            raise OutputDiffError(f"{path} integer is too large") from exc
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise OutputDiffError(f"{path} contains a non-finite number")
        # JSON numbers are compared by value. Normalize integral floats so
        # 1.0 and 1 have the same digest while bool remains distinct.
        return int(value) if value.is_integer() else value
    if type(value) is list:
        return [
            _normalise(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise OutputDiffError(f"{path} object keys must be strings")
        return {
            key: _normalise(item, path=f"{path}.{key}", depth=depth + 1)
            for key, item in value.items()
        }
    raise OutputDiffError(f"{path} contains a non-JSON value")


def _encoded(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OutputDiffError("output is not strict JSON") from exc


def _pointer(value: str | int) -> str:
    token = str(value).replace("~", "~0").replace("/", "~1")
    return f"/{token}"


def _changed_paths(
    before: Any,
    after: Any,
    *,
    path: str = "",
    paths: list[str] | None = None,
    total: list[int] | None = None,
) -> tuple[tuple[str, ...], int, bool]:
    paths = [] if paths is None else paths
    total = [0] if total is None else total

    def changed(current: str) -> None:
        total[0] += 1
        if len(paths) < _MAX_CHANGED_PATHS:
            paths.append(current)

    if type(before) is not type(after):
        changed(path)
    elif isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            child = _pointer(key) if not path else f"{path}{_pointer(key)}"
            if key not in before or key not in after:
                changed(child)
            else:
                _changed_paths(
                    before[key], after[key], path=child, paths=paths, total=total
                )
    elif isinstance(before, list):
        for index in range(max(len(before), len(after))):
            child = f"{path}{_pointer(index)}"
            if index >= len(before) or index >= len(after):
                changed(child)
            else:
                _changed_paths(
                    before[index], after[index], path=child, paths=paths, total=total
                )
    elif before != after:
        changed(path)
    return tuple(paths), total[0], total[0] > len(paths)


def _side(item: object | None) -> OutputSide:
    if item is None:
        return OutputSide(None, None, "missing", None, None, None, False)
    item_id = str(item.id)
    status = item.status
    error = item.error
    if status != "completed" or error is not None:
        return OutputSide(item_id, status, "unavailable", None, None, None, False)
    if getattr(item, "_output_over_limit", False):
        return OutputSide(item_id, status, "over_limit", None, None, None, True)
    raw = getattr(item, "output", None)
    try:
        value = _normalise(raw)
        encoded = _encoded(value)
    except OutputDiffError:
        return OutputSide(item_id, status, "invalid", None, None, None, False)
    size = len(encoded)
    if size > _MAX_OUTPUT_BYTES:
        return OutputSide(item_id, status, "over_limit", None, size, None, True)
    digest = hashlib.sha256(encoded).hexdigest()
    preview_omitted = size > _MAX_PREVIEW_BYTES
    return OutputSide(
        item_id,
        status,
        "available",
        digest,
        size,
        None if preview_omitted else value,
        preview_omitted,
        value,
    )


def compare_output_items(
    baseline: object | None,
    candidate: object | None,
) -> OutputDiff:
    """Compare one exact case/trial pair without mutating or persisting state."""

    before, after = _side(baseline), _side(candidate)
    if before.output_state != "available" or after.output_state != "available":
        missing = (
            "both"
            if before.output_state == after.output_state == "missing"
            else "baseline"
            if before.output_state == "missing"
            else "candidate"
            if after.output_state == "missing"
            else None
        )
        comparison = OutputComparison("not_comparable", missing, (), None, False)
    else:
        paths, count, truncated = _changed_paths(before._value, after._value)
        comparison = OutputComparison(
            "unchanged" if count == 0 else "changed",
            None,
            paths,
            count,
            truncated,
        )
    return OutputDiff(before, after, comparison)


def output_diff_payload(diff: OutputDiff) -> dict[str, Any]:
    """Strip private comparison values for a typed response serializer."""

    def side_payload(side: OutputSide) -> dict[str, Any]:
        return {
            "item_id": side.item_id,
            "status": side.status,
            "output_state": side.output_state,
            "output_digest": side.output_digest,
            "output_bytes": side.output_bytes,
            "output_preview": side.output_preview,
            "preview_omitted": side.preview_omitted,
        }

    return {
        "baseline": side_payload(diff.baseline),
        "candidate": side_payload(diff.candidate),
        "output_comparison": {
            "status": diff.comparison.status,
            "missing_side": diff.comparison.missing_side,
            "changed_paths": list(diff.comparison.changed_paths),
            "changed_path_count": diff.comparison.changed_path_count,
            "truncated": diff.comparison.truncated,
        },
    }
