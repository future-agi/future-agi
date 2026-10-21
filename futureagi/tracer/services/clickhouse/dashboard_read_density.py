"""Predict what an exact dashboard statement will cost before running it.

A cold dashboard widget used to learn that its read was too heavy only by
running it: the full exact statement ran inline under the interactive wall,
failed there, and the background exact worker then re-ran the identical SQL
under identical settings. The wall was spent twice and nothing was published
any sooner.

This module answers the same question from metadata instead. ``EXPLAIN
ESTIMATE`` over the statement's own identity-discovery CTE reports the rows
the part index says that scan will touch, without reading a single part; a
density record the product learns from its own completed statements turns
those rows into milliseconds. The prediction is compared against the deadline
the request actually has left, never against a window or a seconds constant.

Two of the legs are optional and fall back to today's inline path: an unknown
scope (no density record yet) and an unmeasured statement (a transport that
reports no bytes) mean "no prediction". The probe leg does not. A statement
that CAN be costed - it embeds the candidate CTE - and whose probe fails is an
uncosted read, and an uncosted read is exactly the one least safe to issue on
the interactive wall: the shape this gate exists to remove is a probe that
cannot answer followed by the full statement spending the whole wall and
publishing nothing. Absence of proof means schedule, not scan; the bounded
worker then costs it again on a wall that can survive being wrong about it.
A statement with no candidate CTE has nothing cheap to estimate and is never
probed, so it is not "unknown" in this sense and keeps the inline path.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from django.core.cache import cache

from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

logger = structlog.get_logger(__name__)

# Smoothing and lifetime, not query shape: the weight given to the newest
# observation, and how long an unvisited scope's learned density survives.
# Neither bounds a read window; both only decide how fast the record follows
# a scope whose data volume changes.
_DENSITY_EMA_ALPHA = 0.3
_DENSITY_TTL_SECONDS = 7 * 24 * 60 * 60
# Bump when the recorded quantities change meaning.
_DENSITY_CACHE_VERSION = 1
# The probe reads part metadata and no parts at all, so it needs one thread
# and no read budget. Like every other application read it is bounded by the
# client's socket wall, not by a statement deadline.
_ESTIMATE_PROBE_SETTINGS = {"max_threads": 1}


@dataclass(frozen=True)
class ReadDensityRecord:
    """What one scope's exact statements have cost per estimated row."""

    bytes_per_estimated_row: float
    bytes_per_ms: float
    samples: int

    def predict_ms(self, estimated_rows: int) -> float | None:
        """Return predicted statement milliseconds, or None if unpredictable."""

        if self.bytes_per_estimated_row <= 0 or self.bytes_per_ms <= 0:
            return None
        if estimated_rows < 0:
            return None
        return estimated_rows * self.bytes_per_estimated_row / self.bytes_per_ms


def density_scope_key(project_ids: Iterable[str]) -> str:
    """Return the cache key for one read scope.

    Read density is a property of the data a scope holds, so the key is the
    scope itself. It is hashed because project identity is not something a
    cache key needs to carry in the clear.
    """

    digest = hashlib.sha256(
        "\x1f".join(sorted(str(project_id) for project_id in project_ids)).encode()
    ).hexdigest()
    return f"dashboard_read_density:v{_DENSITY_CACHE_VERSION}:{digest}"


