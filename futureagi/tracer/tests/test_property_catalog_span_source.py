from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_backfill import (
    SourceCursor,
    SourceSpan,
)
from tracer.services.clickhouse.v2.property_catalog.models import (
    PropertyRole,
    SourceAdapter,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CheckpointStatus,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import CheckpointWrite
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    PropertySourceError,
    SourceKeysetCursor,
    SourceReadBudget,
    SpanAttributeDefinitionSourceAdapter,
    SpanAttributeKeyGroup,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    AUTHORITATIVE_VALUE_BATCH_MAX_BYTES,
    AUTHORITATIVE_VALUE_BATCH_MAX_ROWS,
    CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    CANONICAL_SPAN_SCAN_WINDOW_HOURS,
    DEV_CANONICAL_SPAN_PAGE_ROWS,
    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    AuthoritativeSpanBuild,
    AuthoritativeSpanReconciler,
    AuthoritativeSpanRole,
    CanonicalSpanSourceReader,
    FrozenSpanSource,
    PropertyCatalogSpanSourceError,
    RevisionPinnedSpanAttributeGroupPageLoader,
    SpanAggregateProof,
    SpanAuditAccumulator,
    SpanScanCursor,
    SpanScanPage,
    _value_batches,
)
from tracer.services.clickhouse.v2.property_catalog.wire import encode_envelope

PROJECT_A = "11111111-1111-4111-8111-111111111111"
PROJECT_B = "22222222-2222-4222-8222-222222222222"
SOURCE_DATABASE = "source_ch25"
CATALOG_DATABASE = "property_catalog_dev_unit"
ORG = "33333333-3333-4333-8333-333333333333"
WORKSPACE = "44444444-4444-4444-8444-444444444444"
BUILD_TOKEN = "55555555-5555-4555-8555-555555555555"
VALUES_STREAM = "66666666-6666-4666-8666-666666666666"
AUDIT_STREAM = "77777777-7777-4777-8777-777777777777"


class _SpanSourceClient:
    source_database = SOURCE_DATABASE

    def __init__(
        self,
        *,
        occupied: Mapping[str, Sequence[datetime]],
        spans: Sequence[str] = (),
        seen_at: datetime | None = None,
        identity_window_start: datetime | None = None,
    ) -> None:
        self.occupied = occupied
        self.spans = tuple(spans)
        self.seen_at = seen_at
        self.identity_window_start = identity_window_start
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.query_limits: list[tuple[int, dict[str, Any]]] = []

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
        settings: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        assert timeout_ms > 0
        assert settings["readonly"] == 2
        copied = dict(params)
        self.calls.append((sql, copied))
        self.query_limits.append((timeout_ms, dict(settings)))
        if " AS occupied_hours" in sql:
            return tuple(
                {
                    "project_id_text": project_id,
                    "occupied_hours": tuple(hours),
                }
                for project_id, hours in sorted(self.occupied.items())
            )
        if "catalog_source_limit" in params:
            if (
                self.identity_window_start is not None
                and params["catalog_window_start"] != self.identity_window_start
            ):
                return ()
            after = str(params["catalog_after_span_id"])
            candidates = tuple(span_id for span_id in self.spans if span_id > after)
            return tuple(_identity(span_id) for span_id in candidates)[
                : int(params["catalog_source_limit"])
            ]
        identities = tuple(params["catalog_source_identities"])
        assert self.seen_at is not None
        return tuple(
            _payload(span_id=str(identity[3]), seen_at=self.seen_at)
            for identity in identities
        )


class _FixedDeadline:
    def __init__(self, remaining_ms: int) -> None:
        self.remaining = remaining_ms
        self.caps: list[int] = []

    def remaining_ms(self, *, cap_ms: int = 8_500) -> int:
        self.caps.append(cap_ms)
        return min(self.remaining, cap_ms)


class _CatalogGroupClient:
    catalog_database = CATALOG_DATABASE

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = tuple(rows)
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Sequence[Mapping[str, Any]]:
        self.calls.append((sql, dict(params), timeout_ms))
        return self.rows


def _catalog_group_context() -> PostgresSnapshotContext:
    return PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT_A, PROJECT_B),
        catalog_epoch=11,
        catalog_revision=12,
        projection_version=1,
        snapshot_cutoff=datetime(2026, 8, 17, 2, 30, tzinfo=UTC),
    )


