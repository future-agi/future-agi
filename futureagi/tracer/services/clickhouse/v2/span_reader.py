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
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import clickhouse_connect

from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)
from tracer.services.clickhouse.v2.query_settings import current_settings


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
    end_time: datetime | None
    latency_ms: int

    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float

    status: str
    status_message: str

    org_id: str | None
    project_version_id: str | None
    end_user_id: str | None
    trace_session_id: str | None
    prompt_version_id: str | None
    prompt_label_id: str | None
    custom_eval_config_id: str | None

    # Inputs / outputs come back as raw JSON-strings from CH; the eval runner
    # currently calls json.loads on them where needed. Keep the shape identical
    # so no downstream `.input` callsite changes.
    input: str
    output: str
    tags: str
    span_events: str
    metadata: str  # JSON string from CH typed JSON column
    resource_attrs: str  # JSON string
    attributes_extra: str  # JSON string

    # Typed Map columns. Maps to Python dicts.
    attrs_string: dict[str, str] = field(default_factory=dict)
    attrs_number: dict[str, float] = field(default_factory=dict)
    attrs_bool: dict[str, int] = field(default_factory=dict)

    # Derived hot columns (materialized in the CH schema)
    llm_request_model: str = ""
    llm_response_model: str = ""
    embedding_model: str = ""
    streaming: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None

    eval_status: str = ""
    semconv_source: str = ""
    is_deleted: int = 0

    # Denormalized onto every span row so a caller gets trace context (e.g. the
    # cluster-RCA agent) without a separate PG read.
    trace_name: str = ""

    @property
    def pk(self):
        """Django-model parity so callers reading ``.pk`` work on a CHSpan."""
        return self.id


@dataclass(frozen=True)
class SpanScope:
    """Minimal span fields for org/project/trace scope checks — read WITHOUT the
    wide JSON columns. See ``CHSpanReader.scope_by_ids``."""

    project_id: str | None
    trace_id: str | None


# Stable column ordering for the CH query. JSON columns wrapped in toJSONString
# so clickhouse-connect can decode them (it cannot yet handle the typed JSON
# column type in result rows — see DECISIONS #015, #018 of the migration).
#
# The toString() id columns are aliased with a ``_str`` suffix, NOT their bare
# column name. A ``toString(project_id) AS project_id`` alias SHADOWS the real
# key column, so a ``WHERE project_id = %(pid)s`` in the same query resolves to
# the alias (a function of the key) and the primary-key index can no longer prune —
# turning every project/org-scoped read into a full-table scan. Decoding is
# positional (``_row_to_chspan`` zips _DATA_KEYS), so the alias name is free to
# differ from the CHSpan field name.
_READ_COLUMNS: tuple[str, ...] = (
    "id",
    "toString(project_id) AS project_id_str",
    "trace_id",
    "parent_span_id",
    "name",
    "observation_type",
    "operation_name",
    "start_time",
    "end_time",
    "latency_ms",
    "model",
    "provider",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "status",
    "status_message",
    "toString(org_id) AS org_id_str",
    "toString(project_version_id) AS project_version_id_str",
    "toString(end_user_id) AS end_user_id_str",
    "toString(trace_session_id) AS trace_session_id_str",
    "toString(prompt_version_id) AS prompt_version_id_str",
    "toString(prompt_label_id) AS prompt_label_id_str",
    "toString(custom_eval_config_id) AS custom_eval_config_id_str",
    "input",
    "output",
    "tags",
    "span_events",
    "toJSONString(metadata) AS metadata",
    "toJSONString(resource_attrs) AS resource_attrs",
    # attributes_extra is a plain String column (schema 013); no toJSONString wrapper.
    "attributes_extra",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "llm_request_model",
    "llm_response_model",
    "embedding_model",
    "streaming",
    "temperature",
    "top_p",
    "max_tokens",
    "eval_status",
    "semconv_source",
    "is_deleted",
    "trace_name",
)

_SELECT_SQL = ", ".join(_READ_COLUMNS)

# Same positional shape with the heavy JSON columns stubbed to '' — decoding
# attributes_extra/span_events/resource_attrs dominates wide reads (a voice
# conversation root carries its whole raw_log there). _row_to_chspan works
# unchanged; the stubbed fields just come back empty.
_HEAVY_COLUMNS = {
    "span_events",
    "toJSONString(resource_attrs) AS resource_attrs",
    "attributes_extra",
}
_LEAN_SELECT_SQL = ", ".join(
    "''" if col in _HEAVY_COLUMNS else col for col in _READ_COLUMNS
)

# ClickHouse 25.3 turns skip indexes OFF under FINAL by default. A trace-id-keyed
# read of this table has no usable primary-key prefilter (trace_id is below the PK),
# so the ``idx_trace_id`` (and roots' ``parent_span_id``) bloom skip indexes are the
# only thing that prunes it — without them FINAL does a full in-order merge across
# every part and the per-part granule buffers blow the memory limit. Re-enable them.
#
# Correctness under non-exact FINAL (25.3 has no exact_mode): a skip index is only
# safe here if it filters a column that is STABLE across a row's ReplacingMergeTree
# versions — otherwise it could prune the granule holding the latest version and
# resurrect an older one. ``trace_id`` / ``parent_span_id`` are sorting-key / stable
# columns, so their bloom indexes are safe. The one hazard is the minmax index on
# ``is_deleted`` (which DOES change across versions): the caller must therefore NOT
# pass an ``is_deleted = 0`` predicate alongside this setting — the two-arg
# ReplacingMergeTree(_version, is_deleted) engine already drops deleted rows under
# FINAL, so the predicate is redundant and only arms the resurrection bug.
_FINAL_SKIP_INDEX_SETTINGS = {"use_skip_indexes_if_final": 1}

# Order in which result_rows columns arrive — bare names (no `AS` aliases) for the
# row→dataclass mapping below.
_DATA_KEYS: tuple[str, ...] = (
    "id",
    "project_id",
    "trace_id",
    "parent_span_id",
    "name",
    "observation_type",
    "operation_name",
    "start_time",
    "end_time",
    "latency_ms",
    "model",
    "provider",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "status",
    "status_message",
    "org_id",
    "project_version_id",
    "end_user_id",
    "trace_session_id",
    "prompt_version_id",
    "prompt_label_id",
    "custom_eval_config_id",
    "input",
    "output",
    "tags",
    "span_events",
    "metadata",
    "resource_attrs",
    "attributes_extra",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "llm_request_model",
    "llm_response_model",
    "embedding_model",
    "streaming",
    "temperature",
    "top_p",
    "max_tokens",
    "eval_status",
    "semconv_source",
    "is_deleted",
    "trace_name",
)

_EXPORT_COLUMN_SQL: dict[str, str] = {
    "project_id": "toString(project_id)",
    "trace_id": "trace_id",
    "parent_span_id": "parent_span_id",
    "name": "name",
    "observation_type": "observation_type",
    "operation_name": "operation_name",
    "start_time": "start_time",
    "end_time": "end_time",
    "latency_ms": "latency_ms",
    "model": "model",
    "provider": "provider",
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "cost": "cost",
    "status": "status",
    "status_message": "status_message",
    "tags": "tags",
    "metadata": "toJSONString(metadata)",
    "span_events": "span_events",
    "resource_attrs": "toJSONString(resource_attrs)",
    "input": "input",
    "output": "output",
    "attrs_string": "attrs_string",
    "attrs_number": "attrs_number",
    "attrs_bool": "attrs_bool",
    "attributes_extra": "attributes_extra",
    "eval_status": "eval_status",
    "semconv_source": "semconv_source",
}

_EXPORT_FIELD_COLUMNS: dict[str, set[str]] = {
    "project": {"project_id"},
    "project_id": {"project_id"},
    "trace": {"trace_id"},
    "trace_id": {"trace_id"},
    "parent_span_id": {"parent_span_id"},
    "id": set(),
    "name": {"name"},
    "observation_type": {"observation_type"},
    "operation_name": {"operation_name"},
    "start_time": {"start_time"},
    "end_time": {"end_time"},
    "latency_ms": {"latency_ms"},
    "model": {"model"},
    "provider": {"provider"},
    "prompt_tokens": {"prompt_tokens"},
    "completion_tokens": {"completion_tokens"},
    "total_tokens": {"total_tokens"},
    "cost": {"cost"},
    "status": {"status"},
    "status_message": {"status_message"},
    "tags": {"tags"},
    "metadata": {"metadata"},
    "span_events": {"span_events"},
    "resource_attributes": {"resource_attrs"},
    "resource_attrs": {"resource_attrs"},
    "input": {"input"},
    "output": {"output"},
    "span_attributes": {
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
    },
    "model_parameters": {"attributes_extra"},
    "input_images": {"attributes_extra"},
    "eval_input": {"attributes_extra"},
    "eval_attributes": {"attributes_extra"},
    "eval_status": {"eval_status"},
    "semconv_source": {"semconv_source"},
}


def _export_columns_for_fields(field_names: set[str]) -> set[str]:
    columns: set[str] = set()
    for field_name in field_names:
        columns.update(_EXPORT_FIELD_COLUMNS.get(field_name, {field_name}))
    return {column for column in columns if column in _EXPORT_COLUMN_SQL}


