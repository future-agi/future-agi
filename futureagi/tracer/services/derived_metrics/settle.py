"""Durable settle queue proposals, published with fields and buckets by B07.

DCM2: indexed physical open/overdue rows are a nomination SUPERSET only.
Verify the latest committed state by immutable (level, unit_id), then re-read
latest source arrival/root-ended state before deciding. An excluded attempt,
stale open row or settled tombstone must never resurrect work. There is no
wall-clock lower bound: overdue units survive arbitrarily long outages.

The writer freezes every returned update alongside its value/bucket updates.
Only manifest publication makes a queue tombstone or reopened state visible.
The source loader is mandatory and shared with the writer's recomputation;
using the queue row's cached arrival/root state as that loader is not valid.

The committed queue arrival is a floor. The writer's live-span arrival can be
lower than it (the newest span was soft-deleted, or a replica lags); due_at
never moves earlier than committed knowledge, and the writer publishes the
field row's open/settled status from the returned LifecycleUpdate.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from tracer.services.derived_metrics.changes import (
    COMMITTED,
    MAX_KEYS,
    MetricChangeReadError,
    MetricReadBudget,
    UnitKey,
    _block_settings,
    _chunks,
    _scope,
    from_ns,
    keyset_after,
    read,
    utc,
)


@dataclass(frozen=True)
class UnitSnapshot:
    key: UnitKey
    last_arrival_ns: int
    first_seen: datetime
    exists: bool = True
    root_ended: bool = False
    span_ended: bool = False


@dataclass(frozen=True)
class UnitState:
    key: UnitKey
    last_arrival_ns: int
    first_seen: datetime
    due_at: datetime
    reopened: bool = False
    is_deleted: bool = False

    def payload(self) -> dict[str, Any]:
        """B07 adds all publication keys before freezing this row for insert."""
        value = asdict(self)
        value.pop("key")
        return {**value, "level": self.key.level, "unit_id": self.key.unit_id}


@dataclass(frozen=True)
class LifecycleUpdate:
    state: UnitState
    status: (
        str  # open, settled, deleted (queue tombstone is distinct from value deletion)
    )
    settled_at: datetime | None = None


def evaluate_settle(
    snapshot: UnitSnapshot,
    *,
    previous: UnitState | None,
    now: datetime,
    settle_minutes: int,
) -> LifecycleUpdate | None:
    """Evaluate a fresh arrival or a due candidate, without mutating either store.

    A settled queue row is a tombstone, not an instruction to forget the unit.
    The arrival path calls this too: a newer span reopens a tombstoned unit and
    moves due_at forward. Repeated ticks on an unchanged tombstone are no-ops.
    """
    if not 1 <= settle_minutes <= 1440:
        raise ValueError("settle_minutes must be between 1 and 1440")
    if snapshot.last_arrival_ns < 0:
        raise ValueError("last arrival cannot be negative")
    if previous is not None and previous.key != snapshot.key:
        raise ValueError("settle state and source keys differ")
    now = utc(now)
    # Never settle earlier than, or forget, an arrival already committed. A
    # lower live-span arrival is expected after the newest span is deleted;
    # raising here would stall every later pass on that one unit.
    last = max(snapshot.last_arrival_ns, previous.last_arrival_ns if previous else 0)
    if not snapshot.exists:
        if previous and previous.is_deleted:
            return None
        return LifecycleUpdate(
            UnitState(
                snapshot.key,
                last,
                utc(snapshot.first_seen),
                now,
                is_deleted=True,
            ),
            "deleted",
        )
    if previous and previous.is_deleted and last == previous.last_arrival_ns:
        return None
    reopened = bool(previous and (previous.reopened or previous.is_deleted))
    due_at = from_ns(last) + timedelta(minutes=settle_minutes)
    if snapshot.key.level == "span":
        ready = snapshot.span_ended
    else:
        ready = now >= due_at and (snapshot.key.level != "trace" or snapshot.root_ended)
    state = UnitState(
        snapshot.key, last, utc(snapshot.first_seen), due_at, reopened, ready
    )
    if not ready and state == previous:
        return None
    return LifecycleUpdate(
        state, "settled" if ready else "open", now if ready else None
    )


@dataclass(frozen=True)
class DuePage:
    updates: tuple[LifecycleUpdate, ...]
    nominated: int
    verified: int
    after: UnitKey | None
    exhausted: bool


def settle_due_page(
    analytics,
    *,
    org_id: str,
    project_id: str,
    committed_attempts: Sequence[str],
    now: datetime,
    settle_minutes: int,
    load_latest: Callable[
        [tuple[UnitKey, ...], MetricReadBudget], Mapping[UnitKey, UnitSnapshot]
    ],
    after: UnitKey | None = None,
    page_size: int = 1000,
    budget: MetricReadBudget | None = None,
) -> DuePage:
    """Read one finite keyset page of the unbounded-age durable backlog.

    Continue with after until exhausted, under the same pinned manifest and
    frozen now, or commit a bounded chunk and restart from the beginning on the
    new head. Never retain an after cursor forever across changing manifests:
    new work may sort behind it. A later page's failure publishes no partial
    state. A caller that accumulates pages must share the same read budget.
    load_latest(keys, budget) must consume this same budget for source reads
    and return an explicit exists=False snapshot for absent units; missing keys
    fail closed, never silently discard overdue work.
    """
    if not 1 <= page_size <= MAX_KEYS:
        raise ValueError("invalid metric settle page size")
    budget = budget or MetricReadBudget()
    params = _scope(org_id, project_id, committed_attempts)
    now = utc(now)
    query_params = {**params, "now": now, "page_limit": page_size}
    keyset = ""
    if after is not None:
        clause, keyset_params = keyset_after(
            ("level", "unit_id"), (after.level, after.unit_id), "due_after"
        )
        keyset = "AND " + clause
        query_params.update(keyset_params)
    # Raw rows, no DISTINCT: read-in-order on the sorting key stops after
    # about one page. Duplicate generations of a unit are adjacent.
    rows = read(
        analytics,
        f"""SELECT level, unit_id FROM metric_unit_state
        PREWHERE org_id = toUUID(%(org_id)s)
            AND project_id = toUUID(%(project_id)s)
        WHERE is_deleted = 0 AND due_at <= %(now)s {keyset}
        ORDER BY level, unit_id LIMIT %(page_limit)s""",
        query_params,
        budget,
        settings=_block_settings(page_size),
    )
    more = len(rows) == page_size
    keys = tuple(
        dict.fromkeys(UnitKey(str(r["level"]), str(r["unit_id"])) for r in rows)
    )
    if keys and after is not None and keys[-1] <= after:
        raise MetricChangeReadError("metric settle keyset did not advance")
    if not keys or not params["committed_attempts"]:
        return DuePage((), len(keys), 0, keys[-1] if keys else after, not more)
    states = committed_unit_states(
        analytics,
        org_id=org_id,
        project_id=project_id,
        committed_attempts=committed_attempts,
        keys=keys,
        budget=budget,
    )
    verified = {
        key: state
        for key, state in states.items()
        if not state.is_deleted and state.due_at <= now
    }
    latest = load_latest(tuple(sorted(verified)), budget) if verified else {}
    if time.monotonic() >= budget.deadline:
        raise MetricChangeReadError("metric settle source deadline exceeded")
    if set(latest) != set(verified):
        raise MetricChangeReadError("latest settle source coverage is incomplete")
    updates = []
    for key, previous in sorted(verified.items()):
        if latest[key].key != key:
            raise MetricChangeReadError("latest settle source key mismatch")
        update = evaluate_settle(
            latest[key], previous=previous, now=now, settle_minutes=settle_minutes
        )
        if update:
            updates.append(update)
    return DuePage(
        tuple(updates), len(keys), len(verified), keys[-1] if keys else after, not more
    )


def committed_unit_states(
    analytics,
    *,
    org_id: str,
    project_id: str,
    committed_attempts: Sequence[str],
    keys: Sequence[UnitKey],
    budget: MetricReadBudget,
) -> dict[UnitKey, UnitState]:
    """Latest committed queue state per immutable (level, unit_id).

    Only immutable keys and the pinned allowlist enter the collapse; due_at and
    is_deleted are never pushed into it. The writer's arrival path uses this
    as ``previous`` for every discovered unit. Units absent from the result
    have no committed state.
    """
    params = _scope(org_id, project_id, committed_attempts)
    result: dict[UnitKey, UnitState] = {}
    if not params["committed_attempts"]:
        return result
    for batch in _chunks(sorted(set(keys))):
        rows = read(
            analytics,
            f"""SELECT level, unit_id,
                tupleElement(payload, 1) AS last_arrival_ns,
                tupleElement(payload, 2) AS first_seen,
                tupleElement(payload, 3) AS due_at,
                tupleElement(payload, 4) AS reopened,
                tupleElement(payload, 5) AS is_deleted
            FROM (
                SELECT level, unit_id,
                    argMax(tuple(last_arrival_ns, first_seen, due_at, reopened,
                        is_deleted), _version) AS payload
                FROM metric_unit_state WHERE {COMMITTED}
                    AND (level, unit_id) IN %(unit_keys)s
                GROUP BY level, unit_id
            )""",
            {**params, "unit_keys": tuple((k.level, k.unit_id) for k in batch)},
            budget,
        )
        for row in rows:
            key = UnitKey(str(row["level"]), str(row["unit_id"]))
            result[key] = UnitState(
                key,
                int(row["last_arrival_ns"]),
                utc(row["first_seen"]),
                utc(row["due_at"]),
                bool(row["reopened"]),
                bool(row["is_deleted"]),
            )
    return result