def test_revision_pinned_group_loader_reads_the_exact_build_once_and_paginates() -> (
    None
):
    context = _catalog_group_context()
    first_seen = datetime(2026, 1, 1, 1)
    last_seen = datetime(2026, 8, 16, 23, 59, 59)
    client = _CatalogGroupClient(
        (
            {
                "attribute_key": "alpha",
                "observed_types": ("string", "number"),
                "project_ids": (PROJECT_A, PROJECT_B),
                "first_seen": first_seen,
                "last_seen": last_seen,
            },
            {
                "attribute_key": "beta",
                "observed_types": ("boolean",),
                "project_ids": (PROJECT_B,),
                "first_seen": first_seen,
                "last_seen": last_seen,
            },
        )
    )
    deadline = _FixedDeadline(7_000)
    loader = RevisionPinnedSpanAttributeGroupPageLoader(
        client,
        context=context,
        build_token=BUILD_TOKEN,
        deadline=deadline,  # type: ignore[arg-type]
        max_groups=10,
    )

    first = loader(context=context, cursor=None, limit=1)
    second = loader(
        context=context,
        cursor=SourceKeysetCursor(context.snapshot_cutoff, "alpha"),
        limit=10,
    )

    assert [group.attribute_key for group in first] == ["alpha"]
    assert first[0].observed_types == ("number", "string")
    assert first[0].project_ids == (PROJECT_A, PROJECT_B)
    assert first[0].first_seen == first_seen.replace(tzinfo=UTC)
    assert [group.attribute_key for group in second] == ["beta"]
    assert len(client.calls) == 1
    sql, params, timeout_ms = client.calls[0]
    assert f"`{CATALOG_DATABASE}`.`span_attribute_value_catalog`" in sql
    assert "FROM retained_values" in sql
    assert "WHERE source_kind = 'custom_attribute'" in sql
    assert "property_catalog_activations" in sql
    assert "max_result_bytes = 67108864" in sql
    assert params == {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "project_ids": (PROJECT_A, PROJECT_B),
        "catalog_epoch": 11,
        "lineage_anchor_revision": 12,
        "prior_active_revision": 0,
        "has_prior_lineage": 0,
        "catalog_revision": 12,
        "build_token": BUILD_TOKEN,
        "projection_version": 1,
        "catalog_group_limit": 11,
    }
    assert timeout_ms == 7_000
    assert deadline.caps == [RUNTIME_LIMITS.state_store_timeout_ms]


def test_revision_pinned_group_loader_unions_prior_active_lineage() -> None:
    context = _catalog_group_context()
    client = _CatalogGroupClient(())
    loader = RevisionPinnedSpanAttributeGroupPageLoader(
        client,
        context=context,
        build_token=BUILD_TOKEN,
        deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
        lineage_anchor_revision=7,
        prior_active_revision=11,
    )

    assert loader(context=context, cursor=None, limit=10) == ()
    sql, params, _ = client.calls[0]
    assert "INNER JOIN active_lineage AS lineage" in sql
    assert "UNION ALL" in sql
    assert params["lineage_anchor_revision"] == 7
    assert params["prior_active_revision"] == 11
    assert params["has_prior_lineage"] == 1


def test_revision_pinned_group_loader_rejects_incomplete_lineage_scope() -> None:
    context = _catalog_group_context()

    with pytest.raises(ValueError, match="require a prior active revision"):
        RevisionPinnedSpanAttributeGroupPageLoader(
            _CatalogGroupClient(()),
            context=context,
            build_token=BUILD_TOKEN,
            deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
            lineage_anchor_revision=7,
        )
    with pytest.raises(ValueError, match="prior active revision"):
        RevisionPinnedSpanAttributeGroupPageLoader(
            _CatalogGroupClient(()),
            context=context,
            build_token=BUILD_TOKEN,
            deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
            lineage_anchor_revision=7,
            prior_active_revision=12,
        )


def test_revision_pinned_group_loader_keeps_source_and_catalog_timeouts_separate() -> (
    None
):
    context = _catalog_group_context()

    with pytest.raises(ValueError, match="catalog group query timeout"):
        RevisionPinnedSpanAttributeGroupPageLoader(
            _CatalogGroupClient(()),
            context=context,
            build_token=BUILD_TOKEN,
            deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
            timeout_ms=DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
        )


