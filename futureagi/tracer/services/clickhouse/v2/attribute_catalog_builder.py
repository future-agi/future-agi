"""Bounded pure row construction for the span-attribute catalog.

The builder consumes one span's already-canonical typed maps. It performs no
I/O and intentionally has no batch API: output memory is bounded by the three
explicit per-span limits rather than by batch size times attribute cardinality.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    CanonicalCatalogScalar,
    encode_catalog_scalar,
)

AttributeType = Literal["string", "number", "boolean", "array", "map", "json"]
CatalogSourceKind = Literal["custom_attribute", "system_attribute"]

GAP_MAX_KEYS = "max_keys"
GAP_MAX_ARRAY_MEMBERS = "max_array_members"
GAP_MAX_ENCODED_BYTES = "max_encoded_bytes"
GAP_INVALID_ATTRIBUTE_KEY = "invalid_attribute_key"
GAP_INVALID_SCALAR = "invalid_scalar"
GAP_INVALID_BOOLEAN = "invalid_boolean"

_GAP_REASON_ORDER = (
    GAP_MAX_KEYS,
    GAP_MAX_ARRAY_MEMBERS,
    GAP_MAX_ENCODED_BYTES,
    GAP_INVALID_ATTRIBUTE_KEY,
    GAP_INVALID_SCALAR,
    GAP_INVALID_BOOLEAN,
)
_TRUNCATION_REASONS = frozenset(
    (GAP_MAX_KEYS, GAP_MAX_ARRAY_MEMBERS, GAP_MAX_ENCODED_BYTES)
)
_ATTRIBUTE_TYPE_RANK = {
    "string": 1,
    "number": 2,
    "boolean": 3,
    "array": 4,
    "map": 5,
    "json": 6,
}


@dataclass(frozen=True, slots=True)
class CatalogScope:
    project_id: str
    seen_at: datetime
    catalog_epoch: int
    source_kind: CatalogSourceKind = "custom_attribute"


@dataclass(frozen=True, slots=True)
class CatalogBuildLimits:
    max_keys: int
    max_array_members: int
    max_encoded_bytes: int


@dataclass(frozen=True, slots=True)
class CatalogKeyRow:
    project_id: str
    source_kind: CatalogSourceKind
    attribute_key: str
    key_folded: str
    attribute_type: AttributeType
    first_seen: datetime
    last_seen: datetime
    catalog_epoch: int


@dataclass(frozen=True, slots=True)
class CatalogValueRow:
    project_id: str
    source_kind: CatalogSourceKind
    attribute_key: str
    attribute_type: AttributeType
    value_fingerprint: str
    value_json: str
    value_search_text: str
    first_seen: datetime
    last_seen: datetime
    catalog_epoch: int


@dataclass(frozen=True, slots=True)
class CatalogBuildMetadata:
    complete: bool
    truncated: bool
    gap_reasons: tuple[str, ...]
    candidate_keys: int
    valid_candidate_keys: int
    key_rows_emitted: int
    keys_omitted: int
    value_rows_emitted: int
    array_members_total: int
    array_members_inspected: int
    array_members_omitted: int
    non_scalar_array_members_skipped: int
    duplicate_values_skipped: int
    invalid_attribute_keys: int
    invalid_scalar_values: int
    invalid_boolean_values: int
    encoded_bytes: int


@dataclass(frozen=True, slots=True)
class CatalogBuildResult:
    key_rows: tuple[CatalogKeyRow, ...]
    value_rows: tuple[CatalogValueRow, ...]
    metadata: CatalogBuildMetadata


@dataclass(frozen=True, slots=True)
class _Candidate:
    key: str
    attribute_type: AttributeType
    value: Any = None
    array: list[Any] | None = None
    key_only: bool = False

    @property
    def sort_key(self) -> tuple[str, int]:
        return self.key, _ATTRIBUTE_TYPE_RANK[self.attribute_type]


@dataclass(frozen=True, slots=True)
class _ReverseCandidate:
    """Reverse comparison makes heap[0] the largest retained candidate."""

    candidate: _Candidate

    def __lt__(self, other: _ReverseCandidate) -> bool:
        return self.candidate.sort_key > other.candidate.sort_key


def build_catalog_rows(
    *,
    scope: CatalogScope,
    attrs_string: Mapping[str, str],
    attrs_number: Mapping[str, int | float | Decimal],
    attrs_bool: Mapping[str, int],
    attributes_extra: Mapping[str, Any],
    limits: CatalogBuildLimits,
    key_only_attributes: frozenset[tuple[str, AttributeType]] = frozenset(),
) -> CatalogBuildResult:
    """Build key/value rows for one span under explicit hard ceilings.

    Keys are a deterministic bounded top-K by ``(key, type)``. Arrays preserve
    source order and share one global inspection budget. Maps and all other
    JSON overflow shapes are key-only. Callers may also explicitly mark a
    projected scalar or array key-only when its source value exceeded the
    projection policy; the key remains complete and no value row is invented.
    Every builder limit or malformed scalar is returned as fixed-order gap
    metadata; no unmarked omission is silently reported as complete.

    Deterministic top-K selection visits every supplied map entry but retains
    at most ``max_keys`` references. The canonical-map producer is responsible
    for bounding a span's source maps; this function bounds all additional
    memory and every emitted/array-expanded row.

    ``encoded_bytes`` counts only dynamic UTF-8 catalog fields. For key rows it
    counts key + ASCII-folded key + type. For value rows it counts key + type +
    64-byte fingerprint + canonical JSON + search text. Fixed-width identity,
    time, and epoch columns are excluded.
    """

    if (
        min(
            limits.max_keys,
            limits.max_array_members,
            limits.max_encoded_bytes,
        )
        < 0
    ):
        raise ValueError("catalog build limits must be non-negative")
    if scope.source_kind not in {"custom_attribute", "system_attribute"}:
        raise ValueError("unsupported catalog source kind")

    selected, valid_keys, invalid_keys = _select_candidates(
        attrs_string,
        attrs_number,
        attrs_bool,
        attributes_extra,
        limits.max_keys,
        key_only_attributes,
    )
    candidate_keys = (
        len(attrs_string) + len(attrs_number) + len(attrs_bool) + len(attributes_extra)
    )
    array_members_total = sum(len(candidate.array or ()) for candidate in selected)
    reasons: set[str] = set()
    if valid_keys > limits.max_keys:
        reasons.add(GAP_MAX_KEYS)
    if array_members_total > limits.max_array_members:
        reasons.add(GAP_MAX_ARRAY_MEMBERS)
    if invalid_keys:
        reasons.add(GAP_INVALID_ATTRIBUTE_KEY)

    key_rows: list[CatalogKeyRow] = []
    value_rows: list[CatalogValueRow] = []
    seen_values: set[tuple[str, AttributeType, str]] = set()
    array_members_inspected = 0
    non_scalar_array_members_skipped = 0
    duplicate_values_skipped = 0
    invalid_scalar_values = 0
    invalid_boolean_values = 0
    encoded_bytes = 0
    byte_limit_reached = False

    def append_scalar(candidate: _Candidate, value: Any) -> str:
        nonlocal duplicate_values_skipped, encoded_bytes
        try:
            encoded, cost, fits = _encode_scalar_for_row(
                candidate.key,
                candidate.attribute_type,
                value,
                limits.max_encoded_bytes,
            )
        except (TypeError, ValueError):
            return GAP_INVALID_SCALAR
        if not fits:
            return GAP_MAX_ENCODED_BYTES
        assert encoded is not None
        identity = (candidate.key, candidate.attribute_type, encoded.fingerprint)
        if identity in seen_values:
            duplicate_values_skipped += 1
            return ""
        if cost > limits.max_encoded_bytes - encoded_bytes:
            return GAP_MAX_ENCODED_BYTES

        seen_values.add(identity)
        value_rows.append(
            CatalogValueRow(
                project_id=scope.project_id,
                source_kind=scope.source_kind,
                attribute_key=candidate.key,
                attribute_type=candidate.attribute_type,
                value_fingerprint=encoded.fingerprint,
                value_json=encoded.value_json,
                value_search_text=encoded.search_text,
                first_seen=scope.seen_at,
                last_seen=scope.seen_at,
                catalog_epoch=scope.catalog_epoch,
            )
        )
        encoded_bytes += cost
        return ""

    for candidate in selected:
        key_cost = _key_row_encoded_size(candidate.key, candidate.attribute_type)
        if key_cost > limits.max_encoded_bytes - encoded_bytes:
            reasons.add(GAP_MAX_ENCODED_BYTES)
            break
        key_rows.append(
            CatalogKeyRow(
                project_id=scope.project_id,
                source_kind=scope.source_kind,
                attribute_key=candidate.key,
                key_folded=_fold_attribute_key(candidate.key),
                attribute_type=candidate.attribute_type,
                first_seen=scope.seen_at,
                last_seen=scope.seen_at,
                catalog_epoch=scope.catalog_epoch,
            )
        )
        encoded_bytes += key_cost

        if candidate.key_only or candidate.attribute_type in ("map", "json"):
            continue
        if candidate.attribute_type == "boolean":
            if type(candidate.value) is not int or candidate.value not in (0, 1):
                invalid_boolean_values += 1
                reasons.add(GAP_INVALID_BOOLEAN)
                continue
            status = append_scalar(candidate, bool(candidate.value))
        elif candidate.attribute_type == "array":
            remaining_members = limits.max_array_members - array_members_inspected
            inspect_count = min(len(candidate.array or ()), max(remaining_members, 0))
            members = candidate.array or ()
            for index in range(inspect_count):
                member = members[index]
                array_members_inspected += 1
                if not _is_selectable_scalar(member):
                    non_scalar_array_members_skipped += 1
                    continue
                status = append_scalar(candidate, member)
                if status == GAP_INVALID_SCALAR:
                    invalid_scalar_values += 1
                    reasons.add(status)
                elif status == GAP_MAX_ENCODED_BYTES:
                    reasons.add(status)
                    byte_limit_reached = True
                    break
            if byte_limit_reached:
                break
            continue
        else:
            if (
                candidate.attribute_type == "string"
                and type(candidate.value) is not str
            ):
                status = GAP_INVALID_SCALAR
            elif candidate.attribute_type == "number" and (
                type(candidate.value) not in (int, float, Decimal)
            ):
                status = GAP_INVALID_SCALAR
            else:
                status = append_scalar(candidate, candidate.value)

        if status == GAP_INVALID_SCALAR:
            invalid_scalar_values += 1
            reasons.add(status)
        elif status == GAP_MAX_ENCODED_BYTES:
            reasons.add(status)
            break

    ordered_reasons = tuple(reason for reason in _GAP_REASON_ORDER if reason in reasons)
    metadata = CatalogBuildMetadata(
        complete=not ordered_reasons,
        truncated=bool(reasons & _TRUNCATION_REASONS),
        gap_reasons=ordered_reasons,
        candidate_keys=candidate_keys,
        valid_candidate_keys=valid_keys,
        key_rows_emitted=len(key_rows),
        keys_omitted=candidate_keys - len(key_rows),
        value_rows_emitted=len(value_rows),
        array_members_total=array_members_total,
        array_members_inspected=array_members_inspected,
        array_members_omitted=array_members_total - array_members_inspected,
        non_scalar_array_members_skipped=non_scalar_array_members_skipped,
        duplicate_values_skipped=duplicate_values_skipped,
        invalid_attribute_keys=invalid_keys,
        invalid_scalar_values=invalid_scalar_values,
        invalid_boolean_values=invalid_boolean_values,
        encoded_bytes=encoded_bytes,
    )
    return CatalogBuildResult(tuple(key_rows), tuple(value_rows), metadata)


def _select_candidates(
    attrs_string: Mapping[str, str],
    attrs_number: Mapping[str, int | float | Decimal],
    attrs_bool: Mapping[str, int],
    attributes_extra: Mapping[str, Any],
    max_keys: int,
    key_only_attributes: frozenset[tuple[str, AttributeType]],
) -> tuple[list[_Candidate], int, int]:
    selected: list[_ReverseCandidate] = []
    valid_keys = 0
    invalid_keys = 0

    def consider(candidate: _Candidate) -> None:
        nonlocal valid_keys, invalid_keys
        if not _valid_attribute_key(candidate.key):
            invalid_keys += 1
            return
        valid_keys += 1
        if max_keys == 0:
            return
        wrapped = _ReverseCandidate(candidate)
        if len(selected) < max_keys:
            heapq.heappush(selected, wrapped)
        elif candidate.sort_key < selected[0].candidate.sort_key:
            heapq.heapreplace(selected, wrapped)

    for key, value in attrs_string.items():
        consider(
            _Candidate(
                key,
                "string",
                value=value,
                key_only=(key, "string") in key_only_attributes,
            )
        )
    for key, value in attrs_number.items():
        consider(
            _Candidate(
                key,
                "number",
                value=value,
                key_only=(key, "number") in key_only_attributes,
            )
        )
    for key, value in attrs_bool.items():
        consider(
            _Candidate(
                key,
                "boolean",
                value=value,
                key_only=(key, "boolean") in key_only_attributes,
            )
        )
    for key, value in attributes_extra.items():
        if isinstance(value, list):
            consider(
                _Candidate(
                    key,
                    "array",
                    array=value,
                    key_only=(key, "array") in key_only_attributes,
                )
            )
        elif isinstance(value, dict):
            consider(
                _Candidate(
                    key,
                    "map",
                    key_only=(key, "map") in key_only_attributes,
                )
            )
        else:
            consider(
                _Candidate(
                    key,
                    "json",
                    key_only=(key, "json") in key_only_attributes,
                )
            )

    return (
        sorted(
            (wrapped.candidate for wrapped in selected), key=lambda item: item.sort_key
        ),
        valid_keys,
        invalid_keys,
    )


def _valid_attribute_key(key: object) -> bool:
    return isinstance(key, str) and _utf8_size_bounded(key, None)[0]


def _fold_attribute_key(key: str) -> str:
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in key)


def _key_row_encoded_size(key: str, attribute_type: AttributeType) -> int:
    valid, key_bytes = _utf8_size_bounded(key, None)
    assert valid and key_bytes is not None
    return (2 * key_bytes) + len(attribute_type)


def _encode_scalar_for_row(
    key: str,
    attribute_type: AttributeType,
    value: Any,
    max_encoded_bytes: int,
) -> tuple[CanonicalCatalogScalar | None, int, bool]:
    valid_key, key_bytes = _utf8_size_bounded(key, max_encoded_bytes)
    if not valid_key:
        raise ValueError("catalog keys must be valid UTF-8")
    if key_bytes is None:
        return None, 0, False
    base = key_bytes + len(attribute_type) + 64
    if base > max_encoded_bytes:
        return None, 0, False
    if isinstance(value, str):
        valid_value, search_bytes = _utf8_size_bounded(value, max_encoded_bytes - base)
        if not valid_value:
            raise ValueError("catalog strings must be valid UTF-8")
        if search_bytes is None:
            return None, 0, False
        json_bytes = _canonical_json_string_size(
            value, max_encoded_bytes - base - search_bytes
        )
        if json_bytes is None:
            return None, 0, False
        cost = base + search_bytes + json_bytes
        return encode_catalog_scalar(value), cost, True

    encoded = encode_catalog_scalar(value)
    cost = base + len(encoded.value_json) + len(encoded.search_text)
    return (encoded, cost, True) if cost <= max_encoded_bytes else (None, 0, False)


def _canonical_json_string_size(value: str, remaining: int) -> int | None:
    if remaining < 2:
        return None
    size = 2
    for char in value:
        if char in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            increment = 2
        elif ord(char) < 0x20:
            increment = 6
        else:
            increment = _utf8_codepoint_size(ord(char))
            if increment == 0:
                raise ValueError(
                    "catalog strings must not contain unpaired UTF-16 surrogates"
                )
        if increment > remaining - size:
            return None
        size += increment
    return size


def _is_selectable_scalar(value: object) -> bool:
    return type(value) in (bool, str, int, float, Decimal)


def _utf8_size_bounded(value: str, limit: int | None) -> tuple[bool, int | None]:
    """Count UTF-8 bytes without allocating an encoded copy.

    The first return value distinguishes invalid UTF-8 from a valid string
    whose size crossed the optional ceiling. Once the ceiling is crossed we
    keep validating without growing the count, matching Go's full UTF-8
    validation while still allocating nothing proportional to the input.
    """

    size = 0
    exceeded = False
    for char in value:
        increment = _utf8_codepoint_size(ord(char))
        if increment == 0:
            return False, None
        if not exceeded:
            if limit is not None and increment > limit - size:
                exceeded = True
            else:
                size += increment
    return (True, None) if exceeded else (True, size)


def _utf8_codepoint_size(codepoint: int) -> int:
    if codepoint <= 0x7F:
        return 1
    if codepoint <= 0x7FF:
        return 2
    if 0xD800 <= codepoint <= 0xDFFF:
        return 0
    if codepoint <= 0xFFFF:
        return 3
    return 4
