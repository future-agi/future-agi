"""Byte-for-byte v1 envelope codec shared with ``fi-collector/propertycatalog``.

The Python reconciler publishes definition envelopes directly during bounded
DEV reconciliation.  It must therefore use the same compact JSON field order,
JSONEachRow bytes, and SHA-256 identities as the Go Kafka path.  Keep this
module framework- and ClickHouse-free so its fixtures can run in both paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)

from .models import (
    CanonicalDefinition,
    PropertyBindingRow,
    PropertyCatalogEnvelope,
    PropertyCategory,
    PropertyKind,
    PropertyRole,
    SourceAdapter,
    VisibilityScope,
)

ENVELOPE_FORMAT = "futureagi.property-catalog-envelope"
ENVELOPE_VERSION = 1
ZERO_SHA256 = "0" * 64
MAX_CHUNK_ROWS = 10_000
MAX_CHUNK_BYTES = 512 << 10
MAX_RECORD_BYTES = 768 << 10
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_UUID_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)

_DEFINITION_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "binding_id",
    "visibility_scope",
    "visibility_id",
    "source_adapter",
    "source_entity_id",
    "source_version",
    "source_fingerprint",
    "producer_stream_id",
    "producer_sequence",
    "property_id",
    "property_kind",
    "category",
    "category_rank",
    "source_rank",
    "definition_source",
    "primary_source",
    "primary_source_folded",
    "source_tokens",
    "value_adapter",
    "name",
    "display_name",
    "sort_name_folded",
    "search_text_folded",
    "role",
    "definition_json",
    "definition_sha256",
    "first_seen",
    "last_seen",
    "is_deleted",
    "deleted_at",
    "state_sha256",
    "emitted_at",
)
_VALUE_COLUMNS = (
    "organization_id",
    "workspace_id",
    "project_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "source_kind",
    "attribute_key",
    "attribute_type",
    "value_fingerprint",
    "value_json",
    "value_search_text_folded",
    "first_seen",
    "last_seen",
)


class PropertyCatalogWireError(ValueError):
    """The unsigned value cannot be represented by the exact v1 wire contract."""


@dataclass(frozen=True, slots=True)
class WireEnvelope:
    raw: bytes
    envelope_id: str
    payload_sha256: str
    document: Mapping[str, Any]


def encode_envelope(
    envelope: PropertyCatalogEnvelope,
    *,
    value_rows: Sequence[Mapping[str, Any]] = (),
) -> WireEnvelope:
    """Encode an immutable Python envelope using Go's v1 JSON byte shape."""

    definition_rows = tuple(_definition_row(row) for row in envelope.definitions)
    values = tuple(_ordered_value_row(row) for row in value_rows)
    if len(values) != envelope.counts.value_count:
        raise PropertyCatalogWireError("value row count does not match envelope")
    if envelope.terminal and (definition_rows or values):
        raise PropertyCatalogWireError("terminal envelope must be empty")
    chunks = _chunks(definition_rows, values)
    payload = {
        "source_batch_digest": envelope.source_batch_digest,
        "outcome": str(envelope.outcome),
        "gap_reasons": list(envelope.gap_reasons),
        "source_rows": envelope.counts.source_count,
        "definition_rows": envelope.counts.definition_count,
        "value_rows": envelope.counts.value_count,
        "tombstone_rows": envelope.counts.tombstone_count,
        "chunks": chunks,
    }
    payload_bytes = _compact(payload)
    payload_sha256 = _sha(payload_bytes)
    unsigned = {
        "format": ENVELOPE_FORMAT,
        "version": ENVELOPE_VERSION,
        "organization_id": envelope.organization_id,
        "workspace_id": envelope.workspace_id,
        "catalog_epoch": envelope.catalog_epoch,
        "catalog_revision": envelope.catalog_revision,
        "build_token": envelope.build_token,
        "projection_version": envelope.projection_version,
        "source_adapter": str(envelope.source_adapter),
        "source_version": envelope.source_version,
        "source_fingerprint": envelope.source_fingerprint,
        "producer_stream_id": envelope.producer_stream_id,
        "sequence": envelope.sequence,
        "terminal": envelope.terminal,
        "previous_payload_sha256": envelope.previous_payload_sha256,
        "payload_sha256": payload_sha256,
        "payload": payload,
    }
    envelope_id = _sha(_compact(unsigned))
    document = {
        "format": ENVELOPE_FORMAT,
        "version": ENVELOPE_VERSION,
        "envelope_id": envelope_id,
        **{
            key: value
            for key, value in unsigned.items()
            if key not in {"format", "version"}
        },
    }
    raw = _compact(document)
    if len(raw) > MAX_RECORD_BYTES:
        raise PropertyCatalogWireError("encoded envelope exceeds the v1 record limit")
    return WireEnvelope(
        raw=raw,
        envelope_id=envelope_id,
        payload_sha256=payload_sha256,
        document=document,
    )