@pytest.mark.parametrize(
    ("observed_types", "expected_role"),
    [
        (("number",), PropertyRole.METRIC),
        (("string",), PropertyRole.DIMENSION),
        (("boolean",), PropertyRole.DIMENSION),
        (("number", "string"), PropertyRole.DIMENSION),
    ],
)
def test_span_attribute_definition_role_matches_aggregation_contract(
    observed_types: tuple[str, ...],
    expected_role: PropertyRole,
) -> None:
    context = _catalog_group_context()
    group = SpanAttributeKeyGroup(
        attribute_key="customer.score",
        observed_types=observed_types,
        project_ids=(PROJECT_A,),
        catalog_revision=context.catalog_revision,
        revision_fenced_at=context.snapshot_cutoff,
        first_seen=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen=datetime(2026, 8, 16, tzinfo=UTC),
    )
    adapter = SpanAttributeDefinitionSourceAdapter(
        group_page_loader=lambda **kwargs: (group,)
    )

    snapshot = adapter.read_snapshot(context=context, budget=SourceReadBudget())

    assert snapshot.terminal is True
    assert len(snapshot.records) == 1
    assert snapshot.records[0].definition.role is expected_role


def test_revision_pinned_group_loader_rejects_context_or_cursor_drift() -> None:
    context = _catalog_group_context()
    loader = RevisionPinnedSpanAttributeGroupPageLoader(
        _CatalogGroupClient(()),
        context=context,
        build_token=BUILD_TOKEN,
        deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
    )
    changed = replace(context, catalog_revision=context.catalog_revision + 1)

    with pytest.raises(PropertySourceError, match="context changed"):
        loader(context=changed, cursor=None, limit=1)
    with pytest.raises(PropertySourceError, match="outside the revision fence"):
        loader(
            context=context,
            cursor=SourceKeysetCursor(
                context.snapshot_cutoff - timedelta(microseconds=1), "alpha"
            ),
            limit=1,
        )


def test_revision_pinned_group_loader_fails_closed_on_bounds_and_scope() -> None:
    context = _catalog_group_context()
    row = {
        "attribute_key": "alpha",
        "observed_types": ("string",),
        "project_ids": ("99999999-9999-4999-8999-999999999999",),
        "first_seen": datetime(2026, 1, 1, tzinfo=UTC),
        "last_seen": datetime(2026, 1, 2, tzinfo=UTC),
    }
    loader = RevisionPinnedSpanAttributeGroupPageLoader(
        _CatalogGroupClient((row,)),
        context=context,
        build_token=BUILD_TOKEN,
        deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
    )
    with pytest.raises(PropertySourceError, match="exceed the build scope"):
        loader(context=context, cursor=None, limit=1)

    over_rows = tuple({**row, "attribute_key": f"key-{index}"} for index in range(3))
    over = RevisionPinnedSpanAttributeGroupPageLoader(
        _CatalogGroupClient(over_rows),
        context=context,
        build_token=BUILD_TOKEN,
        deadline=_FixedDeadline(7_000),  # type: ignore[arg-type]
        max_groups=2,
    )
    with pytest.raises(PropertySourceError, match="row bound"):
        over(context=context, cursor=None, limit=1)


def _reader(
    client: _SpanSourceClient,
    *,
    page_rows: int = 8,
    timeout_ms: int = CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    explicit_initial_backfill: bool = False,
    deadline: Any | None = None,
) -> CanonicalSpanSourceReader:
    return CanonicalSpanSourceReader(
        client,
        source_database=SOURCE_DATABASE,
        catalog_database=CATALOG_DATABASE,
        deadline=deadline or SharedCatalogDeadline(wall_ms=8_500),
        timeout_ms=timeout_ms,
        explicit_initial_backfill=explicit_initial_backfill,
        page_rows=page_rows,
    )


def _identity(span_id: str) -> Mapping[str, Any]:
    return {
        "observation_type": "span",
        "service_name": "svc",
        "trace_id": "trace-a",
        "span_id": span_id,
    }


def _payload(*, span_id: str, seen_at: datetime) -> Mapping[str, Any]:
    return {
        **_identity(span_id),
        "project_id": PROJECT_A,
        "seen_at": seen_at,
        "source_attribute_entries": 0,
        "source_attribute_bytes": 2,
        "attrs_string_projection": (),
        "attrs_number": {},
        "attrs_bool": {},
        "attributes_extra_projection": (),
        "attributes_extra_valid": 1,
        "selectable_projection_complete": 1,
        "system_model": "",
        "system_model_complete": 1,
        "audit_h1": 1,
        "audit_h2": 2,
        "audit_h3": 3,
        "audit_h4": 4,
    }


