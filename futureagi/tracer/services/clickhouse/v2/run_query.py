"""
v1↔v2 query execution wrapper.

Views previously did this for a CH-backed query:

    BuilderCls = get_query_builder_class("SPAN_LIST")
    builder    = BuilderCls(**kwargs)
    sql, params = builder.build()
    result     = analytics.execute_ch_query(sql, params, timeout_ms=…)

That works for V1_ONLY / V2_ONLY / V2_PRIMARY (returns the active version)
but it CAN'T run shadow mode (v1 + v2 in parallel, compare, log diff,
return v1). For that we need to hold BOTH builders + run BOTH queries.

This module provides `run_with_v2_shadow()` — the shadow-aware
replacement. View call sites become:

    result = run_with_v2_shadow(
        query_type="SPAN_LIST",
        build=lambda Cls: Cls(**kwargs).build(),
        execute=lambda sql, params: analytics.execute_ch_query(
            sql, params, timeout_ms=10000),
    )

The `build` callable takes a builder CLASS so the wrapper can pass v1 or
v2 (or both, in shadow mode) without the view code knowing the
difference. `execute` is plumbed the same way so the wrapper can run two
queries with the same execution semantics the view would use.

Routing decisions and diff-logging live in v2/shadow.py — this module
just glues that to the dispatch table + the execute callable.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Tuple

import structlog

from tracer.services.clickhouse.v2.dispatch import (
    get_query_builder_class,
    get_v1_class,
    get_v2_class,
)
from tracer.services.clickhouse.v2.shadow import (
    RoutingMode,
    ShadowResult,
    get_routing_mode,
)

logger = structlog.get_logger(__name__)


# Helper type aliases — these are just callables; declaring them makes the
# helper signatures self-documenting.
BuildFn   = Callable[[type], Tuple[str, dict]]
ExecuteFn = Callable[[str, dict], Any]


def run_with_v2_shadow(
    *,
    query_type: str,
    build:   BuildFn,
    execute: ExecuteFn,
    normalize: Callable[[Any], Any] = lambda x: x,
) -> Any:
    """Run a query under the v1↔v2 dispatch with shadow support.

    Arguments:
      • query_type: e.g. "SPAN_LIST" — used to pick v1 / v2 / shadow per
        the CLICKHOUSE_V2.QUERY_TYPES_* settings.
      • build: closure that takes a builder CLASS and returns
        `(sql, params)`. The wrapper invokes it with v1 and/or v2 class as
        appropriate. The view writes this closure once per logical query
        (the same lambda used for build, build_count_query,
        build_content_query, etc.).
      • execute: closure that takes `(sql, params)` and returns the
        result (typically `analytics.execute_ch_query(sql, params, timeout_ms=…)`).
        The wrapper invokes it the same way for v1 and v2 so behavior on
        connection errors / timeouts is identical.
      • normalize: optional canonicalizer applied before diffing in shadow
        mode (e.g. round floats, sort rows). Default = identity.

    Returns: the result `execute` produces for the user-facing path. In
    SHADOW mode that's v1; in V2_PRIMARY it's v2; in V1_ONLY / V2_ONLY
    it's the only one that ran.
    """
    mode = get_routing_mode(query_type)

    if mode == RoutingMode.DISABLED:
        logger.warning("ch25_query_disabled", query_type=query_type)
        return None

    if mode == RoutingMode.V1_ONLY:
        v1_cls = get_v1_class(query_type)
        sql, params = build(v1_cls)
        return execute(sql, params)

    if mode == RoutingMode.V2_ONLY:
        v2_cls = get_v2_class(query_type)
        if v2_cls is None:
            logger.warning(
                "ch25_v2_only_but_no_v2_class_falling_back",
                query_type=query_type,
            )
            v2_cls = get_v1_class(query_type)
        sql, params = build(v2_cls)
        return execute(sql, params)

    # SHADOW or V2_PRIMARY — run BOTH in parallel, diff, return per mode.
    v1_cls = get_v1_class(query_type)
    v2_cls = get_v2_class(query_type)
    if v2_cls is None:
        logger.warning(
            "ch25_shadow_requested_but_no_v2_class_falling_back_to_v1",
            query_type=query_type,
        )
        sql, params = build(v1_cls)
        return execute(sql, params)

    def _run_v1():
        sql, params = build(v1_cls)
        return execute(sql, params)

    def _run_v2():
        sql, params = build(v2_cls)
        return execute(sql, params)

    sr = ShadowResult(query_type=query_type, mode=mode)
    import time

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_run_v1)
        f2 = ex.submit(_run_v2)
        v1_ok, v2_ok = True, True
        v1_result = v2_result = None
        try:
            t1 = time.time()
            v1_result = f1.result()
            sr.v1_elapsed_ms = (time.time() - t1) * 1000
        except Exception as e:  # noqa: BLE001
            v1_ok = False
            sr.error_v1 = repr(e)[:300]
        try:
            t2 = time.time()
            v2_result = f2.result()
            sr.v2_elapsed_ms = (time.time() - t2) * 1000
        except Exception as e:  # noqa: BLE001
            v2_ok = False
            sr.error_v2 = repr(e)[:300]

    if v1_ok:
        sr.rows_v1 = _safe_len(v1_result)
    if v2_ok:
        sr.rows_v2 = _safe_len(v2_result)

    if v1_ok and v2_ok:
        n1 = normalize(v1_result)
        n2 = normalize(v2_result)
        if n1 != n2:
            sr.diff_count = 1
            sr.diff_examples = [{
                "v1_rows": _safe_len(v1_result),
                "v2_rows": _safe_len(v2_result),
                "v1_sample": _short(n1),
                "v2_sample": _short(n2),
            }]

    log_payload = {
        "query_type": query_type, "mode": mode.value,
        "v1_ms": sr.v1_elapsed_ms, "v2_ms": sr.v2_elapsed_ms,
        "rows_v1": sr.rows_v1, "rows_v2": sr.rows_v2,
        "diff_count": sr.diff_count,
        "err_v1": sr.error_v1, "err_v2": sr.error_v2,
    }
    if sr.diff_count or sr.error_v1 or sr.error_v2:
        logger.warning("ch25_shadow_diff", **log_payload,
                       diff_examples=sr.diff_examples)
    else:
        logger.info("ch25_shadow_match", **log_payload)

    # Return the user-facing result per mode.
    if mode == RoutingMode.V2_PRIMARY:
        if v2_ok:
            return v2_result
        # v2 failed — fall back to v1 to keep the user-facing path alive.
        logger.error("ch25_v2_primary_fallback_to_v1",
                     query_type=query_type, err=sr.error_v2)
        if v1_ok:
            return v1_result
        # Both failed — re-raise the v2 exception so the operator sees it.
        raise RuntimeError(
            f"ch25 V2_PRIMARY both v1 and v2 failed: "
            f"v1={sr.error_v1!r}, v2={sr.error_v2!r}"
        )

    # SHADOW: v1 is user-facing
    if v1_ok:
        return v1_result
    # v1 failed and shadow mode means we MUST return v1 — re-raise the v1 error.
    raise RuntimeError(f"ch25 SHADOW v1 failed: {sr.error_v1!r}")


def _safe_len(x: Any) -> int:
    try:
        return len(x)
    except TypeError:
        # QueryResult-style object — try its `data` attribute
        if hasattr(x, "data") and x.data is not None:
            try:
                return len(x.data)
            except TypeError:
                return 1
        return 1 if x is not None else 0


def _short(v: Any, limit: int = 300) -> str:
    s = repr(v)
    return s if len(s) <= limit else s[:limit] + "…"


__all__ = ["run_with_v2_shadow", "BuildFn", "ExecuteFn"]
