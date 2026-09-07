"""Long frozen intervals through the real scanner and simulated native reads.

This does not choose retained minima or stabilize a continuously changing source.
"""

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_backfill import READ_SETTINGS
from tracer.services.clickhouse.v2.property_catalog import span_source as subject
from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    SourceWindow,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    SharedCatalogDeadline,
)
from tracer.tests.test_property_catalog_span_source import (
    PROJECT_A,
    PROJECT_B,
    SOURCE_DATABASE,
    VALUES_STREAM,
    _authoritative_case,
    _identity,
    _legacy_cursor,
    _payload,
    _reader,
    _ResumeStore,
    _SimulatedCrash,
)

SINCE = datetime(2023, 9, 7, 10, 30, 0, 123456, tzinfo=UTC)
UNTIL = SINCE + timedelta(days=800, hours=3, microseconds=1)
SPLIT = SINCE + timedelta(hours=subject._MAX_WINDOWS)
WEEK = timedelta(hours=subject.CANONICAL_SPAN_SCAN_WINDOW_HOURS)
MASK = (1 << 64) - 1


@dataclass(frozen=True)
class PhysicalRow:
    project: str
    span: str
    seen: datetime
    model: str
    version: int = 1
    deleted: bool = False

    @property
    def key(self):
        return ("span", "svc", "trace-a", self.span)

    @property
    def hashes(self):
        # Simulated native hash columns, independent of the Python accumulator.
        raw = hashlib.sha256(
            json.dumps(
                (self.project, self.span, self.seen.isoformat(), self.model)
            ).encode()
        ).digest()
        return tuple(int.from_bytes(raw[i : i + 8], "big") for i in range(0, 32, 8))


class SourceTransport:
    source_database = SOURCE_DATABASE

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []
        self.before = lambda kind, params: None

    def logical(self, projects, since, until):
        groups = {}
        for row in self.rows:
            if row.project in projects and since <= row.seen < until:
                key = (row.project, row.key)
                if key not in groups or groups[key].version < row.version:
                    groups[key] = row
        return sorted(
            (row for row in groups.values() if not row.deleted),
            key=lambda row: (row.project, row.key),
        )

    @staticmethod
    def aggregate(rows):
        result = {"source_count": len(rows), "state_conflict_count": 0}
        for i in range(4):
            xor, total = 0, 0
            for row in rows:
                value = row.hashes[i]
                xor ^= value
                total = (total + value) & MASK
            result[f"audit_h{i + 1}_xor"] = xor
            result[f"audit_h{i + 1}_sum"] = total
        return result

    def query(self, sql, params, *, timeout_ms, settings):
        assert sql.lstrip().startswith(("SELECT", "WITH"))
        assert "catalog_source_version_fence" not in params
        assert 0 < timeout_ms <= subject.CANONICAL_SPAN_QUERY_TIMEOUT_MS
        assert settings == {
            **READ_SETTINGS,
            "readonly": 2,
            "max_execution_time": max(1, math.ceil(timeout_ms / 1000)),
        }
        if " AS occupied_hours" in sql:
            kind = "occupancy"
        elif "catalog_source_limit" in params:
            kind = "identities"
        elif "catalog_source_identities" in params:
            kind = "payload"
        else:
            assert "latest_state_variants" in sql and "sumWithOverflow" in sql
            kind = "audit"
        self.calls.append((kind, dict(params), timeout_ms, dict(settings)))
        self.before(kind, params)
        if kind in {"occupancy", "audit"}:
            projects = params["catalog_project_ids"]
            since, until = params["catalog_since"], params["catalog_until"]
        else:
            projects = (params["catalog_project_id"],)
            since, until = params["catalog_window_start"], params["catalog_window_end"]
        assert projects and set(projects) <= {PROJECT_A, PROJECT_B}
        assert SINCE <= since < until <= UNTIL
        if kind == "occupancy":
            assert until - since <= timedelta(hours=subject._MAX_WINDOWS)
            assert params["catalog_project_limit"] == len(projects) + 1
            return tuple(
                {
                    "project_id_text": project,
                    "occupied_hours": sorted(
                        {
                            row.seen.replace(minute=0, second=0, microsecond=0)
                            for row in self.rows
                            if row.project == project and since <= row.seen < until
                        }
                    ),
                }
                for project in sorted(projects)
            )
        assert until - since <= WEEK
        rows = self.logical(projects, since, until)
        if kind == "audit":
            return (self.aggregate(rows),)
        if kind == "identities":
            after = tuple(
                params[f"catalog_after_{field}"]
                for field in (
                    "observation_type",
                    "service_name",
                    "trace_id",
                    "span_id",
                )
            )
            return tuple(_identity(row.span) for row in rows if row.key > after)[
                : params["catalog_source_limit"]
            ]
        assert (
            len(params["catalog_source_identities"])
            <= subject.MAX_CANONICAL_SPAN_PAGE_ROWS
        )
        return tuple(
            {
                **_payload(span_id=row.span, seen_at=row.seen),
                "project_id": row.project,
                "system_model": row.model,
                **{f"audit_h{i + 1}": value for i, value in enumerate(row.hashes)},
            }
            for row in rows
            if row.key in params["catalog_source_identities"]
        )