@pytest.mark.parametrize("page_rows", (0, 1_025, True))
def test_canonical_span_page_bound_rejects_invalid_or_implicit_booleans(
    page_rows: Any,
) -> None:
    client = _SpanSourceClient(occupied={})
    with pytest.raises(ValueError, match=r"page_rows must be in \[1, 1024\]"):
        _reader(client, page_rows=page_rows)


def test_default_canonical_span_query_timeout_remains_8500_ms() -> None:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=1), 7)
    client = _SpanSourceClient(occupied={})
    deadline = _FixedDeadline(60_000)

    assert _reader(client, deadline=deadline).read_page(frozen).terminal

    assert deadline.caps == [CANONICAL_SPAN_QUERY_TIMEOUT_MS]
    assert len(client.query_limits) == 1
    timeout_ms, settings = client.query_limits[0]
    assert timeout_ms == CANONICAL_SPAN_QUERY_TIMEOUT_MS
    assert settings["max_execution_time"] == 9


def test_explicit_initial_backfill_can_use_30000_ms_per_source_query() -> None:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=1), 8)
    client = _SpanSourceClient(occupied={})
    deadline = _FixedDeadline(60_000)

    assert (
        _reader(
            client,
            deadline=deadline,
            timeout_ms=DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
            explicit_initial_backfill=True,
        )
        .read_page(frozen)
        .terminal
    )

    assert deadline.caps == [DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS]
    timeout_ms, settings = client.query_limits[0]
    assert timeout_ms == DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
    assert settings["max_execution_time"] == 30


def test_explicit_initial_backfill_query_still_shrinks_to_shared_deadline() -> None:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=1), 9)
    client = _SpanSourceClient(occupied={})
    deadline = _FixedDeadline(1_234)

    assert (
        _reader(
            client,
            deadline=deadline,
            timeout_ms=DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
            explicit_initial_backfill=True,
        )
        .read_page(frozen)
        .terminal
    )

    timeout_ms, settings = client.query_limits[0]
    assert timeout_ms == 1_234
    assert settings["max_execution_time"] == 2


@pytest.mark.parametrize(
    ("timeout_ms", "explicit_initial_backfill", "cap"),
    (
        (8_501, False, 8_500),
        (30_001, True, 30_000),
        (True, True, 30_000),
    ),
)
def test_canonical_span_query_timeout_caps_fail_closed(
    timeout_ms: Any,
    explicit_initial_backfill: bool,
    cap: int,
) -> None:
    with pytest.raises(ValueError, match=rf"\[1, {cap}\]"):
        _reader(
            _SpanSourceClient(occupied={}),
            timeout_ms=timeout_ms,
            explicit_initial_backfill=explicit_initial_backfill,
        )


def test_canonical_span_extended_timeout_requires_literal_explicit_mode() -> None:
    with pytest.raises(ValueError, match="explicit_initial_backfill must be a bool"):
        _reader(
            _SpanSourceClient(occupied={}),
            timeout_ms=DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
            explicit_initial_backfill=1,  # type: ignore[arg-type]
        )


def test_empty_year_uses_one_bounded_occupancy_select_not_hourly_n_plus_one() -> None:
    since = datetime(2025, 8, 15, 10, tzinfo=UTC)
    until = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_B, PROJECT_A), since, until, 7)
    assert len(frozen.units) == 2 * 365 * 24
    client = _SpanSourceClient(occupied={})

    page = _reader(client).read_page(frozen)

    assert page.terminal
    assert page.spans == ()
    assert len(client.calls) == 1
    sql, params = client.calls[0]
    assert "groupUniqArray(8786)" in sql
    assert "LIMIT %(catalog_project_limit)s" in sql
    assert params["catalog_project_ids"] == (PROJECT_A, PROJECT_B)
    assert params["catalog_project_limit"] == 3


def test_sparse_hours_share_one_bounded_weekly_window() -> None:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    occupied_hour = since + timedelta(hours=2)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=4), 8)
    client = _SpanSourceClient(
        occupied={PROJECT_A: (occupied_hour,)},
        spans=("span-a",),
        seen_at=occupied_hour + timedelta(minutes=1),
    )
    reader = _reader(client)

    first = reader.read_page(frozen)
    assert first.terminal
    assert tuple(span.cursor.span_id for span in first.spans) == ("span-a",)
    assert sum(" AS occupied_hours" in sql for sql, _ in client.calls) == 1
    assert sum("catalog_source_limit" in params for _, params in client.calls) == 1


