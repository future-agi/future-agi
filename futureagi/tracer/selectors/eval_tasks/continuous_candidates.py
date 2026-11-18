"""Bounded arrival/change candidates for continuous evaluation tasks.

Candidate discovery is deliberately separate from filter membership.  The
arrival window answers only *which logical entities may have changed*; the
row resolver then replays those candidates against their complete latest
state.  This prevents a root and its matching child/eval/annotation from
having to arrive in the same polling slice.

All statements in this module are SELECT-only and execute through the CH25
read service, whose client forces ``readonly=2``.  Every result is buffered
under a shared deadline/cap before it is returned.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence, Sized
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from tracer.models.eval_task import RowType
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_error,
    is_clickhouse_query_size_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    NIL_UUID,
    resolved_id_expr,
    survivor_map_subquery,
)

_MAX_PUBLIC_CANDIDATES = 10_000
_MAX_QUERY_ATTEMPTS = 128
_WORKFLOW_MAX_QUERY_ATTEMPTS = 32_768
_WORKFLOW_MAX_RESULT_BYTES = 128 * 1024 * 1024
_WORKFLOW_PAGE_SIZE = 1_000
_SAMPLE_CHUNK = 1_000
# Three identity IN lists must fit ClickHouse's default 256 KiB parser cap.
_SPAN_IDENTITY_BATCH_SIZE = 1_000
_CANDIDATE_ID_BATCH_SIZE = 1_000
_RELATION_PAGE_SIZE = 200
_MAX_STATEMENT_TIMEOUT_MS = 30_000
_READ_SETTINGS = {
    "max_execution_time": 30,
    "max_threads": 2,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

_BatchInput = TypeVar("_BatchInput")
_BatchResult = TypeVar("_BatchResult", bound=Hashable)


class ContinuousCandidateReadError(RuntimeError):
    """The complete arrival candidate set could not be proven in budget."""


class ContinuousCandidateOverflow(ContinuousCandidateReadError):
    """More candidates changed than can safely be buffered in one pass."""


class ContinuousCandidateQueryCapExceeded(ContinuousCandidateReadError):
    """The deterministic statement-count envelope cannot prove the window."""


@dataclass(frozen=True)
class ContinuousCandidates:
    """Classifier keys plus sampled public row identities.

    For trace/session/span tasks the two sets use the same logical identity.
    Voice classifiers are trace-scoped, while their public entry identity is
    the root conversation span id, so both representations are retained.
    """

    classifier_ids: tuple[str, ...]
    public_ids: tuple[str, ...]
    covered_through: datetime | None = None


@dataclass
class _ReadBudget:
    deadline: float
    attempts: int = 0
    workflow_row_type: RowType | None = None
    result_bytes: int = 0

    def timeout_ms(self) -> int:
        if self.workflow_row_type is not None:
            if self.attempts >= _WORKFLOW_MAX_QUERY_ATTEMPTS:
                raise ContinuousCandidateReadError(
                    "continuous workflow query budget exceeded"
                )
        elif self.attempts >= _MAX_QUERY_ATTEMPTS:
            raise ContinuousCandidateQueryCapExceeded("continuous query cap exceeded")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ContinuousCandidateReadError("continuous read deadline exceeded")
        self.attempts += 1
        return min(_MAX_STATEMENT_TIMEOUT_MS, max(1, int(remaining * 1000)))

    def record_rows(self, rows: list[dict[str, Any]]) -> None:
        if self.workflow_row_type is None:
            return
        # Bound cumulative estimated result bytes, including duplicates and
        # classifier witnesses, rather than a fixed count of source identities.
        self.result_bytes += sum(len(repr(row).encode("utf-8")) for row in rows)
        if self.result_bytes > _WORKFLOW_MAX_RESULT_BYTES:
            raise ContinuousCandidateReadError(
                "continuous workflow result budget exceeded"
            )


class ContinuousWorkflowReadBudget(_ReadBudget):
    """One finite budget shared by paged discovery, classification and sampling."""

    def __init__(self, *, row_type: str, deadline_seconds: float) -> None:
        super().__init__(
            time.monotonic() + deadline_seconds,
            workflow_row_type=RowType(row_type),
        )


def discover_continuous_candidates(
    analytics: Any,
    *,
    project_id: str,
    row_type: str,
    filters: list[dict[str, Any]],
    floor: datetime,
    ceiling: datetime,
    salt: str,
    sampling_rate: float,
    deadline_seconds: float,
    minimum_ceiling: datetime | None = None,
    workflow_budget: ContinuousWorkflowReadBudget | None = None,
) -> ContinuousCandidates:
    """Return a complete, sampled candidate set for a proven arrival window.

    Span versions are the base trigger.  Eval and annotation tables are read
    only when the active selection depends on those domains.  Their foreign
    references are always projected back through project-scoped spans, because
    score/eval project identifiers are not a reliable tracer tenant boundary.

    A stale continuous cursor can cover more identities than fit in one
    buffered proof. When ``minimum_ceiling`` is supplied, overflow halves the
    requested window until one complete prefix fits. Every probe shares the
    original deadline/query budget. The caller chooses the minimum so accepting
    a prefix can still move its durable watermark; no safe prefix means the
    original deterministic overflow escapes without returning partial state.

    The resolver can retry with ``workflow_budget`` when a dense window cannot
    fit that fast path. Workflow discovery exhausts identity keyset pages and
    collapses unused span fields before buffering; classification and sampling
    consume the same finite workflow budget before anything is materialized.
    """

    if floor >= ceiling or sampling_rate <= 0:
        return ContinuousCandidates((), (), ceiling)
    if row_type not in {
        RowType.SPANS,
        RowType.TRACES,
        RowType.VOICE_CALLS,
        RowType.SESSIONS,
    }:
        raise ValueError(f"Unsupported row_type: {row_type!r}")

    budget = workflow_budget or _ReadBudget(time.monotonic() + deadline_seconds)
    attempted_ceiling = ceiling
    while True:
        try:
            candidates = _discover_continuous_candidate_window(
                analytics,
                project_id=project_id,
                row_type=row_type,
                filters=filters,
                floor=floor,
                ceiling=attempted_ceiling,
                salt=salt,
                sampling_rate=sampling_rate,
                budget=budget,
            )
        except ContinuousCandidateOverflow:
            next_ceiling = _smaller_candidate_ceiling(
                floor=floor,
                attempted_ceiling=attempted_ceiling,
                minimum_ceiling=minimum_ceiling,
            )
            if next_ceiling is None:
                raise
            attempted_ceiling = next_ceiling
            continue
        return ContinuousCandidates(
            candidates.classifier_ids,
            candidates.public_ids,
            attempted_ceiling,
        )


def _smaller_candidate_ceiling(
    *,
    floor: datetime,
    attempted_ceiling: datetime,
    minimum_ceiling: datetime | None,
) -> datetime | None:
    """Return a strictly smaller viable ceiling, or ``None`` to fail closed."""

    if minimum_ceiling is None or attempted_ceiling <= minimum_ceiling:
        return None
    midpoint = floor + (attempted_ceiling - floor) / 2
    next_ceiling = max(midpoint, minimum_ceiling)
    return next_ceiling if next_ceiling < attempted_ceiling else None


def _discover_continuous_candidate_window(
    analytics: Any,
    *,
    project_id: str,
    row_type: str,
    filters: list[dict[str, Any]],
    floor: datetime,
    ceiling: datetime,
    salt: str,
    sampling_rate: float,
    budget: _ReadBudget,
) -> ContinuousCandidates:
    """Prove one fixed window without exposing any partial buffered result."""

    if budget.workflow_row_type is not None:
        affected_rows = _read_workflow_changed_spans(
            analytics,
            project_id=project_id,
            floor=floor,
            ceiling=ceiling,
            budget=budget,
        )
    else:
        changed_identities = _read_changed_span_identities(
            analytics,
            project_id=project_id,
            floor_ns=_epoch_nanoseconds(floor),
            ceiling_ns=_epoch_nanoseconds(ceiling),
            budget=budget,
        )
        affected_rows = _expand_changed_span_identities(
            analytics,
            project_id=project_id,
            identities=changed_identities,
            budget=budget,
        )

    eval_dependent, annotation_dependent = _trigger_domains(filters)
    relation_refs: list[tuple[str, str, str]] = []
    if eval_dependent:
        relation_refs.extend(
            _read_changed_eval_refs(
                analytics,
                project_id=project_id,
                floor=floor,
                ceiling=ceiling,
                budget=budget,
            )
        )
    if annotation_dependent:
        relation_refs.extend(
            _read_changed_annotation_refs(
                analytics,
                project_id=project_id,
                floor=floor,
                ceiling=ceiling,
                budget=budget,
            )
        )
    affected_rows.extend(relation_refs)

    session_identity_dependent = row_type == RowType.SESSIONS or _has_session_filter(
        filters
    )
    if session_identity_dependent:
        changed_session_ids = _read_changed_remap_ids(
            analytics,
            table="trace_session_id_remap",
            floor=floor,
            ceiling=ceiling,
            budget=budget,
        )
        affected_rows.extend(
            _expand_relation_ref_page(
                analytics,
                project_id=project_id,
                refs=[("", "", value) for value in changed_session_ids],
                budget=budget,
            )
        )

    if _has_end_user_filter(filters):
        changed_end_user_ids = list(
            _read_changed_end_user_ids(
                analytics,
                project_id=project_id,
                floor=floor,
                ceiling=ceiling,
                budget=budget,
            )
        )
        changed_end_user_ids.extend(
            _read_changed_remap_ids(
                analytics,
                table="end_user_id_remap",
                floor=floor,
                ceiling=ceiling,
                budget=budget,
            )
        )
        affected_rows.extend(
            _expand_end_user_ids(
                analytics,
                project_id=project_id,
                end_user_ids=_bounded_unique(changed_end_user_ids, budget=budget),
                budget=budget,
            )
        )

    trace_ids = _bounded_unique(
        (row[0] for row in affected_rows if row[0]), budget=budget
    )
    if row_type == RowType.TRACES:
        sampled = _sample_ids(
            analytics,
            trace_ids,
            salt=salt,
            sampling_rate=sampling_rate,
            budget=budget,
        )
        return ContinuousCandidates(sampled, sampled)

    if row_type == RowType.SPANS:
        span_ids = _bounded_unique(
            (row[1] for row in affected_rows if row[1]), budget=budget
        )
        sampled = _sample_ids(
            analytics,
            span_ids,
            salt=salt,
            sampling_rate=sampling_rate,
            budget=budget,
        )
        return ContinuousCandidates(sampled, sampled)

    if row_type == RowType.SESSIONS:
        raw_session_ids = _bounded_unique(
            (row[2] for row in affected_rows if row[2] and row[2] != NIL_UUID),
            budget=budget,
        )
        session_ids = _resolve_session_ids(analytics, raw_session_ids, budget=budget)
        sampled_classifier_ids = _sample_ids(
            analytics,
            session_ids,
            salt=salt,
            sampling_rate=sampling_rate,
            budget=budget,
        )
        sampled_public_ids = _sample_ids(
            analytics,
            _bounded_unique((*raw_session_ids, *session_ids), budget=budget),
            salt=salt,
            sampling_rate=sampling_rate,
            budget=budget,
        )
        return ContinuousCandidates(sampled_classifier_ids, sampled_public_ids)

    # A voice entry is stored by root-span id, but any changed child/eval/score
    # must trigger reclassification of the whole trace.  Include every physical
    # root id ever associated with those bounded traces in C so a tombstoned or
    # re-parented old root can be removed from pending work.
    root_ids = _read_root_ids_for_traces(
        analytics,
        project_id=project_id,
        trace_ids=trace_ids,
        budget=budget,
    )
    sampled_roots = _sample_ids(
        analytics,
        root_ids,
        salt=salt,
        sampling_rate=sampling_rate,
        budget=budget,
    )
    return ContinuousCandidates(trace_ids, sampled_roots)


def sample_public_ids(
    analytics: Any,
    ids: tuple[str, ...] | list[str],
    *,
    salt: str,
    sampling_rate: float,
    deadline_seconds: float,
    workflow_budget: ContinuousWorkflowReadBudget | None = None,
) -> tuple[str, ...]:
    """Apply the exact ClickHouse cityHash sampling contract to buffered IDs."""

    budget = workflow_budget or _ReadBudget(time.monotonic() + deadline_seconds)
    return _sample_ids(
        analytics,
        tuple(ids),
        salt=salt,
        sampling_rate=sampling_rate,
        budget=budget,
    )


def _execute(
    analytics, query: str, params: dict[str, Any], budget: _ReadBudget
) -> list[dict[str, Any]]:
    try:
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=budget.timeout_ms(),
            settings=_READ_SETTINGS,
        )
    except ContinuousCandidateReadError:
        raise
    except Exception as exc:
        if (
            not isinstance(exc, TimeoutError)
            and not is_read_budget_error(exc)
            and not is_clickhouse_query_error(exc)
        ):
            raise
        raise ContinuousCandidateReadError(type(exc).__name__) from exc
    rows = list(result.data)
    budget.record_rows(rows)
    return rows


def _candidate_pages(
    analytics: Any,
    query: str,
    params: dict[str, Any],
    budget: _ReadBudget,
    *,
    keys: tuple[str, ...],
) -> Iterator[list[dict[str, Any]]]:
    """Read a complete DISTINCT/GROUP BY result using a stable identity keyset.

    The input SQL has no LIMIT. The outer LIMIT bounds every transferred page;
    the keyset includes every projected identity field, including timestamp
    ties. Never use an offset or return a capped prefix as a complete result.
    """
    order_by = ", ".join(keys)
    after = None
    while True:
        page_params = {**params, "candidate_limit": _WORKFLOW_PAGE_SIZE}
        keyset = ""
        if after is not None:
            # A driver-bound one-element tuple renders as ('value'), which
            # ClickHouse treats as a scalar. Compare scalar keys directly.
            key_expression = keys[0] if len(keys) == 1 else f"tuple({order_by})"
            page_params["candidate_after"] = after[0] if len(keys) == 1 else after
            keyset = f"WHERE {key_expression} > %(candidate_after)s"
        page = _execute(
            analytics,
            f"""
            SELECT * FROM ({query}) AS candidate_page
            {keyset}
            ORDER BY {order_by}
            LIMIT %(candidate_limit)s
            """,
            page_params,
            budget,
        )
        if not page:
            return
        last = tuple(page[-1][key] for key in keys)
        if after is not None and last <= after:
            raise ContinuousCandidateReadError(
                "continuous candidate page did not advance"
            )
        yield page
        if len(page) < _WORKFLOW_PAGE_SIZE:
            return
        after = last


def _read_candidate_rows(
    analytics: Any,
    query: str,
    params: dict[str, Any],
    budget: _ReadBudget,
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    # Existing small reads keep their exact SQL and overflow sentinel. Dense
    # workflow reads exhaust finite pages under the shared workflow budget.
    if budget.workflow_row_type is None:
        return _execute(
            analytics, query + "\nLIMIT %(candidate_limit)s", params, budget
        )
    if keys == ("trace_id", "id", "session_id"):
        field = {
            RowType.TRACES: "trace_id",
            RowType.VOICE_CALLS: "trace_id",
            RowType.SPANS: "id",
            RowType.SESSIONS: "session_id",
        }[budget.workflow_row_type]
        projection = ", ".join(key if key == field else f"'' AS {key}" for key in keys)
        # Project outside the physical identity predicates. Reusing an empty
        # alias inside their SELECT would shadow source columns in ClickHouse.
        query = f"SELECT DISTINCT {projection} FROM ({query}) AS candidate_relations"
    return [
        row
        for page in _candidate_pages(analytics, query, params, budget, keys=keys)
        for row in page
    ]


def _read_workflow_changed_spans(
    analytics: Any,
    *,
    project_id: str,
    floor: datetime,
    ceiling: datetime,
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    params = {
        "project_id": project_id,
        "nil_uuid": NIL_UUID,
        "arrival_floor_ns": _epoch_nanoseconds(floor),
        "arrival_ceiling_ns": _epoch_nanoseconds(ceiling),
    }
    source = """
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND _version >= %(arrival_floor_ns)s
          AND _version < %(arrival_ceiling_ns)s
    """
    if budget.workflow_row_type != RowType.SESSIONS:
        rows = _read_candidate_rows(
            analytics,
            f"""
            SELECT trace_id, id,
                toString(ifNull(trace_session_id, toUUID(%(nil_uuid)s))) AS session_id
            {source}
            """,
            params,
            budget,
            keys=("trace_id", "id", "session_id"),
        )
        return [
            (str(row["trace_id"]), str(row["id"]), str(row["session_id"]))
            for row in rows
        ]

    # A span can move between sessions. Read changed physical identities in
    # pages, then expand ALL versions of each identity to retain both aliases.
    affected = {}
    for page in _candidate_pages(
        analytics,
        "SELECT trace_id, id, toUnixTimestamp64Micro(start_time) AS start_us "
        + source
        + " GROUP BY trace_id, id, start_us",
        params,
        budget,
        keys=("trace_id", "id", "start_us"),
    ):
        identities = tuple(
            (str(row["trace_id"]), str(row["id"]), int(row["start_us"])) for row in page
        )
        affected.update(
            dict.fromkeys(
                _expand_changed_span_identities(
                    analytics,
                    project_id=project_id,
                    identities=identities,
                    budget=budget,
                )
            )
        )
    return list(affected)


def _read_changed_span_identities(
    analytics,
    *,
    project_id: str,
    floor_ns: int,
    ceiling_ns: int,
    budget: _ReadBudget,
) -> tuple[tuple[str, str, int], ...]:
    rows = _execute(
        analytics,
        """
        SELECT trace_id, id, toUnixTimestamp64Micro(start_time) AS start_us
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND _version >= %(arrival_floor_ns)s
          AND _version < %(arrival_ceiling_ns)s
        GROUP BY trace_id, id, start_us
        LIMIT %(candidate_limit)s
        """,
        {
            "project_id": project_id,
            "arrival_floor_ns": floor_ns,
            "arrival_ceiling_ns": ceiling_ns,
            "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1,
        },
        budget,
    )
    _raise_if_overflow(rows)
    return tuple(
        (str(row["trace_id"]), str(row["id"]), int(row["start_us"])) for row in rows
    )


def _expand_changed_span_identities(
    analytics,
    *,
    project_id: str,
    identities: tuple[tuple[str, str, int], ...],
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    return _read_candidate_batches(
        identities,
        lambda batch: _expand_changed_span_identity_batch(
            analytics, project_id=project_id, identities=batch, budget=budget
        ),
        batch_size=_SPAN_IDENTITY_BATCH_SIZE,
        budget=budget,
    )


def _read_candidate_batches(
    values: Sequence[_BatchInput],
    read_batch: Callable[[tuple[_BatchInput, ...]], Iterable[_BatchResult]],
    *,
    batch_size: int,
    budget: _ReadBudget | None = None,
) -> list[_BatchResult]:
    # Buffer the complete union before exposing candidates to reconciliation.
    # Multiple inputs can map to the same public result across batches. Keep
    # SELECT DISTINCT semantics and the fast-path cap, including after splitting.
    # Workflow reads instead share a query/deadline/result-byte budget.
    pending = [
        tuple(values[offset : offset + batch_size])
        for offset in reversed(range(0, len(values), batch_size))
    ]
    affected: dict[_BatchResult, None] = {}
    while pending:
        batch = pending.pop()
        try:
            rows = read_batch(batch)
        except Exception as exc:
            # Nonstandard/escaped IDs can exceed the cap even in a small batch.
            # Retry only the canonical size error, sharing the original budget.
            if len(batch) <= 1 or not is_clickhouse_query_size_error(exc):
                raise
            midpoint = len(batch) // 2
            pending.extend((batch[midpoint:], batch[:midpoint]))
            continue
        affected.update(dict.fromkeys(rows))
        _raise_if_overflow(affected, budget=budget)
    return list(affected)


def _expand_changed_span_identity_batch(
    analytics,
    *,
    project_id: str,
    identities: tuple[tuple[str, str, int], ...],
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    rows = _read_candidate_rows(
        analytics,
        """
        SELECT DISTINCT
            trace_id,
            id,
            toString(ifNull(trace_session_id, toUUID(%(nil_uuid)s))) AS session_id
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND trace_id IN %(changed_trace_ids)s
          AND id IN %(changed_span_ids)s
        WHERE (trace_id, id, toUnixTimestamp64Micro(start_time))
              IN %(changed_span_identities)s
        """,
        {
            "project_id": project_id,
            "nil_uuid": NIL_UUID,
            "changed_trace_ids": tuple(dict.fromkeys(row[0] for row in identities)),
            "changed_span_ids": tuple(dict.fromkeys(row[1] for row in identities)),
            "changed_span_identities": identities,
            "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1,
        },
        budget,
        keys=("trace_id", "id", "session_id"),
    )
    _raise_if_overflow(rows, budget=budget)
    return [
        (str(row["trace_id"]), str(row["id"]), str(row["session_id"])) for row in rows
    ]


def _read_changed_eval_refs(
    analytics,
    *,
    project_id: str,
    floor: datetime,
    ceiling: datetime,
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    # Eval results are co-located with CH25 spans, but production may retain
    # the legacy physical table name while writing there directly. Follow the
    # authoritative storage selector rather than assuming the empty v2 table.
    table, _ = eval_logger_source()
    if table.endswith("_v2"):
        arrival_column = "_version"
        arrival_predicate = (
            "_version >= %(arrival_floor_ns)s AND _version < %(arrival_ceiling_ns)s"
        )
        bounds: dict[str, Any] = {
            "arrival_floor_ns": _epoch_nanoseconds(floor),
            "arrival_ceiling_ns": _epoch_nanoseconds(ceiling),
        }
    else:
        arrival_column = "_peerdb_synced_at"
        arrival_predicate = (
            "_peerdb_synced_at >= %(arrival_floor)s "
            "AND _peerdb_synced_at < %(arrival_ceiling)s"
        )
        bounds = {"arrival_floor": floor, "arrival_ceiling": ceiling}
    return _read_relation_refs_paged(
        analytics,
        project_id=project_id,
        table=table,
        arrival_column=arrival_column,
        arrival_predicate=arrival_predicate,
        bounds=bounds,
        project_predicate="",
        budget=budget,
    )


def _read_changed_annotation_refs(
    analytics,
    *,
    project_id: str,
    floor: datetime,
    ceiling: datetime,
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    # Production score rows carry the denormalized tracer project UUID. Scope
    # before LIMIT so unrelated tenants cannot crowd out this task, then map
    # the bounded refs through spans to preserve exact trace/span/session shape.
    return _read_relation_refs_paged(
        analytics,
        project_id=project_id,
        table="model_hub_score",
        arrival_column="_peerdb_synced_at",
        arrival_predicate=(
            "_peerdb_synced_at >= %(arrival_floor)s "
            "AND _peerdb_synced_at < %(arrival_ceiling)s"
        ),
        bounds={"arrival_floor": floor, "arrival_ceiling": ceiling},
        project_predicate="AND tracer_project_id = toUUID(%(project_id)s)",
        budget=budget,
    )


def _read_relation_refs_paged(
    analytics,
    *,
    project_id: str,
    table: str,
    arrival_column: str,
    arrival_predicate: str,
    bounds: dict[str, Any],
    project_predicate: str,
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    """Page changed refs, mapping every small page through project spans.

    Eval rows have no reliable tracer-project column, so a global arrival page
    is unavoidable.  Crucially, this never constructs the unbounded FutureSet
    subqueries that timed out production: each page is <=200 driver-bound refs,
    is expanded project-locally, and the shared query/deadline cap fails closed
    if unrelated traffic is too large to prove exhaustion. Score rows are
    project-scoped before paging via ``tracer_project_id``.
    """

    affected: list[tuple[str, str, str]] = []
    after_order: Any | None = None
    after_id = ""
    while True:
        keyset = ""
        params: dict[str, Any] = {
            **bounds,
            "project_id": project_id,
            "nil_uuid": NIL_UUID,
            "relation_page_size": _RELATION_PAGE_SIZE,
        }
        if after_order is not None:
            params.update(
                {"relation_after_order": after_order, "relation_after_id": after_id}
            )
            keyset = f"""
              AND (
                  {arrival_column} > %(relation_after_order)s
                  OR (
                      {arrival_column} = %(relation_after_order)s
                      AND toString(id) > %(relation_after_id)s
                  )
              )
            """
        page = _execute(
            analytics,
            f"""
            SELECT
                {arrival_column} AS arrival_order,
                toString(id) AS relation_id,
                ifNull(toString(trace_id), '') AS trace_id,
                ifNull(toString(observation_span_id), '') AS span_id,
                ifNull(toString(trace_session_id), '') AS session_id
            FROM {table}
            WHERE {arrival_predicate}
              {project_predicate}
              {keyset}
            ORDER BY arrival_order, relation_id
            LIMIT %(relation_page_size)s
            """,
            params,
            budget,
        )
        if not page:
            break
        refs = [
            (str(row["trace_id"]), str(row["span_id"]), str(row["session_id"]))
            for row in page
            if row["trace_id"] or row["span_id"] or row["session_id"]
        ]
        affected.extend(
            _expand_relation_ref_page(
                analytics,
                project_id=project_id,
                refs=refs,
                budget=budget,
            )
        )
        _bounded_unique(
            (
                f"{trace_id}\x00{span_id}\x00{session_id}"
                for trace_id, span_id, session_id in affected
            ),
            budget=budget,
        )
        last = page[-1]
        after_order = last["arrival_order"]
        after_id = str(last["relation_id"])
        if len(page) < _RELATION_PAGE_SIZE:
            break
    return list(dict.fromkeys(affected))


def _expand_relation_ref_page(
    analytics,
    *,
    project_id: str,
    refs: list[tuple[str, str, str]],
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    # Eval/annotation callers already page 200 refs, but session remaps can
    # supply the entire 10,000-ID window here. Bound every caller's SQL.
    return _read_candidate_batches(
        refs,
        lambda batch: _expand_relation_ref_batch(
            analytics, project_id=project_id, refs=batch, budget=budget
        ),
        batch_size=_RELATION_PAGE_SIZE,
        budget=budget,
    )


def _expand_relation_ref_batch(
    analytics,
    *,
    project_id: str,
    refs: tuple[tuple[str, str, str], ...],
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    if not refs:
        return []
    trace_ids = tuple(dict.fromkeys(row[0] for row in refs if row[0]))
    span_ids = tuple(dict.fromkeys(row[1] for row in refs if row[1]))
    session_ids = tuple(
        dict.fromkeys(row[2] for row in refs if row[2] and row[2] != NIL_UUID)
    )
    predicates: list[str] = []
    params: dict[str, Any] = {
        "project_id": project_id,
        "nil_uuid": NIL_UUID,
        "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1,
    }
    if trace_ids:
        params["relation_trace_ids"] = trace_ids
        predicates.append("trace_id IN %(relation_trace_ids)s")
    if span_ids:
        params["relation_span_ids"] = span_ids
        predicates.append("id IN %(relation_span_ids)s")
    if session_ids:
        params["relation_session_ids"] = session_ids
        predicates.append("trace_session_id IN %(relation_session_ids)s")
    if not predicates:
        return []
    rows = _read_candidate_rows(
        analytics,
        f"""
        SELECT DISTINCT
            trace_id,
            id,
            toString(ifNull(trace_session_id, toUUID(%(nil_uuid)s))) AS session_id
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
        WHERE {" OR ".join(f"({predicate})" for predicate in predicates)}
        """,
        params,
        budget,
        keys=("trace_id", "id", "session_id"),
    )
    _raise_if_overflow(rows, budget=budget)
    return [
        (str(row["trace_id"]), str(row["id"]), str(row["session_id"])) for row in rows
    ]


def _read_changed_end_user_ids(
    analytics,
    *,
    project_id: str,
    floor: datetime,
    ceiling: datetime,
    budget: _ReadBudget,
) -> tuple[str, ...]:
    rows = _read_candidate_rows(
        analytics,
        """
        SELECT DISTINCT toString(end_user_id) AS end_user_id
        FROM end_users
        PREWHERE project_id = toUUID(%(project_id)s)
        WHERE version >= %(arrival_floor)s
          AND version < %(arrival_ceiling)s
        """,
        {
            "project_id": project_id,
            "arrival_floor": floor,
            "arrival_ceiling": ceiling,
            "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1,
        },
        budget,
        keys=("end_user_id",),
    )
    _raise_if_overflow(rows, budget=budget)
    return _bounded_unique((str(row["end_user_id"]) for row in rows), budget=budget)


def _read_changed_remap_ids(
    analytics,
    *,
    table: str,
    floor: datetime,
    ceiling: datetime,
    budget: _ReadBudget,
) -> tuple[str, ...]:
    """Exhaust a small remap change stream without an unscoped capped prefix."""

    if table not in {"trace_session_id_remap", "end_user_id_remap"}:
        raise ValueError("unsupported remap table")
    values: list[str] = []
    after_version: Any | None = None
    after_old = ""
    after_new = ""
    while True:
        params: dict[str, Any] = {
            "arrival_floor": floor,
            "arrival_ceiling": ceiling,
            "remap_page_size": _RELATION_PAGE_SIZE,
        }
        keyset = ""
        if after_version is not None:
            params.update(
                {
                    "remap_after_version": after_version,
                    "remap_after_old": after_old,
                    "remap_after_new": after_new,
                }
            )
            keyset = """
              AND (
                  version > %(remap_after_version)s
                  OR (
                      version = %(remap_after_version)s
                      AND (
                          toString(old_id) > %(remap_after_old)s
                          OR (
                              toString(old_id) = %(remap_after_old)s
                              AND toString(new_id) > %(remap_after_new)s
                          )
                      )
                  )
              )
            """
        page = _execute(
            analytics,
            f"""
            SELECT
                version AS arrival_order,
                toString(old_id) AS old_id,
                toString(new_id) AS new_id
            FROM {table} FINAL
            WHERE version >= %(arrival_floor)s
              AND version < %(arrival_ceiling)s
              {keyset}
            ORDER BY arrival_order, old_id, new_id
            LIMIT %(remap_page_size)s
            """,
            params,
            budget,
        )
        if not page:
            break
        for row in page:
            values.extend((str(row["old_id"]), str(row["new_id"])))
        _bounded_unique(values, budget=budget)
        last = page[-1]
        after_version = last["arrival_order"]
        after_old = str(last["old_id"])
        after_new = str(last["new_id"])
        if len(page) < _RELATION_PAGE_SIZE:
            break
    return _bounded_unique(values, budget=budget)


def _expand_end_user_ids(
    analytics,
    *,
    project_id: str,
    end_user_ids: tuple[str, ...],
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    return _read_candidate_batches(
        end_user_ids,
        lambda batch: _expand_end_user_id_batch(
            analytics, project_id=project_id, end_user_ids=batch, budget=budget
        ),
        batch_size=_RELATION_PAGE_SIZE,
        budget=budget,
    )


def _expand_end_user_id_batch(
    analytics,
    *,
    project_id: str,
    end_user_ids: tuple[str, ...],
    budget: _ReadBudget,
) -> list[tuple[str, str, str]]:
    rows = _read_candidate_rows(
        analytics,
        """
        SELECT DISTINCT
            trace_id,
            id,
            toString(ifNull(trace_session_id, toUUID(%(nil_uuid)s))) AS session_id
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND end_user_id IN %(candidate_end_user_ids)s
        """,
        {
            "project_id": project_id,
            "nil_uuid": NIL_UUID,
            "candidate_end_user_ids": end_user_ids,
            "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1,
        },
        budget,
        keys=("trace_id", "id", "session_id"),
    )
    _raise_if_overflow(rows, budget=budget)
    return [
        (str(row["trace_id"]), str(row["id"]), str(row["session_id"])) for row in rows
    ]


def _resolve_session_ids(
    analytics,
    raw_ids: tuple[str, ...],
    *,
    budget: _ReadBudget,
) -> tuple[str, ...]:
    return _bounded_unique(
        _read_candidate_batches(
            raw_ids,
            lambda batch: _resolve_session_id_batch(analytics, batch, budget=budget),
            batch_size=_CANDIDATE_ID_BATCH_SIZE,
            budget=budget,
        ),
        budget=budget,
    )


def _resolve_session_id_batch(
    analytics,
    raw_ids: tuple[str, ...],
    *,
    budget: _ReadBudget,
) -> tuple[str, ...]:
    placeholders = ", ".join(
        f"toUUID(%(session_id_{index})s)" for index in range(len(raw_ids))
    )
    params = {f"session_id_{index}": value for index, value in enumerate(raw_ids)}
    ts_map = survivor_map_subquery("trace_session_id_remap")
    resolved = resolved_id_expr("raw_session_id", "ts_remap")
    rows = _read_candidate_rows(
        analytics,
        f"""
        WITH ts_survivor_map AS ({ts_map})
        SELECT DISTINCT toString({resolved}) AS session_id
        FROM (
            SELECT arrayJoin([{placeholders}]) AS raw_session_id
        ) AS raw_sessions
        LEFT JOIN ts_survivor_map AS ts_remap
            ON raw_session_id = ts_remap.any_id
        """,
        {**params, "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1},
        budget,
        keys=("session_id",),
    )
    _raise_if_overflow(rows, budget=budget)
    return _bounded_unique((str(row["session_id"]) for row in rows), budget=budget)


def _read_root_ids_for_traces(
    analytics,
    *,
    project_id: str,
    trace_ids: tuple[str, ...],
    budget: _ReadBudget,
) -> tuple[str, ...]:
    return _bounded_unique(
        _read_candidate_batches(
            trace_ids,
            lambda batch: _read_root_id_batch(
                analytics, project_id=project_id, trace_ids=batch, budget=budget
            ),
            batch_size=_CANDIDATE_ID_BATCH_SIZE,
            budget=budget,
        ),
        budget=budget,
    )


def _read_root_id_batch(
    analytics,
    *,
    project_id: str,
    trace_ids: tuple[str, ...],
    budget: _ReadBudget,
) -> tuple[str, ...]:
    rows = _read_candidate_rows(
        analytics,
        """
        SELECT DISTINCT id
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND trace_id IN %(candidate_trace_ids)s
        WHERE parent_span_id IS NULL OR parent_span_id = ''
        """,
        {
            "project_id": project_id,
            "candidate_trace_ids": trace_ids,
            "candidate_limit": _MAX_PUBLIC_CANDIDATES + 1,
        },
        budget,
        keys=("id",),
    )
    _raise_if_overflow(rows, budget=budget)
    return _bounded_unique((str(row["id"]) for row in rows), budget=budget)


def _sample_ids(
    analytics,
    ids: tuple[str, ...],
    *,
    salt: str,
    sampling_rate: float,
    budget: _ReadBudget,
) -> tuple[str, ...]:
    unique_ids = _bounded_unique(ids, budget=budget)
    if sampling_rate >= 100:
        return unique_ids
    if sampling_rate <= 0 or not unique_ids:
        return ()
    return _bounded_unique(
        _read_candidate_batches(
            unique_ids,
            lambda batch: _sample_id_batch(
                analytics, batch, salt=salt, sampling_rate=sampling_rate, budget=budget
            ),
            batch_size=_SAMPLE_CHUNK,
            budget=budget,
        ),
        budget=budget,
    )


def _sample_id_batch(
    analytics,
    ids: tuple[str, ...],
    *,
    salt: str,
    sampling_rate: float,
    budget: _ReadBudget,
) -> tuple[str, ...]:
    placeholders = ", ".join(f"%(sample_id_{index})s" for index in range(len(ids)))
    params: dict[str, Any] = {
        f"sample_id_{index}": value for index, value in enumerate(ids)
    }
    params.update({"sampling_salt": salt, "sampling_rate": sampling_rate})
    rows = _execute(
        analytics,
        f"""
        SELECT row_id
        FROM (
            SELECT arrayJoin([{placeholders}]) AS row_id
        )
        WHERE modulo(
            cityHash64(%(sampling_salt)s, toString(row_id)), 100
        ) < %(sampling_rate)s
        ORDER BY row_id
        """,
        params,
        budget,
    )
    return tuple(str(row["row_id"]) for row in rows)


def _trigger_domains(filters: list[dict[str, Any]]) -> tuple[bool, bool]:
    eval_dependent = False
    annotation_dependent = False
    for item in filters:
        column_id = str(item.get("column_id") or item.get("columnId") or "")
        config = item.get("filter_config") or item.get("filterConfig") or {}
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        if col_type == "EVAL_METRIC" or column_id == "has_eval":
            eval_dependent = True
        if col_type == "ANNOTATION" or column_id in {
            "has_annotation",
            "my_annotations",
            "annotator",
        }:
            annotation_dependent = True
    return eval_dependent, annotation_dependent


def _has_session_filter(filters: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("column_id") or item.get("columnId") or "")
        in {"session", "session_id", "trace_session_id"}
        for item in filters
    )


def _has_end_user_filter(filters: list[dict[str, Any]]) -> bool:
    for item in filters:
        column_id = str(item.get("column_id") or item.get("columnId") or "")
        config = item.get("filter_config") or item.get("filterConfig") or {}
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        if column_id in {"end_user_id", "user", "user_id", "user_id_type"}:
            return True
        if col_type == "TRACE_END_USER":
            return True
    return False


def _bounded_unique(values, *, budget: _ReadBudget | None = None) -> tuple[str, ...]:
    unique = tuple(sorted(dict.fromkeys(str(value) for value in values if value)))
    _raise_if_overflow(unique, budget=budget)
    return unique


def _raise_if_overflow(rows: Sized, *, budget: _ReadBudget | None = None) -> None:
    if (budget is None or budget.workflow_row_type is None) and len(
        rows
    ) > _MAX_PUBLIC_CANDIDATES:
        raise ContinuousCandidateOverflow("continuous candidate cap exceeded")


def _epoch_nanoseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


__all__ = [
    "ContinuousCandidateOverflow",
    "ContinuousCandidateQueryCapExceeded",
    "ContinuousCandidateReadError",
    "ContinuousWorkflowReadBudget",
    "ContinuousCandidates",
    "discover_continuous_candidates",
    "sample_public_ids",
]
