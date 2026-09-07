"""Real bounded reader/aggregate composition over simulated physical source rows."""

import math
from dataclasses import replace
from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.property_catalog import span_source as subject
from tracer.tests.test_property_catalog_retained_history import (
    PROJECT_A,
    PROJECT_B,
    SINCE,
    SPLIT,
    UNTIL,
    WEEK,
    PhysicalRow,
    SourceTransport,
    frozen,
    scan,
)
from tracer.tests.test_property_catalog_retained_history import (
    transport as _history_transport,
)
from tracer.tests.test_property_catalog_span_source import _reader

transport = _history_transport


def calls(client, kind):
    return [params for observed, params, _, _ in client.calls if observed == kind]


def assert_independent(proof, client, scope):
    expected = client.aggregate(
        client.logical(scope.project_ids, scope.since, scope.until)
    )
    assert proof.count == expected["source_count"]
    assert proof.xor == tuple(expected[f"audit_h{i}_xor"] for i in range(1, 5))
    assert proof.total == tuple(expected[f"audit_h{i}_sum"] for i in range(1, 5))
    assert proof.state_conflict_count == 0


def audit_windows(client):
    return {
        (
            params["catalog_project_ids"][0],
            params["catalog_since"],
            params["catalog_until"],
        )
        for params in calls(client, "audit")
    }


def occupied_windows(client, scope):
    # Physical supersets include older/deleted rows, not just logical heads.
    # Unaligned weekly boundaries can conservatively share an occupied hour.
    windows = set()
    start = scope.since
    while start < scope.until:
        end = min(start + WEEK, scope.until)
        for row in client.rows:
            hour = row.seen.replace(minute=0, second=0, microsecond=0)
            if (
                row.project in scope.project_ids
                and scope.since <= row.seen < scope.until
                and hour < end
                and hour + timedelta(hours=1) > start
            ):
                windows.add((row.project, start, end))
        start = end
    return windows


@pytest.mark.parametrize(
    "excess",
    [
        timedelta(microseconds=-1),
        timedelta(0),
        timedelta(microseconds=1),
        timedelta(minutes=45),
    ],
)
def test_366_day_boundary_keeps_one_occupied_digest_query(excess):
    scope = subject.FrozenSpanSource((PROJECT_A,), SINCE, SPLIT + excess, 77)
    seen = SPLIT if excess > timedelta(0) else SPLIT - timedelta(hours=2)
    client = SourceTransport([PhysicalRow(PROJECT_A, "recent", seen, "arrived")])
    reader = _reader(client)
    scan(reader, scope)
    client.calls.clear()
    proof = reader.audit(scope)
    assert_independent(proof, client, scope)
    assert len(calls(client, "audit")) == 1
    assert audit_windows(client) == occupied_windows(client, scope)
    assert len(calls(client, "occupancy")) == (2 if excess > timedelta(0) else 0)


def test_revisits_old_and_multiple_occupied_segments_without_changing_digest(transport):
    scope, reader = frozen(), _reader(transport, page_rows=1)
    pages = scan(reader, scope)
    accumulator = subject.SpanAuditAccumulator()
    for page in pages:
        for observation in page.observation_sha256s:
            accumulator.add(observation)
    assert reader._occupied_frozen.project_ids == (PROJECT_B,)
    assert reader._occupied_frozen.since > SPLIT
    for _ in range(2):  # The final runtime audit also revisits the older segments.
        transport.calls.clear()
        proof = reader.audit(scope)
        assert proof == accumulator.proof
        assert_independent(proof, transport, scope)
        assert audit_windows(transport) == occupied_windows(transport, scope)
        assert len(calls(transport, "audit")) == len(audit_windows(transport))
        occupancy = calls(transport, "occupancy")
        assert occupancy[0]["catalog_since"] == SINCE
        assert len(occupancy) == 6  # Three bounded segments per project, not a union.
        assert len(reader._occupied_hours) <= subject._MAX_OCCUPIED_HOURS