def _row_to_chspan(row: tuple) -> CHSpan:
    d = dict(zip(_DATA_KEYS, row, strict=False))
    # CH returns the toString() forms with literal 'NULL' for missing UUIDs in
    # some 25.x patch versions; normalize either case to None.
    for k in (
        "org_id",
        "project_version_id",
        "end_user_id",
        "trace_session_id",
        "prompt_version_id",
        "prompt_label_id",
        "custom_eval_config_id",
    ):
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
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            send_receive_timeout=timeout_sec,
            settings=current_settings() or None,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ─── id-remap helper (P3b step1.5) ───────────────────────────────────────
    def _resolve_session_ids_to_canonical(
        self, session_ids: list[str]
    ) -> dict[str, str]:
        """Map ``{input trace_session_id -> survivor (canonical old) id}``.

        Resolve each caller id to its consolidation group's survivor via the SAME
        survivor map the span side uses (``survivor_map_subquery``), so the input
        side can't disagree: a new id, a non-survivor old, and the survivor all map
        to the survivor; an unmapped id (1:1 / net-new) maps to itself. Pre-flip a
        no-op (gate B). See id_remap_sql.
        """
        from tracer.services.clickhouse.v2.id_remap_sql import survivor_map_subquery

        ids = {str(s) for s in session_ids if s}
        if not ids:
            return {}
        rows = self._client.query(
            "SELECT toString(any_id) AS any_id, toString(survivor_id) AS survivor_id "
            f"FROM ({survivor_map_subquery('trace_session_id_remap')}) "
            "WHERE any_id IN %(ids)s",
            parameters={"ids": tuple(ids)},
        ).result_rows
        id_to_survivor = {str(a): str(s) for (a, s) in rows}
        return {i: id_to_survivor.get(i, i) for i in ids}

    @staticmethod
    def _session_filter_remap() -> tuple[str, str]:
        """Return ``(join_clause, resolved_predicate)`` for a single-session
        equality filter on the bare ``spans FINAL`` scan.

        P3b step1.5 (DESIGN §3 / id_remap_sql): a flat (no per-session GROUP BY)
        ``spans`` aggregation filtered by ONE OLD curated ``trace_session_id``
        must also count a cross-cutover straddler's NEW-id spans. The caller adds
        ``join_clause`` after ``FROM spans FINAL`` and the ``resolved_predicate``
        (``<resolved> = %(sid)s``) to its WHERE list, binding ``%(sid)s`` to the
        OLD id. Pre-flip NO span matches a ``new_id`` → resolved id == own id →
        byte-identical no-op (gate B). The remap table only carries
        ``old_id``/``new_id``, so the unqualified span columns the surrounding
        query selects stay unambiguous under the join.
        """
        join = remap_left_join(
            "spans.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        predicate = (
            f"{resolved_id_expr('spans.trace_session_id', 'ts_remap')} = %(sid)s"
        )
        return join, predicate

    # ─── Single-row by id ────────────────────────────────────────────────────
    def get(self, span_id: str, *, project_id: str | None = None) -> CHSpan | None:
        """Equivalent to ObservationSpan.objects.get(id=span_id), returns None
        if absent (matches the pattern most callers wrap with try/except).

        ``project_id`` (optional) scopes to one tenant; omit for prior behavior.

        ``id`` is below the primary-key prefix, so a bare ``id =`` read prunes
        only via the ``idx_id`` bloom — which CH 25.3 disables under FINAL by
        default. Without it a point-read does a full in-order merge over every
        part and the fat ``attributes_extra`` granule buffers blow the memory
        limit on a wide (voice) span. Re-enable skip indexes; ``id`` is stable
        across a row's ReplacingMergeTree versions, so the bloom is safe."""
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS.
        where = ["id = %(span_id)s"]
        params: dict[str, Any] = {"span_id": span_id}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} LIMIT 1",
            parameters=params,
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        if not rows:
            return None
        return _row_to_chspan(rows[0])

    # ─── One trace's curated fields (the `traces` store the list endpoints read)
    def get_trace_row(
        self, trace_id: str, *, project_id: str | None = None
    ) -> dict | None:
        """Read one trace's curated fields from the CH ``traces`` table by id."""
        where = ["id = %(tid)s", "is_deleted = 0"]
        params: dict[str, Any] = {"tid": str(trace_id)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        keys = (
            "id",
            "project_id",
            "project_version_id",
            "name",
            "session_id",
            "external_id",
            "tags",
            "metadata",
            "input",
            "output",
            "error",
            "error_analysis_status",
            "created_at",
        )
        cols = (
            "toString(id)",
            "toString(project_id)",
            "toString(project_version_id)",
            "name",
            "toString(session_id)",
            "external_id",
            "tags",
            "metadata",
            "input",
            "output",
            "error",
            "error_analysis_status",
            "created_at",
        )
        rows = self._client.query(
            f"SELECT {', '.join(cols)} FROM traces FINAL "
            f"WHERE {' AND '.join(where)} LIMIT 1",
            parameters=params,
        ).result_rows
        if not rows:
            return None
        return dict(zip(keys, rows[0], strict=False))

    # ─── All spans in a trace ────────────────────────────────────────────────
    def list_by_trace(
        self,
        trace_id: str,
        *,
        include_heavy: bool = True,
        project_id: str | None = None,
    ) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(trace=trace, deleted=False).

        Returned in start_time, id order so the eval runner's trace-walking
        logic sees spans in a deterministic chronological order. ``project_id``
        (optional) scopes the read to one tenant; omit for prior behavior.

        With ``include_heavy=False`` the fat JSON columns (attributes_extra /
        span_events / resource_attrs) come back as '' — opt out when only
        scalar columns are needed (e.g. the lean-first eval path).

        ``trace_id`` sits below the primary-key prefix, so this prunes only via
        the ``idx_trace_id`` bloom — off under FINAL on CH 25.3 by default,
        leaving a full merge that OOMs on a fat (voice) trace's spans. Re-enable
        skip indexes (``trace_id`` is a stable sorting-key column, so safe).
        """
        select = _SELECT_SQL if include_heavy else _LEAN_SELECT_SQL
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS.
        where = ["trace_id = %(trace_id)s"]
        params: dict[str, Any] = {"trace_id": trace_id}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        rows = self._client.query(
            f"SELECT {select} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY start_time, id",
            parameters=params,
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    def first_span_by_type(self, trace_id: str, observation_type: str) -> CHSpan | None:
        """First span of a given type in a trace, ordered by start_time.

        Single-row CH read — replaces listing every span in a trace just to
        find the first LLM/TOOL/etc. span.
        """
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS. Prunes via
        # the ``idx_trace_id`` bloom (off under FINAL without the setting).
        rows = self._client.query(
            f"SELECT {_LEAN_SELECT_SQL} FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s "
            "AND lower(observation_type) = %(otype)s "
            "ORDER BY start_time, id LIMIT 1",
            parameters={"trace_id": trace_id, "otype": observation_type.lower()},
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        return _row_to_chspan(rows[0]) if rows else None

    # ─── All spans in a session ──────────────────────────────────────────────
    def list_by_session(self, session_id: str) -> list[CHSpan]:
        """For session-level evals (`EvalLogger.target_type='session'`).

        P3b step1.5 (DESIGN §3 / id_remap_sql): ``session_id`` is the OLD curated
        ``TraceSession.id`` (still the primary key). A cross-cutover straddler's
        NEW (deterministic-id) spans carry ``trace_session_id = new_id``; resolve
        each span's ``trace_session_id`` new→old through ``trace_session_id_remap``
        and match the OLD id on the RESOLVED value, so a session-level eval sees
        old + new spans as ONE session. The returned ``trace_session_id`` column
        stays the span's RAW id (these are real span rows). Pre-flip NO span
        matches a ``new_id``, so the resolved id == the span's own id and this is
        a byte-identical no-op (gate B).
        """
        remap_join = remap_left_join(
            "spans.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("spans.trace_session_id", "ts_remap")
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            f"{remap_join} "
            f"WHERE {resolved_ts} = %(session_id)s AND is_deleted = 0 "
            "ORDER BY start_time, id",
            parameters={"session_id": session_id},
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Bulk fetch by trace ids ──────────────────────────────────────────────
    def list_by_trace_ids(
        self,
        trace_ids: list[str],
        *,
        project_id: str | None = None,
        include_heavy: bool = True,
    ) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(trace_id__in=trace_ids).

        Returns spans across multiple traces in (trace_id, start_time, id) order.
        Empty input returns empty list.

        ``project_id`` (optional) scopes the read to one tenant. ``trace_id`` sits
        below ``project_id`` in the sorting key and is absent from the primary key,
        so an unscoped ``trace_id IN`` read cannot prune parts and a FINAL merge
        spans the whole table — pass ``project_id`` whenever the caller knows the
        traces belong to a single project so the primary-key prefix prunes the
        scan. Omit for prior (cross-project) behavior.

        ``include_heavy`` defaults to True here (unlike ``roots_by_trace_ids``,
        which defaults to lean) because most callers consume span_events /
        resource_attrs / attributes_extra and must stay byte-identical. Pass False
        to stub those three fat columns: under FINAL's in-order read the per-part
        granule buffers for attributes_extra dominate memory, so a caller that only
        needs input/output/metadata/attrs_string should read lean.
        """
        if not trace_ids:
            return []
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS.
        where = ["trace_id IN %(trace_ids)s"]
        params: dict[str, Any] = {"trace_ids": tuple(trace_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        select = _SELECT_SQL if include_heavy else _LEAN_SELECT_SQL
        rows = self._client.query(
            f"SELECT {select} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY trace_id, start_time, id",
            parameters=params,
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Root spans only (parentless) across many traces ─────────────────────
    def roots_by_trace_ids(
        self,
        trace_ids: list[str],
        *,
        include_heavy: bool = False,
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> list[CHSpan]:
        """Parentless spans for the given traces, same shape/order as
        list_by_trace_ids. Fetches one row per root instead of every span.

        Unless ``include_heavy``, attributes_extra / span_events /
        resource_attrs come back as '' — decoding them dominates the read
        (a voice conversation root carries its whole raw_log in
        attributes_extra). input/output/attrs_string stay real. ``project_id``
        (optional) scopes the read to one tenant; omit for prior behavior.

        NOTE: structural root (parentless span), NOT the cluster-RCA agent's
        argMin "representative trace" — deliberately different reads.
        """
        if not trace_ids:
            return []
        select = _SELECT_SQL if include_heavy else _LEAN_SELECT_SQL
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS.
        where = [
            "trace_id IN %(trace_ids)s",
            "(parent_span_id IS NULL OR parent_span_id = '')",
        ]
        params: dict[str, Any] = {"trace_ids": tuple(trace_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        if org_id:
            where.append("org_id = %(oid)s")
            params["oid"] = str(org_id)
        rows = self._client.query(
            f"SELECT {select} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY trace_id, start_time, id",
            parameters=params,
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Per-trace rollups (latency / tokens) ─────────────────────────────────
    def totals_by_trace_ids(
        self, trace_ids: list[str]
    ) -> dict[str, tuple[int | None, int | None, int | None]]:
        """{trace_id: (latency_ms, prompt_tokens, completion_tokens)} summed
        in CH — replaces materializing every span just to add three ints.

        No FINAL: analytics-aggregate convention (matches the dashboard query
        builders) — an unmerged duplicate inflates a sum until the merge runs,
        acceptable for summary stats and far cheaper on fat-row tables."""
        if not trace_ids:
            return {}
        rows = self._client.query(
            "SELECT trace_id, sum(latency_ms) AS lat, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct "
            "FROM spans "
            "WHERE trace_id IN %(trace_ids)s AND is_deleted = 0 "
            "GROUP BY trace_id",
            parameters={"trace_ids": tuple(trace_ids)},
        ).result_rows
        return {
            str(tid): (
                int(lat) if lat else None,
                int(pt) if pt else None,
                int(ct) if ct else None,
            )
            for tid, lat, pt, ct in rows
        }

    # ─── Children of a parent span ────────────────────────────────────────────
    def list_by_parent(
        self, parent_span_id: str, *, limit: int | None = None
    ) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(parent_span_id=, deleted=False)
        ordered by start_time, id. `limit` caps the result for display-list paths
        (e.g. `[:20]` slices that the AI-tools `get_span` does)."""
        lim_clause = f" LIMIT {int(limit)}" if limit else ""
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS. Prunes via
        # the ``idx_parent_span_id`` bloom (off under FINAL without the setting);
        # parent_span_id is stable across versions, so the bloom is safe.
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            "WHERE parent_span_id = %(parent)s "
            f"ORDER BY start_time, id{lim_clause}",
            parameters={"parent": parent_span_id},
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    # ─── Spans by id batch ────────────────────────────────────────────────────
    def list_by_ids(
        self,
        span_ids: list[str],
        *,
        include_heavy: bool = True,
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(id__in=span_ids).

        Result order is NOT preserved relative to the input list (CH orders
        by id for determinism). Callers that need a specific order should
        sort the result themselves.

        With ``include_heavy=False`` the fat JSON columns (attributes_extra /
        span_events / resource_attrs) come back as '' — opt out when only
        id/scalar columns are needed.
        """
        if not span_ids:
            return []
        select = _SELECT_SQL if include_heavy else _LEAN_SELECT_SQL
        # No is_deleted predicate — see _FINAL_SKIP_INDEX_SETTINGS. Prunes via
        # the ``idx_id`` bloom (off under FINAL without the setting); a fat
        # (voice) span in the batch otherwise OOMs the full in-order merge.
        where = ["id IN %(ids)s"]
        params: dict[str, Any] = {"ids": tuple(span_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        if org_id:
            where.append("org_id = %(oid)s")
            params["oid"] = str(org_id)
        rows = self._client.query(
            f"SELECT {select} FROM spans FINAL WHERE {' AND '.join(where)} ORDER BY id",
            parameters=params,
            settings=_FINAL_SKIP_INDEX_SETTINGS,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    def export_fields_by_ids(
        self,
        span_ids: list[str],
        field_names: set[str],
        *,
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Fetch only the span columns needed for dataset export cells.

        Dataset export can map a handful of fields across hundreds of spans.
        Using ``list_by_ids`` here hydrates every CHSpan column, including fat
        payload columns like ``attributes_extra`` and ``span_events``. This
        method keeps the read on canonical CH25 ``spans`` while avoiding
        ``FINAL`` and selecting only the columns implied by the requested
        mapping.
        """
        if not span_ids:
            return {}

        columns = _export_columns_for_fields(field_names)
        select_exprs = ["id"]
        aliases = ["id"]
        for alias in sorted(columns):
            select_exprs.append(
                f"argMax({_EXPORT_COLUMN_SQL[alias]}, _version) AS {alias}"
            )
            aliases.append(alias)

        where = ["id IN %(ids)s"]
        params: dict[str, Any] = {"ids": tuple(str(span_id) for span_id in span_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)
        if org_id:
            where.append("org_id = %(oid)s")
            params["oid"] = str(org_id)

        rows = self._client.query(
            "SELECT "
            f"{', '.join(select_exprs)} "
            "FROM spans "
            f"PREWHERE {' AND '.join(where)} "
            "GROUP BY id "
            "HAVING argMax(is_deleted, _version) = 0",
            parameters=params,
            settings={
                "max_threads": 1,
                "max_bytes_before_external_group_by": 256 * 1024 * 1024,
            },
        ).result_rows

        return {str(row[0]): dict(zip(aliases, row, strict=False)) for row in rows}

    # ─── Batch helpers for dataset child-tree export ─────────────────────────

    def trace_ids_for_span_ids(
        self,
        span_ids: list[str],
        *,
        project_id: str | None = None,
    ) -> dict[str, str]:
        """Return {span_id: trace_id} for the given span IDs.

        Lightweight query: selects only (id, trace_id), avoids FINAL by using
        argMax dedup. Used to batch-resolve trace membership before fetching
        full trace trees.
        """
        if not span_ids:
            return {}
        where = ["id IN %(ids)s"]
        params: dict[str, Any] = {"ids": tuple(span_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)

        rows = self._client.query(
            "SELECT id, argMax(trace_id, _version) AS trace_id "
            "FROM spans "
            f"PREWHERE {' AND '.join(where)} "
            "GROUP BY id "
            "HAVING argMax(is_deleted, _version) = 0",
            parameters=params,
            settings={"max_threads": 2},
        ).result_rows
        return {str(r[0]): str(r[1]) for r in rows}

    _CHILD_TREE_COLUMNS: tuple[tuple[str, str], ...] = (
        ("parent_span_id", "parent_span_id"),
        ("name", "name"),
        ("observation_type", "observation_type"),
        ("operation_name", "operation_name"),
        ("status", "status"),
        ("status_message", "status_message"),
        ("model", "model"),
        ("provider", "provider"),
        ("input", "input"),
        ("output", "output"),
        ("toJSONString(metadata)", "metadata"),
        ("attrs_string", "attrs_string"),
        ("attrs_number", "attrs_number"),
        ("attrs_bool", "attrs_bool"),
        ("attributes_extra", "attributes_extra"),
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
        ("latency_ms", "latency_ms"),
        ("cost", "cost"),
        ("tags", "tags"),
        ("span_events", "span_events"),
        ("start_time", "start_time"),
    )

    _CHILD_TREE_ALIASES: tuple[str, ...] = (
        "id",
        "trace_id",
        "parent_span_id",
        "name",
        "observation_type",
        "operation_name",
        "status",
        "status_message",
        "model",
        "provider",
        "input",
        "output",
        "metadata",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
        "cost",
        "tags",
        "span_events",
        "start_time",
    )

    def child_tree_spans_by_trace_ids(
        self,
        trace_ids: list[str],
        *,
        project_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch all spans for the given traces, returning only columns needed
        for child-tree serialization (excludes resource_attrs).

        Uses argMax dedup (no FINAL) with external GROUP BY spill to avoid OOM.
        Returns {trace_id: [row_dict, ...]} ordered by start_time DESC within
        each trace (matching legacy ObservationSpan.Meta.ordering).
        """
        if not trace_ids:
            return {}

        select_exprs = ["id", "trace_id"]
        for expr, alias in self._CHILD_TREE_COLUMNS:
            select_exprs.append(f"argMax({expr}, _version) AS {alias}")

        where = ["trace_id IN %(tids)s"]
        params: dict[str, Any] = {"tids": tuple(trace_ids)}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = str(project_id)

        rows = self._client.query(
            f"SELECT {', '.join(select_exprs)} "
            "FROM spans "
            f"PREWHERE {' AND '.join(where)} "
            "GROUP BY id, trace_id "
            "HAVING argMax(is_deleted, _version) = 0",
            parameters=params,
            settings={
                "max_threads": 2,
                "max_bytes_before_external_group_by": 512 * 1024 * 1024,
            },
        ).result_rows

        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            d = dict(zip(self._CHILD_TREE_ALIASES, row, strict=False))
            tid = str(d["trace_id"])
            result.setdefault(tid, []).append(d)

        # Sort each trace's spans by start_time DESC (matching legacy ordering)
        for spans in result.values():
            spans.sort(key=lambda s: s.get("start_time") or "", reverse=True)

        return result

    def root_ids_by_project(
        self,
        project_id: str,
        *,
        org_id: str | None = None,
        exclude_trace_ids: list[str] | None = None,
    ) -> list[str]:
        if not project_id:
            return []
        where = [
            "project_id = %(pid)s",
            "is_deleted = 0",
            "(parent_span_id IS NULL OR parent_span_id = '')",
        ]
        params: dict[str, Any] = {"pid": str(project_id)}
        if org_id:
            where.append("org_id = %(oid)s")
            params["oid"] = str(org_id)
        if exclude_trace_ids:
            where.append("trace_id NOT IN %(exclude_trace_ids)s")
            params["exclude_trace_ids"] = tuple(exclude_trace_ids)
        rows = self._client.query(
            "SELECT id FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY trace_id, start_time, id",
            parameters=params,
        ).result_rows
        return [str(row[0]) for row in rows]

    def ids_by_project(
        self,
        project_id: str,
        *,
        org_id: str | None = None,
        exclude_ids: list[str] | None = None,
    ) -> list[str]:
        if not project_id:
            return []
        where = ["project_id = %(pid)s", "is_deleted = 0"]
        params: dict[str, Any] = {"pid": str(project_id)}
        if org_id:
            where.append("org_id = %(oid)s")
            params["oid"] = str(org_id)
        if exclude_ids:
            where.append("id NOT IN %(exclude_ids)s")
            params["exclude_ids"] = tuple(exclude_ids)
        rows = self._client.query(
            "SELECT id FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY start_time, id",
            parameters=params,
        ).result_rows
        return [str(row[0]) for row in rows]

    def scope_by_ids(self, span_ids: list[str]) -> dict[str, SpanScope]:
        """Map ``span_id -> SpanScope(project_id, trace_id)``, reading ONLY those
        two columns instead of the full span row.

        The full-row reads (``get`` / ``list_by_ids``) pull the wide JSON
        columns — ``attributes_extra`` / ``input`` / ``output`` / ``metadata`` /
        ``attrs_string`` — which on a fat span (a voice root carrying its whole
        raw log) blow the shared ClickHouse memory limit (code 241). The
        annotation ``for-source`` scope checks only need each span's project
        (and, for the scores panel, its trace), so read just those: a single
        panel-open must not OOM the shared cluster. ``FINAL`` is kept —
        project/trace are stable across versions and a two-column ``FINAL`` read
        stays well under the limit.
        """
        if not span_ids:
            return {}
        rows = self._client.query(
            "SELECT id, toString(project_id) AS project_id, "
            "toString(trace_id) AS trace_id FROM spans FINAL "
            "WHERE id IN %(ids)s AND is_deleted = 0",
            parameters={"ids": tuple(span_ids)},
        ).result_rows

        def _norm(v: Any) -> str | None:
            return (
                None
                if v in (None, "", "NULL", "00000000-0000-0000-0000-000000000000")
                else str(v)
            )

        return {
            str(sid): SpanScope(project_id=_norm(pid), trace_id=_norm(tid))
            for sid, pid, tid in rows
        }

    def root_ids_by_trace_ids(
        self, trace_ids: list[str], project_ids: list[str] | None = None
    ) -> dict[str, tuple[str, str | None]]:
        """``{trace_id: (root_span_id, project_id)}`` reading only id/trace_id/
        project_id — leaner than ``roots_by_trace_ids`` (whose lean select still
        reads input/output), to dodge the CH OOM (code 241) on fat voice roots.
        ``project_ids`` (optional) prunes the scan via the sort-key prefix.
        Ordered so the first parentless span per trace wins."""
        if not trace_ids:
            return {}
        where = [
            "trace_id IN %(trace_ids)s",
            "is_deleted = 0",
            "(parent_span_id IS NULL OR parent_span_id = '')",
        ]
        params: dict[str, Any] = {"trace_ids": tuple(trace_ids)}
        if project_ids:
            where.append("project_id IN %(project_ids)s")
            params["project_ids"] = tuple(project_ids)
        rows = self._client.query(
            "SELECT id, toString(trace_id) AS trace_id, "
            "toString(project_id) AS project_id FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY trace_id, start_time, id",
            parameters=params,
        ).result_rows

        def _norm(v: Any) -> str | None:
            return (
                None
                if v in (None, "", "NULL", "00000000-0000-0000-0000-000000000000")
                else str(v)
            )

        result: dict[str, tuple[str, str | None]] = {}
        for sid, tid, pid in rows:
            tid = str(tid)
            if tid not in result:  # first root per trace wins
                result[tid] = (str(sid), _norm(pid))
        return result

    # ─── Aggregations across many traces ──────────────────────────────────────
    def aggregate_by_trace_ids(self, trace_ids: list[str]) -> dict[str, Any]:
        """Sum(tokens, cost) + count across multiple traces in one query.

        Used by AI tools that compute trace-list totals (e.g. get_trace_timeline
        bucketing spans by time). Returns a single aggregate row across all
        input trace_ids; for per-trace aggregates use trace_aggregate() in a
        loop or extend this method later if a real call site needs it.
        """
        if not trace_ids:
            return {
                "span_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
            }
        rows = self._client.query(
            "SELECT count() AS n, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost "
            "FROM spans FINAL "
            "WHERE trace_id IN %(trace_ids)s AND is_deleted = 0",
            parameters={"trace_ids": tuple(trace_ids)},
        ).result_rows
        if not rows:
            return {
                "span_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
            }
        n, pt, ct, tt, c = rows[0]
        return {
            "span_count": int(n or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
        }

    # ─── Session-level aggregation ────────────────────────────────────────────
    def session_aggregate(self, session_id: str) -> dict[str, Any]:
        """Same shape as trace_aggregate but scoped by trace_session_id. Used by
        get_session_analytics + the session detail view. Includes the start/end
        bracket so callers can compute session duration.

        P3b step1.5 (DESIGN §3 / id_remap_sql): ``session_id`` is the OLD curated
        ``TraceSession.id`` (still primary). A cross-cutover straddler's NEW
        (deterministic-id) spans carry ``trace_session_id = new_id``; resolve each
        span's ``trace_session_id`` new→old through ``trace_session_id_remap`` and
        match the OLD id on the RESOLVED value so old + new spans aggregate as ONE
        session. ``resolved_id_expr`` is the zero-uuid-guarded map (NOT a COALESCE
        — an unmatched LEFT JOIN fills ``old_id`` with the zero-uuid, not NULL).
        Pre-flip NO span matches a ``new_id`` → resolved id == the span's own id →
        byte-identical no-op (gate B).
        """
        remap_join = remap_left_join(
            "spans.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("spans.trace_session_id", "ts_remap")
        rows = self._client.query(
            "SELECT count() AS n, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost, "
            "min(start_time) AS start_time, max(end_time) AS end_time "
            "FROM spans FINAL "
            f"{remap_join} "
            f"WHERE {resolved_ts} = %(sid)s AND is_deleted = 0",
            parameters={"sid": session_id},
        ).result_rows
        if not rows:
            return {}
        n, pt, ct, tt, c, st, et = rows[0]
        return {
            "span_count": int(n or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
            "start_time": st,
            "end_time": et,
        }

    # ─── Project-scoped fetches ───────────────────────────────────────────────
    def list_by_project(
        self,
        project_id: str,
        *,
        observation_type: str | None = None,
        project_version_id: str | None = None,
        limit: int | None = None,
    ) -> list[CHSpan]:
        """Equivalent to ObservationSpan.objects.filter(project_id=, ...).

        Keyword filters compose as ANDs. Used by views that scope spans to a
        single project (delete cascades, project_version queries, etc.).
        `limit` caps the result for paginated paths.
        """
        where = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        if project_version_id:
            where.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        lim_clause = f" LIMIT {int(limit)}" if limit else ""
        rows = self._client.query(
            f"SELECT {_SELECT_SQL} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY start_time, id{lim_clause}",
            parameters=params,
        ).result_rows
        return [_row_to_chspan(r) for r in rows]

    def count_by_project(
        self,
        project_id: str,
        *,
        observation_type: str | None = None,
        project_version_id: str | None = None,
    ) -> int:
        """Count of spans matching the project + optional filters. Equivalent
        to ObservationSpan.objects.filter(project_id=, ...).count()."""
        where = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        if project_version_id:
            where.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        rows = self._client.query(
            f"SELECT count() FROM spans FINAL WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        return int(rows[0][0]) if rows else 0

    def count_by_trace(self, trace_id: str) -> int:
        """Equivalent to ObservationSpan.objects.filter(trace_id=).count()."""
        rows = self._client.query(
            "SELECT count() FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s AND is_deleted = 0",
            parameters={"trace_id": trace_id},
        ).result_rows
        return int(rows[0][0]) if rows else 0

    def exists_for_trace(self, trace_id: str) -> bool:
        """Equivalent to ObservationSpan.objects.filter(trace_id=).exists()."""
        return self.count_by_trace(trace_id) > 0

    # ─── Per-trace aggregates (replaces feed.py / project_version.py rollups) ─
    def per_trace_aggregate(self, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Equivalent to:
            ObservationSpan.objects.filter(trace_id__in=trace_ids)
                .values("trace_id")
                .annotate(span_count=Count("id"), prompt_tokens=Sum(...),
                          completion_tokens=Sum(...), total_tokens=Sum(...),
                          cost=Sum(...), start_time=Min(...), end_time=Max(...),
                          latency_ms=Sum(...))

        Returns a dict keyed by trace_id (str). Missing trace_ids return an
        empty entry (use .get(tid, {}) for safety). Empty input returns {}.
        """
        if not trace_ids:
            return {}
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, "
            "count() AS n, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost, "
            "min(start_time) AS st, max(end_time) AS et, "
            "sum(latency_ms) AS lat "
            "FROM spans FINAL "
            "WHERE trace_id IN %(tids)s AND is_deleted = 0 "
            "GROUP BY toString(trace_id)",
            parameters={"tids": tuple(trace_ids)},
        ).result_rows
        return {
            tid: {
                "span_count": int(n or 0),
                "prompt_tokens": int(pt or 0),
                "completion_tokens": int(ct or 0),
                "total_tokens": int(tt or 0),
                "cost": float(c or 0.0),
                "start_time": st,
                "end_time": et,
                "latency_ms": int(lat or 0),
            }
            for (tid, n, pt, ct, tt, c, st, et, lat) in rows
        }

    # ─── Root-span start_time per trace (replay_session ordering helper) ─────
    def per_trace_root_span_start_times(
        self, trace_ids: list[str]
    ) -> dict[str, datetime | None]:
        """Equivalent to:
            Subquery(ObservationSpan.objects.filter(trace_id=OuterRef("id"),
                                                     parent_span_id__isnull=True)
                                              .values("start_time")[:1])

        Returns a dict trace_id → start_time of the trace's root span (or
        None if no root span exists in CH yet). Used to order trace lists
        by their first activity time without joining cross-store.

        CH stores parent_span_id as non-nullable String (schema 001); root
        spans have an empty string. We pick min(start_time) for ties.
        """
        if not trace_ids:
            return {}
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, min(start_time) AS st "
            "FROM spans FINAL "
            "WHERE trace_id IN %(tids)s AND is_deleted = 0 "
            "  AND parent_span_id = '' "
            "GROUP BY toString(trace_id)",
            parameters={"tids": tuple(trace_ids)},
        ).result_rows
        result: dict[str, datetime | None] = dict.fromkeys(trace_ids)
        for tid, st in rows:
            result[tid] = st
        return result

    # ─── Scan-sweep: completed-trace candidates since a watermark ────────────
    def ch_now(self) -> datetime:
        """ClickHouse server clock as tz-aware UTC — the sweep bounds its window
        off this so the watermark is a real CH timestamp (clock-skew-proof).
        tz-aware so it compares against Django's tz-aware ``last_swept_at``."""
        dt = self._client.query("SELECT now64(6, 'UTC')").result_rows[0][0]
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    # created_at is unindexed, but spans is PARTITION BY toDate(start_time) /
    # PK toStartOfHour(start_time). A trace's start_time precedes its created_at,
    # so flooring start_time at (lower - this) prunes old partitions while still
    # catching exports up to this late. No start_time UPPER bound: created_at is
    # server-set and skew-proof, start_time is producer-set and may run ahead, so
    # an upper bound would silently drop valid in-window traces.
    _CANDIDATE_START_FLOOR = timedelta(days=7)

    def root_trace_candidates(
        self, project_id: str, lower: datetime, upper: datetime
    ) -> list[tuple[str, datetime]]:
        """``(trace_id, created_at)`` for distinct roots (``parent_span_id = ''``)
        with ``lower <= created_at <= upper`` — the scan sweep's candidates.

        Cursor is ``created_at`` (CH ingest time), not ``start_time``, so long-
        running / late-exported traces can't slip behind the watermark. The
        lower bound is INCLUSIVE: the caller parks its watermark on the oldest
        still-unscanned created_at and must re-see that trace next tick. No
        ``FINAL``: dedup via ``GROUP BY`` (min created_at per trace), and the
        caller's anti-join makes a redundant dispatch a no-op.
        """
        if lower > upper:
            return []
        start_floor = lower - self._CANDIDATE_START_FLOOR
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, min(created_at) AS ca FROM spans "
            "WHERE project_id = %(p)s AND parent_span_id = '' "
            "  AND is_deleted = 0 "
            "  AND start_time >= %(start_floor)s "
            "  AND created_at >= %(lower)s AND created_at <= %(upper)s "
            "GROUP BY tid",
            parameters={
                "p": str(project_id),
                "lower": lower,
                "upper": upper,
                "start_floor": start_floor,
            },
        ).result_rows
        # CH hands back created_at tz-naive even for a DateTime64(_, 'UTC')
        # column; force UTC (as ch_now does) so the caller can compare it
        # against the tz-aware watermark without a naive/aware TypeError.
        return [(r[0], r[1] if r[1].tzinfo else r[1].replace(tzinfo=UTC)) for r in rows]

    # ─── Distinct end_users per trace (feed user-count rollup) ────────────────
    def distinct_end_users_by_trace_ids(
        self, trace_ids: list[str]
    ) -> dict[str, set[str]]:
        """Equivalent to:
            ObservationSpan.objects.filter(trace_id__in=trace_ids,
                                            end_user__isnull=False)
                .values("trace_id", "end_user_id").distinct()
            grouped into {trace_id: {end_user_id, ...}}

        Pushes DISTINCT into CH so we don't materialize all spans Python-
        side just to count distinct users. Empty trace_ids returns {}.
        """
        if not trace_ids:
            return {}
        # P3b step1.5 (DESIGN §3 / id_remap_sql): resolve each span's end_user_id
        # new→old through end_user_id_remap BEFORE the per-trace DISTINCT, so a
        # straddler's old + new spans collapse to ONE (the OLD curated) id per
        # trace. Pre-flip NO span matches a `new_id`, so the resolved id == the
        # span's own id and the distinct set is unchanged (gate B).
        remap_join = remap_left_join("rs.end_user_id", "end_user_id_remap")
        resolved_eu = resolved_id_expr("rs.end_user_id")
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, "
            f"toString({resolved_eu}) AS uid "
            "FROM ("
            "  SELECT trace_id, end_user_id FROM spans FINAL "
            "  WHERE trace_id IN %(tids)s AND is_deleted = 0 "
            "    AND end_user_id IS NOT NULL "
            ") AS rs "
            f"{remap_join} "
            f"GROUP BY toString(trace_id), toString({resolved_eu})",
            parameters={"tids": tuple(trace_ids)},
        ).result_rows
        out: dict[str, set[str]] = {tid: set() for tid in trace_ids}
        for tid, uid in rows:
            if uid and uid != "00000000-0000-0000-0000-000000000000":
                out[tid].add(uid)
        return out

    # ─── End-user metrics (tasks/session.py user rollups) ─────────────────────
    def aggregate_by_end_user(
        self,
        end_user_id: str,
        *,
        project_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """User-scoped roll-up used by session.py background tasks. Equivalent to:
            ObservationSpan.objects.filter(end_user=user[, optional ...])
                .aggregate(Sum(total_tokens), Sum(cost), Count("id"),
                           Min(start_time), Max(end_time))

        Plus distinct trace_count via COUNT(DISTINCT trace_id) in one CH pass.
        Returns zeros + None timestamps if no spans match (rather than {}).

        P3b step1.5 (DESIGN §3 / id_remap_sql): ``end_user_id`` here is the OLD
        curated id (callers pass ``str(EndUser.id)`` from PG). A cross-cutover
        straddler's NEW (deterministic-id) spans carry ``end_user_id = new_id``,
        so we resolve each span new→old through ``end_user_id_remap`` and match
        the OLD id on the RESOLVED value — old + new spans roll up as ONE user.
        Pre-flip NO span matches a ``new_id`` (every span is old-id), so the
        resolved id == the span's own id and this is a no-op (gate B). The non-
        user predicates (project / time / soft-delete) stay on the inner scan;
        only the identity match moves to the resolved layer.
        """
        inner_where = ["is_deleted = 0", "isNotNull(end_user_id)"]
        params: dict[str, Any] = {"uid": end_user_id}
        if project_id:
            inner_where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if since:
            inner_where.append("start_time >= %(since)s")
            params["since"] = since
        if until:
            inner_where.append("start_time <  %(until)s")
            params["until"] = until
        # P3b step1.5 — DUAL remap (DESIGN §3 / id_remap_sql): this read both
        # filters by the OLD curated end_user_id AND reports `uniqExact(
        # trace_session_id)`. A cross-cutover straddler would split on BOTH axes,
        # so resolve BOTH columns new→old. The two joins hang off the SAME inner
        # scan `rs` and so MUST carry DISTINCT aliases (the default `id_remap`
        # would collide) — `eu_remap` for end_user, `ts_remap` for session. The
        # session resolution makes `uniqExact` count the UNIFIED (old) session id,
        # so a straddler's old+new spans count as ONE session, not two. Pre-flip
        # NO span matches either `new_id`, so both resolved ids == own id and this
        # is a byte-identical no-op (gate B).
        eu_join = remap_left_join("rs.end_user_id", "end_user_id_remap", "eu_remap")
        ts_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_eu = resolved_id_expr("rs.end_user_id", "eu_remap")
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")
        rows = self._client.query(
            "SELECT count() AS n, "
            "uniqExact(trace_id) AS traces, "
            "uniqExact(trace_session_id) AS sessions, "
            "sum(prompt_tokens) AS pt, sum(completion_tokens) AS ct, "
            "sum(total_tokens) AS tt, sum(cost) AS cost, "
            "min(start_time) AS first_seen, max(end_time) AS last_seen "
            "FROM ("
            "  SELECT "
            f"    {resolved_eu} AS end_user_id, "
            f"    {resolved_ts} AS trace_session_id, "
            "    rs.trace_id AS trace_id, "
            "    rs.prompt_tokens AS prompt_tokens, "
            "    rs.completion_tokens AS completion_tokens, "
            "    rs.total_tokens AS total_tokens, rs.cost AS cost, "
            "    rs.start_time AS start_time, rs.end_time AS end_time "
            "  FROM ("
            "    SELECT end_user_id, trace_id, trace_session_id, prompt_tokens, "
            "           completion_tokens, total_tokens, cost, start_time, end_time "
            f"    FROM spans FINAL WHERE {' AND '.join(inner_where)}"
            "  ) AS rs "
            f"  {eu_join}"
            f"  {ts_join}"
            ") WHERE end_user_id = %(uid)s",
            parameters=params,
        ).result_rows
        if not rows:
            return {
                "span_count": 0,
                "trace_count": 0,
                "session_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
                "first_seen": None,
                "last_seen": None,
            }
        n, traces, sessions, pt, ct, tt, c, fs, ls = rows[0]
        return {
            "span_count": int(n or 0),
            "trace_count": int(traces or 0),
            "session_count": int(sessions or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
            "cost": float(c or 0.0),
            "first_seen": fs,
            "last_seen": ls,
        }

    # ─── Time-bucketed aggregates (graphs.py / monitor.py) ───────────────────
    def time_bucket_aggregate(
        self,
        project_id: str,
        *,
        interval: str = "hour",
        since: datetime,
        until: datetime,
        observation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Equivalent to:
            ObservationSpan.objects.filter(project_id=, start_time__range=...)
                .annotate(bucket=TruncHour/Day/Month("start_time"))
                .values("bucket")
                .annotate(span_count=Count, tokens=Sum, cost=Sum, latency=Avg)
                .order_by("bucket")

        `interval` ∈ {"hour", "day", "week", "month"}; mapped to the CH
        toStartOfX function. Returns one row per non-empty bucket.
        """
        # Codex wave-2 P2: align weekly bucket with the shared CH builder
        # convention (tracer/services/clickhouse/query_builders/base.py:139
        # uses `toMonday`). `toStartOfWeek` defaults to Sunday in CH 25.x;
        # inconsistent with the rest of the codebase.
        bucket_fn = {
            "hour": "toStartOfHour",
            "day": "toStartOfDay",
            "week": "toMonday",
            "month": "toStartOfMonth",
        }.get(interval)
        if bucket_fn is None:
            raise ValueError(
                f"interval={interval!r} not in {{'hour','day','week','month'}}"
            )
        where = [
            "is_deleted = 0",
            "project_id = %(pid)s",
            "start_time >= %(since)s",
            "start_time <  %(until)s",
        ]
        params: dict[str, Any] = {"pid": project_id, "since": since, "until": until}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        rows = self._client.query(
            f"SELECT {bucket_fn}(start_time) AS bucket, "
            "count() AS span_count, sum(total_tokens) AS tokens, "
            "sum(cost) AS cost, avg(latency_ms) AS latency_ms "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            f"GROUP BY {bucket_fn}(start_time) "
            f"ORDER BY {bucket_fn}(start_time)",
            parameters=params,
        ).result_rows
        return [
            {
                "bucket": bucket,
                "span_count": int(n or 0),
                "tokens": int(toks or 0),
                "cost": float(c or 0.0),
                "latency_ms": float(lat or 0.0),
            }
            for (bucket, n, toks, c, lat) in rows
        ]

    # ─── Generic filtered count (eval_task.py Q-object replacement) ──────────
    def count_with_filters(
        self,
        *,
        project_id: str | None = None,
        trace_ids: list[str] | None = None,
        observation_type: list[str] | str | None = None,
        session_id: str | list[str] | None = None,
        created_at_gte: datetime | None = None,
        created_at_range: tuple[datetime, datetime] | None = None,
    ) -> int:
        """Replaces ObservationSpan.objects.filter(<Q-object>).count() for
        the specific filter set produced by parsing_evaltask_filters().

        Equivalent to building a Q with those kwargs and counting. NOT
        a general-purpose Q→CH translator; intentionally narrow to the
        eval-task filter shape so behavior is testable in isolation.

        Codex wave-2 fixes (2026-05-26):
          • P1: created_at_* predicates target the CH `created_at` column
            (schema 002 has it — earlier impl wrongly mapped to start_time
            which broke eval-task rerun sampling parity with the PG path).
          • P2: trace_ids=[] now correctly returns 0 (an empty IN-list is
            "match nothing" per the Django Q semantic; the previous code
            silently dropped the filter, overcounting).
        """
        where = ["is_deleted = 0"]
        params: dict[str, Any] = {}
        session_join = ""
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            # Explicit empty-list = match nothing (matches Django
            # .filter(trace_id__in=[]).count() == 0).
            if len(trace_ids) == 0:
                return 0
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type:
            if isinstance(observation_type, list | tuple | set):
                if len(observation_type) == 0:
                    return 0
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            # P3b step1.5 (id_remap_sql): resolve `trace_session_id` new→old so a
            # straddler's new-id spans are counted under the OLD id (gate B no-op
            # pre-flip). Accept a scalar id OR the UI list shape — match via IN so
            # a list is never bound to a scalar `=` (CH TYPE_MISMATCH).
            session_join, _ = self._session_filter_remap()
            resolved_ts = resolved_id_expr("spans.trace_session_id", "ts_remap")
            sids = (
                list(session_id)
                if isinstance(session_id, list | tuple | set)
                else [session_id]
            )
            sids = [str(s) for s in sids if s]
            if not sids:
                return 0
            where.append(f"{resolved_ts} IN %(sids)s")
            params["sids"] = tuple(sids)
        if created_at_gte:
            # CH v2 spans table has a real `created_at` column (schema
            # 002_spans_v2.sql); use it directly for parity with the PG
            # Q-object path.
            where.append("created_at >= %(cag)s")
            params["cag"] = created_at_gte
        if created_at_range:
            where.append("created_at BETWEEN %(cr_s)s AND %(cr_e)s")
            params["cr_s"], params["cr_e"] = created_at_range
        rows = self._client.query(
            f"SELECT count() FROM spans FINAL {session_join} "
            f"WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        return int(rows[0][0]) if rows else 0

    # ─── Candidate session ids for a session-level eval (P3b step2, Slice C) ──
    def distinct_session_ids_with_filters(
        self,
        *,
        project_id: str | None = None,
        trace_ids: list[str] | None = None,
        observation_type: list[str] | str | None = None,
        session_id: str | list[str] | None = None,
        created_at_gte: datetime | None = None,
        created_at_range: tuple[datetime, datetime] | None = None,
    ) -> list[str]:
        """Distinct ``trace_session_id`` of the sessions whose spans match the
        eval-task filters — the CH re-derivation of ``process_eval_task``'s
        SESSIONS candidate set (Slice C, DESIGN §5 / PG_ORM_READ_MIGRATION).

        Replaces ``Trace.objects.filter(span matches).exclude(session=None)
        .values('session_id').distinct()`` — the ``Trace.session`` FK is ``None``
        post-flip (only spans carry ``trace_session_id``), so the PG derivation
        silently omits every net-new session. Same filter kwargs as
        ``count_with_filters`` (the eval-task filter shape, produced by
        ``parsing_evaltask_filters_for_ch``); selects off raw ``spans`` (NOT
        ``spans_per_session``, which carries no ``observation_type``/attribute
        columns to scope a filtered task on).

        Remap-aware (``id_remap_sql``): each span's ``trace_session_id`` is
        resolved new→old BEFORE the DISTINCT, so a cross-cutover straddler's old
        AND new id spans collapse to ONE survivor id (it appears once, and the
        id handed to the per-session dispatch is the canonical/old one). A
        net-new session's deterministic id has no remap row → resolves to itself
        → included. Pre-flip every span is old-id → no ``new_id`` match → the
        resolve is a no-op (gate B). ``project_id`` SHOULD be pinned (the spans
        table is multi-tenant) so the candidate set can't leak another tenant's
        sessions — mirrors ``trace_session_dict_reader.session_exists``.

        Limit (same as the CH filter translator): ``span_attributes_filters``
        are NOT handled here — the caller must fall back to the v2 FilterEngine
        path for that subset (or confirm the task carries none).
        """
        # The remap join is ALWAYS present (the SELECT resolves new→old); a
        # session_id filter, if any, reuses the SAME ``ts_remap`` alias rather
        # than adding a second join.
        session_join, _ = self._session_filter_remap()
        resolved_ts = resolved_id_expr("spans.trace_session_id", "ts_remap")
        # A session candidate must carry a session id (mirrors the
        # spans_per_session MV's ``WHERE trace_session_id IS NOT NULL``).
        where = ["is_deleted = 0", "trace_session_id IS NOT NULL"]
        params: dict[str, Any] = {}
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            # Explicit empty-list = match nothing (Django .filter(trace_id__in=[])).
            if len(trace_ids) == 0:
                return []
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type:
            if isinstance(observation_type, list | tuple | set):
                if len(observation_type) == 0:
                    return []
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            # Accept BOTH a scalar id AND the UI list shape (task forms send
            # `session_id` as a list, e.g. ["<uuid>"]). Resolve straddler new-id
            # spans (reuses the unconditional ts_remap join) and match via IN, so
            # a list never gets bound to a scalar `=` (which CH rejects:
            # "Cannot convert '['<uuid>']' to UUID", TYPE_MISMATCH).
            sids = (
                list(session_id)
                if isinstance(session_id, list | tuple | set)
                else [session_id]
            )
            sids = [str(s) for s in sids if s]
            if not sids:
                return []
            where.append(f"{resolved_ts} IN %(sids)s")
            params["sids"] = tuple(sids)
        if created_at_gte:
            where.append("created_at >= %(cag)s")
            params["cag"] = created_at_gte
        if created_at_range:
            where.append("created_at BETWEEN %(cr_s)s AND %(cr_e)s")
            params["cr_s"], params["cr_e"] = created_at_range
        rows = self._client.query(
            f"SELECT DISTINCT {resolved_ts} AS sid "
            f"FROM spans FINAL {session_join} "
            f"WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        return [str(r[0]) for r in rows]

    # ─── Distinct trace ids of ONE session (the session→trace_ids fix) ────────
    def session_trace_ids(self, project_id: str, session_id: str) -> list[str]:
        """Distinct ``trace_id`` of every (live) span belonging to ``session_id``
        — the CH re-derivation of the dead ``Trace.session`` reverse-FK walk
        (Slice D, DESIGN §5 / PG_ORM_READ_MIGRATION).

        Replaces ``Trace.objects.filter(session=session)`` /
        ``trace_session.traces.annotate(...)`` — post-flip the ``Trace.session``
        FK is ``None`` for EVERY trace (only spans carry ``trace_session_id``),
        so the PG walk returns EMPTY for ALL sessions (net-new AND historical).
        This reads the span fact directly: a span's ``trace_session_id`` is the
        single source of session membership.

        Remap-aware on BOTH sides (``id_remap_sql``), so a cross-cutover straddler
        reads as ONE session whose trace set is its OLD-id spans' traces UNION its
        NEW-id spans' traces:

          • The INPUT ``session_id`` is resolved new→old first
            (``_resolve_session_ids_to_canonical``): a straddler queried by EITHER
            its old curated id or its new deterministic id collapses to the one
            survivor (old) id; a net-new id (no remap row) resolves to itself; an
            already-old id resolves to itself.
          • Each SPAN's ``trace_session_id`` is then resolved new→old
            (``_session_filter_remap`` — the same join/predicate
            ``distinct_session_ids_with_filters`` uses) and matched against that
            resolved input id. So a straddler's NEW-id spans (carrying the new id)
            AND its OLD-id spans (carrying the old id) BOTH match the survivor →
            their traces are returned as ONE complete set. Pre-flip every span is
            old-id (no ``new_id`` match) → resolved == own id → byte-identical
            no-op (gate B).

        ``project_id`` is REQUIRED and pinned: the spans table is multi-tenant, so
        an unscoped read could surface another tenant's traces if a session id
        ever collided — mirrors ``session_exists`` /
        ``distinct_session_ids_with_filters``. Returns ``[]`` when either argument
        is falsy or the session has no live spans (the caller treats an empty set
        the same as the old empty queryset).

        The returned ids are the spans' RAW ``trace_id`` strings (a trace is never
        re-keyed by the id-remap — only the session surrogate is), suitable for a
        downstream ``Trace.objects.filter(id__in=…)``.
        """
        if not project_id or not session_id:
            return []
        # Resolve the INPUT id new→old so a straddler queried by either id, and a
        # net-new id, both compare survivor==survivor against the span side below.
        resolved_input = self._resolve_session_ids_to_canonical([str(session_id)])[
            str(session_id)
        ]
        # The span-side remap join + ``<resolved> = %(sid)s`` predicate (same as
        # the single-session filter ``distinct_session_ids_with_filters`` uses).
        session_join, session_pred = self._session_filter_remap()
        rows = self._client.query(
            f"SELECT DISTINCT toString(spans.trace_id) AS tid "
            f"FROM spans FINAL {session_join} "
            f"WHERE spans.project_id = %(p)s AND {session_pred} "
            f"  AND spans.is_deleted = 0",
            parameters={"p": str(project_id), "sid": resolved_input},
        ).result_rows
        return [str(r[0]) for r in rows]

    # ─── Group-by name with aggregates (error_analysis tool patterns) ────────
    def per_project_group_by_name(
        self,
        project_id: str,
        *,
        observation_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Equivalent to:
            ObservationSpan.objects.filter(project_id=[, observation_type=]
                                            [, start_time__gte=since])
                .values("name").annotate(usage_count=Count("id"),
                                          error_count=Count("id", filter=Q(status="error")),
                                          avg_latency=Avg("latency_ms"),
                                          total_cost=Sum("cost"))
                .order_by("-usage_count")[:limit]

        Used by AI-tool patterns (tool-usage analysis, retrieval patterns).
        `status_filter` ANDs into the WHERE (e.g. include only error rows).
        """
        where = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {"pid": project_id}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        if since:
            where.append("start_time >= %(since)s")
            params["since"] = since
        if until:
            where.append("start_time <  %(until)s")
            params["until"] = until
        if status_filter:
            where.append("status = %(stat)s")
            params["stat"] = status_filter
        rows = self._client.query(
            "SELECT name, count() AS usage_count, "
            "countIf(lower(status) = 'error') AS error_count, "
            "avg(latency_ms) AS avg_latency, sum(cost) AS total_cost "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY name "
            "ORDER BY count() DESC "
            f"LIMIT {int(limit)}",
            parameters=params,
        ).result_rows
        return [
            {
                "name": name,
                "usage_count": int(usage or 0),
                "error_count": int(errs or 0),
                "avg_latency": float(lat or 0.0),
                "total_cost": float(cost or 0.0),
            }
            for (name, usage, errs, lat, cost) in rows
        ]

    # ─── Parsing eval-task filters for CH (companion to PG Q-object) ─────────
    @staticmethod
    def parsing_evaltask_filters_for_ch(filters: dict) -> dict[str, Any]:
        """Companion to tracer/utils/eval_tasks.py::parsing_evaltask_filters.
        Same input shape — produces kwargs for count_with_filters() instead
        of a Django Q object.

        Returns a dict of kwargs ready for `**` into count_with_filters
        (or any other CH method that accepts the same subset).

        Limits: span_attributes_filters are NOT translated here (they need
        the FilterEngine v2 path). Caller should fall back to the v2 query
        builder for that subset.
        """
        out: dict[str, Any] = {}
        if not filters:
            return out
        if otype := filters.get("observation_type"):
            out["observation_type"] = otype
        if sid := filters.get("session_id"):
            # `session_id` may be a scalar OR the UI list shape (["<uuid>"]).
            # Preserve the list — do NOT str() it (str(["<uuid>"]) yields the
            # repr "['<uuid>']", which then fails the CH UUID bind). The
            # consumers (distinct_session_ids_with_filters / count_with_filters)
            # accept both shapes and match via IN.
            out["session_id"] = (
                [str(s) for s in sid]
                if isinstance(sid, list | tuple | set)
                else str(sid)
            )
        if dr := filters.get("date_range"):
            if isinstance(dr, list | tuple) and len(dr) == 2:
                out["created_at_range"] = (dr[0], dr[1])
        if cag := filters.get("created_at"):
            out["created_at_gte"] = cag
        if pid := filters.get("project_id"):
            out["project_id"] = str(pid)
        # `trace_ids` derived from `session_id` (Trace lookup) is the
        # caller's responsibility; this helper stays narrow.
        return out

    def stream_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        batch_size: int = 10_000,
    ) -> Iterator[list[str]]:
        """Stream a query's first column as strings, re-chunked to ``batch_size``
        so neither the client nor the caller holds the full result in memory — a
        large historical scan can be consumed in waves."""
        batch: list[str] = []
        with self._client.query_row_block_stream(
            sql, parameters=params or {}
        ) as stream:
            for block in stream:
                for row in block:
                    batch.append(str(row[0]))
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
        if batch:
            yield batch

    # ─── Wave-3 reader extensions (commit 2f7d55e14 follow-up) ────────────────

    def time_bucket_aggregate_with_filters(
        self,
        *,
        interval: str,
        since: datetime,
        until: datetime,
        project_id: str | None = None,
        trace_ids: list[str] | None = None,
        observation_type: list[str] | str | None = None,
        session_id: str | None = None,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Bucketed aggregation with the full eval-task filter shape PLUS
        error_count. Equivalent to:
            .filter(<filters>, start_time__range=(since, until))
                .annotate(bucket=TruncHour/Day/Week/Month("start_time"))
                .values("bucket")
                .annotate(span_count=Count, error_count=Count(filter=Q(status="error")),
                          tokens=Sum, cost=Sum, latency_ms=Avg)
                .order_by("bucket")

        Used by monitor.py / monitor_graphs.py time-window metrics where
        a stratified status filter is needed (ERROR_FREE_SESSION_RATES,
        per-provider error rates).
        """
        bucket_fn = {
            "hour": "toStartOfHour",
            "day": "toStartOfDay",
            "week": "toMonday",
            "month": "toStartOfMonth",
        }.get(interval)
        if bucket_fn is None:
            raise ValueError(
                f"interval={interval!r} not in {{'hour','day','week','month'}}"
            )
        where = ["is_deleted = 0", "start_time >= %(since)s", "start_time <  %(until)s"]
        params: dict[str, Any] = {"since": since, "until": until}
        session_join = ""
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            if len(trace_ids) == 0:
                return []
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type is not None:
            if isinstance(observation_type, list | tuple | set):
                # Codex wave-3 P2: empty list = match nothing (matches
                # the Django .filter(observation_type__in=[]) semantic).
                if len(observation_type) == 0:
                    return []
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            # P3b step1.5 (id_remap_sql): resolve `trace_session_id` new→old so a
            # straddler's new-id spans roll into this monitor window under the OLD
            # id (gate B no-op pre-flip). The bucketing is by time, not session.
            session_join, session_pred = self._session_filter_remap()
            where.append(session_pred)
            params["sid"] = session_id
        if status_filter:
            where.append("status = %(stat)s")
            params["stat"] = status_filter
        rows = self._client.query(
            f"SELECT {bucket_fn}(start_time) AS bucket, "
            "count() AS span_count, "
            "countIf(lower(status) = 'error') AS error_count, "
            "sum(total_tokens) AS tokens, sum(cost) AS cost, "
            "avg(latency_ms) AS latency_ms "
            f"FROM spans FINAL {session_join} "
            f"WHERE {' AND '.join(where)} "
            f"GROUP BY {bucket_fn}(start_time) "
            f"ORDER BY {bucket_fn}(start_time)",
            parameters=params,
        ).result_rows
        return [
            {
                "bucket": bucket,
                "span_count": int(n or 0),
                "error_count": int(errs or 0),
                "tokens": int(toks or 0),
                "cost": float(c or 0.0),
                "latency_ms": float(lat or 0.0),
            }
            for (bucket, n, errs, toks, c, lat) in rows
        ]

    def aggregate_window_with_filters(
        self,
        *,
        since: datetime,
        until: datetime,
        project_id: str | None = None,
        trace_ids: list[str] | None = None,
        observation_type: list[str] | str | None = None,
        session_id: str | None = None,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """Single-bucket variant of time_bucket_aggregate_with_filters. Used
        by monitor._get_metric_value paths that want one window-wide number.
        """
        where = ["is_deleted = 0", "start_time >= %(since)s", "start_time <  %(until)s"]
        params: dict[str, Any] = {"since": since, "until": until}
        session_join = ""
        if project_id:
            where.append("project_id = %(pid)s")
            params["pid"] = project_id
        if trace_ids is not None:
            if len(trace_ids) == 0:
                return {
                    "span_count": 0,
                    "error_count": 0,
                    "tokens": 0,
                    "cost": 0.0,
                    "latency_ms": 0.0,
                }
            where.append("trace_id IN %(tids)s")
            params["tids"] = tuple(trace_ids)
        if observation_type is not None:
            if isinstance(observation_type, list | tuple | set):
                # Codex wave-3 P2: empty list = match nothing.
                if len(observation_type) == 0:
                    return {
                        "span_count": 0,
                        "error_count": 0,
                        "tokens": 0,
                        "cost": 0.0,
                        "latency_ms": 0.0,
                    }
                where.append("observation_type IN %(otypes)s")
                params["otypes"] = tuple(observation_type)
            else:
                where.append("observation_type = %(otype)s")
                params["otype"] = observation_type
        if session_id:
            # P3b step1.5 (id_remap_sql): resolve `trace_session_id` new→old so a
            # straddler's new-id spans roll into this single-window number under
            # the OLD id (gate B no-op pre-flip).
            session_join, session_pred = self._session_filter_remap()
            where.append(session_pred)
            params["sid"] = session_id
        if status_filter:
            where.append("status = %(stat)s")
            params["stat"] = status_filter
        rows = self._client.query(
            "SELECT count() AS n, countIf(lower(status) = 'error') AS errs, "
            "sum(total_tokens) AS toks, sum(cost) AS cost, "
            "avg(latency_ms) AS lat "
            f"FROM spans FINAL {session_join} "
            f"WHERE {' AND '.join(where)}",
            parameters=params,
        ).result_rows
        n, errs, toks, c, lat = rows[0] if rows else (0, 0, 0, 0.0, 0.0)
        return {
            "span_count": int(n or 0),
            "error_count": int(errs or 0),
            "tokens": int(toks or 0),
            "cost": float(c or 0.0),
            "latency_ms": float(lat or 0.0),
        }

    def list_root_spans_by_trace_ids(
        self,
        trace_ids: list[str],
        *,
        include_heavy: bool = True,
        observation_type: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, CHSpan]:
        """For each trace_id, return the root span (parent_span_id = '').
        Picks the earliest by (start_time, id) on ties. Returns a dict so
        callers can do O(1) trace_id → root_span lookups without zipping
        result orders to input orders.

        Replaces patterns like:
            ObservationSpan.objects.filter(trace_id__in=, parent_span_id="",
                                            deleted=False)
                .order_by("trace_id", "start_time").distinct("trace_id")

        With ``include_heavy=False`` the fat JSON columns (attributes_extra /
        span_events / resource_attrs) come back as '' — opt out when only
        id/scalar columns are needed. Pass ``project_id`` to prune the scan to
        one project (avoids a full-table scan across every project's spans).
        """
        if not trace_ids:
            return {}
        select = _SELECT_SQL if include_heavy else _LEAN_SELECT_SQL
        where = [
            "is_deleted = 0",
            "trace_id IN %(tids)s",
            "parent_span_id = ''",
        ]
        params: dict[str, Any] = {"tids": tuple(trace_ids)}
        if project_id:
            # Qualify with the table name: the SELECT aliases
            # ``toString(project_id) AS project_id``, which otherwise shadows the
            # sort-key column here and defeats primary-key partition pruning.
            where.append("spans.project_id = %(pid)s")
            params["pid"] = str(project_id)
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        rows = self._client.query(
            f"SELECT {select} FROM spans FINAL "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY trace_id, start_time, id "
            "LIMIT 1 BY trace_id",
            parameters=params,
        ).result_rows
        return {span.trace_id: span for span in map(_row_to_chspan, rows)}

    def aggregate_by_session_ids(
        self,
        session_ids: list[str],
        *,
        project_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-session rollup across many sessions in one CH query.

        Replaces .filter(trace__session_id__in=session_ids)
                  .values("trace__session_id")
                  .annotate(Count, Sum(tokens), Sum(cost), distinct trace_count,
                            Min(start_time), Max(end_time))
        """
        if not session_ids:
            return {}
        # P3b step1.5 (DESIGN §3 / id_remap_sql): the input ``session_ids`` are
        # OLD curated ``TraceSession.id``s (still primary). A cross-cutover
        # straddler's NEW (deterministic-id) spans carry ``trace_session_id =
        # new_id``; resolve each span's ``trace_session_id`` new→old through
        # ``trace_session_id_remap`` and BOTH filter and GROUP BY the RESOLVED id
        # so old + new spans roll up under ONE (old) session key. The non-id
        # predicates (soft-delete / project) stay on the inner bare scan. Then —
        # mirroring ``end_user_dict_reader`` (commit 9e4ba4f7e: "result key stays
        # the caller's input id") — re-key the output by EVERY caller input id via
        # the remap, so a caller indexing by either the old id OR a new id gets the
        # unified row. Pre-flip NO span matches a ``new_id`` (resolved id == own
        # id) AND no input id is a new_id, so this is a byte-identical no-op (gate
        # B). ``resolved_id_expr`` is the zero-uuid-guarded map (NOT a COALESCE).
        # Resolve every caller input id to its CANONICAL (old) id first, then bind
        # the canonical set into the WHERE — the grouped `sid` IS the resolved
        # (canonical) value, so a caller passing a NEW id still selects the right
        # group (its spans resolve to the old id). An OLD id is its own canonical.
        canon = self._resolve_session_ids_to_canonical(session_ids)
        canonical_ids = {canon.get(str(s), str(s)) for s in session_ids if s}
        inner_where = ["is_deleted = 0"]
        params: dict[str, Any] = {"csids": tuple(canonical_ids)}
        if project_id:
            inner_where.append("project_id = %(pid)s")
            params["pid"] = project_id
        remap_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")
        rows = self._client.query(
            "SELECT toString(sid) AS sid, "
            "count() AS span_count, "
            "uniqExact(trace_id) AS traces_count, "
            "sum(total_tokens) AS tokens, sum(cost) AS cost, "
            "min(start_time) AS start_time, max(end_time) AS end_time "
            "FROM ("
            f"  SELECT {resolved_ts} AS sid, rs.trace_id AS trace_id, "
            "    rs.total_tokens AS total_tokens, rs.cost AS cost, "
            "    rs.start_time AS start_time, rs.end_time AS end_time "
            "  FROM ("
            "    SELECT trace_session_id, trace_id, total_tokens, cost, "
            "           start_time, end_time "
            f"    FROM spans FINAL WHERE {' AND '.join(inner_where)}"
            "  ) AS rs "
            f"  {remap_join}"
            ") "
            "WHERE sid IN %(csids)s "
            "GROUP BY sid",
            parameters=params,
        ).result_rows
        by_canonical = {
            str(sid): {
                "span_count": int(sc or 0),
                "traces_count": int(tc or 0),
                "tokens": int(toks or 0),
                "cost": float(c or 0.0),
                "start_time": st,
                "end_time": et,
            }
            for (sid, sc, tc, toks, c, st, et) in rows
        }
        # Re-key by each caller input id → its canonical group (an input OLD id is
        # its own canonical; a NEW id resolves to its old_id). Inputs with no spans
        # are simply absent, matching the committed dict-comprehension behaviour.
        out: dict[str, dict[str, Any]] = {}
        for sid in session_ids:
            metrics = by_canonical.get(canon.get(str(sid), str(sid)))
            if metrics is not None:
                out[str(sid)] = metrics
        return out

    def has_root_spans_of_type(self, project_id: str, observation_type: str) -> bool:
        """Equivalent to:
            ObservationSpan.objects.filter(project_id=, observation_type=,
                                            parent_span_id__isnull=True,
                                            deleted=False).exists()

        Used for has_voice_traces / has_<type>_traces gate checks.
        """
        rows = self._client.query(
            "SELECT 1 FROM spans FINAL "
            "WHERE is_deleted = 0 AND project_id = %(pid)s "
            "  AND observation_type = %(otype)s "
            "  AND parent_span_id = '' "
            "LIMIT 1",
            parameters={"pid": project_id, "otype": observation_type},
        ).result_rows
        return bool(rows)

    def per_project_version_metric_aggregate(
        self,
        project_version_ids: list[str],
        *,
        observation_type: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-project-version rollups across many versions in one query.

        Replaces patterns in tracer/views/project_version.py that do:
            .filter(project_version_id__in=).values("project_version_id")
            .annotate(avg_latency_root=Avg(...), avg_cost=Avg(...),
                      total_tokens=Sum(...), span_count=Count(...))

        For root-only metrics (e.g. average root-span latency), pass
        observation_type=None and the caller filters parent_span_id='';
        we expose both `avg_latency_root` (parent_span_id='') and overall
        `avg_latency` so callers pick which they need.
        """
        if not project_version_ids:
            return {}
        where = ["is_deleted = 0", "project_version_id IN %(pvids)s"]
        params: dict[str, Any] = {"pvids": tuple(project_version_ids)}
        if observation_type:
            where.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        rows = self._client.query(
            "SELECT toString(project_version_id) AS pvid, "
            "count() AS span_count, "
            "uniqExact(trace_id) AS trace_count, "
            "avgIf(latency_ms, parent_span_id = '') AS avg_latency_root, "
            "avg(latency_ms) AS avg_latency, "
            "avg(cost) AS avg_cost, "
            "sum(total_tokens) AS total_tokens, "
            "sum(cost) AS total_cost "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY toString(project_version_id)",
            parameters=params,
        ).result_rows
        return {
            pvid: {
                "span_count": int(sc or 0),
                "trace_count": int(tc or 0),
                "avg_latency_root": float(alr) if alr is not None else None,
                "avg_latency": float(al) if al is not None else None,
                "avg_cost": float(ac) if ac is not None else None,
                "total_tokens": int(tt or 0),
                "total_cost": float(tc2 or 0.0),
            }
            for (pvid, sc, tc, alr, al, ac, tt, tc2) in rows
        }

    def trace_aggregate_with_stddev(
        self,
        trace_ids: list[str],
        *,
        parent_only: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """Per-trace aggregate including stddev(latency_ms), used by
        project_version.py outlier-detection paths. `parent_only` restricts
        to root spans (matches the legacy ORM call's
        parent_span_id__isnull=True scoping).

        Equivalent to:
            .filter(trace_id__in=[, parent_span_id__isnull=parent_only]).values("trace_id")
            .annotate(latency=Avg, latency_stddev=StdDev, cost=Sum, count=Count)

        Codex wave-3 P1 (2026-05-26): Django's bare `StdDev()` is
        STDDEV_POP (population), not sample. Use CH `stddevPop()` so
        consumer z-scores match the legacy PG path. Callers computing
        outliers from this method's `stddev_latency_ms` see the same
        bands they did before.
        """
        if not trace_ids:
            return {}
        where = ["is_deleted = 0", "trace_id IN %(tids)s"]
        params: dict[str, Any] = {"tids": tuple(trace_ids)}
        if parent_only:
            where.append("parent_span_id = ''")
        rows = self._client.query(
            "SELECT toString(trace_id) AS tid, "
            "count() AS span_count, "
            "avg(latency_ms) AS avg_latency_ms, "
            "stddevPop(latency_ms) AS stddev_latency_ms, "
            "sum(cost) AS cost, sum(total_tokens) AS tokens "
            f"FROM spans FINAL WHERE {' AND '.join(where)} "
            "GROUP BY toString(trace_id)",
            parameters=params,
        ).result_rows
        return {
            tid: {
                "span_count": int(sc or 0),
                "avg_latency_ms": float(al) if al is not None else None,
                "stddev_latency_ms": float(sd) if sd is not None else None,
                "cost": float(c or 0.0),
                "tokens": int(t or 0),
            }
            for (tid, sc, al, sd, c, t) in rows
        }

    def prev_next_span_by_start_time(
        self,
        *,
        project_id: str,
        span_id: str,
        project_version_id: str | None = None,
        observation_type: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Return (prev_span_id, next_span_id) by start_time ordering within
        the project (and optionally project_version + observation_type) scope.
        None for boundary cases.

        Replaces the row-walking pattern in
        tracer/views/observation_span.py::get_trace_id_by_index_spans_as_*.
        """
        # Codex wave-3 P2 (2026-05-26): scope the anchor lookup itself to
        # the same project / version / type as the prev-next walk. An
        # unscoped anchor leaked one tenant's start_time into another
        # tenant's walk window (silent cross-org via foreign timestamp).
        anchor_where = ["id = %(sid)s", "is_deleted = 0", "project_id = %(pid)s"]
        anchor_params: dict[str, Any] = {"sid": span_id, "pid": project_id}
        if project_version_id:
            anchor_where.append("project_version_id = %(pvid)s")
            anchor_params["pvid"] = project_version_id
        if observation_type:
            anchor_where.append("observation_type = %(otype)s")
            anchor_params["otype"] = observation_type
        anchor_rows = self._client.query(
            f"SELECT start_time, id FROM spans FINAL "
            f"WHERE {' AND '.join(anchor_where)} LIMIT 1",
            parameters=anchor_params,
        ).result_rows
        if not anchor_rows:
            return (None, None)
        anchor_st, anchor_id = anchor_rows[0]
        where_base = ["is_deleted = 0", "project_id = %(pid)s"]
        params: dict[str, Any] = {
            "pid": project_id,
            "anchor_st": anchor_st,
            "anchor_id": anchor_id,
        }
        if project_version_id:
            where_base.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        if observation_type:
            where_base.append("observation_type = %(otype)s")
            params["otype"] = observation_type
        base = " AND ".join(where_base)
        prev = self._client.query(
            f"SELECT id FROM spans FINAL WHERE {base} "
            "  AND (start_time, id) < (%(anchor_st)s, %(anchor_id)s) "
            "ORDER BY start_time DESC, id DESC LIMIT 1",
            parameters=params,
        ).result_rows
        nxt = self._client.query(
            f"SELECT id FROM spans FINAL WHERE {base} "
            "  AND (start_time, id) > (%(anchor_st)s, %(anchor_id)s) "
            "ORDER BY start_time ASC, id ASC LIMIT 1",
            parameters=params,
        ).result_rows
        return (
            prev[0][0] if prev else None,
            nxt[0][0] if nxt else None,
        )

    def prev_next_trace_by_start_time(
        self,
        *,
        project_id: str,
        trace_id: str,
        project_version_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Return (prev_trace_id, next_trace_id) ordered by the trace's
        root-span start_time. Mirrors prev_next_span_by_start_time but at
        the trace level. Used by experiment-mode trace navigation.
        """
        # Codex wave-3 P2 (2026-05-26): scope the anchor lookup to the
        # same project (and optionally version) as the walk; an unscoped
        # anchor leaks foreign trace timestamps into the walk window.
        anchor_where = [
            "trace_id = %(tid)s",
            "is_deleted = 0",
            "parent_span_id = ''",
            "project_id = %(pid)s",
        ]
        anchor_params: dict[str, Any] = {"tid": trace_id, "pid": project_id}
        if project_version_id:
            anchor_where.append("project_version_id = %(pvid)s")
            anchor_params["pvid"] = project_version_id
        anchor_rows = self._client.query(
            "SELECT min(start_time) AS st FROM spans FINAL "
            f"WHERE {' AND '.join(anchor_where)}",
            parameters=anchor_params,
        ).result_rows
        if not anchor_rows or anchor_rows[0][0] is None:
            return (None, None)
        anchor_st = anchor_rows[0][0]
        where_base = [
            "is_deleted = 0",
            "project_id = %(pid)s",
            "parent_span_id = ''",
        ]
        params: dict[str, Any] = {
            "pid": project_id,
            "anchor_st": anchor_st,
            "anchor_tid": trace_id,
        }
        if project_version_id:
            where_base.append("project_version_id = %(pvid)s")
            params["pvid"] = project_version_id
        base = " AND ".join(where_base)
        # Codex wave-3 P1 (2026-05-26): `trace_id` is declared as String in
        # schema 002 (not UUID), so wrapping the param in toUUID() yielded
        # a type mismatch in the tuple compare. Compare String-to-String.
        prev = self._client.query(
            f"SELECT toString(trace_id) FROM spans FINAL WHERE {base} "
            "  AND (start_time, toString(trace_id)) < (%(anchor_st)s, %(anchor_tid)s) "
            "ORDER BY start_time DESC, toString(trace_id) DESC LIMIT 1",
            parameters=params,
        ).result_rows
        nxt = self._client.query(
            f"SELECT toString(trace_id) FROM spans FINAL WHERE {base} "
            "  AND (start_time, toString(trace_id)) > (%(anchor_st)s, %(anchor_tid)s) "
            "ORDER BY start_time ASC, toString(trace_id) ASC LIMIT 1",
            parameters=params,
        ).result_rows
        return (
            prev[0][0] if prev else None,
            nxt[0][0] if nxt else None,
        )

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
        # shape v1 consumers expect. Single source of truth: _ch_span_attributes.
        span_attributes = _ch_span_attributes(span)
        # tags / span_events come from CH as JSON strings; the serializer
        # returns them as Python objects.
        try:
            tags_parsed = json.loads(span.tags) if span.tags else []
        except json.JSONDecodeError:
            tags_parsed = []
        try:
            span_events_parsed = (
                json.loads(span.span_events) if span.span_events else []
            )
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
            "model_parameters": None,  # not on CH spans yet — see docstring
            "latency_ms": span.latency_ms,
            "org_id": span.org_id,
            "org_user_id": None,  # not on CH spans yet
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "total_tokens": span.total_tokens,
            "response_time": None,  # not on CH spans yet
            "eval_id": None,  # not on CH spans yet
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


def merge_span_attributes(
    attrs_string: dict[str, Any] | None,
    attrs_number: dict[str, Any] | None,
    attrs_bool: dict[str, Any] | None,
    attributes_extra: Any,
) -> dict[str, Any]:
    """Merge typed maps + ``attributes_extra`` into one ``span_attributes`` dict.

    Single source of truth. Maps first, ``attributes_extra`` (str or dict)
    overrides; bad JSON skipped; ``attrs_bool`` coerced to real booleans.
    """
    out: dict[str, Any] = {}
    out.update(attrs_string or {})
    out.update(attrs_number or {})
    out.update({k: bool(v) for k, v in (attrs_bool or {}).items()})
    extra = attributes_extra
    if isinstance(extra, str):
        try:
            extra = json.loads(extra) if extra else {}
        except json.JSONDecodeError:
            extra = {}
    if isinstance(extra, dict):
        out.update(extra)
    return out


def _ch_span_attributes(span: CHSpan) -> dict[str, Any]:
    """CHSpan adapter for :func:`merge_span_attributes`."""
    return merge_span_attributes(
        span.attrs_string, span.attrs_number, span.attrs_bool, span.attributes_extra
    )


def _ch_json_obj(raw: str, *, default: Any) -> Any:
    """``json.loads`` a CH JSON-string column, returning *default* (``{}`` / ``[]``)
    on null/empty/malformed input rather than raising — so one bad span row does
    not 500 the annotate-detail render. Parse failures are the caller's to log."""
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed


def chspan_to_annotation_source_dict(span: CHSpan) -> dict[str, Any]:
    """Map a :class:`CHSpan` to the ``observation_span`` content/preview dict the
    annotation selectors emit for a PG ``ObservationSpan`` — the single place that
    owns the CHSpan field renames (attrs_* merge, json.loads of the string columns,
    latency→response_time, start/end→created/updated, eval_attributes={}) so the
    preview and content branches never diverge. Pure (no IO)."""
    metadata = _ch_json_obj(span.metadata, default={})
    return {
        "type": "observation_span",
        "span_id": str(span.id),
        "trace_id": str(span.trace_id) if span.trace_id else None,
        "name": span.name or "",
        "observation_type": span.observation_type or "",
        "project_id": str(span.project_id) if span.project_id else None,
        "created_at": span.start_time,
        "updated_at": span.end_time,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "input": _maybe_json(span.input),
        "output": _maybe_json(span.output),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "events": _ch_json_obj(span.span_events, default=[]),
        "latency_ms": span.latency_ms,
        # CH has no response_time column; latency_ms is the only timing signal.
        "response_time_ms": span.latency_ms,
        "model": span.model or None,
        "provider": span.provider or None,
        "cost": span.cost,
        "prompt_tokens": span.prompt_tokens,
        "completion_tokens": span.completion_tokens,
        "total_tokens": span.total_tokens,
        "status": span.status or None,
        "status_message": span.status_message or None,
        "tags": _ch_json_obj(span.tags, default=[]),
        "span_attributes": _ch_span_attributes(span),
        "resource_attributes": _ch_json_obj(span.resource_attrs, default={}),
        # empty (not omitted) for PG-branch shape parity — CH has no per-eval dict
        "eval_attributes": {},
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