def parse_envelope(raw: bytes) -> WireEnvelope:
    """Strictly parse a canonical v1 document and rederive both SHA identities."""

    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RECORD_BYTES:
        raise PropertyCatalogWireError("invalid envelope byte length")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PropertyCatalogWireError("invalid envelope JSON") from exc
    if not isinstance(document, dict) or _compact(document) != raw:
        raise PropertyCatalogWireError("envelope is not canonical compact JSON")
    required = (
        "format",
        "version",
        "envelope_id",
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
        "projection_version",
        "source_adapter",
        "source_version",
        "source_fingerprint",
        "producer_stream_id",
        "sequence",
        "terminal",
        "previous_payload_sha256",
        "payload_sha256",
        "payload",
    )
    if (
        tuple(document) != required
        or document["format"] != ENVELOPE_FORMAT
        or document["version"] != ENVELOPE_VERSION
    ):
        raise PropertyCatalogWireError("unsupported envelope shape")
    payload = document["payload"]
    payload_fields = (
        "source_batch_digest",
        "outcome",
        "gap_reasons",
        "source_rows",
        "definition_rows",
        "value_rows",
        "tombstone_rows",
        "chunks",
    )
    if not isinstance(payload, dict) or tuple(payload) != payload_fields:
        raise PropertyCatalogWireError("envelope payload must be an object")
    payload_sha256 = _sha(_compact(payload))
    if payload_sha256 != document["payload_sha256"]:
        raise PropertyCatalogWireError("payload SHA-256 mismatch")
    unsigned = {key: value for key, value in document.items() if key != "envelope_id"}
    envelope_id = _sha(_compact(unsigned))
    if envelope_id != document["envelope_id"]:
        raise PropertyCatalogWireError("envelope SHA-256 mismatch")
    _validate_document(document)
    return WireEnvelope(
        raw=raw,
        envelope_id=envelope_id,
        payload_sha256=payload_sha256,
        document=document,
    )


