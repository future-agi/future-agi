"""Tests under integrations/ run the Langfuse import without a real ClickHouse.

Before it stores a trace, tracer/utils/langfuse_upsert.py asks ClickHouse
which of the trace's span ids another project holds there, and it stores
nothing of the trace when ClickHouse cannot be read. The Langfuse import
suites here run without a ClickHouse holding such spans, so this answers the
read for every test here: no other project holds any of the ids.

This answer applies even where a test ClickHouse is up, so no import test
depends on rows other tests seeded there. A test opts into the real read by
setting ``tracer.utils.langfuse_upsert.get_v2_query_client`` to
``tracer.services.clickhouse.v2.query_service.get_v2_query_client``, as
tracer/tests/integration/test_langfuse_span_id_check_clickhouse.py does, or
to its own stand-in for held ids or a failing read.

The client is replaced where the upsert looks it up, not in query_service,
so every other ClickHouse reader keeps its own client.
"""

import pytest


class NoSpanIdHeldElsewhere:
    """A ClickHouse read client whose answer to the upsert's id check is: none."""

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        return [], [], 0.0


@pytest.fixture(autouse=True)
def _langfuse_upsert_reads_no_clickhouse(monkeypatch):
    # raising=False: before the id check exists the upsert has no such name.
    monkeypatch.setattr(
        "tracer.utils.langfuse_upsert.get_v2_query_client",
        NoSpanIdHeldElsewhere,
        raising=False,
    )
