"""Finite latest-state ClickHouse reads for one trace-detail response.

The production ``spans`` table is direct-write ReplacingMergeTree data. A
logical span version is identified by ``(project_id, trace_id, id,
start_time)``; a bare span id or ``FINAL`` is not a safe identity. This module
first discovers a bounded set of light physical identities, then hydrates only
those exact identities in small partition-pruned batches. Eval and annotation
reads are scoped to the same trace/span candidates and resolve versions with
``argMax`` instead of merging an unbounded table with ``FINAL``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_error,
    is_read_budget_error,
)

_MAX_ACCESSIBLE_PROJECTS = 4096
_MAX_PHYSICAL_SPANS = 1000
_CONTENT_BATCH_SIZE = 200
_MAX_EVAL_ROWS = 5000
_MAX_ANNOTATION_ROWS = 5000
MAX_TRACE_DETAIL_EVAL_CONFIGS = 4096
_DETAIL_DEADLINE_MS = 9_500
_QUERY_TIMEOUT_MS = 9_500
_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": 8192,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
}


class TraceDetailReadUnavailable(RuntimeError):
    """A finite detail read could not prove a complete response."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TraceDetailNotFound(LookupError):
    """No live trace exists inside the authorized project scope."""


@dataclass(frozen=True)
class PhysicalSpanIdentity:
    project_id: str
    trace_id: str
    span_id: str
    start_time: datetime

    @property
    def start_us(self) -> int:
        value = (
            self.start_time.replace(tzinfo=UTC)
            if self.start_time.tzinfo is None
            else self.start_time.astimezone(UTC)
        )
        delta = value - datetime(1970, 1, 1, tzinfo=UTC)
        return (
            delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
        )


class _Result(Protocol):
    data: list[dict[str, Any]]


class _Analytics(Protocol):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> _Result: ...


@dataclass(frozen=True)
class TraceDetailRead:
    project_id: str
    spans: tuple[dict[str, Any], ...]
    eval_config_ids: tuple[str, ...]
    evals: tuple[dict[str, Any], ...]
    annotations: tuple[dict[str, Any], ...]
    query_count: int
    elapsed_ms: float


