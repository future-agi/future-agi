"""
pg_to_ch_adapter — convert PG tracer_observation_span rows to CH spans rows.

This module is INTENTIONALLY framework-free: no DB drivers, no I/O. It takes a
dict (PG row) and returns a tuple of CH column values in the order expected by
the `spans` table.

Why the split is here and not in SQL anymore:
    The previous pipeline used a CH materialized view that called
    JSONExtractKeysAndValuesRaw on every insert. That MV is exactly what we
    are eliminating (it OOMs on fat customer payloads). Doing the split in
    Python during backfill — once, with bounded memory per row — is the
    correct cure. The OTel Collector's `fi_adapter_processor` will do the same
    split for production-path inserts going forward, so this adapter is the
    canonical reference for what that processor must implement.

Splitting rules (must match the legacy spans_mv exactly, per DECISIONS #014):
    For each key/value in span_attributes:
        • string value          → attrs_string[k] = v
        • bool value (True/False) → attrs_bool[k] = 1 if v else 0
        • int or float value    → attrs_number[k] = float(v)
        • everything else (None, list, dict, mixed types under same key
          across rows, very large numbers that overflow Float64) →
          attributes_extra[k] = v   (lands in the JSON overflow column)

    CRITICAL Python gotcha: bool is a subclass of int, so the bool check
    MUST come before the int/float check. `isinstance(True, int) is True`.

    Numbers that don't fit Float64 (abs > 1.7e308) go to overflow. We do not
    silently truncate.
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# Sentinel for keys we never want to elevate into typed maps even if their
# scalar type fits — they're nested objects or arrays of LLM content, better
# kept whole in the JSON overflow.
_OVERFLOW_KEY_PREFIXES = (
    "llm.prompt",
    "llm.completion",
    "llm.messages",
    "input.value",
    "output.value",
    "retrieval.documents",
    "embedding.embeddings",
)


@dataclass
class CHSpanRow:
    """Mirrors the column order in `spans` table create.

    The orchestrator turns this into a tuple in `to_tuple()` for native-driver
    bulk insert. Keep this class field order locked to schema/002_spans_v2.sql.
    """

    project_id: str
    observation_type: str
    service_name: str
    start_time: datetime
    trace_id: str
    id: str
    parent_span_id: str
    name: str

    end_time: Optional[datetime]
    latency_ms: int

    org_id: Optional[str]
    project_version_id: Optional[str]
    end_user_id: Optional[str]
    trace_session_id: Optional[str]
    prompt_version_id: Optional[str]
    prompt_label_id: Optional[str]
    custom_eval_config_id: Optional[str]

    status: str
    status_message: str

    model: str
    provider: str
    gen_ai_system: str
    gen_ai_operation: str
    operation_name: str

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float

    attrs_string: dict[str, str] = field(default_factory=dict)
    attrs_number: dict[str, float] = field(default_factory=dict)
    attrs_bool: dict[str, int] = field(default_factory=dict)

    attributes_extra: str = "{}"
    resource_attrs: str = "{}"
    metadata: str = "{}"

    input: str = ""
    output: str = ""
    input_gcs_url: Optional[str] = None
    output_gcs_url: Optional[str] = None
    tags: str = "[]"
    span_events: str = "[]"

    eval_status: str = ""

    semconv_source: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_deleted: int = 0


# ─── Public errors ────────────────────────────────────────────────────────────
class AdapterError(Exception):
    """Raised when a PG row cannot be converted. Caller is expected to write
    the offending row to spans_v2_dead_letter rather than crashing the batch.
    """


# ─── Splitting ────────────────────────────────────────────────────────────────
def split_attributes(
    attrs: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, float], dict[str, int], dict[str, Any]]:
    """Route each top-level key into one of (string_map, number_map, bool_map, overflow_dict).

    Matches the production CH spans_mv splitting rules so per-window count and
    per-key value parity hold during validation.
    """
    if not attrs:
        return {}, {}, {}, {}

    s: dict[str, str] = {}
    n: dict[str, float] = {}
    b: dict[str, int] = {}
    extra: dict[str, Any] = {}

    for k, v in attrs.items():
        if not isinstance(k, str):
            # PG JSON object keys are always strings; defensive.
            extra[str(k)] = v
            continue
        if any(k.startswith(p) for p in _OVERFLOW_KEY_PREFIXES):
            extra[k] = v
            continue

        if v is None:
            extra[k] = None
        elif isinstance(v, bool):                                  # MUST come before int (bool subclasses int)
            b[k] = 1 if v else 0
        elif isinstance(v, int):
            # Bound to Float64; anything bigger goes to overflow rather than lose precision.
            if -9_007_199_254_740_992 <= v <= 9_007_199_254_740_992:
                n[k] = float(v)
            else:
                extra[k] = v
        elif isinstance(v, float):
            if math.isfinite(v):
                n[k] = v
            else:
                extra[k] = v                                       # NaN/Inf go to overflow rather than corrupt Float64 maps
        elif isinstance(v, str):
            s[k] = v
        else:                                                       # list, dict, other
            extra[k] = v
    return s, n, b, extra


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _as_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return v if isinstance(v, str) else str(v)


def _as_uuid_str(v: Any) -> Optional[str]:
    """Return canonical UUID string or None. Raises AdapterError on bad UUID."""
    if v is None or v == "":
        return None
    if isinstance(v, uuid.UUID):
        return str(v)
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, AttributeError, TypeError) as e:
        raise AdapterError(f"invalid uuid: {v!r}") from e


def _ensure_dt_utc(v: Any) -> datetime:
    """Best-effort coercion to a UTC datetime. Raises AdapterError on failure."""
    if v is None:
        raise AdapterError("expected datetime, got None")
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError as e:
            raise AdapterError(f"unparseable datetime: {v!r}") from e
    raise AdapterError(f"expected datetime, got {type(v).__name__}: {v!r}")


def _maybe_dt(v: Any) -> Optional[datetime]:
    if v in (None, ""):
        return None
    return _ensure_dt_utc(v)


def _to_json_text(v: Any) -> str:
    """Serialize Python value to a JSON string CH can ingest as JSON.

    Uses default=str so datetime/UUID/Decimal survive without an extra encoder.
    """
    if v is None:
        return "{}"
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", errors="replace")
    if isinstance(v, str):
        # Trust strings that look like JSON; otherwise wrap as a literal.
        s = v.strip()
        if s and s[0] in "[{" and s[-1] in "]}":
            return v
        return json.dumps(v)
    return json.dumps(v, default=str, ensure_ascii=False)


def _value_or_empty_json(v: Any) -> str:
    """For input/output: PG stores these as JSONField, can be dict/list/str/None."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, default=str, ensure_ascii=False)