def _validate_document(document: Mapping[str, Any]) -> None:
    """Prevalidate every chunk and row before a caller can persist the wire.

    The Go consumer deliberately validates the complete envelope before its
    first INSERT.  Python evidence and direct reconciliation must have the same
    all-or-nothing validation boundary.
    """

    for field in (
        "organization_id",
        "workspace_id",
        "build_token",
        "producer_stream_id",
    ):
        if (
            not isinstance(document[field], str)
            or _UUID_RE.fullmatch(document[field]) is None
        ):
            raise PropertyCatalogWireError(f"{field} is not a canonical UUID")
    for field in (
        "envelope_id",
        "source_fingerprint",
        "previous_payload_sha256",
        "payload_sha256",
    ):
        if (
            not isinstance(document[field], str)
            or _SHA256_RE.fullmatch(document[field]) is None
        ):
            raise PropertyCatalogWireError(f"{field} is not lowercase SHA-256")
    for field, maximum in (
        ("catalog_epoch", (1 << 16) - 1),
        ("catalog_revision", (1 << 64) - 1),
        ("projection_version", (1 << 16) - 1),
        ("source_version", (1 << 64) - 1),
        ("sequence", (1 << 64) - 1),
    ):
        value = document[field]
        if type(value) is not int or not 1 <= value <= maximum:
            raise PropertyCatalogWireError(f"{field} is outside its unsigned range")
    try:
        source_adapter = SourceAdapter(document["source_adapter"])
    except (TypeError, ValueError) as exc:
        raise PropertyCatalogWireError("source_adapter is unsupported") from exc
    if type(document["terminal"]) is not bool:
        raise PropertyCatalogWireError("terminal must be boolean")
    if document["sequence"] == 1 and document["previous_payload_sha256"] != ZERO_SHA256:
        raise PropertyCatalogWireError("sequence one must use the zero previous digest")

    payload = document["payload"]
    if (
        not isinstance(payload["source_batch_digest"], str)
        or _SHA256_RE.fullmatch(payload["source_batch_digest"]) is None
    ):
        raise PropertyCatalogWireError("source_batch_digest is not lowercase SHA-256")
    outcome = payload["outcome"]
    gaps = payload["gap_reasons"]
    if (
        outcome not in {"committed", "gap"}
        or not isinstance(gaps, list)
        or not all(
            isinstance(reason, str) and reason and reason.strip() == reason
            for reason in gaps
        )
    ):
        raise PropertyCatalogWireError("payload outcome/gap reasons are invalid")
    if gaps != sorted(set(gaps)):
        raise PropertyCatalogWireError("gap reasons must be strictly sorted")
    if (outcome == "committed") != (not gaps):
        raise PropertyCatalogWireError("payload outcome does not match gap reasons")
    for field in ("source_rows", "definition_rows", "value_rows", "tombstone_rows"):
        value = payload[field]
        if type(value) is not int or not 0 <= value < (1 << 64):
            raise PropertyCatalogWireError(f"{field} is not a UInt64")
    if payload["tombstone_rows"] > payload["definition_rows"]:
        raise PropertyCatalogWireError("tombstone count exceeds definition count")

    chunks = payload["chunks"]
    if not isinstance(chunks, list):
        raise PropertyCatalogWireError("chunks must be an array")
    definition_count = value_count = tombstone_count = 0
    saw_value = False
    for expected_index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict) or tuple(chunk) != (
            "table",
            "index",
            "row_count",
            "encoded_sha256",
            "json_each_row",
        ):
            raise PropertyCatalogWireError("wire chunk has an unsupported shape")
        table = chunk["table"]
        if table not in {"property_definition_catalog", "span_attribute_value_catalog"}:
            raise PropertyCatalogWireError("wire chunk targets a forbidden table")
        if saw_value and table == "property_definition_catalog":
            raise PropertyCatalogWireError(
                "definition chunks must precede value chunks"
            )
        saw_value = saw_value or table == "span_attribute_value_catalog"
        if (
            chunk["index"] != expected_index
            or type(chunk["row_count"]) is not int
            or chunk["row_count"] < 1
        ):
            raise PropertyCatalogWireError("wire chunk index/row count is invalid")
        if (
            not isinstance(chunk["encoded_sha256"], str)
            or _SHA256_RE.fullmatch(chunk["encoded_sha256"]) is None
        ):
            raise PropertyCatalogWireError("wire chunk digest is invalid")
        try:
            encoded = base64.b64decode(chunk["json_each_row"], validate=True)
        except (TypeError, ValueError) as exc:
            raise PropertyCatalogWireError("wire chunk base64 is invalid") from exc
        if not encoded or len(encoded) > MAX_CHUNK_BYTES or not encoded.endswith(b"\n"):
            raise PropertyCatalogWireError("wire chunk bytes are invalid")
        if _sha(encoded) != chunk["encoded_sha256"]:
            raise PropertyCatalogWireError("wire chunk digest mismatch")
        lines = encoded.splitlines()
        if len(lines) != chunk["row_count"] or any(not line for line in lines):
            raise PropertyCatalogWireError("wire chunk row_count mismatch")
        columns = (
            _DEFINITION_COLUMNS
            if table == "property_definition_catalog"
            else _VALUE_COLUMNS
        )
        for line in lines:
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PropertyCatalogWireError(
                    "wire chunk row is invalid JSON"
                ) from exc
            if (
                not isinstance(row, dict)
                or tuple(row) != columns
                or _compact(row) != line
            ):
                raise PropertyCatalogWireError(
                    "wire chunk row is not canonical JSONEachRow"
                )
            if table == "property_definition_catalog":
                _validate_definition_wire_row(row, document, source_adapter)
                definition_count += 1
                tombstone_count += row["is_deleted"]
            else:
                _validate_value_wire_row(row, document, source_adapter)
                value_count += 1
    if (
        definition_count != payload["definition_rows"]
        or value_count != payload["value_rows"]
        or tombstone_count != payload["tombstone_rows"]
    ):
        raise PropertyCatalogWireError("payload row totals do not match chunks")
    if document["terminal"] and (
        outcome != "committed"
        or gaps
        or chunks
        or any(
            payload[field]
            for field in (
                "source_rows",
                "definition_rows",
                "value_rows",
                "tombstone_rows",
            )
        )
    ):
        raise PropertyCatalogWireError("terminal envelope must be empty and committed")


