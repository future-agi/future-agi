"""
ch_span_reader — read API the eval runner can call to load spans directly from CH.

Drop-in for the existing Django ORM access in tracer/utils/eval.py:

    PG path (today):
        observation_span = ObservationSpan.objects.get(id=span_id)
        spans = ObservationSpan.objects.filter(trace=trace, deleted=False)

    CH path (target post-cutover):
        reader = CHSpanReader(host=..., port=...)
        observation_span = reader.get(span_id)
        spans = reader.list_by_trace(trace_id)

The shapes match the Django model fields the eval runner actually touches
(see grep -n "observation_span[.]" tracer/utils/eval.py for the surface).

Design goals:
  • SAME FIELD NAMES as the Django model, so eval code can be swapped over
    with a one-line `.objects.get(id=...)` → `reader.get(...)` change.
  • Frozen dataclasses so callers cannot accidentally mutate (CH is the
    authoritative store; the read path is meant to be pure).
  • Single small query per call (no N+1 joins). The CH schema denormalizes
    trace_session_id / org_id / project_version_id onto each span row, so
    most eval reads need exactly one row from `spans FINAL`.

CRITICAL non-goal: write back. Eval results (EvalLogger rows) still go to
PG until that's also migrated to CH (separate task). This reader is
read-only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

import clickhouse_connect


# Field list that the eval runner actually reads off of an ObservationSpan.
# Mirrored from the grep:
#   tracer/utils/eval.py:725, 1108, 1493, 1578, 1711  → .get(id=...)
#   tracer/utils/eval.py:210, 219, 271, 289, 306, 2218 → .filter(...) aggregates
# Adding a field here is cheap; removing one is a breaking change for callers.
@dataclass(frozen=True)
class CHSpan:
    id: str
    project_id: str
    trace_id: str
    parent_span_id: str
    name: str
    observation_type: str
    operation_name: str

    start_time: datetime
    end_time: Optional[datetime]
    latency_ms: int

    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float

    status: str
    status_message: str

    org_id: Optional[str]
    project_version_id: Optional[str]
    end_user_id: Optional[str]
    trace_session_id: Optional[str]
    prompt_version_id: Optional[str]
    prompt_label_id: Optional[str]
    custom_eval_config_id: Optional[str]

    # Inputs / outputs come back as raw JSON-strings from CH; the eval runner
    # currently calls json.loads on them where needed. Keep the shape identical
    # so no downstream `.input` callsite changes.
    input: str
    output: str
    tags: str
    span_events: str
    metadata: str                                                   # JSON string from CH typed JSON column
    resource_attrs: str                                             # JSON string
    attributes_extra: str                                           # JSON string

    # Typed Map columns. Maps to Python dicts.
    attrs_string: dict[str, str] = field(default_factory=dict)
    attrs_number: dict[str, float] = field(default_factory=dict)
    attrs_bool: dict[str, int] = field(default_factory=dict)

    # Derived hot columns (materialized in the CH schema)
    llm_request_model: str = ""
    llm_response_model: str = ""
    embedding_model: str = ""
    streaming: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None

    eval_status: str = ""
    semconv_source: str = ""
    is_deleted: int = 0


# Stable column ordering for the CH query. JSON columns wrapped in toJSONString
# so clickhouse-connect can decode them (it cannot yet handle the typed JSON
# column type in result rows — see DECISIONS #015, #018 of the migration).
_READ_COLUMNS: tuple[str, ...] = (
    "id", "toString(project_id) AS project_id", "trace_id", "parent_span_id",
    "name", "observation_type", "operation_name",
    "start_time", "end_time", "latency_ms",
    "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens", "cost",
    "status", "status_message",
    "toString(org_id) AS org_id", "toString(project_version_id) AS project_version_id",
    "toString(end_user_id) AS end_user_id", "toString(trace_session_id) AS trace_session_id",
    "toString(prompt_version_id) AS prompt_version_id",
    "toString(prompt_label_id) AS prompt_label_id",
    "toString(custom_eval_config_id) AS custom_eval_config_id",
    "input", "output", "tags", "span_events",
    "toJSONString(metadata) AS metadata",
    "toJSONString(resource_attrs) AS resource_attrs",
    "toJSONString(attributes_extra) AS attributes_extra",
    "attrs_string", "attrs_number", "attrs_bool",
    "llm_request_model", "llm_response_model", "embedding_model",
    "streaming", "temperature", "top_p", "max_tokens",
    "eval_status", "semconv_source", "is_deleted",
)

_SELECT_SQL = ", ".join(_READ_COLUMNS)

# Order in which result_rows columns arrive — bare names (no `AS` aliases) for the
# row→dataclass mapping below.
_DATA_KEYS: tuple[str, ...] = (
    "id", "project_id", "trace_id", "parent_span_id",
    "name", "observation_type", "operation_name",
    "start_time", "end_time", "latency_ms",
    "model", "provider", "prompt_tokens", "completion_tokens", "total_tokens", "cost",
    "status", "status_message",
    "org_id", "project_version_id", "end_user_id", "trace_session_id",
    "prompt_version_id", "prompt_label_id", "custom_eval_config_id",
    "input", "output", "tags", "span_events",
    "metadata", "resource_attrs", "attributes_extra",
    "attrs_string", "attrs_number", "attrs_bool",
    "llm_request_model", "llm_response_model", "embedding_model",
    "streaming", "temperature", "top_p", "max_tokens",
    "eval_status", "semconv_source", "is_deleted",
)


def _row_to_chspan(row: tuple) -> CHSpan:
    d = dict(zip(_DATA_KEYS, row))
    # CH returns the toString() forms with literal 'NULL' for missing UUIDs in
    # some 25.x patch versions; normalize either case to None.
    for k in ("org_id", "project_version_id", "end_user_id", "trace_session_id",
              "prompt_version_id", "prompt_label_id", "custom_eval_config_id"):
        v = d.get(k)
        d[k] = None if v in (None, "", "00000000-0000-0000-0000-000000000000") else v
    return CHSpan(**d)


class CHSpanReader:
    """Read-only span fetcher backed by ClickHouse `spans FINAL`.

    Thread-safe. Holds a clickhouse-connect HTTP client; safe to share between
    threads but each `query` call holds the connection briefly. For concurrent
    high-fanout reads (parallel eval runners) instantiate one reader per worker.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 19001,
        username: str = "default",
        password: str = "",
        database: str = "default",
        timeout_sec: int = 30,
    ):
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password,
            database=database, send_receive_timeout=timeout_sec,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ─── Single-row by id ────────────────────────────────────────────────────
    def get(self, span_id: str) -> Optional[CHSpan]:
        """Equivalent to ObservationSpan.objects.get(id=span_id), returns None
        if absent (matches the pattern most callers wrap with try/except)."""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE id = %(span_id)s AND is_deleted = 0 LIMIT 1",
            parameters={"span_id": span_id},
        ).result_rows
        if not rows:
            return None
        return _row_to_chspan(rows[0])

    # ─── All spans in a trace ────────────────────────────────────────────────
    def list_by_trace(self, trace_id: str) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(trace=trace, deleted=False).

        Returned in start_time, id order so the eval runner's trace-walking
        logic sees spans in a deterministic chronological order.
        """
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s AND is_deleted = 0 "
            "ORDER BY start_time, id",
            parameters={"trace_id": trace_id},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── All spans in a session ──────────────────────────────────────────────
    def list_by_session(self, session_id: str) -> list[CHSpan]:
        """For session-level evals (`EvalLogger.target_type='session'`)."""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE trace_session_id = %(session_id)s AND is_deleted = 0 "
            "ORDER BY start_time, id",
            parameters={"session_id": session_id},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Aggregations ────────────────────────────────────────────────────────
    def trace_aggregate(self, trace_id: str) -> dict[str, Any]:
        """Computes the same aggregate the eval runner needs for trace-level
        evals: total tokens, total cost, span count, max end_time.
        """
        rows = self._client.query(
            "SELECT count() AS span_count, "
            "sum(prompt_tokens) AS prompt_tokens, "
            "sum(completion_tokens) AS completion_tokens, "
            "sum(total_tokens) AS total_tokens, "
            "sum(cost) AS cost, "
            "max(end_time) AS last_end "
            "FROM spans FINAL WHERE trace_id = %(trace_id)s AND is_deleted = 0",
            parameters={"trace_id": trace_id},
        ).result_rows
        if not rows:
            return {}
        n, pt, ct, tt, c, last_end = rows[0]
        return {
            "span_count": int(n or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
            "last_end": last_end,
        }

    # ─── Convenience: JSON-decoded input/output ──────────────────────────────
    @staticmethod
    def input_as_json(span: CHSpan) -> Any:
        return _maybe_json(span.input)

    @staticmethod
    def output_as_json(span: CHSpan) -> Any:
        return _maybe_json(span.output)

    @staticmethod
    def attributes_extra_as_dict(span: CHSpan) -> dict:
        try:
            return json.loads(span.attributes_extra) if span.attributes_extra else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def to_django_dict(span: CHSpan) -> dict[str, Any]:
        """Convert a CHSpan to a dict shaped like ObservationSpanSerializer output.

        Drop-in for consumers that did:

            spans_data = ObservationSpanSerializer(qs, many=True).data

        Mapping notes (per the serializer's `fields` list in
        tracer/serializers/observation_span.py):
          • FK fields (`project`, `trace`, `project_version`, `custom_eval_config`,
            `prompt_version`) are emitted as ID strings — same as the
            PrimaryKeyRelatedField serializer output.
          • Derived `provider_logo` / `span_attributes` are computed from
            the span (provider→logo URL is a static map; span_attributes is
            the merge of attrs_string/number/bool + attributes_extra).
          • Fields that exist in the Django model but NOT in the CH spans
            table (`model_parameters`, `response_time`, `eval_id`,
            `org_user_id`) emit None. The consumer either doesn't read them
            (most cases — frontend just renders what's present) or will
            need the CH schema to add them in a future migration.
        """
        try:
            metadata_parsed = json.loads(span.metadata) if span.metadata else {}
        except json.JSONDecodeError:
            metadata_parsed = {}
        # span_attributes is the legacy serializer field that flattens
        # attrs_string/number/bool + attributes_extra into one dict — the
        # shape v1 consumers expect.
        span_attributes: dict[str, Any] = {}
        span_attributes.update(span.attrs_string or {})
        span_attributes.update(span.attrs_number or {})
        span_attributes.update(span.attrs_bool or {})
        try:
            extra = json.loads(span.attributes_extra) if span.attributes_extra else {}
            if isinstance(extra, dict):
                span_attributes.update(extra)
        except json.JSONDecodeError:
            pass
        # tags / span_events come from CH as JSON strings; the serializer
        # returns them as Python objects.
        try:
            tags_parsed = json.loads(span.tags) if span.tags else []
        except json.JSONDecodeError:
            tags_parsed = []
        try:
            span_events_parsed = json.loads(span.span_events) if span.span_events else []
        except json.JSONDecodeError:
            span_events_parsed = []
        return {
            "id": span.id,
            "project": span.project_id,
            "project_version": span.project_version_id,
            "trace": span.trace_id,
            "parent_span_id": span.parent_span_id or None,
            "name": span.name,
            "observation_type": span.observation_type,
            "start_time": span.start_time.isoformat() if span.start_time else None,
            "end_time": span.end_time.isoformat() if span.end_time else None,
            "input": _maybe_json(span.input),
            "output": _maybe_json(span.output),
            "model": span.model,
            "model_parameters": None,                                   # not on CH spans yet — see docstring
            "latency_ms": span.latency_ms,
            "org_id": span.org_id,
            "org_user_id": None,                                        # not on CH spans yet
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "total_tokens": span.total_tokens,
            "response_time": None,                                      # not on CH spans yet
            "eval_id": None,                                            # not on CH spans yet
            "cost": span.cost,
            "status": span.status,
            "status_message": span.status_message,
            "tags": tags_parsed,
            "metadata": metadata_parsed,
            "span_events": span_events_parsed,
            "provider": span.provider,
            "provider_logo": _provider_logo_url(span.provider),
            "span_attributes": span_attributes,
            "custom_eval_config": span.custom_eval_config_id,
            "eval_status": span.eval_status,
            "prompt_version": span.prompt_version_id,
        }


# Provider → logo URL map. Mirrors what the serializer's get_provider_logo() does
# in tracer/serializers/observation_span.py. Cached as a module constant.
_PROVIDER_LOGOS: dict[str, str] = {
    "openai": "https://app.futureagi.com/static/providers/openai.svg",
    "anthropic": "https://app.futureagi.com/static/providers/anthropic.svg",
    "google": "https://app.futureagi.com/static/providers/google.svg",
    "gcp.vertex.agent": "https://app.futureagi.com/static/providers/google.svg",
    "vapi": "https://app.futureagi.com/static/providers/vapi.svg",
    "retell": "https://app.futureagi.com/static/providers/retell.svg",
}


def _provider_logo_url(provider: str | None) -> str | None:
    """Return the provider logo URL, or None if the provider isn't mapped.

    Kept simple — the serializer has more elaborate fallback logic but
    most callers just render the URL or fall back to a generic icon.
    """
    if not provider:
        return None
    return _PROVIDER_LOGOS.get(provider.lower())


def _maybe_json(s: str) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s
