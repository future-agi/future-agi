# ruff: noqa: F811 -- shared isolated ClickHouse and PostgreSQL fixtures
"""An empty raw nomination is a proof, never a substitute for winner collapse."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.derived_metrics import reads
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import numeric_definition

AT = datetime(2026, 1, 10, 12, tzinfo=UTC)


def publish_field(writer, definition, *, status, start=AT, deleted=0, version=1):
    attempt = writer.begin()
    sql, params = field_query("finality-unit", metric=definition.metric_id, start=start)
    writer.freeze(
        attempt,
        FIELD_VALUES,
        f"SELECT * REPLACE(%(probe_status)s AS status, "
        f"toUInt8(%(probe_deleted)s) AS is_deleted, "
        f"toUInt32(%(probe_version)s) AS field_version) FROM ({sql})",
        {
            **params,
            "probe_status": status,
            "probe_deleted": deleted,
            "probe_version": version,
        },
    )
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


def context(writer):
    definition = numeric_definition(uuid4())
    metric = SimpleNamespace(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    return metric, definition


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("checkpoint", [False, True])
def test_no_raw_open_rows_skips_complete_key_collapse(
    live_writer, monkeypatch, checkpoint
):
    writer = live_writer
    metric, definition = context(writer)
    head = publish_field(writer, definition, status="ok")
    if checkpoint:
        head = writer.compact()

    def unexpected_collapse(*args, **kwargs):
        pytest.fail("an empty raw nomination must not plan the complete-key collapse")

    monkeypatch.setattr(reads, "windowed_rows_select", unexpected_collapse)
    assert (
        reads.open_buckets(
            writer.store, metric, definition, head, AT, AT + timedelta(hours=1)
        )
        == {}
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "winner", ["open", "closed", "deleted", "moved_out", "moved_in"]
)
@pytest.mark.parametrize("checkpoint", [False, True])
def test_raw_open_nominees_still_collapse_all_revisions(
    live_writer, winner, checkpoint
):
    writer = live_writer
    metric, definition = context(writer)
    publish_field(
        writer,
        definition,
        status="open",
        start=AT - timedelta(hours=1) if winner == "moved_in" else AT,
    )
    if checkpoint:
        writer.compact()
    head = publish_field(
        writer,
        definition,
        status="ok" if winner == "closed" else "open",
        deleted=int(winner == "deleted"),
        start=AT + timedelta(hours=1) if winner == "moved_out" else AT,
    )
    assert reads.open_buckets(
        writer.store, metric, definition, head, AT, AT + timedelta(hours=1)
    ) == ({AT: 1} if winner in {"open", "moved_in"} else {})


@pytest.mark.django_db(transaction=True)
def test_empty_probe_keeps_version_window_and_attempt_scope(live_writer, monkeypatch):
    writer = live_writer
    metric, definition = context(writer)
    # Neither a different version nor the exclusive end is a candidate.
    publish_field(writer, definition, status="open", version=2)
    head = publish_field(
        writer, definition, status="open", start=AT + timedelta(hours=1)
    )
    # A later committed open revision is not allowed by the pinned older head.
    publish_field(writer, definition, status="open")

    def unexpected_collapse(*args, **kwargs):
        pytest.fail("out-of-scope open rows must not enter the nomination")

    monkeypatch.setattr(reads, "windowed_rows_select", unexpected_collapse)
    assert (
        reads.open_buckets(
            writer.store, metric, definition, head, AT, AT + timedelta(hours=1)
        )
        == {}
    )


@pytest.mark.django_db(transaction=True)
def test_open_bucket_cap_still_fails_closed(live_writer, monkeypatch):
    writer = live_writer
    metric, definition = context(writer)
    head = publish_field(writer, definition, status="open")
    monkeypatch.setattr(reads, "MAX_OPEN_BUCKETS", 0)
    with pytest.raises(reads.MetricUnavailableError) as error:
        reads.open_buckets(
            writer.store, metric, definition, head, AT, AT + timedelta(hours=1)
        )
    assert error.value.code == "metric_finality_unavailable"


@pytest.mark.parametrize("minutes", [60, 67, 90 * 24 * 60])
@pytest.mark.parametrize("start_offset", [0, 7])
@pytest.mark.parametrize("cut", [-15, 0, 7, 15, 37, 60, 90 * 24 * 60])
@pytest.mark.parametrize("has_open", [False, True])
def test_sparse_finality_matches_exhaustive_quarter_oracle(
    minutes, start_offset, cut, has_open
):
    start = AT + timedelta(minutes=start_offset)
    end = start + timedelta(minutes=minutes)
    settled = AT + timedelta(minutes=cut)
    opened = (
        {
            start - reads.QUARTER: 2,
            start: 3,
            start + timedelta(minutes=7): 5,
            start + 2 * reads.QUARTER: 7,
            end: 11,
        }
        if has_open
        else {}
    )
    finality = reads.Finality(settled + reads.QUARTER, settled, opened)
    window = {"window_start": start, "window_end": end}
    expected = []
    bucket = start
    while bucket < end:
        if bucket + reads.QUARTER > settled or bucket in opened:
            expected.append(bucket)
        bucket += reads.QUARTER
    assert finality.nonfinal(window) == expected
    assert finality.complete_through(window) == (expected[0] if expected else end)
    assert finality.disclosure(window) == {
        "complete_through": expected[0] if expected else end,
        "partial": bool(expected),
        "nonfinal_buckets": len(expected),
        "open_units": sum(n for at, n in opened.items() if start <= at < end),
    }


def test_finality_does_not_enumerate_the_already_settled_prefix(monkeypatch):
    end = AT + timedelta(days=90)
    finality = reads.Finality(end, end, {})
    window = {"window_start": AT, "window_end": end}
    original = reads.Finality.buckets
    visited = []

    def observe(window):
        for bucket in original(window):
            visited.append(bucket)
            yield bucket

    monkeypatch.setattr(reads.Finality, "buckets", staticmethod(observe))
    assert finality.nonfinal(window) == []
    assert finality.complete_through(window) == end
    assert finality.disclosure(window)["partial"] is False
    assert visited == []