@pytest.fixture
def transport():
    old = PhysicalRow(PROJECT_A, "old", SINCE + timedelta(days=1), "obsolete")
    current = replace(old, model="older-than-366-days", version=2)
    deleted = PhysicalRow(PROJECT_A, "deleted", SINCE + timedelta(days=2), "removed")
    return SourceTransport(
        [
            old,
            current,
            current,
            deleted,
            replace(deleted, version=2, deleted=True),
            PhysicalRow(
                PROJECT_A, "z-before", SPLIT - timedelta(hours=2), "before-segment"
            ),
            PhysicalRow(
                PROJECT_A, "a-after", SPLIT + timedelta(hours=1), "after-segment"
            ),
            PhysicalRow(PROJECT_A, "recent", UNTIL - timedelta(minutes=1), "recent"),
            PhysicalRow(PROJECT_B, "other-old", SINCE + timedelta(days=3), "other-old"),
            PhysicalRow(
                PROJECT_B, "other-late", UNTIL - timedelta(days=1), "other-late"
            ),
        ]
    )


def frozen():
    return subject.FrozenSpanSource((PROJECT_A, PROJECT_B), SINCE, UNTIL, 77)


def scan(reader, scope, cursor=None):
    pages = []
    while True:
        page = reader.read_page(scope, cursor=cursor)
        pages.append(page)
        if page.terminal:
            return pages
        assert page.next_cursor is not None
        cursor = page.next_cursor


def test_long_interval_and_lazy_coordinates_keep_original_encoding():
    scope = frozen()
    assert SourceWindow(SINCE, UNTIL).since == SINCE
    assert scope.hours_per_project == 800 * 24 + 4
    assert scope.unit_count == scope.hours_per_project * 2
    assert not isinstance(scope.units, (list, tuple))
    assert scope.units[:1] == ((PROJECT_A, SINCE, SINCE + timedelta(hours=1)),)
    assert scope.units[scope.hours_per_project][0:2] == (PROJECT_B, SINCE)
    assert scope.units[-1][2] == UNTIL
    assert len(scope.units[:]) == scope.unit_count
    with pytest.raises(IndexError):
        scope.unit_at(scope.unit_count)
    cursor = subject.SpanScanCursor(scope.unit_count - 1)
    assert subject.SpanScanCursor.decode(cursor.encode()) == cursor
    assert (
        subject.SpanScanCursor.decode(
            _legacy_cursor(3, subject.SourceCursor())
        ).unit_index
        == 3
    )
    start = int((SINCE - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1))
    end = int((UNTIL - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1))
    assert BuildPlanSourceScope((PROJECT_A,), start, end).span_since_us == start


@pytest.mark.parametrize("index", [True, -1, 1 << 64, "1"])
def test_cursor_integer_bound_stays_strict(index):
    with pytest.raises(ValueError, match="unit index"):
        subject.SpanScanCursor(index)


