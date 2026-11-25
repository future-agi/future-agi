# ruff: noqa: F401, F811 -- shared isolated native fixtures
"""Execution vector size must not inherit the private payload-page row limit."""

from uuid import uuid4

import pytest

from tracer.services.derived_metrics.publication import (
    MAX_BATCH_ROWS,
    WRITE_SETTINGS,
)
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_clickhouse import metric_ch
from tracer.tests.test_derived_metric_publication_clickhouse import (
    field_query,
    live_writer,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_scan_vectors_are_bounded_and_independent_of_transport(publication_store):
    sql = (
        "SELECT sum(number), max(blockSize()), uniqExact(blockNumber()) "
        "FROM numbers(50000)"
    )
    legacy = publication_store.client.execute(
        sql, settings={**WRITE_SETTINGS, "max_block_size": MAX_BATCH_ROWS}
    )[0]
    actual = publication_store.execute(sql)[0]
    assert actual[0] == legacy[0] == 49999 * 50000 // 2
    assert MAX_BATCH_ROWS < actual[1] <= 8192
    assert actual[2] < legacy[2] / 4
    assert WRITE_SETTINGS["preferred_block_size_bytes"] == 4 * 1024 * 1024
    assert WRITE_SETTINGS["max_memory_usage"] == 512 * 1024 * 1024
    assert WRITE_SETTINGS["max_threads"] == 2
    assert WRITE_SETTINGS["max_execution_time"] == 60


def test_larger_execution_vectors_do_not_enlarge_frozen_pages(live_writer):
    writer = live_writer
    attempt = writer.begin()
    # A producer with more than one publication page must still freeze bounded
    # pages. Exercise the real writer, not a synthetic transport implementation.
    query, params = field_query("vector-owner", metric=uuid4())
    query = (
        "SELECT * REPLACE (concat(unit_id, '-', toString(number)) AS unit_id) "
        f"FROM ({query}) AS fields CROSS JOIN numbers(1201) AS units"
    )
    writer.freeze(attempt, FIELD_VALUES, query, params)
    plans = list(writer.plans(attempt))
    assert sum(len(plan["rows"]) for plan in plans) == 1201
    assert max(len(plan["rows"]) for plan in plans) == MAX_BATCH_ROWS
    assert len(plans) == 3


def test_empty_digest_proves_exhaustion_without_payload_work(live_writer, monkeypatch):
    from hashlib import sha256

    from tracer.services.derived_metrics.compaction_ranges import (
        canonical,
        descriptor,
        digest_select,
        stamped_select,
    )
    from tracer.services.derived_metrics.publication import MetricVisibilityError
    from tracer.services.derived_metrics.refresh import TABLE_KINDS
    from tracer.tests.test_derived_metric_compaction_ranges_clickhouse import (
        prepare,
        seed_all,
    )

    writer = live_writer
    seed_all(writer, count=1)
    attempt = prepare(writer)
    empty = attempt.compaction_ranges.filter(row_count=0).first()
    table = TABLE_KINDS[empty.table_kind]
    sql, params = stamped_select(writer, attempt, empty, table)
    queries = []
    execute = writer.store.execute

    def record(sql, params=None, **kwargs):
        queries.append(sql)
        return execute(sql, params, **kwargs)

    monkeypatch.setattr(writer.store, "execute", record)
    actual = digest_select(writer.store, table, sql, params, empty)
    assert actual == {
        "count": 0,
        "bytes": 0,
        "sha256": sha256(
            b"metric-range-root-v1\0" + canonical(descriptor(empty)) + b"\0"
        ).hexdigest(),
    }
    assert len(queries) == 1
    assert queries[0].startswith("SELECT count() FROM")
    assert "formatRow" not in queries[0] and "groupArray" not in queries[0]
    # A stale empty expectation over a genuinely nonempty source must fail;
    # zero is never trusted merely because the descriptor says so.
    nonempty = attempt.compaction_ranges.filter(row_count__gt=0).first()
    table = TABLE_KINDS[nonempty.table_kind]
    sql, params = stamped_select(writer, attempt, nonempty, table)
    nonempty.row_count = 0
    with pytest.raises(MetricVisibilityError, match="gained rows"):
        digest_select(writer.store, table, sql, params, nonempty)