def read_density_record(scope_key: str) -> ReadDensityRecord | None:
    """Return the learned record for *scope_key*, or None before it exists."""

    try:
        stored = cache.get(scope_key)
    except Exception:
        logger.warning("dashboard_read_density_unavailable", exc_info=True)
        return None
    if not isinstance(stored, dict):
        return None
    try:
        record = ReadDensityRecord(
            bytes_per_estimated_row=float(stored["bytes_per_estimated_row"]),
            bytes_per_ms=float(stored["bytes_per_ms"]),
            samples=int(stored["samples"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return record


def _blend(previous: float, observed: float) -> float:
    if previous <= 0:
        return observed
    return (1 - _DENSITY_EMA_ALPHA) * previous + _DENSITY_EMA_ALPHA * observed


def observe_completed_read(
    scope_key: str | None,
    estimated_rows: int | None,
    result: Any,
) -> None:
    """Fold one completed statement into the scope's density record.

    ``estimated_rows`` is absent whenever the statement was not probed (the
    background worker never probes), in which case only throughput is learned.
    A statement the transport could not measure teaches nothing at all.
    """

    if not scope_key:
        return
    try:
        read_bytes = getattr(result, "read_bytes", None)
        elapsed_ms = getattr(result, "query_time_ms", None)
        if read_bytes is None or not read_bytes:
            return
        previous = read_density_record(scope_key)
        bytes_per_estimated_row = previous.bytes_per_estimated_row if previous else 0.0
        bytes_per_ms = previous.bytes_per_ms if previous else 0.0
        samples = previous.samples if previous else 0
        if estimated_rows:
            bytes_per_estimated_row = _blend(
                bytes_per_estimated_row, read_bytes / estimated_rows
            )
        if elapsed_ms:
            bytes_per_ms = _blend(bytes_per_ms, read_bytes / elapsed_ms)
        cache.set(
            scope_key,
            {
                "bytes_per_estimated_row": bytes_per_estimated_row,
                "bytes_per_ms": bytes_per_ms,
                "samples": samples + 1,
            },
            _DENSITY_TTL_SECONDS,
        )
    except Exception:
        logger.warning("dashboard_read_density_observe_failed", exc_info=True)


def _estimate_key(estimate_sql: str, params: dict) -> str:
    """Identify one probe by the statement *and* the values bound into it.

    Two metric groups filtering the same attribute for different values render
    byte-identical candidate SQL and scan wildly different numbers of rows.
    Keying on the text alone would hand one group's estimate to the other.
    """

    return f"{estimate_sql}\x00{sorted((params or {}).items(), key=repr)!r}"


def probe_candidate_estimates(
    statements: Sequence[tuple[str, dict]],
    *,
    analytics: Any,
    deadline: ReadDeadline,
) -> dict[str, int | None]:
    """Estimate each distinct candidate read these statements embed.

    The returned mapping is keyed by statement text together with the values
    bound into it, so ``estimated_rows_for`` recovers a statement's own
    estimate and never a sibling's. A statement with no candidate CTE is not
    in the mapping at all: there is nothing cheap to estimate for it. A
    statement whose probe could not answer - it raised, or the deadline was
    already gone before it could be asked - is in the mapping as ``None``:
    costable, and NOT costed. The two are different facts and
    ``exceeds_remaining_deadline`` treats them differently.
    """

    estimates: dict[str, int | None] = {}
    for sql, params in statements:
        estimate_sql = DashboardQueryBuilderV2.candidate_estimate_statement(sql)
        if estimate_sql is None:
            continue
        key = _estimate_key(estimate_sql, params)
        if key in estimates:
            continue
        try:
            timeout_ms = deadline.remaining_ms()
        except ReadDeadlineExceeded:
            # No time to ask, so no answer: unknown, not absent.
            estimates[key] = None
            continue
        try:
            result = analytics.execute_ch_query(
                estimate_sql,
                params,
                timeout_ms=timeout_ms,
                settings=dict(_ESTIMATE_PROBE_SETTINGS),
            )
            estimates[key] = sum(
                int(row.get("rows") or 0) for row in (result.data or [])
            )
        except Exception:
            # ``None`` is "unknown", never "small". The caller routes an
            # unknown read to the wall that can survive being wrong about it.
            logger.info("dashboard_candidate_estimate_unavailable", exc_info=True)
            estimates[key] = None
    return estimates


def estimated_rows_for(
    estimates: dict[str, int | None],
    sql: str,
    params: dict,
) -> int | None:
    """Return the estimate probed for this statement, or None if unprobed."""

    estimate_sql = DashboardQueryBuilderV2.candidate_estimate_statement(sql)
    if estimate_sql is None:
        return None
    return estimates.get(_estimate_key(estimate_sql, params))


def exceeds_remaining_deadline(
    estimates: dict[str, int | None],
    *,
    scope_key: str,
    remaining_ms: int,
) -> bool:
    """Whether any probed statement is predicted to outlast the request.

    A costable statement whose probe could not answer is not admitted to the
    interactive wall: nobody could say what it costs, and treating "unknown"
    as "affordable" reproduces the defect this gate removes one probe earlier,
    precisely when the system knows least about the read. Unknown means
    schedule. A scope with no density record yet is a different case - the
    estimate is known, only the rate is not - and keeps the inline path.
    """

    if not estimates:
        return False
    if any(estimated_rows is None for estimated_rows in estimates.values()):
        logger.info("dashboard_trace_read_uncosted_not_admitted_inline")
        return True
    record = read_density_record(scope_key)
    if record is None:
        return False
    for estimated_rows in estimates.values():
        predicted_ms = record.predict_ms(estimated_rows)
        if predicted_ms is None or predicted_ms <= remaining_ms:
            continue
        logger.info(
            "dashboard_trace_read_predicted_over_deadline",
            estimated_rows=estimated_rows,
            predicted_ms=round(predicted_ms, 3),
            remaining_ms=remaining_ms,
            density_samples=record.samples,
        )
        return True
    return False


__all__ = [
    "ReadDensityRecord",
    "density_scope_key",
    "estimated_rows_for",
    "exceeds_remaining_deadline",
    "observe_completed_read",
    "probe_candidate_estimates",
    "read_density_record",
]