def test_unaligned_scan_checks_every_overlapping_wall_hour_without_omission() -> None:
    since = datetime(2026, 8, 15, 10, 30, tzinfo=UTC)
    second_unit = since + timedelta(hours=1)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=2), 9)
    client = _SpanSourceClient(
        occupied={PROJECT_A: (datetime(2026, 8, 15, 11, tzinfo=UTC),)},
        spans=("span-a",),
        seen_at=second_unit + timedelta(minutes=15),
    )

    page = _reader(client).read_page(frozen)

    assert page.terminal
    assert tuple(span.cursor.span_id for span in page.spans) == ("span-a",)
    identity_params = [
        params for _, params in client.calls if "catalog_source_limit" in params
    ]
    assert len(identity_params) == 1
    assert identity_params[0]["catalog_window_start"] == since
    assert identity_params[0]["catalog_window_end"] == frozen.until


def test_nonempty_hour_keyset_strictly_advances_without_rediscovery() -> None:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=1), 9)
    client = _SpanSourceClient(
        occupied={PROJECT_A: (since,)},
        spans=("span-a", "span-b"),
        seen_at=since + timedelta(minutes=1),
    )
    reader = _reader(client, page_rows=1)

    first = reader.read_page(frozen)
    assert first.next_cursor is not None
    first_cursor = SpanScanCursor.decode(first.next_cursor)
    assert first_cursor.unit_index == 0
    assert first_cursor.source_cursor.span_id == "span-a"

    second = reader.read_page(frozen, cursor=first.next_cursor)
    assert second.terminal
    assert tuple(span.cursor.span_id for span in second.spans) == ("span-b",)
    identity_params = [
        params for _, params in client.calls if "catalog_source_limit" in params
    ]
    assert [params["catalog_after_span_id"] for params in identity_params] == [
        "",
        "span-a",
    ]
    assert sum(" AS occupied_hours" in sql for sql, _ in client.calls) == 1


def _legacy_cursor(unit_index: int, source_cursor: SourceCursor) -> str:
    raw = json.dumps(
        {
            "observation_type": source_cursor.observation_type,
            "service_name": source_cursor.service_name,
            "span_id": source_cursor.span_id,
            "trace_id": source_cursor.trace_id,
            "unit_index": unit_index,
            "v": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_v1_resume_finishes_its_hour_before_v2_weekly_window_resets_keyset() -> None:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_A,), since, since + timedelta(hours=4), 10)

    class WindowClient(_SpanSourceClient):
        def query(
            self,
            sql: str,
            params: Mapping[str, Any],
            *,
            timeout_ms: int,
            settings: Mapping[str, Any],
        ) -> Sequence[Mapping[str, Any]]:
            if " AS occupied_hours" in sql:
                self.calls.append((sql, dict(params)))
                return (
                    {
                        "project_id_text": PROJECT_A,
                        "occupied_hours": (since, since + timedelta(hours=1)),
                    },
                )
            if "catalog_source_limit" in params:
                self.calls.append((sql, dict(params)))
                start = params["catalog_window_start"]
                after = str(params["catalog_after_span_id"])
                # The later window deliberately starts below the old hour's
                # keyset.  Resetting only after the old hour completes keeps
                # both a and b without replaying z.
                candidates = ("span-z",) if start == since else ("span-a", "span-b")
                return tuple(
                    _identity(span_id) for span_id in candidates if span_id > after
                )[: int(params["catalog_source_limit"])]
            self.calls.append((sql, dict(params)))
            return tuple(
                _payload(span_id=str(identity[3]), seen_at=since)
                for identity in params["catalog_source_identities"]
            )

    client = WindowClient(occupied={})
    reader = _reader(client, page_rows=1)
    old = _legacy_cursor(
        0,
        SourceCursor("span", "svc", "trace-a", "span-m"),
    )

    old_hour = reader.read_page(frozen, cursor=old)
    assert tuple(span.cursor.span_id for span in old_hour.spans) == ("span-z",)
    assert old_hour.next_cursor is not None
    upgraded = SpanScanCursor.decode(old_hour.next_cursor)
    assert upgraded.unit_index == 1
    assert upgraded.source_cursor == SourceCursor()
    assert upgraded.window_hours == CANONICAL_SPAN_SCAN_WINDOW_HOURS

    weekly_first = reader.read_page(frozen, cursor=old_hour.next_cursor)
    assert tuple(span.cursor.span_id for span in weekly_first.spans) == ("span-a",)
    assert weekly_first.next_cursor is not None
    weekly_second = reader.read_page(frozen, cursor=weekly_first.next_cursor)
    assert tuple(span.cursor.span_id for span in weekly_second.spans) == ("span-b",)
    assert weekly_second.terminal

    identity_calls = [
        (sql, params)
        for sql, params in client.calls
        if "catalog_source_limit" in params
    ]
    assert identity_calls[0][1]["catalog_window_end"] == since + timedelta(hours=1)
    assert identity_calls[1][1]["catalog_window_start"] == since + timedelta(hours=1)
    assert identity_calls[1][1]["catalog_window_end"] == frozen.until
    assert all("toStartOfHour(start_time)," not in sql for sql, _ in identity_calls)