# ─── Top-level adapter ────────────────────────────────────────────────────────
# These are the column names we expect on the PG row dict — match the Django
# ObservationSpan model. The orchestrator selects exactly these columns.
EXPECTED_PG_COLUMNS: tuple[str, ...] = (
    "id", "project_id", "project_version_id", "trace_id", "parent_span_id",
    "name", "observation_type", "operation_name",
    "start_time", "end_time",
    "input", "output",
    "model", "model_parameters", "latency_ms",
    "org_id", "org_user_id",
    "prompt_tokens", "completion_tokens", "total_tokens", "response_time",
    "eval_id", "cost",
    "status", "status_message",
    "tags", "metadata", "span_events", "provider",
    "input_images", "eval_input", "eval_attributes",
    "custom_eval_config_id", "eval_status",
    "end_user_id", "prompt_version_id", "prompt_label_id",
    "span_attributes", "resource_attributes", "semconv_source",
    "created_at", "updated_at", "deleted",
    # joined-in column (from tracer_trace.trace_session_id)
    "trace_session_id",
)


def adapt(pg_row: dict[str, Any]) -> CHSpanRow:
    """Convert a single PG row dict into a CHSpanRow.

    Raises AdapterError on any field-level conversion failure. The orchestrator
    catches this, writes the original row to spans_v2_dead_letter, and moves on.
    Never returns a partially-populated row.
    """
    # ── Required identity ────────────────────────────────────────────────────
    try:
        project_id_str = _as_uuid_str(pg_row["project_id"])
        if project_id_str is None:
            raise AdapterError("project_id is null")
        span_id = _as_str(pg_row["id"])
        if not span_id:
            raise AdapterError("span id is empty")
        trace_id = _as_str(pg_row["trace_id"])
        if not trace_id:
            raise AdapterError("trace_id is empty")
        observation_type = _as_str(pg_row.get("observation_type"), "unknown")
        start_time = _ensure_dt_utc(pg_row.get("start_time") or pg_row.get("created_at"))
    except KeyError as e:
        raise AdapterError(f"missing required column: {e}") from e

    # ── Attribute split ──────────────────────────────────────────────────────
    span_attributes = pg_row.get("span_attributes") or {}
    if isinstance(span_attributes, str):
        try:
            span_attributes = json.loads(span_attributes)
        except json.JSONDecodeError as e:
            raise AdapterError(f"span_attributes is not valid JSON: {e}") from e
    if not isinstance(span_attributes, dict):
        # Malformed — keep the raw payload in overflow, leave typed maps empty.
        span_attributes = {"__non_dict_payload__": span_attributes}

    s_map, n_map, b_map, overflow = split_attributes(span_attributes)

    # PG has additional JSONField columns that aren't part of span_attributes
    # but ARE semantically span attributes the eval runner and dashboards read:
    #   • model_parameters — generation config (temperature, top_p, etc, sometimes)
    #   • input_images     — multi-modal inputs (array of urls / base64)
    #   • eval_input       — fields the eval evaluator pinned at run time
    #   • eval_attributes  — per-eval custom attrs
    # Merge them into the overflow JSON under their column name so they
    # round-trip through to attributes_extra and are queryable via path access
    # (DECISIONS #018). Caught by codex review on 2026-05-24 as silent drop.
    for col in ("model_parameters", "input_images", "eval_input", "eval_attributes"):
        v = pg_row.get(col)
        if v is None:
            continue
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                continue
        if v in ({}, []):                                          # treat empty containers as "not set"
            continue
        overflow[col] = v

    # Derive hot LLM fields from attrs when the dedicated PG column is null.
    model = _as_str(pg_row.get("model")) or s_map.get("gen_ai.request.model", "") or s_map.get("llm.model_name", "")
    provider = _as_str(pg_row.get("provider")) or s_map.get("gen_ai.system", "") or s_map.get("llm.provider", "")
    gen_ai_system = s_map.get("gen_ai.system", "")
    gen_ai_operation = s_map.get("gen_ai.operation.name", "")

    # service_name comes from OTel resource attributes — `service.name` is the
    # OTel-canonical column. It's the column dashboards filter on, so writing
    # `semconv_source` (a flag like "traceai" / "openllmetry") into it would
    # silently mis-group every backfilled row. semconv_source has its own
    # column on the CH table.
    resource_attrs = pg_row.get("resource_attributes") or {}
    if isinstance(resource_attrs, str):
        try:
            resource_attrs = json.loads(resource_attrs)
        except json.JSONDecodeError:
            resource_attrs = {}
    if not isinstance(resource_attrs, dict):
        resource_attrs = {}
    service_name = _as_str(resource_attrs.get("service.name"), "")

    # ── Build the row ────────────────────────────────────────────────────────
    row = CHSpanRow(
        project_id=project_id_str,
        observation_type=observation_type,
        service_name=service_name,
        start_time=start_time,
        trace_id=trace_id,
        id=span_id,
        parent_span_id=_as_str(pg_row.get("parent_span_id"), ""),
        name=_as_str(pg_row.get("name"), ""),

        end_time=_maybe_dt(pg_row.get("end_time")),
        latency_ms=_as_int(pg_row.get("latency_ms"), 0),

        org_id=_as_uuid_str(pg_row.get("org_id")),
        project_version_id=_as_uuid_str(pg_row.get("project_version_id")),
        end_user_id=_as_uuid_str(pg_row.get("end_user_id")),
        trace_session_id=_as_uuid_str(pg_row.get("trace_session_id")),
        prompt_version_id=_as_uuid_str(pg_row.get("prompt_version_id")),
        prompt_label_id=_as_uuid_str(pg_row.get("prompt_label_id")),
        custom_eval_config_id=_as_uuid_str(pg_row.get("custom_eval_config_id")),

        status=_as_str(pg_row.get("status"), ""),
        status_message=_as_str(pg_row.get("status_message"), ""),

        model=model,
        provider=provider,
        gen_ai_system=gen_ai_system,
        gen_ai_operation=gen_ai_operation,
        operation_name=_as_str(pg_row.get("operation_name"), ""),

        prompt_tokens=_as_int(pg_row.get("prompt_tokens"), 0),
        completion_tokens=_as_int(pg_row.get("completion_tokens"), 0),
        total_tokens=_as_int(pg_row.get("total_tokens"), 0),
        cost=_as_float(pg_row.get("cost"), 0.0),

        attrs_string=s_map,
        attrs_number=n_map,
        attrs_bool=b_map,

        attributes_extra=_to_json_text(overflow),
        resource_attrs=_to_json_text(resource_attrs),
        metadata=_to_json_text(pg_row.get("metadata") or {}),

        input=_value_or_empty_json(pg_row.get("input")),
        output=_value_or_empty_json(pg_row.get("output")),
        input_gcs_url=None,
        output_gcs_url=None,
        tags=_to_json_text(pg_row.get("tags") or []),
        span_events=_to_json_text(pg_row.get("span_events") or []),

        eval_status=_as_str(pg_row.get("eval_status"), ""),

        semconv_source=_as_str(pg_row.get("semconv_source"), ""),
        created_at=_ensure_dt_utc(pg_row.get("created_at") or start_time),
        updated_at=_ensure_dt_utc(pg_row.get("updated_at") or pg_row.get("created_at") or start_time),
        is_deleted=1 if pg_row.get("deleted") else 0,
    )
    return row


