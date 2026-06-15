"""
ShadowedQueryBuilder — multi-phase shadow execution wrapper.

Many views do MULTIPLE build+execute pairs on the same conceptual query
(SpanList view: build → build_count_query → build_content_query). For each
phase to participate in shadow mode (run v1 + v2 in parallel, compare),
the wrapper needs to hold BOTH builder instances and dispatch every method
call through the v1↔v2 routing decision.

Single-phase queries should use `run_with_v2_shadow()` from run_query.py.
Multi-phase queries with multiple sequential build+execute calls use this.

Operator UX from a view:

    ctx = ShadowedQueryBuilder(
        query_type="SPAN_LIST",
        builder_kwargs=dict(project_id=…, filters=…, page_number=…, …),
        analytics=analytics,
        timeout_ms=10000,
    )
    main_result    = ctx.run("build")
    count_result   = ctx.run("build_count_query")
    content_result = ctx.run("build_content_query", span_ids)

The wrapper handles every routing mode the same way as `run_with_v2_shadow`:

  • V1_ONLY:    runs v1.<method>(*args).build() → execute → return
  • V2_ONLY:    runs v2.<method>(*args).build() → execute → return
  • SHADOW:     runs BOTH in parallel; v1 returned; diff logged
  • V2_PRIMARY: runs BOTH in parallel; v2 returned; v1 still shadowed
  • DISABLED:   returns None

Every `ctx.run()` call is INDEPENDENT — the routing decision is fresh
(supports operator flipping mid-soak). The wrapper holds builder instances
across calls so initialization cost is paid once per view request.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

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


class ShadowedQueryBuilder:
    """Holds v1 + v2 builder instances (per the active routing mode) and
    dispatches build+execute method calls through the right path.

    `analytics` is the same `AnalyticsQueryService` instance views already
    have; the wrapper uses its `execute_ch_query(sql, params, timeout_ms)`
    method to run the SQL.

    Thread-safety: the wrapper is not designed for concurrent `run()` calls
    on the same instance — views typically run phases sequentially. The
    parallel execution INSIDE a single `run()` call (v1 + v2 in shadow mode)
    uses a fresh ThreadPoolExecutor per call.
    """

    def __init__(
        self,
        *,
        query_type: str,
        builder_kwargs: dict,
        analytics,
        timeout_ms: int = 10000,
        normalize=lambda x: x,
    ):
        self.query_type = query_type
        self.builder_kwargs = builder_kwargs
        self.analytics = analytics
        self.timeout_ms = timeout_ms
        self._normalize = normalize

        # Resolve the routing mode ONCE at construction time. Subsequent
        # routing-mode changes (operator flips an env var mid-request) only
        # take effect on the next view request, not mid-page-load.
        self._mode = get_routing_mode(query_type)

        # Build v1/v2 instances lazily based on the mode. We pre-build both
        # in SHADOW/V2_PRIMARY so that each `run(method)` call doesn't pay
        # construction cost twice.
        self._v1: Optional[Any] = None
        self._v2: Optional[Any] = None

        if self._mode in (RoutingMode.V1_ONLY, RoutingMode.SHADOW, RoutingMode.V2_PRIMARY):
            self._v1 = get_v1_class(query_type)(**builder_kwargs)
        if self._mode in (RoutingMode.V2_ONLY, RoutingMode.SHADOW, RoutingMode.V2_PRIMARY):
            v2_cls = get_v2_class(query_type)
            if v2_cls is None:
                logger.warning(
                    "ch25_no_v2_class_falling_back_to_v1",
                    query_type=query_type, mode=self._mode.value,
                )
                # Re-resolve as if V1_ONLY for the rest of the request
                self._mode = RoutingMode.V1_ONLY
                if self._v1 is None:
                    self._v1 = get_v1_class(query_type)(**builder_kwargs)
            else:
                self._v2 = v2_cls(**builder_kwargs)

    # ─── Method dispatch ────────────────────────────────────────────────────
    def run(self, method_name: str, *args, **kwargs):
        """Build (sql, params) via `<builder>.<method_name>(*args)`, execute, return."""
        if self._mode == RoutingMode.DISABLED:
            logger.warning("ch25_disabled", query_type=self.query_type, method=method_name)
            return None

        if self._mode == RoutingMode.V1_ONLY:
            return self._run_one("v1", method_name, args, kwargs)

        if self._mode == RoutingMode.V2_ONLY:
            return self._run_one("v2", method_name, args, kwargs)

        # SHADOW / V2_PRIMARY — run both in parallel
        sr = ShadowResult(query_type=self.query_type, mode=self._mode)

        def _v1():
            return self._run_one("v1", method_name, args, kwargs)

        def _v2():
            return self._run_one("v2", method_name, args, kwargs)

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(_v1)
            f2 = ex.submit(_v2)
            v1_ok = v2_ok = True
            v1_result = v2_result = None
            try:
                t0 = time.time()
                v1_result = f1.result()
                sr.v1_elapsed_ms = (time.time() - t0) * 1000
            except Exception as e:  # noqa: BLE001
                v1_ok = False
                sr.error_v1 = repr(e)[:300]
            try:
                t1 = time.time()
                v2_result = f2.result()
                sr.v2_elapsed_ms = (time.time() - t1) * 1000
            except Exception as e:  # noqa: BLE001
                v2_ok = False
                sr.error_v2 = repr(e)[:300]

        # Diff + log
        if v1_ok and v2_ok:
            n1 = self._normalize(_rows_for_diff(v1_result))
            n2 = self._normalize(_rows_for_diff(v2_result))
            if n1 != n2:
                sr.diff_count = 1
                sr.diff_examples = [{
                    "v1_rows": _safe_len(v1_result),
                    "v2_rows": _safe_len(v2_result),
                }]

        log_payload = {
            "query_type": self.query_type, "mode": self._mode.value,
            "method": method_name,
            "v1_ms": sr.v1_elapsed_ms, "v2_ms": sr.v2_elapsed_ms,
            "rows_v1": _safe_len(v1_result) if v1_ok else None,
            "rows_v2": _safe_len(v2_result) if v2_ok else None,
            "diff_count": sr.diff_count,
            "err_v1": sr.error_v1, "err_v2": sr.error_v2,
        }
        if sr.diff_count or sr.error_v1 or sr.error_v2:
            logger.warning("ch25_shadow_diff", **log_payload)
        else:
            logger.info("ch25_shadow_match", **log_payload)

        # Pick user-facing result per mode.
        if self._mode == RoutingMode.V2_PRIMARY:
            if v2_ok:
                return v2_result
            logger.error("ch25_v2_primary_fallback_to_v1",
                         query_type=self.query_type, method=method_name,
                         err=sr.error_v2)
            if v1_ok:
                return v1_result
            raise RuntimeError(
                f"ch25 V2_PRIMARY {self.query_type}.{method_name} both v1 and v2 failed: "
                f"v1={sr.error_v1!r}, v2={sr.error_v2!r}"
            )

        # SHADOW: v1 is user-facing
        if v1_ok:
            return v1_result
        raise RuntimeError(
            f"ch25 SHADOW {self.query_type}.{method_name} v1 failed: {sr.error_v1!r}"
        )

    # ─── Per-version build+execute ──────────────────────────────────────────
    def _run_one(self, version: str, method_name: str, args: tuple, kwargs: dict):
        builder = self._v1 if version == "v1" else self._v2
        if builder is None:
            raise RuntimeError(f"ShadowedQueryBuilder: no {version} builder registered")
        method = getattr(builder, method_name)
        sql, params = method(*args, **kwargs)
        return self.analytics.execute_ch_query(sql, params, timeout_ms=self.timeout_ms)

    # ─── Static-style classmethod passthrough ──────────────────────────────
    def classmethod_call(self, method_name: str, *args, **kwargs):
        """For static-style helpers like `SpanListQueryBuilder.pivot_eval_results(rows)`.
        v1 and v2 are the same classmethod (v2 inherits unchanged), so just
        call it on whichever builder we have."""
        builder = self._v1 if self._v1 is not None else self._v2
        if builder is None:
            raise RuntimeError("ShadowedQueryBuilder: no builders registered")
        method = getattr(type(builder), method_name)
        return method(*args, **kwargs)


def _rows_for_diff(result) -> Any:
    """Pull the comparable rows out of a QueryResult-like object."""
    if result is None:
        return None
    if hasattr(result, "data"):
        return result.data
    return result


def _safe_len(x) -> int:
    if x is None:
        return 0
    if hasattr(x, "data") and x.data is not None:
        try:
            return len(x.data)
        except TypeError:
            return 1
    try:
        return len(x)
    except TypeError:
        return 1


__all__ = ["ShadowedQueryBuilder"]