def test_weekly_windows_remove_actual_occupied_hour_select_floor() -> None:
    since = datetime(2025, 8, 15, 10, tzinfo=UTC)
    until = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource((PROJECT_A, PROJECT_B), since, until, 11)
    # Spread 713 occupied hours across the whole year so every weekly window
    # for the dense project remains a genuine worst-case positive hint.
    dense_hours = tuple(since + timedelta(hours=12 * index) for index in range(712))
    client = _SpanSourceClient(
        occupied={PROJECT_A: dense_hours, PROJECT_B: (until - timedelta(hours=1),)},
    )

    terminal = _reader(client, page_rows=DEV_CANONICAL_SPAN_PAGE_ROWS).read_page(frozen)

    assert terminal.terminal
    identity_calls = [
        params for _, params in client.calls if "catalog_source_limit" in params
    ]
    weekly_windows_per_project = math.ceil(
        (365 * 24) / CANONICAL_SPAN_SCAN_WINDOW_HOURS
    )
    assert len(identity_calls) <= weekly_windows_per_project + 1
    assert len(identity_calls) < 713 // 10
    assert all(
        params["catalog_source_limit"] == DEV_CANONICAL_SPAN_PAGE_ROWS + 1
        for params in identity_calls
    )

    # Even with adversarial distribution across non-empty windows, 12,248
    # rows need at most one partially-filled page per weekly window in addition
    # to the full 256-row pages.  This replaces the old >=1,426 identity and
    # hydration SELECT floor with a small, mechanically derived bound.
    data_pages = math.ceil(12_248 / DEV_CANONICAL_SPAN_PAGE_ROWS)
    bounded_pages = data_pages + weekly_windows_per_project
    assert bounded_pages == 101
    assert 1 + 2 * bounded_pages < 205


def _encoded_batch_bytes(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        + 1
        for row in rows
    )


def test_value_batches_raise_row_throughput_without_weakening_byte_bound() -> None:
    tiny = tuple({"i": index} for index in range(4_501))
    row_limited = _value_batches(tiny)
    assert [len(batch) for batch in row_limited] == [2_000, 2_000, 501]

    payload = "x" * 200_000
    byte_limited = _value_batches(
        (
            {"i": 1, "payload": payload},
            {"i": 2, "payload": payload},
            {"i": 3, "payload": payload},
        )
    )
    assert [len(batch) for batch in byte_limited] == [2, 1]
    assert all(
        len(batch) <= AUTHORITATIVE_VALUE_BATCH_MAX_ROWS
        and _encoded_batch_bytes(batch) <= AUTHORITATIVE_VALUE_BATCH_MAX_BYTES
        for batch in (*row_limited, *byte_limited)
    )

    with pytest.raises(
        PropertyCatalogSpanSourceError,
        match="one value row exceeds envelope budget",
    ):
        _value_batches(({"payload": "x" * AUTHORITATIVE_VALUE_BATCH_MAX_BYTES},))