# Ordered tuple matching the spans table column layout in 002_spans_v2.sql.
# Used by the orchestrator's native-driver bulk insert.
CH_INSERT_COLUMNS: tuple[str, ...] = (
    "project_id", "observation_type", "service_name", "start_time",
    "trace_id", "id", "parent_span_id", "name",
    "end_time", "latency_ms",
    "org_id", "project_version_id", "end_user_id", "trace_session_id",
    "prompt_version_id", "prompt_label_id", "custom_eval_config_id",
    "status", "status_message",
    "model", "provider", "gen_ai_system", "gen_ai_operation", "operation_name",
    "prompt_tokens", "completion_tokens", "total_tokens", "cost",
    "attrs_string", "attrs_number", "attrs_bool",
    "attributes_extra", "resource_attrs", "metadata",
    "input", "output", "input_gcs_url", "output_gcs_url", "tags", "span_events",
    "eval_status",
    "semconv_source", "created_at", "updated_at", "is_deleted",
)


def row_to_tuple(r: CHSpanRow) -> tuple:
    """Project CHSpanRow → tuple in CH_INSERT_COLUMNS order."""
    return (
        r.project_id, r.observation_type, r.service_name, r.start_time,
        r.trace_id, r.id, r.parent_span_id, r.name,
        r.end_time, r.latency_ms,
        r.org_id, r.project_version_id, r.end_user_id, r.trace_session_id,
        r.prompt_version_id, r.prompt_label_id, r.custom_eval_config_id,
        r.status, r.status_message,
        r.model, r.provider, r.gen_ai_system, r.gen_ai_operation, r.operation_name,
        r.prompt_tokens, r.completion_tokens, r.total_tokens, r.cost,
        r.attrs_string, r.attrs_number, r.attrs_bool,
        r.attributes_extra, r.resource_attrs, r.metadata,
        r.input, r.output, r.input_gcs_url, r.output_gcs_url, r.tags, r.span_events,
        r.eval_status,
        r.semconv_source, r.created_at, r.updated_at, r.is_deleted,
    )