class TraceDetailReadBuilder:
    TABLE = "spans"
    SCORE_TABLE = "model_hub_score"

    def __init__(self, *, project_ids: list[str], trace_id: str) -> None:
        normalized = tuple(dict.fromkeys(str(value) for value in project_ids if value))
        if not normalized:
            raise TraceDetailNotFound
        if len(normalized) > _MAX_ACCESSIBLE_PROJECTS:
            raise TraceDetailReadUnavailable("project_scope_too_large")
        self.project_ids = normalized
        self.trace_id = str(trace_id)

    def build_identity_query(self) -> tuple[str, dict[str, Any]]:
        return (
            f"""
            SELECT
                toString(project_id) AS project_id,
                trace_id,
                id AS span_id,
                start_time,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE project_id IN %(detail_project_ids)s
              AND trace_id = %(detail_trace_id)s
            GROUP BY project_id, trace_id, id, start_time
            ORDER BY start_time ASC, span_id ASC
            LIMIT {_MAX_PHYSICAL_SPANS + 1}
            """,
            {
                "detail_project_ids": self.project_ids,
                "detail_trace_id": self.trace_id,
            },
        )

    def build_span_anchor_query(self, span_id: str) -> tuple[str, dict[str, Any]]:
        """Resolve one span id inside the authorized project scope.

        Span ids are not a globally unique physical identity.  Collapse every
        candidate identity to its latest tombstone state and keep a sentinel so
        an id collision can never become an arbitrary ``LIMIT 1`` choice.
        """

        return (
            f"""
            SELECT
                toString(project_id) AS project_id,
                trace_id,
                id AS span_id,
                start_time,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE project_id IN %(detail_project_ids)s
              AND id = %(detail_span_id)s
            GROUP BY project_id, trace_id, id, start_time
            ORDER BY start_time DESC, trace_id DESC, project_id DESC
            LIMIT 3
            """,
            {
                "detail_project_ids": self.project_ids,
                "detail_span_id": str(span_id),
            },
        )

    def build_content_query(
        self, identities: list[PhysicalSpanIdentity]
    ) -> tuple[str, dict[str, Any]]:
        if not identities:
            return "", {}
        if len(identities) > _CONTENT_BATCH_SIZE:
            raise ValueError("trace detail content batch exceeds bounded limit")
        params = {
            "detail_project_ids": tuple(
                dict.fromkeys(identity.project_id for identity in identities)
            ),
            "detail_trace_id": self.trace_id,
            "detail_span_ids": tuple(
                dict.fromkeys(identity.span_id for identity in identities)
            ),
            "detail_span_dates": tuple(
                dict.fromkeys(identity.start_time.date() for identity in identities)
            ),
            "detail_span_identities": tuple(
                (
                    identity.project_id,
                    identity.trace_id,
                    identity.span_id,
                    identity.start_us,
                )
                for identity in identities
            ),
            "detail_content_limit": len(identities) + 1,
        }
        return (
            f"""
            SELECT
                toString(grouped_project_id) AS project_id,
                grouped_id AS id,
                grouped_trace_id AS trace_id,
                latest_parent_span_id AS parent_span_id,
                latest_name AS name,
                latest_observation_type AS observation_type,
                grouped_start_time AS start_time,
                latest_end_time AS end_time,
                latest_input AS input,
                latest_output AS output,
                latest_model AS model,
                latest_latency_ms AS latency_ms,
                latest_prompt_tokens AS prompt_tokens,
                latest_completion_tokens AS completion_tokens,
                latest_total_tokens AS total_tokens,
                latest_cost AS cost,
                latest_status AS status,
                latest_status_message AS status_message,
                latest_tags AS tags,
                latest_span_events AS span_events,
                latest_provider AS provider,
                latest_attributes_extra AS span_attributes,
                latest_project_version_id AS project_version_id,
                latest_custom_eval_config_id AS custom_eval_config_id,
                toString(latest_trace_session_id) AS trace_session_id,
                toJSONString(latest_metadata) AS metadata_json,
                latest_attrs_string AS attrs_string,
                latest_attrs_number AS attrs_number,
                latest_attrs_bool AS attrs_bool
            FROM (
                SELECT
                    project_id AS grouped_project_id,
                    trace_id AS grouped_trace_id,
                    id AS grouped_id,
                    start_time AS grouped_start_time,
                    argMax(tuple(parent_span_id), _version).1
                        AS latest_parent_span_id,
                    argMax(name, _version) AS latest_name,
                    argMax(observation_type, _version) AS latest_observation_type,
                    argMax(tuple(end_time), _version).1 AS latest_end_time,
                    argMax(input, _version) AS latest_input,
                    argMax(output, _version) AS latest_output,
                    argMax(model, _version) AS latest_model,
                    argMax(latency_ms, _version) AS latest_latency_ms,
                    argMax(prompt_tokens, _version) AS latest_prompt_tokens,
                    argMax(completion_tokens, _version)
                        AS latest_completion_tokens,
                    argMax(total_tokens, _version) AS latest_total_tokens,
                    argMax(cost, _version) AS latest_cost,
                    argMax(status, _version) AS latest_status,
                    argMax(status_message, _version) AS latest_status_message,
                    argMax(tags, _version) AS latest_tags,
                    argMax(span_events, _version) AS latest_span_events,
                    argMax(provider, _version) AS latest_provider,
                    argMax(tuple(attributes_extra), _version).1
                        AS latest_attributes_extra,
                    argMax(tuple(project_version_id), _version).1
                        AS latest_project_version_id,
                    argMax(tuple(custom_eval_config_id), _version).1
                        AS latest_custom_eval_config_id,
                    argMax(tuple(trace_session_id), _version).1
                        AS latest_trace_session_id,
                    argMax(metadata, _version) AS latest_metadata,
                    argMax(attrs_string, _version) AS latest_attrs_string,
                    argMax(attrs_number, _version) AS latest_attrs_number,
                    argMax(attrs_bool, _version) AS latest_attrs_bool,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM {self.TABLE}
                PREWHERE project_id IN %(detail_project_ids)s
                  AND trace_id = %(detail_trace_id)s
                  AND id IN %(detail_span_ids)s
                WHERE toDate(start_time) IN %(detail_span_dates)s
                  AND (
                      toString(project_id), trace_id, id,
                      toUnixTimestamp64Micro(start_time)
                  ) IN %(detail_span_identities)s
                GROUP BY project_id, trace_id, id, start_time
            ) AS latest_physical_spans
            WHERE latest_is_deleted = 0
            ORDER BY grouped_start_time ASC, grouped_id ASC
            LIMIT %(detail_content_limit)s
            """,
            params,
        )

    def build_eval_query(
        self,
        *,
        project_id: str,
        span_ids: list[str],
        eval_config_ids: Iterable[str],
    ) -> tuple[str, dict[str, Any]]:
        normalized_config_ids = tuple(
            dict.fromkeys(str(value) for value in eval_config_ids if value)
        )
        if not span_ids or not normalized_config_ids:
            return "", {}
        if len(normalized_config_ids) > MAX_TRACE_DETAIL_EVAL_CONFIGS:
            raise TraceDetailReadUnavailable("eval_config_scope_too_large")
        table, _ = eval_logger_source()
        v2 = table.endswith("_v2")
        version = "_version" if v2 else "_peerdb_version"
        status_aggregate = "'completed'" if v2 else f"argMax(status, {version})"
        skipped_reason_aggregate = (
            "CAST(NULL AS Nullable(String))"
            if v2
            else f"argMax(tuple(skipped_reason), {version}).1"
        )
        deleted = (
            f"argMax(is_deleted, {version})"
            if v2
            else (
                f"greatest(argMax(_peerdb_is_deleted, {version}), "
                f"coalesce(argMax(deleted, {version}), 0))"
            )
        )
        return (
            f"""
            SELECT
                latest_span_id AS span_id,
                toString(latest_eval_config_id) AS eval_config_id,
                latest_output_float AS output_float,
                latest_output_bool AS output_bool,
                latest_output_str AS output_str,
                latest_output_str_list AS output_str_list,
                latest_eval_explanation AS eval_explanation,
                latest_error AS error,
                latest_error_message AS error_message,
                latest_status AS status,
                latest_skipped_reason AS skipped_reason
            FROM (
                SELECT
                    id AS grouped_eval_id,
                    argMax(observation_span_id, {version}) AS latest_span_id,
                    argMax(custom_eval_config_id, {version})
                        AS latest_eval_config_id,
                    argMax(tuple(output_float), {version}).1 AS latest_output_float,
                    argMax(tuple(output_bool), {version}).1 AS latest_output_bool,
                    argMax(tuple(output_str), {version}).1 AS latest_output_str,
                    argMax(tuple(output_str_list), {version}).1
                        AS latest_output_str_list,
                    argMax(tuple(eval_explanation), {version}).1
                        AS latest_eval_explanation,
                    argMax(error, {version}) AS latest_error,
                    argMax(tuple(error_message), {version}).1
                        AS latest_error_message,
                    {status_aggregate} AS latest_status,
                    {skipped_reason_aggregate} AS latest_skipped_reason,
                    {deleted} AS latest_is_deleted
                FROM {table}
                PREWHERE trace_id = %(detail_eval_trace_id)s
                  AND observation_span_id IN %(detail_eval_span_ids)s
                  AND toString(custom_eval_config_id)
                        IN %(detail_eval_config_ids)s
                GROUP BY id
            ) AS latest_trace_evals
            WHERE latest_is_deleted = 0
              AND toString(latest_eval_config_id)
                    IN %(detail_eval_config_ids)s
            ORDER BY span_id ASC, eval_config_id ASC
            LIMIT {_MAX_EVAL_ROWS + 1}
            """,
            {
                "detail_eval_trace_id": self.trace_id,
                "detail_eval_span_ids": tuple(dict.fromkeys(span_ids)),
                "detail_eval_config_ids": normalized_config_ids,
                # Retained for executor auditing/log correlation and to make
                # the tenant proof explicit at the call boundary; the eval
                # table itself has no tracer-project column.
                "detail_project_id": project_id,
            },
        )

    def build_annotation_query(
        self, *, project_id: str, span_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        if not span_ids:
            return "", {}
        if str(project_id) not in self.project_ids:
            raise TraceDetailReadUnavailable("annotation_project_out_of_scope")
        return (
            f"""
            SELECT
                latest_span_id AS span_id,
                toString(latest_label_id) AS label_id,
                latest_value AS value
            FROM (
                SELECT
                    id AS grouped_score_id,
                    argMax(observation_span_id, _peerdb_version)
                        AS latest_span_id,
                    argMax(label_id, _peerdb_version) AS latest_label_id,
                    argMax(value, _peerdb_version) AS latest_value,
                    greatest(
                        argMax(_peerdb_is_deleted, _peerdb_version),
                        argMax(deleted, _peerdb_version)
                    ) AS latest_is_deleted
                FROM {self.SCORE_TABLE}
                PREWHERE tracer_project_id = %(detail_annotation_project_id)s
                  AND trace_id = %(detail_annotation_trace_id)s
                  AND observation_span_id IN %(detail_annotation_span_ids)s
                GROUP BY id
            ) AS latest_trace_annotations
            WHERE latest_is_deleted = 0
            ORDER BY span_id ASC, label_id ASC
            LIMIT {_MAX_ANNOTATION_ROWS + 1}
            """,
            {
                "detail_annotation_project_id": project_id,
                "detail_annotation_trace_id": self.trace_id,
                "detail_annotation_span_ids": tuple(dict.fromkeys(span_ids)),
            },
        )


def read_trace_detail(
    *,
    analytics: _Analytics,
    project_ids: list[str],
    trace_id: str,
    eval_config_ids_resolver: Callable[[str], Iterable[str]] | None = None,
    include_annotations: bool = True,
    deadline_ms: int = _DETAIL_DEADLINE_MS,
) -> TraceDetailRead:
    """Execute the finite identity/content/eval/annotation read plan."""

    builder = TraceDetailReadBuilder(project_ids=project_ids, trace_id=trace_id)
    started = monotonic()
    deadline = started + deadline_ms / 1000
    query_count = 0

    def execute(
        query: str,
        params: dict[str, Any],
        *,
        max_result_rows: int,
    ) -> list[dict[str, Any]]:
        nonlocal query_count
        remaining_ms = int((deadline - monotonic()) * 1000)
        if remaining_ms < 25:
            raise TraceDetailReadUnavailable("deadline_exceeded")
        try:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=min(_QUERY_TIMEOUT_MS, remaining_ms),
                settings={**_READ_SETTINGS, "max_result_rows": max_result_rows},
            )
        except Exception as exc:
            if is_read_budget_error(exc):
                raise TraceDetailReadUnavailable("read_budget_exceeded") from None
            if is_clickhouse_query_error(exc):
                raise TraceDetailReadUnavailable("clickhouse_query_failed") from None
            raise
        query_count += 1
        return list(result.data or [])

    identity_query, identity_params = builder.build_identity_query()
    identity_rows = execute(
        identity_query,
        identity_params,
        max_result_rows=_MAX_PHYSICAL_SPANS + 1,
    )
    if len(identity_rows) > _MAX_PHYSICAL_SPANS:
        raise TraceDetailReadUnavailable("span_limit_exceeded")

    identities = [
        PhysicalSpanIdentity(
            project_id=str(row.get("project_id") or ""),
            trace_id=str(row.get("trace_id") or ""),
            span_id=str(row.get("span_id") or ""),
            start_time=row["start_time"],
        )
        for row in identity_rows
        if not row.get("latest_is_deleted")
        and row.get("project_id")
        and row.get("trace_id")
        and row.get("span_id")
        and isinstance(row.get("start_time"), datetime)
    ]
    if not identities:
        raise TraceDetailNotFound

    projects = {identity.project_id for identity in identities}
    if len(projects) != 1:
        raise TraceDetailReadUnavailable("ambiguous_trace_identity")
    duplicate_ids = [
        span_id
        for span_id, count in Counter(
            identity.span_id for identity in identities
        ).items()
        if count > 1
    ]
    if duplicate_ids:
        # Parent links and the public response use a bare span id. Returning
        # either live physical span would silently attach children/evals to the
        # wrong node, so fail closed instead of guessing.
        raise TraceDetailReadUnavailable("ambiguous_span_identity")

    content_rows: list[dict[str, Any]] = []
    for offset in range(0, len(identities), _CONTENT_BATCH_SIZE):
        batch = identities[offset : offset + _CONTENT_BATCH_SIZE]
        query, params = builder.build_content_query(batch)
        rows = execute(query, params, max_result_rows=len(batch) + 1)
        if len(rows) != len(batch):
            raise TraceDetailReadUnavailable("incomplete_content_replay")
        content_rows.extend(rows)

    project_id = next(iter(projects))
    span_ids = [identity.span_id for identity in identities]

    # The eval table has no tracer-project column. Resolve the selected
    # project's authorized config IDs only after CH has proved the trace's
    # project identity, then bind those IDs into the eval query. With no
    # resolver/configs the read fails closed to an empty eval collection.
    eval_config_ids = tuple(
        dict.fromkeys(
            str(value)
            for value in (
                eval_config_ids_resolver(project_id)
                if eval_config_ids_resolver is not None
                else ()
            )
            if value
        )
    )
    if len(eval_config_ids) > MAX_TRACE_DETAIL_EVAL_CONFIGS:
        raise TraceDetailReadUnavailable("eval_config_scope_too_large")
    eval_query, eval_params = builder.build_eval_query(
        project_id=project_id,
        span_ids=span_ids,
        eval_config_ids=eval_config_ids,
    )
    eval_rows = (
        execute(
            eval_query,
            eval_params,
            max_result_rows=_MAX_EVAL_ROWS + 1,
        )
        if eval_query
        else []
    )
    if len(eval_rows) > _MAX_EVAL_ROWS:
        raise TraceDetailReadUnavailable("eval_limit_exceeded")

    annotation_rows: list[dict[str, Any]] = []
    if include_annotations:
        annotation_query, annotation_params = builder.build_annotation_query(
            project_id=project_id, span_ids=span_ids
        )
        annotation_rows = execute(
            annotation_query,
            annotation_params,
            max_result_rows=_MAX_ANNOTATION_ROWS + 1,
        )
    if len(annotation_rows) > _MAX_ANNOTATION_ROWS:
        raise TraceDetailReadUnavailable("annotation_limit_exceeded")

    return TraceDetailRead(
        project_id=project_id,
        spans=tuple(content_rows),
        eval_config_ids=eval_config_ids,
        evals=tuple(eval_rows),
        annotations=tuple(annotation_rows),
        query_count=query_count,
        elapsed_ms=(monotonic() - started) * 1000,
    )