def test_out_of_scope_uint64_cursor_rejected_before_source_read(transport):
    reader = _reader(transport)
    cursor = subject.SpanScanCursor((1 << 64) - 1).encode()
    with pytest.raises(subject.PropertyCatalogSpanSourceError, match="exceeds source"):
        reader.read_page(frozen(), cursor=cursor)
    assert transport.calls == []


def test_real_reader_covers_all_segments_and_independent_aggregates_compose(transport):
    scope = frozen()
    reader = _reader(transport, page_rows=1)
    pages = scan(reader, scope)
    observed = [
        (page.project_id, row.cursor.span_id) for page in pages for row in page.spans
    ]
    expected_rows = transport.logical(scope.project_ids, SINCE, UNTIL)
    assert sorted(observed) == sorted((row.project, row.span) for row in expected_rows)
    assert len(observed) == len(set(observed)) == 6
    assert all(not page.terminal for page in pages[:-1])
    assert pages[-1].terminal
    occupancy = [
        params for kind, params, _, _ in transport.calls if kind == "occupancy"
    ]
    assert len(occupancy) >= 6  # More than two bounded segments per project.
    assert any(params["catalog_since"] >= SPLIT for params in occupancy)
    assert len(reader._occupied_hours) <= subject._MAX_OCCUPIED_HOURS

    # A different reader with no occupancy cache must issue every audit shard.
    audit_reader = _reader(transport)
    proof = audit_reader.audit(scope)
    independent = transport.aggregate(expected_rows)
    assert proof.count == 6
    assert proof.xor == tuple(independent[f"audit_h{i}_xor"] for i in range(1, 5))
    assert proof.total == tuple(independent[f"audit_h{i}_sum"] for i in range(1, 5))
    accumulator = subject.SpanAuditAccumulator()
    for page in pages:
        for observation in page.observation_sha256s:
            accumulator.add(observation)
    assert proof == accumulator.proof
    for project in scope.project_ids:
        shards = [
            params
            for kind, params, _, _ in transport.calls
            if kind == "audit" and params["catalog_project_ids"] == (project,)
        ]
        assert shards[0]["catalog_since"] == SINCE
        assert shards[-1]["catalog_until"] == UNTIL
        assert all(
            left["catalog_until"] == right["catalog_since"]
            for left, right in zip(shards, shards[1:], strict=False)
        )


def test_boundary_window_and_keyset_are_identical_after_reader_restart(transport):
    scope = frozen()
    reader = _reader(transport, page_rows=1)
    reader.read_page(scope)  # Retain an occupancy segment ending at SPLIT.
    unit = (
        subject._MAX_WINDOWS // subject.CANONICAL_SPAN_SCAN_WINDOW_HOURS
    ) * subject.CANONICAL_SPAN_SCAN_WINDOW_HOURS
    start = SINCE + timedelta(hours=unit)
    cursor = subject.SpanScanCursor(unit).encode()
    first = reader.read_page(scope, cursor=cursor)
    assert first.spans[0].cursor.span_id == "a-after"
    assert subject.SpanScanCursor.decode(first.next_cursor).unit_index == unit
    resumed = _reader(transport, page_rows=1).read_page(scope, cursor=first.next_cursor)
    assert resumed.spans[0].cursor.span_id == "z-before"
    windows = [
        params
        for kind, params, _, _ in transport.calls
        if kind == "identities" and params["catalog_window_start"] == start
    ]
    assert len(windows) == 2
    assert all(params["catalog_window_end"] == start + WEEK for params in windows)
    assert windows[1]["catalog_after_span_id"] == "a-after"


