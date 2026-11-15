"""Bounded metric invalidation, not an ingestion change log.

The arrival readers are only the fast path: physical source versions can merge
away and arrivals can become visible outside the overlap. Committed dependency
preimages plus a COMPLETE retained-history sweep provide the repair path. An
initial sweep must publish dependencies before a definition can be activated.

Every API returns a proposal. B07 must freeze it in the attempt ledger and
publish dependencies, values, lifecycle and cursor_updates in the SAME fenced
manifest transaction. Neither a successful SELECT nor a returned page advances
persistent progress. Store the reconciliation checkpoint in
MetricProjectRefresh.reconciliation_cursor only on publication. No PG writes,
independent dependency inserts, sampling, event-date cutoff or sliding repair
window belong here. The proposed 24-hour coverage bound still needs T14/T17
throughput measurement; coverage_status deliberately does not claim that proof.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from clickhouse_driver.errors import ErrorCodes

from tracer.selectors.eval_tasks import continuous_candidates as arrivals
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    WallCappedAnalytics,
    _clickhouse_error_code,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    NIL_UUID,
    bounded_survivor_map_subquery,
)

OVERLAP = timedelta(minutes=5)
PROPOSED_REPAIR_BOUND = timedelta(hours=24)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_KEYS = 10_000
# Bounded IN lists: 500 ids, twice in a survivor map, stay far below the
# 256 KiB parser cap; a full 10,000-key proposal needs about 80 statements.
KEY_BATCH = 500
# Empty start_time days are skipped inside one page, up to this many probes.
# Each probe reads one partition, so an empty day costs one cheap statement.
MAX_DAYS_PER_PAGE = 31
# ClickHouse stops a read-in-order LIMIT only at a block boundary. With the
# default 65,409-row block a 1,000-key page reads 65k rows per part (probe).
MAX_BLOCK_ROWS = 65_409
SOURCE_ORDER = (
    "observation_type",
    "service_name",
    "toStartOfHour(start_time)",
    "trace_id",
    "id",
)


class MetricChangeReadError(arrivals.ContinuousCandidateReadError):
    """A complete metric discovery proposal could not be proved."""


def memory_limit_exceeded(exc: BaseException) -> bool:
    """Whether a wrapped read failed on ClickHouse's memory cap (Code 241).

    Timeouts and every other read failure stay failures: only the memory cap
    is evidence that the window holds more keys than one pass can buffer.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if _clickhouse_error_code(exc) == ErrorCodes.MEMORY_LIMIT_EXCEEDED:
            return True
        exc = exc.__cause__
    return False


