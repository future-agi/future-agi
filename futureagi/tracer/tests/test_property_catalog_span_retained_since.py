"""Offline contract tests for bounded physical source lower-bound discovery."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_backfill import READ_SETTINGS
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    CanonicalSpanSourceReader,
    PropertyCatalogSpanSourceError,
)

PROJECT_A = "11111111-1111-4111-8111-111111111111"
PROJECT_B = "22222222-2222-4222-8222-222222222222"
PROJECT_OTHER = "33333333-3333-4333-8333-333333333333"
UNTIL = datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
FALLBACK = datetime(2026, 9, 1, 3, 17, tzinfo=UTC)
OLDEST = datetime(2021, 2, 3, 4, 59, 59, 999999, tzinfo=UTC)
OLDEST_HOUR = OLDEST.replace(minute=0, second=0, microsecond=0)


class SourceClient:
    source_database = "source_ch25"
    source_table = "spans"

    def __init__(self, rows=({"retained_since": OLDEST},), *, failure=None):
        self.rows = rows
        self.failure = failure
        self.calls = []

    def query(self, sql, params, *, timeout_ms, settings):
        self.calls.append((sql, dict(params), timeout_ms, dict(settings)))
        if self.failure is not None:
            raise self.failure
        return self.rows


def reader(client, *, deadline=None, timeout_ms=1700):
    return CanonicalSpanSourceReader(
        client,
        source_database=client.source_database,
        source_table=client.source_table,
        catalog_database="property_catalog_dev_unit",
        deadline=deadline or SharedCatalogDeadline(wall_ms=2000, clock=lambda: 0),
        timeout_ms=timeout_ms,
    )


def discover(source, **kwargs):
    return source.retained_since(
        **{
            "project_ids": (PROJECT_A,),
            "until": UNTIL,
            "fallback": FALLBACK,
            **kwargs,
        }
    )


def test_exact_physical_aggregate_is_project_scoped_upper_exclusive_and_bounded():
    client = SourceClient()
    result = discover(reader(client), project_ids=[PROJECT_B, PROJECT_A])
    assert result == OLDEST_HOUR
    assert result.tzinfo is UTC
    assert len(client.calls) == 1
    sql, params, timeout, settings = client.calls[0]
    assert " ".join(sql.split()) == (
        "SELECT minOrNull(start_time) AS retained_since "
        "FROM `source_ch25`.`spans` "
        "PREWHERE project_id IN %(catalog_project_ids)s "
        "AND start_time < toDateTime64(%(catalog_until)s, 6, 'UTC') LIMIT 1"
    )
    assert params == {
        "catalog_project_ids": (PROJECT_A, PROJECT_B),
        "catalog_until": "2026-09-06 12:30:00.000000",
    }
    assert timeout == 1700
    assert settings == {**READ_SETTINGS, "readonly": 2, "max_execution_time": 2}
    for key in (
        "max_threads",
        "max_rows_to_read",
        "max_bytes_to_read",
        "max_memory_usage",
        "max_result_bytes",
    ):
        assert type(settings[key]) is int and settings[key] > 0
    for key in ("timeout_overflow_mode", "read_overflow_mode", "result_overflow_mode"):
        assert settings[key] == "throw"


@pytest.mark.parametrize("deleted,version", [(0, 1), (1, 1), (1, 2**64 - 1)])
def test_deleted_and_old_versions_participate_without_cross_project_or_future_rows(
    deleted, version
):
    class PhysicalClient(SourceClient):
        def query(self, sql, params, **kwargs):
            super().query(sql, params, **kwargs)
            assert all(
                forbidden not in sql
                for forbidden in ("is_deleted", "_version", "FINAL", "argMax")
            )
            # Synthetic physical rows, not a substitute for live SQL qualification.
            physical = (
                (PROJECT_A, OLDEST, deleted, version),
                (PROJECT_A, FALLBACK, 0, 2**64 - 1),
                (PROJECT_OTHER, OLDEST - timedelta(days=1), 0, 1),
                (PROJECT_B, UNTIL, 0, 1),
                (PROJECT_B, UNTIL + timedelta(microseconds=1), 0, 1),
            )
            eligible = [
                at
                for project, at, _, _ in physical
                if project in params["catalog_project_ids"]
                and at
                < datetime.fromisoformat(params["catalog_until"]).replace(tzinfo=UTC)
            ]
            return ({"retained_since": min(eligible, default=None)},)

    client = PhysicalClient()
    assert discover(reader(client), project_ids=(PROJECT_A, PROJECT_B)) == OLDEST_HOUR
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "minimum,expected",
    [
        (OLDEST, OLDEST_HOUR),
        (OLDEST.replace(tzinfo=None), OLDEST_HOUR),
        (OLDEST.astimezone(timezone(timedelta(hours=5, minutes=30))), OLDEST_HOUR),
        (FALLBACK, FALLBACK.replace(minute=0)),
        (FALLBACK + timedelta(days=1), FALLBACK),
        (UNTIL - timedelta(microseconds=1), FALLBACK),
        (None, FALLBACK),
    ],
)
def test_utc_floor_hour_only_widens_fallback(minimum, expected):
    client = SourceClient(({"retained_since": minimum},))
    result = discover(reader(client))
    assert result == expected
    assert result.tzinfo is UTC
    assert result <= FALLBACK
    assert len(client.calls) == 1


def test_earlier_unaligned_fallback_is_preserved_exactly():
    client = SourceClient()
    fallback = OLDEST_HOUR - timedelta(microseconds=1)
    assert discover(reader(client), fallback=fallback) is fallback


def test_native_cutoff_binding_retains_fractional_seconds():
    from clickhouse_driver.util.escape import escape_param

    until = UNTIL.replace(microsecond=123456)
    client = SourceClient(({"retained_since": until - timedelta(microseconds=1)},))
    assert discover(reader(client), until=until) is FALLBACK
    sql, params, _, _ = client.calls[0]
    assert "toDateTime64(%(catalog_until)s, 6, 'UTC')" in sql
    assert escape_param(params["catalog_until"], {}) == "'2026-09-06 12:30:00.123456'"


@pytest.mark.parametrize("projects", [[], ()])
def test_empty_authorized_projects_use_fallback_without_read(projects):
    client = SourceClient(failure=AssertionError("no source IO authorized"))
    assert discover(reader(client), project_ids=projects) is FALLBACK
    assert client.calls == []


@pytest.mark.parametrize(
    "rows",
    [
        (),
        ({},),
        ({"minimum": OLDEST},),
        ({"retained_since": OLDEST, "extra": 1},),
        ({"retained_since": OLDEST}, {"retained_since": None}),
        (None,),
        ("retained_since",),
        ({"retained_since": "2021-02-03 04:59:59"},),
        ({"retained_since": 0},),
        ({"retained_since": True},),
        ({"retained_since": OLDEST.date()},),
        ({"retained_since": UNTIL},),
        ({"retained_since": UNTIL + timedelta(microseconds=1)},),
    ],
)
def test_invalid_aggregate_fails_closed_without_fallback_or_retry(rows):
    client = SourceClient(rows)
    with pytest.raises(PropertyCatalogSpanSourceError, match="retained minimum"):
        discover(reader(client))
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_ids": PROJECT_A},
        {"project_ids": {PROJECT_A}},
        {"project_ids": ("not-a-project",)},
        {"project_ids": (PROJECT_A, PROJECT_A)},
        {"fallback": None},
        {"until": "2026-09-06"},
        {"fallback": FALLBACK.replace(tzinfo=None)},
        {"until": UNTIL.replace(tzinfo=None)},
        {"fallback": FALLBACK.astimezone(timezone(timedelta(hours=1)))},
        {"until": UNTIL.astimezone(timezone(timedelta(hours=1)))},
        {"fallback": UNTIL},
        {"fallback": UNTIL + timedelta(microseconds=1)},
    ],
)
def test_invalid_scope_or_bounds_fail_before_io(overrides):
    client = SourceClient()
    with pytest.raises((TypeError, ValueError)):
        discover(reader(client), **overrides)
    assert client.calls == []


@pytest.mark.parametrize("attribute", ["source_database", "source_table"])
def test_source_identity_drift_is_rejected_before_io(attribute):
    client = SourceClient()
    source = reader(client)
    setattr(client, attribute, "other")
    with pytest.raises(PropertyCatalogSpanSourceError, match="identity changed"):
        discover(source)
    assert client.calls == []


def test_exact_nondefault_source_table_is_preserved():
    client = SourceClient()
    client.source_database = "captured_source"
    client.source_table = "capture_123"
    assert discover(reader(client)) == OLDEST_HOUR
    assert "FROM `captured_source`.`capture_123`" in client.calls[0][0]
    assert "`spans`" not in client.calls[0][0]


def test_query_failure_propagates_without_retry_or_narrowing_fallback():
    failure = TimeoutError("source aggregate exceeded read budget")
    client = SourceClient(failure=failure)
    with pytest.raises(TimeoutError) as caught:
        discover(reader(client))
    assert caught.value is failure
    assert len(client.calls) == 1


def test_shared_deadline_bounds_read_and_expiry_prevents_io():
    clock = [0.0]
    deadline = SharedCatalogDeadline(wall_ms=2000, clock=lambda: clock[0])
    client = SourceClient()
    source = reader(client, deadline=deadline)
    clock[0] = 1.5
    assert discover(source) == OLDEST_HOUR
    assert client.calls[0][2] == 500
    assert client.calls[0][3]["max_execution_time"] == 1
    clock[0] = 2.0
    with pytest.raises(PropertyCatalogPublishError, match="deadline exceeded"):
        discover(source)
    assert len(client.calls) == 1


def test_repeated_discovery_does_not_reuse_a_different_scope_result():
    client = SourceClient()
    source = reader(client)
    assert discover(source) == OLDEST_HOUR
    client.rows = ({"retained_since": None},)
    assert discover(source, project_ids=(PROJECT_B,)) is FALLBACK
    assert len(client.calls) == 2
    assert client.calls[0][1]["catalog_project_ids"] == (PROJECT_A,)
    assert client.calls[1][1]["catalog_project_ids"] == (PROJECT_B,)