def test_real_reconciler_resumes_durable_boundary_checkpoint_without_chunk_eos(
    transport,
):
    scope = frozen()
    _, build, _, publishers, _ = _authoritative_case()

    class Store(_ResumeStore):
        crash = True

        def append(self, value):
            super().append(value)
            if (
                self.crash
                and value.processed_rows == 2
                and not value.checkpoint.terminal
            ):
                self.crash = False
                raise _SimulatedCrash("after boundary checkpoint")

    store = Store(fail_first_append=False)
    with pytest.raises(_SimulatedCrash, match="boundary"):
        subject.AuthoritativeSpanReconciler(
            reader=_reader(transport, page_rows=1),
            publishers=publishers,
            checkpoint_store=store,
        ).run(frozen=scope, build=build)
    saved = store.latest[VALUES_STREAM]
    assert saved.source_cursor
    assert not saved.checkpoint.terminal
    assert (
        subject.SpanScanCursor.decode(saved.source_cursor).source_cursor.span_id
        == "a-after"
    )
    before = len(transport.calls)
    result = subject.AuthoritativeSpanReconciler(
        reader=_reader(transport, page_rows=1),
        publishers=publishers,
        checkpoint_store=store,
    ).run(frozen=scope, build=build)
    assert result.values.source_count == result.source_audit.source_count == 6
    assert result.values.source_digest == result.source_audit.source_digest
    first_payload = next(
        params for kind, params, _, _ in transport.calls[before:] if kind == "payload"
    )
    assert first_payload["catalog_source_identities"][0][-1] == "z-before"
    calls = publishers[subject.AuthoritativeSpanRole.VALUES].calls
    assert sum(envelope.terminal for envelope, _, _ in calls) == 1
    assert calls[-1][0].terminal
    assert [envelope.sequence for envelope, _, _ in calls] == list(
        range(1, len(calls) + 1)
    )
    values = [json.loads(row["value_json"]) for _, rows, _ in calls for row in rows]
    assert sorted(values) == sorted(
        row.model for row in transport.logical(scope.project_ids, SINCE, UNTIL)
    )
    assert "obsolete" not in values and "removed" not in values
    assert {value.checkpoint.build_token for value in store.writes} == {
        build.build_token
    }
    assert {value.source_version_fence for value in store.writes} == {77}


def test_late_source_change_is_not_hidden_by_long_range_scan(transport):
    scope = frozen()
    _, build, _, publishers, store = _authoritative_case()
    store.fail_first_append = False
    added = False

    def append_before_audit(kind, params):
        nonlocal added
        if kind == "audit" and not added:
            added = True
            transport.rows.append(
                PhysicalRow(
                    PROJECT_A,
                    "very-late",
                    SINCE + timedelta(days=4),
                    "late-physical-row",
                    version=1,
                )
            )

    transport.before = append_before_audit
    with pytest.raises(
        subject.PropertyCatalogSourceChanged, match="independent source audit disagree"
    ):
        subject.AuthoritativeSpanReconciler(
            reader=_reader(transport, page_rows=1),
            publishers=publishers,
            checkpoint_store=store,
        ).run(frozen=scope, build=build)
    assert publishers[subject.AuthoritativeSpanRole.SOURCE_AUDIT].calls == []


def test_later_segment_read_failure_does_not_become_terminal(transport):
    scope = frozen()
    reader = _reader(transport, page_rows=1)

    def fail_later(kind, params):
        if kind == "occupancy" and params["catalog_since"] >= SPLIT:
            raise RuntimeError("native max_bytes_to_read exceeded")

    transport.before = fail_later
    with pytest.raises(RuntimeError, match="max_bytes_to_read"):
        scan(reader, scope)
    transport.before = lambda kind, params: None
    assert (
        len([row for page in scan(_reader(transport), scope) for row in page.spans])
        == 6
    )


def test_empty_cached_traversal_and_audit_obey_shared_deadline():
    clock = [0.0]
    transport = SourceTransport([])

    def elapsed(kind, params):
        clock[0] += 0.6

    transport.before = elapsed
    deadline = SharedCatalogDeadline(wall_ms=1000, clock=lambda: clock[0])
    with pytest.raises(PropertyCatalogPublishError, match="deadline"):
        _reader(transport, deadline=deadline).read_page(frozen())
    assert len(transport.calls) == 2
    assert transport.calls[1][2] < transport.calls[0][2]
    clock[0] = 0.0
    transport.calls.clear()
    with pytest.raises(PropertyCatalogPublishError, match="deadline"):
        _reader(
            transport,
            deadline=SharedCatalogDeadline(wall_ms=1000, clock=lambda: clock[0]),
        ).audit(frozen())
    assert len(transport.calls) == 2
