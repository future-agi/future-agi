from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresReadBudget,
    PostgresSnapshotContext,
    PostgresSourcePage,
    ReadOnlyPostgresPropertyAdapter,
    validate_postgres_adapter,
    validate_postgres_page,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    _annotation_definition,
    _dataset_column_definition,
)


class _StubAdapter:
    source_adapter = SourceAdapter.DATASET_COLUMN
    read_only = True
    isolation_level = "repeatable_read"

    def read_page(self, *, context, cursor, budget):  # type: ignore[no-untyped-def]
        raise AssertionError("pure contract test must not perform source I/O")


def test_read_only_postgres_adapter_contract_is_runtime_checkable() -> None:
    adapter = _StubAdapter()
    assert isinstance(adapter, ReadOnlyPostgresPropertyAdapter)
    validate_postgres_adapter(adapter)


@pytest.mark.parametrize(
    "budget",
    [
        {"statement_timeout_ms": 8_001},
        {"wall_timeout_seconds": 8.51},
        {"max_rows_per_page": 1_001},
        {"max_total_rows": 1_000_001},
    ],
)
def test_postgres_read_budget_rejects_unbounded_values(
    budget: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        PostgresReadBudget(**budget)  # type: ignore[arg-type]


def test_postgres_read_budget_extends_only_explicit_long_running_modes() -> None:
    scheduled = PostgresReadBudget(
        wall_timeout_seconds=120.0,
        scheduled_reconcile=True,
    )
    extended = PostgresReadBudget(
        wall_timeout_seconds=540.0,
        initial_backfill=True,
    )

    assert scheduled.wall_timeout_seconds == 120.0
    assert scheduled.scheduled_reconcile is True
    assert extended.wall_timeout_seconds == 540.0
    assert extended.initial_backfill is True

    with pytest.raises(ValueError):
        PostgresReadBudget(
            wall_timeout_seconds=120.01,
            scheduled_reconcile=True,
        )
    with pytest.raises(ValueError):
        PostgresReadBudget(
            wall_timeout_seconds=540.01,
            initial_backfill=True,
        )
    with pytest.raises(ValueError):
        PostgresReadBudget(initial_backfill=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PostgresReadBudget(scheduled_reconcile=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="mutually exclusive"):
        PostgresReadBudget(initial_backfill=True, scheduled_reconcile=True)


def test_postgres_snapshot_models_are_metadata_only() -> None:
    context = PostgresSnapshotContext(
        organization_id="11111111-1111-4111-8111-111111111111",
        workspace_id="22222222-2222-4222-8222-222222222222",
        project_ids=("33333333-3333-4333-8333-333333333333",),
        catalog_epoch=1,
        catalog_revision=1,
        projection_version=1,
        snapshot_cutoff=datetime(2026, 8, 14, tzinfo=UTC),
    )
    page = PostgresSourcePage(
        definitions=(),
        next_cursor=None,
        terminal=True,
        source_count=0,
        source_digest=hashlib.sha256(b"").hexdigest(),
    )
    assert context.catalog_revision == 1
    assert page.terminal
    validate_postgres_page(page, budget=PostgresReadBudget())

    with pytest.raises(ValueError, match="terminal pages"):
        PostgresSourcePage(
            definitions=(),
            next_cursor="unexpected",
            terminal=True,
            source_count=0,
            source_digest=hashlib.sha256(b"").hexdigest(),
        )


def test_postgres_adapter_must_be_read_only_repeatable_read() -> None:
    adapter = _StubAdapter()
    adapter.read_only = False
    with pytest.raises(ValueError, match="read-only"):
        validate_postgres_adapter(adapter)
    adapter.read_only = True
    adapter.isolation_level = "read committed"
    with pytest.raises(ValueError, match="repeatable read"):
        validate_postgres_adapter(adapter)


def test_postgres_page_is_checked_against_caller_budget() -> None:
    page = PostgresSourcePage(
        definitions=(),
        next_cursor="cursor-2",
        terminal=False,
        source_count=11,
        source_digest=hashlib.sha256(b"page").hexdigest(),
    )
    with pytest.raises(ValueError, match="max_total_rows"):
        validate_postgres_page(
            page,
            budget=PostgresReadBudget(max_total_rows=10),
        )


@pytest.mark.parametrize("raw_name", [None, "", "   "])
def test_dataset_column_definition_survives_blank_legacy_name(
    raw_name: object,
) -> None:
    source_id = "33333333-3333-4333-8333-333333333333"

    definition = _dataset_column_definition(
        {
            "id": source_id,
            "name": raw_name,
            "data_type": "text",
        }
    )

    assert definition.display_name == f"Dataset column {source_id}"


def test_annotation_definition_survives_blank_legacy_name() -> None:
    source_id = "44444444-4444-4444-8444-444444444444"

    definition = _annotation_definition(
        {
            "id": source_id,
            "name": "",
            "type": "numeric",
            "settings": {},
        }
    )

    assert definition.display_name == f"Annotation {source_id}"