def read_span_detail(
    *,
    analytics: _Analytics,
    project_ids: list[str],
    span_id: str,
    eval_config_ids_resolver: Callable[[str], Iterable[str]] | None = None,
    include_annotations: bool = True,
    deadline_ms: int = _DETAIL_DEADLINE_MS,
) -> TraceDetailRead:
    """Resolve a public span id, then reuse the exact trace-detail replay.

    The light anchor and the trace replay share one request wall budget.  A
    tombstoned id is absent; multiple live physical identities are ambiguous
    and fail closed rather than leaking or selecting another project's row.
    """

    started = monotonic()
    deadline = started + deadline_ms / 1000
    anchor_builder = TraceDetailReadBuilder(project_ids=project_ids, trace_id="")
    anchor_query, anchor_params = anchor_builder.build_span_anchor_query(span_id)
    remaining_ms = int((deadline - monotonic()) * 1000)
    if remaining_ms < 25:
        raise TraceDetailReadUnavailable("deadline_exceeded")
    try:
        anchor_result = analytics.execute_ch_query(
            anchor_query,
            anchor_params,
            timeout_ms=min(_QUERY_TIMEOUT_MS, remaining_ms),
            settings={**_READ_SETTINGS, "max_result_rows": 3},
        )
    except Exception as exc:
        if is_read_budget_error(exc):
            raise TraceDetailReadUnavailable("read_budget_exceeded") from None
        if is_clickhouse_query_error(exc):
            raise TraceDetailReadUnavailable("clickhouse_query_failed") from None
        raise

    anchor_rows = list(anchor_result.data or [])
    if len(anchor_rows) > 2:
        raise TraceDetailReadUnavailable("span_anchor_limit_exceeded")
    live_rows = [row for row in anchor_rows if not row.get("latest_is_deleted")]
    if not live_rows:
        raise TraceDetailNotFound
    if len(live_rows) != 1:
        raise TraceDetailReadUnavailable("ambiguous_span_identity")

    anchor = live_rows[0]
    project_id = str(anchor.get("project_id") or "")
    trace_id = str(anchor.get("trace_id") or "")
    if (
        not project_id
        or not trace_id
        or str(anchor.get("span_id") or "") != str(span_id)
    ):
        raise TraceDetailReadUnavailable("invalid_span_anchor")

    remaining_ms = int((deadline - monotonic()) * 1000)
    if remaining_ms < 25:
        raise TraceDetailReadUnavailable("deadline_exceeded")
    detail = read_trace_detail(
        analytics=analytics,
        project_ids=[project_id],
        trace_id=trace_id,
        eval_config_ids_resolver=eval_config_ids_resolver,
        include_annotations=include_annotations,
        deadline_ms=remaining_ms,
    )
    matches = [row for row in detail.spans if str(row.get("id") or "") == str(span_id)]
    if not matches:
        raise TraceDetailNotFound
    if len(matches) != 1:
        raise TraceDetailReadUnavailable("ambiguous_span_identity")
    return detail


__all__ = [
    "PhysicalSpanIdentity",
    "MAX_TRACE_DETAIL_EVAL_CONFIGS",
    "TraceDetailNotFound",
    "TraceDetailRead",
    "TraceDetailReadBuilder",
    "TraceDetailReadUnavailable",
    "read_span_detail",
    "read_trace_detail",
]
