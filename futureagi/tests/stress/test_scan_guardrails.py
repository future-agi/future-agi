"""Benchmark for the trace-scanner's per-query ClickHouse guardrails.

Two reportable facts, both against a loadgen-seeded ClickHouse:

  1. TRIPWIRE — under ``scan_ch_guardrails()`` with ``max_memory_usage`` pinned
     down to 1 MB, the scanner's real read (``list_by_trace_ids`` — ``FROM spans
     FINAL`` pulling the fat JSON columns, unchunked over all noise trace ids) is
     rejected with a query-level CH **code 241** instead of exhausting the
     server. A subsequent normal query on the same CH succeeds, proving the
     server stayed healthy after the query-level rejection (not a box-wide OOM).

  2. HEADROOM — a realistic scanner-sized batch (``_SWEEP_BATCH_SIZE`` trace ids)
     read under the *real* 4 GiB guardrail completes, and its ``system.query_log``
     peak ``memory_usage`` sits far below the cap. The peak is printed so the PR
     can report the measured number.

The guardrail's *sizing* (4 GiB right-headroom-but-below-ceiling) is justified by
the prod-scale dev-box numbers; this local run proves the *mechanism* end-to-end
on the scanner's actual reader.
"""

from __future__ import annotations

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from tests.stress import ch_asserts
from tests.stress.ch_asserts import ch_query_budget
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.query_settings import ch_query_settings
from tracer.tasks.trace_scanner import (
    _SWEEP_BATCH_SIZE,
    SCAN_CH_GUARDRAILS,
    scan_ch_guardrails,
)

pytestmark = pytest.mark.stress

# 1 MB is far below any real spans scan, so the code-241 rejection is
# deterministic regardless of dataset size.
_TINY_MEMORY_CAP = 1_000_000  # 1 MB


def test_scan_read_rejected_at_tiny_cap_server_stays_healthy(stress_dataset):
    """The scanner's read fails at the query level (code 241); server survives."""
    noise = stress_dataset.noise

    # Inner ch_query_settings overrides only max_memory_usage; the execution-time
    # and external-sort caps from scan_ch_guardrails() stay in effect.
    with pytest.raises(DatabaseError) as exc_info:
        with scan_ch_guardrails():
            with ch_query_settings(max_memory_usage=_TINY_MEMORY_CAP):
                with get_reader() as reader:
                    reader.list_by_trace_ids(noise.trace_ids)

    assert "241" in str(exc_info.value)

    # A fresh client outside any guardrail context must still complete a trivial
    # read — the query-level rejection did not take the server down.
    client = ch_asserts._client()
    try:
        cnt = client.query("SELECT count() FROM spans").result_rows[0][0]
        assert int(cnt) >= 0
    finally:
        client.close()


def test_realistic_scan_batch_peaks_below_cap(stress_dataset):
    """A scanner-sized batch read under the real 4 GiB guardrail; report the peak."""
    noise = stress_dataset.noise
    batch = noise.trace_ids[:_SWEEP_BATCH_SIZE]  # the scanner reads in these batches

    with scan_ch_guardrails():
        with ch_query_budget("scan-guardrail-bench") as budget:
            with get_reader() as reader:
                spans = reader.list_by_trace_ids(batch)

    assert budget.count >= 1, "the tagged scanner read was not captured in query_log"
    peak = budget.max("memory_usage")
    read_rows = budget.max("read_rows")
    cap = SCAN_CH_GUARDRAILS["max_memory_usage"]

    # Report the measured peak so the number lands in the PR / terminal output.
    print(
        f"\nSCAN-GUARDRAIL-BENCH :: batch={len(batch)} traces "
        f"spans={len(spans)} read_rows={read_rows:.0f} "
        f"peak_memory={peak / 2**20:.1f} MiB "
        f"cap={cap / 2**30:.0f} GiB "
        f"headroom={cap / max(peak, 1):.0f}x"
    )

    # A normal scanner batch must sit comfortably under the cap — otherwise the
    # guardrail would false-positive on legitimate work.
    assert peak < cap
