"""S14: per-query CH guardrail tripwire.

Inside ``eval_ch_guardrails()`` with ``max_memory_usage`` overridden down to
1 MB via a nested ``ch_query_settings``, a deliberately heavy read (all noise
trace ids in one unchunked root lookup with fat JSON columns and no project
scoping, causing a full-table scan) must raise a CH code-241 query-level
memory error. A subsequent minimal COUNT on the same CH instance must succeed,
proving the server stayed healthy after the query-level rejection.
"""

from __future__ import annotations

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from tests.stress import ch_asserts
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.query_settings import ch_query_settings
from tracer.services.eval_tasks.ch_guardrails import eval_ch_guardrails

pytestmark = pytest.mark.stress

# 1 MB is far below any real spans scan, so the code-241 rejection is
# deterministic regardless of dataset size.
_TINY_MEMORY_CAP = 1_000_000  # 1 MB


def test_s14_guardrails_reject_heavy_query_server_stays_healthy(stress_dataset):
    """Query-level memory guard fires as a code-241 error; server stays healthy."""
    noise = stress_dataset.noise

    # The inner ch_query_settings overrides the outer eval_ch_guardrails value
    # for max_memory_usage only; execution_time and external_sort thresholds
    # from eval_ch_guardrails remain in effect.
    with pytest.raises(DatabaseError) as exc_info:
        with eval_ch_guardrails():
            with ch_query_settings(max_memory_usage=_TINY_MEMORY_CAP):
                with get_reader() as reader:
                    # All noise trace ids, unchunked, include_heavy=True pulls
                    # fat JSON columns (attributes_extra, span_events,
                    # resource_attrs). No project_id → full-table scan.
                    reader.list_root_spans_by_trace_ids(
                        noise.trace_ids,
                        include_heavy=True,
                    )

    assert "241" in str(exc_info.value)

    # Server is still healthy: a fresh client outside any guardrail context
    # must complete a trivial count without error.
    client = ch_asserts._client()
    try:
        cnt = client.query("SELECT count() FROM spans").result_rows[0][0]
        assert int(cnt) >= 0
    finally:
        client.close()
