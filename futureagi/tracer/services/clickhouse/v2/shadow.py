"""
Parity-shadow harness for the CH 25.3 query-layer migration.

The cutover from legacy CH (v1, span_attr_str etc.) to new CH (v2, attrs_string
etc.) is risky if done in a single flag-flip — the new query builders touch
different columns with different access syntax (typed JSON path access vs
JSONExtractString, ReplacingMergeTree(_version, is_deleted) vs the older
engine, etc.). A regression here would be silent: the FE would still get
results, just SUBTLY wrong ones (different counts, different attribute values,
different aggregations).

The shadow runner mitigates this by executing BOTH the v1 and v2 query
builders for a given request, comparing the result rows, and:
  • returning the v1 result to the user (the user-facing path is unchanged)
  • logging any mismatch with enough context to triage
  • emitting a Prometheus counter for "shadow_diff_count{query_type=X}"

Once a query type logs ZERO diffs across a 24-48h observation window, the
operator flips that query type to v2-primary via the
`CLICKHOUSE_V2.QUERY_TYPES_V2_PRIMARY` setting; v1 stops running for that
type. After all types are flipped, v1 query builders can be deleted.

This module is intentionally thin — the parity comparison is shape-agnostic
so each query type's specific normalization (sorting, float-rounding,
UTC datetime coercion) lives in the per-builder code.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)


class RoutingMode(str, Enum):
    """How the dispatcher routes a query type."""
    V1_ONLY    = "v1"          # legacy CH path only (default until flipped)
    V2_ONLY    = "v2"          # new CH path only (post-cutover steady state)
    SHADOW     = "shadow"      # both run; v1 returned to user; diff logged
    V2_PRIMARY = "v2_primary"  # v2 returned; v1 still run in shadow for safety
    DISABLED   = "disabled"    # neither run (e.g., during a CH outage drill)


@dataclass
class ShadowResult:
    """Outcome of a shadow comparison for one request."""
    query_type: str
    mode: RoutingMode
    v1_elapsed_ms: float | None = None
    v2_elapsed_ms: float | None = None
    rows_v1:       int | None = None
    rows_v2:       int | None = None
    diff_count:    int | None = None
    diff_examples: list[dict[str, Any]] = field(default_factory=list)
    error_v1:      str | None = None
    error_v2:      str | None = None


def get_routing_mode(query_type: str) -> RoutingMode:
    """Resolve routing mode for a given query type from settings.

    Settings layout (in `tfc/settings/settings.py`):
        CLICKHOUSE_V2 = {
            "QUERY_TYPES_V2_PRIMARY": "span_list,trace_list",     # v2 returned, v1 shadow
            "QUERY_TYPES_V2_ONLY":    "",                          # v2 returned, no shadow
            "QUERY_TYPES_SHADOW":     "dashboard,monitor_metrics", # v1 returned, v2 shadow
            "QUERY_TYPES_DISABLED":   "",
            # Anything not listed = V1_ONLY (default — safe until explicitly flipped).
        }
    """
    cfg = getattr(settings, "CLICKHOUSE_V2", {}) or {}
    qt = query_type.lower()

    def _matches(key: str) -> bool:
        raw = (cfg.get(key) or "").strip()
        if not raw:
            return False
        return qt in {t.strip().lower() for t in raw.split(",") if t.strip()}

    if _matches("QUERY_TYPES_DISABLED"):
        return RoutingMode.DISABLED
    if _matches("QUERY_TYPES_V2_ONLY"):
        return RoutingMode.V2_ONLY
    if _matches("QUERY_TYPES_V2_PRIMARY"):
        return RoutingMode.V2_PRIMARY
    if _matches("QUERY_TYPES_SHADOW"):
        return RoutingMode.SHADOW
    return RoutingMode.V1_ONLY


def run_with_shadow(
    *,
    query_type: str,
    v1_fn: Callable[[], Any],
    v2_fn: Callable[[], Any],
    normalize: Callable[[Any], Any] = lambda x: x,
    max_diff_examples: int = 5,
) -> tuple[Any, ShadowResult]:
    """Run v1 and v2 according to settings; return the user-facing result + diagnostics.

    The two callables are expected to return result-row lists (whatever shape
    the query builder produces). `normalize` is the per-query-type adapter
    that turns the raw result into a comparable canonical form (typically:
    sort rows, round floats, coerce datetimes to UTC strings).

    Concurrency: in SHADOW or V2_PRIMARY mode, v1 and v2 run in parallel via
    a 2-thread pool so the user-facing latency is max(v1_ms, v2_ms), not sum.
    """
    mode = get_routing_mode(query_type)
    sr = ShadowResult(query_type=query_type, mode=mode)

    if mode == RoutingMode.DISABLED:
        logger.warning("shadow_disabled", query_type=query_type)
        return [], sr

    if mode == RoutingMode.V1_ONLY:
        t0 = time.time()
        try:
            result = v1_fn()
            sr.v1_elapsed_ms = (time.time() - t0) * 1000
            sr.rows_v1 = _rowcount(result)
        except Exception as e:
            sr.error_v1 = repr(e)[:300]
            raise
        return result, sr

    if mode == RoutingMode.V2_ONLY:
        t0 = time.time()
        try:
            result = v2_fn()
            sr.v2_elapsed_ms = (time.time() - t0) * 1000
            sr.rows_v2 = _rowcount(result)
        except Exception as e:
            sr.error_v2 = repr(e)[:300]
            raise
        return result, sr

    # SHADOW or V2_PRIMARY — run both in parallel.
    with ThreadPoolExecutor(max_workers=2) as ex:
        t0 = time.time()
        f1 = ex.submit(_timed, v1_fn)
        f2 = ex.submit(_timed, v2_fn)
        r1, e1, ms1 = f1.result()
        r2, e2, ms2 = f2.result()

    sr.v1_elapsed_ms = ms1
    sr.v2_elapsed_ms = ms2
    if e1 is not None:
        sr.error_v1 = repr(e1)[:300]
    else:
        sr.rows_v1 = _rowcount(r1)
    if e2 is not None:
        sr.error_v2 = repr(e2)[:300]
    else:
        sr.rows_v2 = _rowcount(r2)

    # Diff and log
    if e1 is None and e2 is None:
        n1 = normalize(r1)
        n2 = normalize(r2)
        sr.diff_count, sr.diff_examples = _diff(n1, n2, max_diff_examples)

    log_payload = {
        "query_type": query_type, "mode": mode.value,
        "v1_ms": ms1, "v2_ms": ms2,
        "rows_v1": sr.rows_v1, "rows_v2": sr.rows_v2,
        "diff_count": sr.diff_count,
        "err_v1": sr.error_v1, "err_v2": sr.error_v2,
    }
    if sr.diff_count or sr.error_v1 or sr.error_v2:
        logger.warning("shadow_diff", **log_payload, diff_examples=sr.diff_examples)
    else:
        logger.info("shadow_match", **log_payload)

    # Choose what to return to the user.
    if mode == RoutingMode.V2_PRIMARY:
        if e2 is not None:
            # v2 failed — fall back to v1 to keep the user-facing path alive.
            logger.error("shadow_v2_primary_fallback_to_v1", err=sr.error_v2)
            if e1 is not None:
                raise e2
            return r1, sr
        return r2, sr
    else:  # SHADOW
        if e1 is not None:
            raise e1
        return r1, sr


def _timed(fn: Callable[[], Any]) -> tuple[Any, Exception | None, float]:
    t0 = time.time()
    try:
        return fn(), None, (time.time() - t0) * 1000
    except Exception as e:  # noqa: BLE001
        return None, e, (time.time() - t0) * 1000


def _rowcount(result: Any) -> int:
    try:
        return len(result)
    except TypeError:
        return 1 if result is not None else 0


def _diff(a: Any, b: Any, max_examples: int) -> tuple[int, list[dict[str, Any]]]:
    """Shape-agnostic diff. Both sides must already be in canonical form
    (sorted, normalized) — the per-query-type `normalize` callback's job."""
    examples: list[dict[str, Any]] = []
    if a == b:
        return 0, examples

    if isinstance(a, list) and isinstance(b, list):
        # Count diffs and capture up to max_examples
        if len(a) != len(b):
            examples.append({"kind": "length", "v1_len": len(a), "v2_len": len(b)})
        for i, (ra, rb) in enumerate(zip(a, b)):
            if ra != rb:
                if len(examples) >= max_examples:
                    break
                examples.append({"kind": "row", "i": i, "v1": _short(ra), "v2": _short(rb)})
        return max(abs(len(a) - len(b)), sum(1 for ra, rb in zip(a, b) if ra != rb)), examples

    # Scalar / non-list: one diff
    examples.append({"kind": "scalar", "v1": _short(a), "v2": _short(b)})
    return 1, examples


def _short(v: Any, limit: int = 300) -> str:
    s = repr(v)
    return s if len(s) <= limit else s[:limit] + "…"