def _validate_definition_wire_row(
    row: Mapping[str, Any],
    document: Mapping[str, Any],
    source_adapter: SourceAdapter,
) -> None:
    try:
        decoded_definition = json.loads(row["definition_json"])
        definition = CanonicalDefinition(
            property_id=row["property_id"],
            property_kind=PropertyKind(row["property_kind"]),
            category=PropertyCategory(row["category"]),
            category_rank=row["category_rank"],
            source_rank=row["source_rank"],
            definition_source=row["definition_source"],
            primary_source=row["primary_source"],
            primary_source_folded=row["primary_source_folded"],
            source_tokens=tuple(row["source_tokens"]),
            value_adapter=row["value_adapter"],
            name=row["name"],
            display_name=row["display_name"],
            sort_name_folded=row["sort_name_folded"],
            search_text_folded=row["search_text_folded"],
            value_type=decoded_definition["value_type"],
            output_type=decoded_definition["output_type"],
            role=PropertyRole(row["role"]),
            definition_json=row["definition_json"],
            definition_sha256=row["definition_sha256"],
        )
        PropertyBindingRow(
            organization_id=row["organization_id"],
            workspace_id=row["workspace_id"],
            catalog_epoch=row["catalog_epoch"],
            catalog_revision=row["catalog_revision"],
            build_token=row["build_token"],
            projection_version=row["projection_version"],
            binding_id=row["binding_id"],
            visibility_scope=VisibilityScope(row["visibility_scope"]),
            visibility_id=row["visibility_id"],
            definition=definition,
            source_adapter=SourceAdapter(row["source_adapter"]),
            source_entity_id=row["source_entity_id"],
            source_version=row["source_version"],
            source_fingerprint=row["source_fingerprint"],
            is_deleted=_wire_bool(row["is_deleted"]),
            deleted_at=_parse_wire_time(row["deleted_at"]),
            state_sha256=row["state_sha256"],
            producer_stream_id=row["producer_stream_id"],
            producer_sequence=row["producer_sequence"],
            first_seen=_parse_wire_time(row["first_seen"]),
            last_seen=_parse_wire_time(row["last_seen"]),
            emitted_at=_parse_wire_time(row["emitted_at"], required=True),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PropertyCatalogWireError("definition row violates the v1 model") from exc
    if (
        row["organization_id"] != document["organization_id"]
        or row["workspace_id"] != document["workspace_id"]
        or row["catalog_epoch"] != document["catalog_epoch"]
        or row["catalog_revision"] != document["catalog_revision"]
        or row["build_token"] != document["build_token"]
        or row["projection_version"] != document["projection_version"]
        or row["source_adapter"] != source_adapter
        or row["source_version"] != document["source_version"]
        or row["source_fingerprint"] != document["source_fingerprint"]
        or row["producer_stream_id"] != document["producer_stream_id"]
        or row["producer_sequence"] != document["sequence"]
    ):
        raise PropertyCatalogWireError("definition row scope does not match envelope")


def _validate_value_wire_row(
    row: Mapping[str, Any],
    document: Mapping[str, Any],
    source_adapter: SourceAdapter,
) -> None:
    if source_adapter is not SourceAdapter.SPAN_ATTRIBUTE:
        raise PropertyCatalogWireError("value rows require span_attribute source")
    if (
        row["organization_id"] != document["organization_id"]
        or row["workspace_id"] != document["workspace_id"]
        or row["catalog_epoch"] != document["catalog_epoch"]
        or row["catalog_revision"] != document["catalog_revision"]
        or row["build_token"] != document["build_token"]
    ):
        raise PropertyCatalogWireError("value row scope does not match envelope")
    try:
        if _UUID_RE.fullmatch(row["project_id"]) is None:
            raise ValueError("project")
        if row["source_kind"] not in {"custom_attribute", "system_attribute"}:
            raise ValueError("source_kind")
        if row["attribute_type"] not in {
            "string",
            "number",
            "boolean",
            "array",
            "map",
            "json",
        }:
            raise ValueError("attribute_type")
        decoded = json.loads(row["value_json"], parse_float=Decimal, parse_int=int)
        scalar = encode_catalog_scalar(decoded)
        if (
            scalar.value_json != row["value_json"]
            or scalar.fingerprint != row["value_fingerprint"]
        ):
            raise ValueError("scalar identity")
        if scalar.search_text.casefold() != row["value_search_text_folded"]:
            raise ValueError("folded search")
        first = _parse_wire_time(row["first_seen"], required=True)
        last = _parse_wire_time(row["last_seen"], required=True)
        if last < first:
            raise ValueError("time order")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PropertyCatalogWireError("value row violates the v1 model") from exc


def _wire_bool(value: Any) -> bool:
    if type(value) is not int or value not in (0, 1):
        raise ValueError("wire boolean must be 0 or 1")
    return bool(value)


def _parse_wire_time(value: Any, *, required: bool = False) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("wire timestamp must be text")
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
    if parsed.strftime("%Y-%m-%d %H:%M:%S.%f") != value:
        raise ValueError("wire timestamp is not canonical DateTime64(6)")
    return parsed


def _chunks(
    definitions: Sequence[Mapping[str, Any]], values: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for table, rows in (
        ("property_definition_catalog", definitions),
        ("span_attribute_value_catalog", values),
    ):
        current: list[Mapping[str, Any]] = []
        current_bytes = 0
        for row in rows:
            encoded = _json_each_row_bytes(row)
            if len(encoded) > MAX_CHUNK_BYTES:
                raise PropertyCatalogWireError(
                    "one JSONEachRow row exceeds the v1 chunk limit"
                )
            if current and (
                len(current) >= MAX_CHUNK_ROWS
                or current_bytes + len(encoded) > MAX_CHUNK_BYTES
            ):
                chunks.append(_chunk(table, len(chunks), current))
                current, current_bytes = [], 0
            current.append(row)
            current_bytes += len(encoded)
        if current:
            chunks.append(_chunk(table, len(chunks), current))
    return chunks


def _chunk(table: str, index: int, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    encoded = b"".join(_json_each_row_bytes(row) for row in rows)
    return {
        "table": table,
        "index": index,
        "row_count": len(rows),
        "encoded_sha256": _sha(encoded),
        "json_each_row": base64.b64encode(encoded).decode("ascii"),
    }


def _definition_row(row: PropertyBindingRow) -> dict[str, Any]:
    definition = row.definition
    values = {
        "organization_id": row.organization_id,
        "workspace_id": row.workspace_id,
        "catalog_epoch": row.catalog_epoch,
        "catalog_revision": row.catalog_revision,
        "build_token": row.build_token,
        "projection_version": row.projection_version,
        "binding_id": row.binding_id,
        "visibility_scope": str(row.visibility_scope),
        "visibility_id": row.visibility_id,
        "source_adapter": str(row.source_adapter),
        "source_entity_id": row.source_entity_id,
        "source_version": row.source_version,
        "source_fingerprint": row.source_fingerprint,
        "producer_stream_id": row.producer_stream_id,
        "producer_sequence": row.producer_sequence,
        "property_id": definition.property_id,
        "property_kind": str(definition.property_kind),
        "category": str(definition.category),
        "category_rank": definition.category_rank,
        "source_rank": definition.source_rank,
        "definition_source": definition.definition_source,
        "primary_source": definition.primary_source,
        "primary_source_folded": definition.primary_source_folded,
        "source_tokens": list(definition.source_tokens),
        "value_adapter": definition.value_adapter,
        "name": definition.name,
        "display_name": definition.display_name,
        "sort_name_folded": definition.sort_name_folded,
        "search_text_folded": definition.search_text_folded,
        "role": str(definition.role),
        "definition_json": definition.definition_json,
        "definition_sha256": definition.definition_sha256,
        "first_seen": _time_or_none(row.first_seen),
        "last_seen": _time_or_none(row.last_seen),
        "is_deleted": int(row.is_deleted),
        "deleted_at": _time_or_none(row.deleted_at),
        "state_sha256": row.state_sha256,
        "emitted_at": _time(row.emitted_at),
    }
    return {column: values[column] for column in _DEFINITION_COLUMNS}


def definition_json_each_row_size(
    row: PropertyBindingRow,
    *,
    producer_sequence: int | None = None,
) -> int:
    """Return the exact canonical JSONEachRow size for one definition.

    Reconciliation uses the optional sequence override to size every row at
    the widest valid UInt64 sequence before assigning envelope sequences.  It
    can therefore split a source segment without guessing at duplicated wire
    fields or UTF-8/JSON escaping overhead.
    """

    values = _definition_row(row)
    if producer_sequence is not None:
        if type(producer_sequence) is not int or not 1 <= producer_sequence < (1 << 64):
            raise PropertyCatalogWireError(
                "producer_sequence is outside its unsigned range"
            )
        values["producer_sequence"] = producer_sequence
    return len(_json_each_row_bytes(values))


def _ordered_value_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if set(row) != set(_VALUE_COLUMNS):
        raise PropertyCatalogWireError("value row does not have the exact v1 columns")
    return {column: row[column] for column in _VALUE_COLUMNS}


def _time_or_none(value: datetime | None) -> str | None:
    return None if value is None else _time(value)


def _time(value: datetime) -> str:
    if value.tzinfo is None:
        raise PropertyCatalogWireError("wire timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _compact(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _json_each_row_bytes(row: Mapping[str, Any]) -> bytes:
    return _compact(row) + b"\n"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "ENVELOPE_FORMAT",
    "ENVELOPE_VERSION",
    "MAX_CHUNK_BYTES",
    "MAX_RECORD_BYTES",
    "PropertyCatalogWireError",
    "WireEnvelope",
    "definition_json_each_row_size",
    "encode_envelope",
    "parse_envelope",
]
