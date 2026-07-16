"""Per-query ClickHouse guardrail settings for the eval-task engine.

These caps ensure that a heavy eval read fails at the query level (a visible,
retryable error) rather than exhausting server memory. Sorts spill to disk
instead of OOM-ing the CH process.

Values are conservative starting points. Tune them against dev-GCP reality
once production-scale data and real server memory headroom are available:
measure peak per-query memory and execution time there, then record the finals
here and in the operational runbook.
"""

from __future__ import annotations

from contextlib import contextmanager

from tracer.services.clickhouse.v2.query_settings import ch_query_settings

# Per-query limits applied to every CH read the eval engine issues.
EVAL_CH_GUARDRAILS: dict[str, int] = {
    "max_memory_usage": 4 * 2**30,  # 4 GiB — hard cap per query
    "max_execution_time": 120,  # seconds — kill runaway queries
    "max_bytes_before_external_sort": 2 * 2**30,  # 2 GiB spill threshold
}


@contextmanager
def eval_ch_guardrails():
    """Apply per-query CH guardrails for eval reads.

    Nest around every CH read the eval engine issues. Inner
    ``ch_query_settings`` overrides win (e.g. test fixtures can tighten
    ``max_memory_usage`` to a smaller value to probe the tripwire).
    """
    with ch_query_settings(**EVAL_CH_GUARDRAILS):
        yield