def test_representative_kartik_page_has_fewer_envelopes_than_eight_row_pages() -> None:
    # The timed DEV evidence contained 36,803 projected values for 1,010
    # source spans.  Preserve that adversarial density in one 256-span page.
    values_per_span = math.ceil(36_803 / 1_010)
    representative = tuple(
        {
            "organization_id": ORG,
            "workspace_id": WORKSPACE,
            "project_id": PROJECT_A,
            "catalog_epoch": 1,
            "catalog_revision": 2,
            "build_token": BUILD_TOKEN,
            "source_kind": "custom_attribute",
            "attribute_key": f"attribute-{index % values_per_span:02d}",
            "attribute_type": "string",
            "value_fingerprint": f"{index:064x}",
            "value_json": json.dumps(f"value-{index}"),
            "value_search_text_folded": f"value-{index}",
            "first_seen": "2026-08-15 10:00:00.000000",
            "last_seen": "2026-08-15 10:00:00.000000",
        }
        for index in range(DEV_CANONICAL_SPAN_PAGE_ROWS * values_per_span)
    )
    batches = _value_batches(representative)

    assert len(representative) == 9_472
    assert len(batches) < DEV_CANONICAL_SPAN_PAGE_ROWS // 8
    assert all(
        len(batch) <= AUTHORITATIVE_VALUE_BATCH_MAX_ROWS
        and _encoded_batch_bytes(batch) <= AUTHORITATIVE_VALUE_BATCH_MAX_BYTES
        for batch in batches
    )


class _SimulatedCrash(RuntimeError):
    pass


class _AuthoritativeReader:
    def __init__(self, frozen: FrozenSpanSource) -> None:
        self.frozen = frozen
        self.first_cursor = SpanScanCursor(
            0,
            SourceCursor("span", "svc", "trace-a", "span-a"),
        ).encode()
        self.observations = ("1" * 64, "2" * 64)
        self.read_cursors: list[str | None] = []
        self.audit_calls = 0

    def read_page(
        self, frozen: FrozenSpanSource, *, cursor: str | None = None
    ) -> SpanScanPage:
        assert frozen == self.frozen
        self.read_cursors.append(cursor)
        if cursor in (None, ""):
            return SpanScanPage(
                project_id=PROJECT_A,
                spans=(self._span("span-a", "alpha"),),
                observation_sha256s=(self.observations[0],),
                next_cursor=self.first_cursor,
                terminal=False,
            )
        if cursor == self.first_cursor:
            return SpanScanPage(
                project_id=PROJECT_A,
                spans=(self._span("span-b", "beta"),),
                observation_sha256s=(self.observations[1],),
                next_cursor=None,
                terminal=True,
            )
        raise AssertionError(f"unexpected span cursor {cursor!r}")

    def audit(self, frozen: FrozenSpanSource) -> SpanAggregateProof:
        assert frozen == self.frozen
        self.audit_calls += 1
        accumulator = SpanAuditAccumulator()
        for observation in self.observations:
            accumulator.add(observation)
        return accumulator.proof

    def _span(self, span_id: str, value: str) -> SourceSpan:
        return SourceSpan(
            cursor=SourceCursor("span", "svc", "trace-a", span_id),
            seen_at=self.frozen.since + timedelta(minutes=1),
            attrs_string={"customer": value},
            attrs_number={},
            attrs_bool={},
            attributes_extra={},
        )


class _RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Mapping[str, Any], ...], str]] = []

    def publish(
        self, envelope: Any, *, value_rows: Sequence[Mapping[str, Any]] = ()
    ) -> str:
        rows = tuple(value_rows)
        payload_sha256 = encode_envelope(envelope, value_rows=rows).payload_sha256
        self.calls.append((envelope, rows, payload_sha256))
        return payload_sha256


