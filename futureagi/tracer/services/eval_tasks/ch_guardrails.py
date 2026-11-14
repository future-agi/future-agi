"""Per-query ClickHouse guardrail settings for the eval-task engine.

These caps keep every background eval statement inside the shared production
read policy. Sorts still spill to disk before the per-query memory ceiling.
"""

from __future__ import annotations

from contextlib import contextmanager

from tracer.services.clickhouse.v2.query_settings import ch_query_settings

# Per-query limits applied to every CH read the eval engine issues.
EVAL_CH_GUARDRAILS: dict[str, int] = {
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "max_execution_time": 30,
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