class MetricReadBudget(arrivals._ReadBudget):
    """One server-capped deadline and buffered-result envelope per proposal.

    The shared monotonic wall is sent to ClickHouse as a per-statement
    max_execution_time through WallCappedAnalytics. The executor must accept
    per-query settings; otherwise reads fail closed. Returned rows and their
    estimated bytes are checked after the statement completes. max_read_bytes
    remains a best-effort setting only: application_read_settings deliberately
    removes scan-byte and result-abort caps, so it is not part of the enforced
    budget contract. The application policy retains max_memory_usage,
    max_threads and max_block_size.
    """

    def __init__(
        self,
        *,
        deadline_seconds: float = 30,
        max_queries: int = 128,
        max_rows: int = 100_000,
        max_result_bytes: int = 32 * 1024 * 1024,
        max_memory_bytes: int = 256 * 1024 * 1024,
        max_read_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        if (
            min(
                deadline_seconds,
                max_queries,
                max_rows,
                max_result_bytes,
                max_memory_bytes,
                max_read_bytes,
            )
            <= 0
        ):
            raise ValueError("metric read budgets must be positive")
        started = time.monotonic()
        super().__init__(started + deadline_seconds)
        self.server_deadline = ReadDeadline(
            total_ms=max(1, math.floor(deadline_seconds * 1000)),
            started=started,
            enforce_on_server=True,
        )
        self.max_queries = min(max_queries, 128)
        self.max_rows = max_rows
        self.max_result_bytes = max_result_bytes
        self.rows = 0
        self.settings = {
            "max_threads": 2,
            # See queries.QUERY_SETTINGS: one index-analysis thread.
            "max_threads_for_indexes": 1,
            "max_memory_usage": max_memory_bytes,
            "max_bytes_to_read": max_read_bytes,
            "max_result_rows": max_rows,
            "max_result_bytes": max_result_bytes,
            "result_overflow_mode": "throw",
        }

    def timeout_ms(self) -> int:
        if self.attempts >= self.max_queries:
            raise MetricChangeReadError("metric query budget exceeded")
        return super().timeout_ms()

    def record_rows(self, rows: list[dict[str, Any]]) -> None:
        if time.monotonic() >= self.deadline:
            raise MetricChangeReadError("metric read deadline exceeded")
        self.rows += len(rows)
        self.result_bytes += sum(len(repr(row).encode("utf-8")) for row in rows)
        if self.rows > self.max_rows or self.result_bytes > self.max_result_bytes:
            raise MetricChangeReadError("metric result budget exceeded")


class _BudgetedAnalytics:
    def __init__(
        self,
        analytics: Any,
        budget: MetricReadBudget,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if getattr(analytics, "supports_per_query_read_settings", False) is not True:
            raise MetricChangeReadError(
                "metric read executor cannot enforce a server-side deadline"
            )
        self.analytics, self.budget, self.extra = analytics, budget, dict(extra or {})
        self._server_capped = WallCappedAnalytics(
            analytics, budget.server_deadline, floor_ms=1
        )

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        return self._server_capped.execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings={**settings, **self.extra, **self.budget.settings},
        )


def read(
    analytics,
    query,
    params,
    budget: MetricReadBudget,
    *,
    settings: Mapping[str, Any] | None = None,
):
    """Use the existing read-error normalization with tighter metric budgets.

    ``settings`` may add statement-shaping settings (block size); it can never
    loosen the budget's resource caps, which are applied last.
    """
    return arrivals._execute(
        _BudgetedAnalytics(analytics, budget, settings), query, params, budget
    )


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def ns(value: datetime) -> int:
    return arrivals._epoch_nanoseconds(value)


def from_ns(value: int) -> datetime:
    return EPOCH + timedelta(microseconds=value // 1000)


@dataclass(frozen=True, order=True)
class UnitKey:
    level: str
    unit_id: str

    def __post_init__(self):
        if self.level not in {"conversation", "trace", "span"} or not self.unit_id:
            raise ValueError("invalid metric unit key")


@dataclass(frozen=True, order=True)
class SourceKey:
    trace_id: str
    span_id: str


@dataclass(frozen=True)
class Dependency:
    """Last known membership of one span.

    ``unit_start`` is the span's own start_time. The conversation's start is
    min(start_time) over its live spans; B07 decides which one it stores.
    """

    trace_id: str
    span_id: str
    raw_session_id: str
    canonical_session_id: str
    raw_user_id: str
    canonical_user_id: str
    unit_start: datetime
    is_deleted: bool = False

    @property
    def key(self) -> SourceKey:
        return SourceKey(self.trace_id, self.span_id)

    def membership(self) -> tuple[str, str, str, str]:
        return (
            self.raw_session_id,
            self.canonical_session_id,
            self.raw_user_id,
            self.canonical_user_id,
        )

    def payload(self) -> dict[str, Any]:
        """B07 adds org/project/generation/attempt/_version before frozen insert."""
        return asdict(self)


@dataclass(frozen=True)
class ChangeBatch:
    source_keys: tuple[SourceKey, ...]
    units: tuple[UnitKey, ...]
    user_ids: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    prior_dependencies: tuple[Dependency, ...]
    cursor_updates: Mapping[str, Any]


def manifest_params(attempt_ids: Sequence[str]) -> dict[str, Any]:
    # Includes the checkpoint's publishing attempt: dependencies and lifecycle
    # stay in their own tables, rather than the field/bucket checkpoint tables.
    return {"committed_attempts": tuple(str(UUID(str(v))) for v in attempt_ids)}


def _scope(org_id, project_id, attempt_ids):
    return {
        "org_id": str(UUID(str(org_id))),
        "project_id": str(UUID(str(project_id))),
        **manifest_params(attempt_ids),
    }


COMMITTED = """org_id = toUUID(%(org_id)s)
    AND project_id = toUUID(%(project_id)s)
    AND attempt_id IN %(committed_attempts)s"""


def keyset_after(columns: Sequence[str], values: Sequence[Any], name: str):
    """Expanded lexicographic ``columns > values`` plus its parameters.

    ClickHouse key analysis prunes this OR/AND chain by the sorting-key prefix;
    a ``tuple(...) > tuple(...)`` comparison read every project row (probe).
    """
    if len(columns) != len(values) or not columns:
        raise ValueError("keyset columns and values differ")
    params = {f"{name}_{i}": value for i, value in enumerate(values)}
    last = len(columns) - 1
    clause = f"{columns[last]} > %({name}_{last})s"
    for i in range(last - 1, -1, -1):
        clause = (
            f"({columns[i]} > %({name}_{i})s OR "
            f"({columns[i]} = %({name}_{i})s AND {clause}))"
        )
    return clause, params


def _block_settings(page_limit: int) -> dict[str, int]:
    return {"max_block_size": max(1, min(page_limit, MAX_BLOCK_ROWS))}


def _limit(keys):
    result = tuple(sorted(set(keys)))
    if len(result) > MAX_KEYS:
        raise arrivals.ContinuousCandidateOverflow("metric candidate cap exceeded")
    return result


def _chunks(values):
    values = tuple(values)
    for offset in range(0, len(values), KEY_BATCH):
        yield values[offset : offset + KEY_BATCH]


def _nonempty(value):
    return "" if value is None or str(value) == NIL_UUID else str(value)


def _dependencies(analytics, keys, params, budget):
    result = {}
    if not params["committed_attempts"]:
        return result
    for batch in _chunks(keys):
        rows = read(
            analytics,
            f"""SELECT trace_id, span_id,
                tupleElement(payload, 1) AS raw_session_id,
                tupleElement(payload, 2) AS canonical_session_id,
                tupleElement(payload, 3) AS raw_user_id,
                tupleElement(payload, 4) AS canonical_user_id,
                tupleElement(payload, 5) AS unit_start,
                tupleElement(payload, 6) AS is_deleted
            FROM (
                SELECT trace_id, span_id,
                    argMax(tuple(raw_session_id, canonical_session_id, raw_user_id,
                        canonical_user_id, unit_start, is_deleted), _version) AS payload
                FROM metric_source_dependencies WHERE {COMMITTED}
                    AND (trace_id, span_id) IN %(source_keys)s
                GROUP BY trace_id, span_id
            )""",
            {**params, "source_keys": tuple((k.trace_id, k.span_id) for k in batch)},
            budget,
        )
        for row in rows:
            dependency = _dependency(row)
            result[dependency.key] = dependency
    return result


def _dependency(row):
    return Dependency(
        str(row["trace_id"]),
        str(row["span_id"]),
        _nonempty(row["raw_session_id"]),
        _nonempty(row["canonical_session_id"]),
        _nonempty(row["raw_user_id"]),
        _nonempty(row["canonical_user_id"]),
        utc(row["unit_start"]),
        bool(row["is_deleted"]),
    )


def _latest_membership(analytics, keys, params, budget):
    result = {}
    for batch in _chunks(keys):
        # Same logical (project, trace, span) collapse and tenant guard as the
        # writer's source read. Do not push deletion/session predicates ahead
        # of it, and do not use a start bound.
        rows = read(
            analytics,
            """SELECT trace_id, id AS span_id, start_time AS unit_start,
                trace_session_id AS raw_session_id, end_user_id AS raw_user_id,
                is_deleted
            FROM (
                SELECT project_id, trace_id, id, start_time, trace_session_id,
                    end_user_id, is_deleted, _version
                FROM spans PREWHERE project_id = toUUID(%(project_id)s)
                WHERE (trace_id, id) IN %(source_keys)s
                    AND (org_id = toUUID(%(org_id)s) OR isNull(org_id))
                ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
            )""",
            {**params, "source_keys": tuple((k.trace_id, k.span_id) for k in batch)},
            budget,
        )
        sessions = _resolve_ids(
            analytics,
            "trace_session_id_remap",
            [r["raw_session_id"] for r in rows],
            budget,
        )
        users = _resolve_ids(
            analytics, "end_user_id_remap", [r["raw_user_id"] for r in rows], budget
        )
        for row in rows:
            raw_session, raw_user = (
                _nonempty(row["raw_session_id"]),
                _nonempty(row["raw_user_id"]),
            )
            dependency = _dependency(
                {
                    **row,
                    "canonical_session_id": sessions.get(raw_session, raw_session),
                    "canonical_user_id": users.get(raw_user, raw_user),
                }
            )
            result[dependency.key] = dependency
    return result


def _physical_units(analytics, keys, params, budget):
    """Bounded preimages for nominated identities, not another history sweep.

    A partial repair can publish a whole conversation before every member has
    a committed dependency. An arrival-filtered sweep must therefore retain
    physical owners older than its window, just as fast-path discovery does.
    These are opportunistic preimages; committed dependencies and full repair
    remain necessary when source merges have already removed old revisions.
    """
    sessions = set()
    for batch in _chunks(keys):
        rows = read(
            analytics,
            """SELECT DISTINCT trace_session_id AS raw_session_id
            FROM spans
            PREWHERE project_id = toUUID(%(project_id)s)
                AND trace_id IN %(owner_traces)s AND id IN %(owner_spans)s
            WHERE (trace_id, id) IN %(source_keys)s
                AND (org_id = toUUID(%(org_id)s) OR isNull(org_id))
            LIMIT %(owner_limit)s""",
            {
                **params,
                "source_keys": tuple((key.trace_id, key.span_id) for key in batch),
                "owner_traces": tuple(dict.fromkeys(key.trace_id for key in batch)),
                "owner_spans": tuple(dict.fromkeys(key.span_id for key in batch)),
                "owner_limit": MAX_KEYS + 1,
            },
            budget,
        )
        sessions.update(_nonempty(row["raw_session_id"]) for row in rows)
        sessions.discard("")
        _limit(sessions)
    canonical = _resolve_ids(analytics, "trace_session_id_remap", sessions, budget)
    return _limit(UnitKey("conversation", canonical.get(s, s)) for s in sessions)


def _resolve_ids(analytics, table, values, budget):
    """Map raw ids to the survivor-old canonical id, in bounded statements."""
    ids = tuple(sorted({_nonempty(v) for v in values} - {""}))
    result = {}
    # Use the prevailing survivor-old identity, not new_id: curated dimensions
    # remain old-keyed. This also expands siblings when a survivor changes.
    mapping = bounded_survivor_map_subquery(table, candidate_param="resolve_ids")
    for batch in _chunks(ids):
        rows = read(
            analytics,
            f"SELECT toString(any_id) AS any_id, toString(survivor_id) AS survivor_id "
            f"FROM ({mapping})",
            {"resolve_ids": batch},
            budget,
        )
        result.update({str(r["any_id"]): str(r["survivor_id"]) for r in rows})
    return result


def _remap_aliases(analytics, table, values, budget):
    if not values:
        return ()
    mapping = bounded_survivor_map_subquery(table, candidate_param="resolve_ids")
    aliases = set(values)
    for batch in _chunks(values):
        rows = read(
            analytics,
            f"SELECT toString(any_id) AS any_id FROM ({mapping}) LIMIT %(cap)s",
            {"resolve_ids": batch, "cap": MAX_KEYS + 1},
            budget,
        )
        aliases.update(str(r["any_id"]) for r in rows)
        _limit(aliases)
    return _limit(aliases)


def _dependency_keys_for_aliases(analytics, params, sessions, users, budget):
    if not params["committed_attempts"] or not (sessions or users):
        return ()
    keys = set()
    for kind, ids in (("session", sessions), ("user", users)):
        for batch in _chunks(ids):
            rows = read(
                analytics,
                f"""SELECT DISTINCT trace_id, span_id FROM metric_source_dependencies
                WHERE {COMMITTED}
                    AND (raw_{kind}_id IN %(aliases)s
                         OR canonical_{kind}_id IN %(aliases)s)
                LIMIT %(cap)s""",
                {**params, "aliases": batch, "cap": MAX_KEYS + 1},
                budget,
            )
            # Historical committed memberships are a safe nomination superset.
            # _dependencies subsequently verifies the latest allowed payload.
            keys.update(SourceKey(str(r["trace_id"]), str(r["span_id"])) for r in rows)
            _limit(keys)
    return _limit(keys)


def _batch(
    analytics,
    keys,
    params,
    budget,
    cursor_updates,
    *,
    extra_units: Iterable[UnitKey] = (),
    divergent_only: bool = False,
):
    keys = _limit(keys)
    before = _dependencies(analytics, keys, params, budget)
    after = _latest_membership(analytics, keys, params, budget)
    if divergent_only:
        # The source phase visited every key that has a physical span row and
        # nominated its latest and committed owners. Keep only keys it could
        # not see (no source row) or whose committed state diverges.
        def state(item):
            return (item.membership(), item.is_deleted)

        keys = tuple(
            key
            for key in keys
            if key not in after
            or key not in before
            or state(after[key]) != state(before[key])
        )
    units, users = set(extra_units), set()
    for key in keys:
        for item in (before.get(key), after.get(key)):
            # Include even deleted memberships. The old now-empty unit still
            # needs a value/bucket tombstone; source OPTIMIZE is not a
            # before-image recovery.
            if item is None:
                continue
            if item.canonical_session_id:
                units.add(UnitKey("conversation", item.canonical_session_id))
            if item.canonical_user_id:
                users.add(item.canonical_user_id)
    dependencies = []
    for key in keys:
        if key in after:
            dependencies.append(after[key])
        elif key in before:
            dependencies.append(
                Dependency(**{**asdict(before[key]), "is_deleted": True})
            )
    return ChangeBatch(
        keys,
        _limit(units),
        _limit(users),
        tuple(dependencies),
        tuple(before[key] for key in keys if key in before),
        cursor_updates,
    )


def _arrival_floor(cursor):
    return max(EPOCH, from_ns(cursor) - OVERLAP)


def _relation_refs(
    analytics,
    source,
    params,
    *,
    eval_watermark_ns: int,
    remap_cursor_ns: int,
    ceiling: datetime,
    eval_config_ids: Sequence[str],
    budget: MetricReadBudget,
):
    """Eval and id-remap arrivals of one window: (refs, alias dependency keys).

    The refs are project spans (trace, span, physical session); the alias keys
    are committed dependencies of every remapped session or user alias.
    """
    end = ns(ceiling)
    refs = []
    configs = tuple(str(UUID(str(v))) for v in eval_config_ids)
    if configs:
        table, _ = eval_logger_source()
        v2 = table.endswith("_v2")
        arrival_column = "_version" if v2 else "_peerdb_synced_at"
        floor = _arrival_floor(eval_watermark_ns)
        bounds = {
            "metric_eval_config_ids": configs,
            "metric_floor": ns(floor) if v2 else floor,
            "metric_ceiling": end if v2 else ceiling,
        }
        refs.extend(
            arrivals._read_relation_refs_paged(
                source,
                project_id=params["project_id"],
                table=table,
                arrival_column=arrival_column,
                arrival_predicate=(
                    f"{arrival_column} >= %(metric_floor)s "
                    f"AND {arrival_column} < %(metric_ceiling)s"
                ),
                bounds=bounds,
                project_predicate="AND custom_eval_config_id IN %(metric_eval_config_ids)s",
                budget=budget,
            )
        )
    sessions, users = [], []
    for table, target in (
        ("trace_session_id_remap", sessions),
        ("end_user_id_remap", users),
    ):
        changed = arrivals._read_changed_remap_ids(
            source,
            table=table,
            floor=_arrival_floor(remap_cursor_ns),
            ceiling=ceiling,
            budget=budget,
        )
        target.extend(_remap_aliases(analytics, table, changed, budget))
    if sessions:
        refs.extend(
            arrivals._expand_relation_ref_page(
                source,
                project_id=params["project_id"],
                refs=[("", "", v) for v in sessions],
                budget=budget,
            )
        )
    if users:
        refs.extend(
            arrivals._expand_end_user_ids(
                source,
                project_id=params["project_id"],
                end_user_ids=tuple(users),
                budget=budget,
            )
        )
    return refs, _dependency_keys_for_aliases(
        analytics, params, sessions, users, budget
    )


def discover_changes(
    analytics,
    *,
    org_id: str,
    project_id: str,
    committed_attempts: Sequence[str],
    watermark_ns: int,
    eval_watermark_ns: int,
    remap_cursor_ns: int,
    ceiling: datetime,
    eval_config_ids: Sequence[str] = (),
    budget: MetricReadBudget | None = None,
) -> ChangeBatch:
    """Prove a half-open arrival window with five-minute overlap, or raise.

    The caller supplies the exact pinned manifest's attempts, including those
    holding dependencies/lifecycle after field checkpoint compaction. An empty
    allowlist means no committed rows, never 'read every physical attempt'.
    """
    budget = budget or MetricReadBudget()
    params = _scope(org_id, project_id, committed_attempts)
    ceiling = utc(ceiling)
    end = ns(ceiling)
    if min(watermark_ns, eval_watermark_ns, remap_cursor_ns) < 0 or end < max(
        watermark_ns, eval_watermark_ns, remap_cursor_ns
    ):
        raise ValueError("metric change cursors cannot move backwards")
    source = _BudgetedAnalytics(analytics, budget)
    identities = arrivals._read_changed_span_identities(
        source,
        project_id=params["project_id"],
        floor_ns=ns(_arrival_floor(watermark_ns)),
        ceiling_ns=end,
        budget=budget,
    )
    refs = arrivals._expand_changed_span_identities(
        source, project_id=params["project_id"], identities=identities, budget=budget
    )
    relation_refs, alias_keys = _relation_refs(
        analytics,
        source,
        params,
        eval_watermark_ns=eval_watermark_ns,
        remap_cursor_ns=remap_cursor_ns,
        ceiling=ceiling,
        eval_config_ids=eval_config_ids,
        budget=budget,
    )
    refs.extend(relation_refs)
    keys = {SourceKey(t, s) for t, s, _ in refs}
    keys.update(SourceKey(t, s) for t, s, _ in identities)
    keys.update(alias_keys)
    # Physical (possibly superseded) session ids are an opportunistic
    # before-image. Units are keyed by the canonical survivor-old id.
    raw_sessions = {_nonempty(s) for _, _, s in refs} - {""}
    canonical = _resolve_ids(analytics, "trace_session_id_remap", raw_sessions, budget)
    extra_units = (
        UnitKey("conversation", canonical.get(s, s)) for s in sorted(raw_sessions)
    )
    return _batch(
        analytics,
        keys,
        params,
        budget,
        {"watermark_ns": end, "eval_watermark_ns": end, "remap_cursor_ns": end},
        extra_units=extra_units,
    )


@dataclass(frozen=True)
class ArrivalWindow:
    start_floor: datetime | None
    # None when eval/remap changes need spans whose versions predate the window.
    source_floor_ns: int | None


def arrival_start_floor(analytics, **kwargs) -> datetime | None:
    """Compatibility reader for the earliest event-time touched by arrivals."""
    return arrival_window(analytics, **kwargs).start_floor


def arrival_window(
    analytics,
    *,
    org_id: str,
    project_id: str,
    committed_attempts: Sequence[str],
    watermark_ns: int,
    eval_watermark_ns: int,
    remap_cursor_ns: int,
    ceiling: datetime,
    eval_config_ids: Sequence[str] = (),
    budget: MetricReadBudget | None = None,
) -> ArrivalWindow:
    """Event-time floor and optional source-version floor of an arrival window.

    The same three arrival windows discover_changes proves, each from its
    cursor minus the overlap up to ``ceiling``. Span arrivals are reduced to
    min(start_time) in one aggregate, so a burst too large for discovery to
    buffer is still dated cheaply. Eval and remap arrivals are resolved to keys
    by discovery's own reads and dated by their latest physical and committed
    start. A source sweep visiting every physical span from the returned
    instant onward therefore nominates every key discovery would have, so it
    can consume the same windows. A None start_floor means nothing arrived. Raises
    ContinuousCandidateOverflow when the eval/remap keys exceed discovery's cap
    or a nominated key has no physical span row (only a full sweep visits it).
    """
    budget = budget or MetricReadBudget()
    params = _scope(org_id, project_id, committed_attempts)
    ceiling = utc(ceiling)
    end = ns(ceiling)
    if min(watermark_ns, eval_watermark_ns, remap_cursor_ns) < 0 or end < max(
        watermark_ns, eval_watermark_ns, remap_cursor_ns
    ):
        raise ValueError("metric change cursors cannot move backwards")
    rows = read(
        analytics,
        """SELECT count() AS arrivals, min(start_time) AS first_start
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
            AND _version >= %(arrival_floor_ns)s
            AND _version < %(arrival_ceiling_ns)s""",
        {
            "project_id": params["project_id"],
            "arrival_floor_ns": ns(_arrival_floor(watermark_ns)),
            "arrival_ceiling_ns": end,
        },
        budget,
    )
    starts = [utc(rows[0]["first_start"])] if int(rows[0]["arrivals"]) else []
    refs, alias_keys = _relation_refs(
        analytics,
        _BudgetedAnalytics(analytics, budget),
        params,
        eval_watermark_ns=eval_watermark_ns,
        remap_cursor_ns=remap_cursor_ns,
        ceiling=ceiling,
        eval_config_ids=eval_config_ids,
        budget=budget,
    )
    keys = _limit({SourceKey(t, s) for t, s, _ in refs} | set(alias_keys))
    if keys:
        physical = _latest_membership(analytics, keys, params, budget)
        if set(keys) - physical.keys():
            raise arrivals.ContinuousCandidateOverflow(
                "a changed metric key has no physical span row"
            )
        committed = _dependencies(analytics, keys, params, budget)
        starts.extend(d.unit_start for d in (*physical.values(), *committed.values()))
    # Source-only overflow can read just the frozen version interval. A late
    # old-start span must not force the next burst to rescan all older source
    # rows after that start. Relation changes intentionally keep the broader
    # event-time sweep: their source spans need not have a new _version.
    return ArrivalWindow(
        min(starts) if starts else None,
        ns(_arrival_floor(watermark_ns)) if not keys else None,
    )


PHASES = ("sources", "dependencies", "units", "complete")


@dataclass(frozen=True)
class ReconciliationCheckpoint:
    """JSON-safe persistent progress of one complete retained-history pass.

    Phases, each a keyset aligned with its own table's sorting key:
    ``sources`` sweeps every physical span row (any version, deleted or not)
    one start_time day at a time; ``dependencies`` adds committed keys with no
    source row, or whose latest membership or deletion state differs from the
    committed one; ``units`` adds every committed
    lifecycle unit (missing or orphaned dependencies). ``covered_through`` is
    the start of the last complete pass: any write visible before it was
    visited, except a key whose sort position changed mid-pass, which the fast
    path or the next complete pass visits (so the bound is two passes).

    ``floor`` marks a burst sweep of a forward-only project: it replaces one
    overflowing arrival window, not a full pass. Its source phase starts at the
    earliest span start that window's arrivals touch (arrival_start_floor), it
    has no committed phases, and completing it consumes the window without
    counting as a complete pass (completed_at, completed_passes and
    covered_through stay those of the last full pass).
    """

    started_at: datetime
    completed_at: datetime | None = None
    phase: str = "sources"
    after: tuple[str, ...] | None = None
    scanned: int = 0
    completed_passes: int = 0
    day: date | None = None
    last_day: date | None = None
    covered_through: datetime | None = None
    floor: datetime | None = None
    arrival_floor_ns: int | None = None

    def __post_init__(self):
        if self.phase not in PHASES or self.scanned < 0:
            raise ValueError("invalid metric reconciliation checkpoint")
        if self.floor is not None and self.phase != "sources":
            raise ValueError("a burst sweep has only a source phase")
        if self.arrival_floor_ns is not None and (
            self.floor is None or not 0 <= self.arrival_floor_ns <= ns(self.started_at)
        ):
            raise ValueError("invalid burst arrival window")
        width = {"sources": 5, "dependencies": 2, "units": 2}.get(self.phase)
        if self.after is not None and len(self.after) != width:
            raise ValueError("invalid reconciliation keyset")
        if (self.day is None) != (self.last_day is None) or (
            self.day is not None and self.phase != "sources"
        ):
            raise ValueError("invalid reconciliation day range")
        if self.after is not None and self.phase == "sources" and self.day is None:
            raise ValueError("invalid reconciliation keyset")

    def to_dict(self):
        def iso(value):
            return None if value is None else value.isoformat()

        return {
            "started_at": utc(self.started_at).isoformat(),
            "completed_at": iso(self.completed_at and utc(self.completed_at)),
            "phase": self.phase,
            "after": list(self.after) if self.after is not None else None,
            "scanned": self.scanned,
            "completed_passes": self.completed_passes,
            "day": iso(self.day),
            "last_day": iso(self.last_day),
            "covered_through": iso(self.covered_through and utc(self.covered_through)),
            # Only a burst sweep has the key: every other cursor is unchanged.
            **({"floor": iso(utc(self.floor))} if self.floor is not None else {}),
            **(
                {"arrival_floor_ns": self.arrival_floor_ns}
                if self.arrival_floor_ns is not None
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, value):
        def moment(name):
            raw = value.get(name)
            return datetime.fromisoformat(raw) if raw else None

        def day(name):
            raw = value.get(name)
            return date.fromisoformat(raw) if raw else None

        return cls(
            started_at=datetime.fromisoformat(value["started_at"]),
            completed_at=moment("completed_at"),
            phase=value["phase"],
            after=tuple(value["after"]) if value.get("after") else None,
            scanned=value.get("scanned", 0),
            completed_passes=value.get("completed_passes", 0),
            day=day("day"),
            last_day=day("last_day"),
            covered_through=moment("covered_through"),
            floor=moment("floor"),
            arrival_floor_ns=value.get("arrival_floor_ns"),
        )

    def coverage_status(self, now: datetime):
        now = utc(now)
        covered = utc(self.covered_through) if self.covered_through else None
        age = (now - covered).total_seconds() if covered else None
        return {
            "complete_passes": self.completed_passes,
            "incomplete": self.phase != "complete",
            "last_complete_at": self.completed_at,
            "covered_through": covered,
            "coverage_age_seconds": age,
            "stale": age is None or age > PROPOSED_REPAIR_BOUND.total_seconds(),
            "moved_key_bound_passes": 2,
            "proposed_bound_seconds": PROPOSED_REPAIR_BOUND.total_seconds(),
            "throughput_bound_proven": False,
        }


@dataclass(frozen=True)
class ReconciliationPage:
    changes: ChangeBatch
    checkpoint: ReconciliationCheckpoint


def _floor_value(floor):
    return utc(floor).strftime("%Y-%m-%d %H:%M:%S.%f")


def _source_days(
    analytics,
    params,
    budget,
    floor=None,
    *,
    arrival_ceiling=None,
    arrival_floor_ns=None,
):
    # A floor prunes whole start_time partitions: a forward-only sweep never
    # reads the days before its coverage start.
    bound = (
        ""
        if floor is None
        else "AND start_time >= toDateTime64(%(source_floor)s, 6, 'UTC')"
    )
    query_params = dict(params)
    if floor is not None:
        query_params["source_floor"] = _floor_value(floor)
    if arrival_ceiling is not None:
        bound += " AND _version < %(source_arrival_ceiling)s"
        query_params["source_arrival_ceiling"] = ns(arrival_ceiling)
    if arrival_floor_ns is not None:
        bound += " AND _version >= %(source_arrival_floor)s"
        query_params["source_arrival_floor"] = arrival_floor_ns
    rows = read(
        analytics,
        f"""SELECT toString(toDate(min(start_time))) AS first_day,
            toString(toDate(max(start_time))) AS last_day, count() AS source_rows
        FROM spans PREWHERE project_id = toUUID(%(project_id)s) {bound}""",
        query_params,
        budget,
    )
    if not rows or not int(rows[0]["source_rows"]):
        return None
    return date.fromisoformat(rows[0]["first_day"]), date.fromisoformat(
        rows[0]["last_day"]
    )


def _source_key_values(row) -> tuple[str, ...]:
    return (
        str(row["observation_type"]),
        str(row["service_name"]),
        utc(row["hour"]).strftime("%Y-%m-%d %H:%M:%S"),
        str(row["trace_id"]),
        str(row["span_id"]),
    )


def _source_page(analytics, params, budget, cp, page_size, floor=None):
    """One page of physical span keys in sorting-key order within single days.

    Returns (keys, rows read, next day, next after, phase finished). Every
    statement is scoped to one toDate(start_time) partition, so read-in-order
    stops after about one page instead of reading the project. A floor also
    bounds the first day: nothing before it is nominated.
    """
    day, last_day, after = cp.day, cp.last_day, cp.after
    keys: dict[SourceKey, None] = {}
    scanned = 0
    for _ in range(MAX_DAYS_PER_PAGE):
        remaining = page_size - scanned
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        floored = floor is not None and utc(floor) > start
        query_params = {
            **params,
            "day_start": _floor_value(floor)
            if floored
            else start.strftime("%Y-%m-%d %H:%M:%S"),
            "day_end": (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "page_limit": remaining,
        }
        # Freeze the burst's ARRIVAL window, not its event-time window. A
        # start_time ceiling would miss late-arriving old-start spans; without
        # any ceiling, sustained arrivals ahead of the keyset prevent completion.
        # Revisions merged away during the sweep are covered by the next
        # arrival window (and ordinary full reconciliation remains unchanged).
        arrival_bound = ""
        if cp.floor is not None:
            arrival_bound = "AND _version < %(source_arrival_ceiling)s"
            query_params["source_arrival_ceiling"] = ns(cp.started_at)
        if cp.arrival_floor_ns is not None:
            arrival_bound += " AND _version >= %(source_arrival_floor)s"
            query_params["source_arrival_floor"] = cp.arrival_floor_ns
        keyset = ""
        if after is not None:
            clause, keyset_params = keyset_after(SOURCE_ORDER, after, "source_after")
            # The hour literal is a UTC wall-clock string.
            clause = clause.replace(
                "%(source_after_2)s", "toDateTime(%(source_after_2)s, 'UTC')"
            )
            keyset = "WHERE " + clause
            query_params.update(keyset_params)
        rows = read(
            analytics,
            f"""SELECT observation_type, service_name,
                toStartOfHour(start_time) AS hour, trace_id, id AS span_id
            FROM spans
            PREWHERE project_id = toUUID(%(project_id)s)
                AND start_time >= toDateTime64(%(day_start)s, 6, 'UTC')
                AND start_time < toDateTime64(%(day_end)s, 6, 'UTC')
                {arrival_bound}
            {keyset}
            ORDER BY {", ".join(SOURCE_ORDER)}
            LIMIT %(page_limit)s""",
            query_params,
            budget,
            settings=_block_settings(remaining),
        )
        for row in rows:
            keys[SourceKey(str(row["trace_id"]), str(row["span_id"]))] = None
        scanned += len(rows)
        if len(rows) == remaining:
            next_after = _source_key_values(rows[-1])
            if after is not None and next_after <= tuple(after):
                raise MetricChangeReadError(
                    "metric reconciliation keyset did not advance"
                )
            return tuple(keys), scanned, day, next_after, False
        after = None
        if day >= last_day:
            return tuple(keys), scanned, None, None, True
        day += timedelta(days=1)
    return tuple(keys), scanned, day, None, False


def _committed_page(analytics, params, budget, table, columns, after, page_size):
    """Keyset page over a committed metric table, in its sorting-key order."""
    query_params = {**params, "page_limit": page_size}
    keyset = ""
    if after is not None:
        clause, keyset_params = keyset_after(columns, after, "committed_after")
        keyset = "AND " + clause
        query_params.update(keyset_params)
    rows = read(
        analytics,
        f"""SELECT {", ".join(columns)} FROM {table}
        PREWHERE org_id = toUUID(%(org_id)s) AND project_id = toUUID(%(project_id)s)
        WHERE attempt_id IN %(committed_attempts)s {keyset}
        ORDER BY {", ".join(columns)}
        LIMIT %(page_limit)s""",
        query_params,
        budget,
        settings=_block_settings(page_size),
    )
    values = list(dict.fromkeys(tuple(str(r[c]) for c in columns) for r in rows))
    finished = len(rows) < page_size
    next_after = values[-1] if values and not finished else None
    if next_after is not None and after is not None and next_after <= tuple(after):
        raise MetricChangeReadError("metric reconciliation keyset did not advance")
    return values, len(rows), next_after, finished


def reconcile_page(
    analytics,
    *,
    org_id: str,
    project_id: str,
    committed_attempts: Sequence[str],
    now: datetime,
    checkpoint: ReconciliationCheckpoint | None = None,
    page_size: int = 1000,
    budget: MetricReadBudget | None = None,
    floor: datetime | None = None,
) -> ReconciliationPage:
    """Repair ALL retained keys, also used for initial dependency backfill.

    Recompute every nominated unit even if membership/version is unchanged:
    this is what repairs eval arrivals delayed beyond the overlap. New keys
    appearing behind a keyset are visited on the next complete pass. Full
    passes apply no source-version minimum or sampling. ``floor`` bounds the
    source sweep to a coverage/history start; committed phases are unchanged.
    Bursts sweep only sources below their frozen arrival ceiling. Source-only
    bursts also use an arrival minimum and their own event-time floor, even
    before coverage, to nominate backdated corrections. Eligibility remains a
    publication decision. Relation bursts keep the later of both event floors.
    """
    if not 1 <= page_size <= MAX_KEYS:
        raise ValueError("invalid metric reconciliation page size")
    budget = budget or MetricReadBudget()
    params = _scope(org_id, project_id, committed_attempts)
    now = utc(now)
    cp = checkpoint or ReconciliationCheckpoint(now)
    if cp.phase == "complete":
        cp = ReconciliationCheckpoint(
            now,
            cp.completed_at,
            completed_passes=cp.completed_passes,
            covered_through=cp.covered_through,
        )
    burst = cp.floor is not None
    if burst:
        # Version-bounded arrivals may move an existing key before coverage.
        # Nominate that key too; the writer, not discovery, owns eligibility.
        floor = (
            cp.floor
            if floor is None or cp.arrival_floor_ns is not None
            else max(utc(floor), utc(cp.floor))
        )
    phase, after, day, last_day = cp.phase, cp.after, cp.day, cp.last_day
    source_keys: tuple[SourceKey, ...] = ()
    extra_units: tuple[UnitKey, ...] = ()
    scanned = 0
    divergent_only = False
    if phase == "sources" and day is None:
        days = _source_days(
            analytics,
            params,
            budget,
            floor,
            arrival_ceiling=cp.started_at if burst else None,
            arrival_floor_ns=cp.arrival_floor_ns,
        )
        if days is None:
            phase = "dependencies"
        else:
            day, last_day = days
    if phase == "sources":
        progress = replace(cp, day=day, last_day=last_day)
        source_keys, scanned, day, after, finished = _source_page(
            analytics, params, budget, progress, page_size, floor
        )
        if cp.arrival_floor_ns is not None:
            # Do not advance the proposed keyset unless both old and latest
            # owners can be corrected in its atomic publication. This lookup
            # is limited to this page's identities and shares its read budget.
            extra_units = _physical_units(analytics, source_keys, params, budget)
        if finished:
            phase, day, last_day = "dependencies", None, None
    elif (
        phase in {"dependencies", "units"}
        and params["committed_attempts"]
        and not burst
    ):
        if phase == "dependencies":
            table, columns = "metric_source_dependencies", ("trace_id", "span_id")
        else:
            table, columns = "metric_unit_state", ("level", "unit_id")
        values, scanned, after, finished = _committed_page(
            analytics, params, budget, table, columns, after, page_size
        )
        if phase == "dependencies":
            source_keys = tuple(SourceKey(*v) for v in values)
            divergent_only = True
        else:
            extra_units = tuple(UnitKey(*v) for v in values)
        if finished:
            phase = "units" if phase == "dependencies" else "complete"
    elif phase in {"dependencies", "units"}:
        # An empty manifest has no committed dependency or lifecycle rows.
        phase, after = "complete", None
    if burst and phase in {"dependencies", "units"}:
        # A burst sweep's source phase is its whole proof: every arrival of
        # its window is a physical span starting at or after its floor.
        phase, after = "complete", None
    complete = phase == "complete"
    full = complete and not burst
    proposed = ReconciliationCheckpoint(
        started_at=cp.started_at,
        completed_at=now if full else cp.completed_at,
        phase=phase,
        after=None if complete else after,
        scanned=cp.scanned + scanned,
        completed_passes=cp.completed_passes + int(full),
        day=day if phase == "sources" else None,
        last_day=last_day if phase == "sources" else None,
        covered_through=cp.started_at if full else cp.covered_through,
        floor=None if complete else cp.floor,
        arrival_floor_ns=None if complete else cp.arrival_floor_ns,
    )
    changes = _batch(
        analytics,
        source_keys,
        params,
        budget,
        {
            "reconciliation_cursor": proposed.to_dict(),
            "reconciliation_started_at": proposed.started_at,
            "reconciliation_completed_at": proposed.completed_at,
            "pending_repair": not complete,
        },
        extra_units=extra_units,
        divergent_only=divergent_only,
    )
    return ReconciliationPage(changes, proposed)


def require_initial_dependencies(checkpoint: ReconciliationCheckpoint) -> None:
    """Activation guard; the checkpoint must itself have been published by B07."""
    if not checkpoint.completed_passes or checkpoint.completed_at is None:
        raise MetricChangeReadError("initial metric dependency backfill is incomplete")