class _ResumeStore:
    def __init__(self, *, fail_first_append: bool) -> None:
        self.fail_first_append = fail_first_append
        self.latest: dict[str, CheckpointWrite] = {}
        self.writes: list[CheckpointWrite] = []

    def append(self, value: CheckpointWrite) -> None:
        self.latest[value.checkpoint.producer_stream_id] = value
        self.writes.append(value)
        if self.fail_first_append:
            self.fail_first_append = False
            raise _SimulatedCrash("crash after durable running checkpoint")

    def load_checkpoint_write(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        catalog_revision: int,
        build_token: str,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> CheckpointWrite | None:
        assert organization_id == ORG
        assert workspace_id == WORKSPACE
        assert catalog_epoch == 1
        assert catalog_revision == 2
        assert build_token == BUILD_TOKEN
        assert source_adapter is SourceAdapter.SPAN_ATTRIBUTE
        return self.latest.get(producer_stream_id)


def _authoritative_case() -> tuple[
    FrozenSpanSource,
    AuthoritativeSpanBuild,
    _AuthoritativeReader,
    dict[AuthoritativeSpanRole, _RecordingPublisher],
    _ResumeStore,
]:
    since = datetime(2026, 8, 15, 10, tzinfo=UTC)
    frozen = FrozenSpanSource(
        (PROJECT_A,),
        since,
        since + timedelta(hours=1),
        77,
    )
    build = AuthoritativeSpanBuild(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=2,
        build_token=BUILD_TOKEN,
        projection_version=1,
        emitted_at=since,
        values_producer_stream_id=VALUES_STREAM,
        audit_producer_stream_id=AUDIT_STREAM,
    )
    return (
        frozen,
        build,
        _AuthoritativeReader(frozen),
        {
            AuthoritativeSpanRole.VALUES: _RecordingPublisher(),
            AuthoritativeSpanRole.SOURCE_AUDIT: _RecordingPublisher(),
        },
        _ResumeStore(fail_first_append=True),
    )


def _crash_after_first_page() -> tuple[
    FrozenSpanSource,
    AuthoritativeSpanBuild,
    _AuthoritativeReader,
    dict[AuthoritativeSpanRole, _RecordingPublisher],
    _ResumeStore,
    CheckpointWrite,
]:
    frozen, build, reader, publishers, store = _authoritative_case()
    reconciler = AuthoritativeSpanReconciler(
        reader=reader,  # type: ignore[arg-type]
        publishers=publishers,  # type: ignore[arg-type]
        checkpoint_store=store,
    )
    with pytest.raises(_SimulatedCrash, match="durable running checkpoint"):
        reconciler.run(frozen=frozen, build=build)
    return (
        frozen,
        build,
        reader,
        publishers,
        store,
        store.latest[VALUES_STREAM],
    )


def test_non_dry_span_reconcile_resumes_multipage_state_and_writes_terminal_watermarks() -> (
    None
):
    frozen, build, reader, publishers, store, running = _crash_after_first_page()

    assert running.checkpoint.status is CheckpointStatus.RUNNING
    assert not running.checkpoint.terminal
    assert running.source_cursor == reader.first_cursor
    assert running.watermark != running.source_cursor
    assert running.source_fingerprint == running.checkpoint.source_digest
    assert re.fullmatch(r"[0-9a-f]{64}", running.source_fingerprint)
    restored = SpanAuditAccumulator.decode(
        running.watermark,
        expected_digest=running.checkpoint.source_digest,
    )
    assert restored.proof.count == running.processed_rows == 1

    result = AuthoritativeSpanReconciler(
        reader=reader,  # type: ignore[arg-type]
        publishers=publishers,  # type: ignore[arg-type]
        checkpoint_store=store,
    ).run(frozen=frozen, build=build)

    assert result.values.status is CheckpointStatus.COMPLETE
    assert result.values.terminal
    assert result.values.source_count == 2
    assert result.values.value_count == 2
    assert result.values.delivery_count == 3
    assert result.source_audit.status is CheckpointStatus.COMPLETE
    assert result.source_audit.source_digest == result.values.source_digest
    assert reader.read_cursors == ["", reader.first_cursor]
    assert reader.audit_calls == 1

    values_write = store.latest[VALUES_STREAM]
    audit_write = store.latest[AUDIT_STREAM]
    for terminal in (values_write, audit_write):
        assert terminal.checkpoint.terminal
        assert terminal.source_cursor == ""
        assert terminal.watermark == str(frozen.audit_generation)
        assert terminal.source_fingerprint == terminal.checkpoint.source_digest
    value_envelopes = [
        call[0] for call in publishers[AuthoritativeSpanRole.VALUES].calls
    ]
    assert [envelope.sequence for envelope in value_envelopes] == [1, 2, 3]
    assert [envelope.terminal for envelope in value_envelopes] == [False, False, True]
    assert (
        value_envelopes[1].previous_payload_sha256
        == publishers[AuthoritativeSpanRole.VALUES].calls[0][2]
    )


@pytest.mark.parametrize("watermark", ["a", "not-an-accumulator", "A" * 513])
def test_span_resume_rejects_malformed_or_oversize_accumulator_watermark(
    watermark: str,
) -> None:
    frozen, build, reader, publishers, store, running = _crash_after_first_page()
    store.latest[VALUES_STREAM] = replace(running, watermark=watermark)
    calls_before_resume = tuple(reader.read_cursors)

    with pytest.raises(
        PropertyCatalogSpanSourceError,
        match="span audit resume state is invalid",
    ):
        AuthoritativeSpanReconciler(
            reader=reader,  # type: ignore[arg-type]
            publishers=publishers,  # type: ignore[arg-type]
            checkpoint_store=store,
        ).run(frozen=frozen, build=build)

    assert tuple(reader.read_cursors) == calls_before_resume