def test_occupied_old_window_is_independently_read_not_replaced_by_scan_digest():
    scope = frozen()
    row = PhysicalRow(PROJECT_A, "old", SINCE + timedelta(days=1), "before")
    client = SourceTransport([row])
    reader = _reader(client)
    scan(reader, scope)
    before = reader.audit(scope)
    client.rows.append(replace(row, model="after", version=2))
    client.calls.clear()
    after = reader.audit(scope)
    assert before.digest != after.digest
    assert_independent(after, client, scope)
    assert audit_windows(client) == occupied_windows(client, scope)


def test_genuinely_empty_wide_scope_needs_bounded_occupancy_not_empty_aggregates():
    client = SourceTransport([])
    reader, scope = _reader(client), frozen()
    assert scan(reader, scope)[-1].terminal
    client.calls.clear()
    assert_independent(reader.audit(scope), client, scope)
    assert calls(client, "audit") == []
    assert len(calls(client, "occupancy")) == 6
    assert reader._occupied_hours == frozenset()


@pytest.mark.parametrize(
    "change", ["other_project", "expanded_projects", "since", "until", "generation"]
)
def test_different_frozen_scope_cannot_borrow_segment_hints(change):
    original = subject.FrozenSpanSource((PROJECT_A,), SINCE, UNTIL, 77)
    changed = {
        "other_project": replace(original, project_ids=(PROJECT_B,)),
        "expanded_projects": replace(original, project_ids=(PROJECT_A, PROJECT_B)),
        "since": replace(original, since=SINCE + WEEK),
        "until": replace(original, until=UNTIL - WEEK),
        "generation": replace(original, audit_generation=78),
    }[change]
    client = SourceTransport(
        [PhysicalRow(PROJECT_B, "other-tenant-project", SINCE + WEEK, "other")]
    )
    reader = _reader(client)
    scan(reader, original)
    client.calls.clear()
    assert_independent(reader.audit(changed), client, changed)
    assert calls(client, "occupancy") == []
    assert len(calls(client, "audit")) == len(changed.project_ids) * math.ceil(
        (changed.until - changed.since) / WEEK
    )
    # Only a scan bound to the new scope enables its own segmented hints.
    scan(reader, changed)
    client.calls.clear()
    assert_independent(reader.audit(changed), client, changed)
    assert audit_windows(client) == occupied_windows(client, changed)


def test_fresh_components_bypass_empty_segment_hints_and_read_all_windows():
    client, scope = SourceTransport([]), frozen()
    reader = _reader(client)
    scan(reader, scope)
    client.rows.append(PhysicalRow(PROJECT_A, "new-old", SINCE + WEEK, "late"))
    cached = (reader._occupied_frozen, reader._occupied_hours, reader._occupancy_scope)
    client.calls.clear()
    components = reader.audit_components(scope)
    assert_independent(subject.combine_audit_components(components), client, scope)
    assert calls(client, "occupancy") == []
    assert len(calls(client, "audit")) == len(scope.project_ids) * math.ceil(
        (scope.until - scope.since) / WEEK
    )
    assert cached == (
        reader._occupied_frozen,
        reader._occupied_hours,
        reader._occupancy_scope,
    )
    for project in scope.project_ids:
        shards = [c for c in components if c.project_id == project]
        assert shards[0].since == SINCE and shards[-1].until == UNTIL
        assert all(a.until == b.since for a, b in zip(shards, shards[1:], strict=False))


def test_segmented_audit_propagates_occupancy_read_failure():
    client, scope = SourceTransport([]), frozen()
    reader = _reader(client)
    scan(reader, scope)
    client.calls.clear()

    def fail(kind, params):
        assert kind == "occupancy"
        raise RuntimeError("native max_bytes_to_read exceeded")

    client.before = fail
    with pytest.raises(RuntimeError, match="max_bytes_to_read"):
        reader.audit(scope)
    assert len(calls(client, "occupancy")) == 1
    assert calls(client, "audit") == []


def test_segmented_audit_rejects_changed_source_database():
    client, scope = SourceTransport([]), frozen()
    reader = _reader(client)
    scan(reader, scope)
    client.calls.clear()
    client.source_database = "another_tenant_source"
    with pytest.raises(
        subject.PropertyCatalogSpanSourceError, match="identity changed"
    ):
        reader.audit(scope)
    assert client.calls == []
