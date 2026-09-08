from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.db.models.expressions import Col, Subquery
from django.db.models.functions import Greatest

from tracer.services.clickhouse.v2.property_catalog import source_adapters
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresReadBudget,
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    DatasetColumnSourceAdapter,
    PropertySourceError,
    SourceReadBudget,
    _BoundedSourceAdapter,
    _group_project_relationships,
    _load_annotation_label_page,
    _load_dataset_column_page,
    _load_eval_config_page,
    _load_eval_template_page,
    _load_simulation_eval_config_page,
    postgres_revision_snapshot,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
TEMPLATE = "44444444-4444-4444-8444-444444444444"
CONFIG = "55555555-5555-4555-8555-555555555555"
RUN_TEST = "66666666-6666-4666-8666-666666666666"
AGENT = "77777777-7777-4777-8777-777777777777"
DATASET = "88888888-8888-4888-8888-888888888888"
COLUMN = "99999999-9999-4999-8999-999999999999"
LABEL = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=1)


def _context(*, revision: int = 2) -> PostgresSnapshotContext:
    return PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=revision,
        projection_version=1,
        snapshot_cutoff=NOW,
    )


def _columns(expression: Any) -> set[tuple[str, str]]:
    return {
        (item.target.model._meta.label_lower, item.target.name)
        for item in expression.get_source_expressions()
        if isinstance(item, Col)
    }


@pytest.mark.parametrize(
    ("loader", "expected_columns"),
    (
        (
            _load_eval_template_page,
            {
                ("model_hub.evaltemplate", "updated_at"),
                ("model_hub.evaltemplate", "deleted_at"),
            },
        ),
        (
            _load_eval_config_page,
            {
                ("tracer.customevalconfig", "updated_at"),
                ("tracer.customevalconfig", "deleted_at"),
                ("tracer.project", "updated_at"),
                ("tracer.project", "deleted_at"),
                ("model_hub.evaltemplate", "updated_at"),
                ("model_hub.evaltemplate", "deleted_at"),
            },
        ),
        (
            _load_simulation_eval_config_page,
            {
                ("simulate.simulateevalconfig", "updated_at"),
                ("simulate.simulateevalconfig", "deleted_at"),
                ("simulate.runtest", "updated_at"),
                ("simulate.runtest", "deleted_at"),
                ("simulate.agentdefinition", "updated_at"),
                ("simulate.agentdefinition", "deleted_at"),
                ("model_hub.evaltemplate", "updated_at"),
                ("model_hub.evaltemplate", "deleted_at"),
            },
        ),
        (
            _load_annotation_label_page,
            {
                ("model_hub.annotationslabels", "updated_at"),
                ("model_hub.annotationslabels", "deleted_at"),
                ("tracer.project", "updated_at"),
                ("tracer.project", "deleted_at"),
            },
        ),
        (
            _load_dataset_column_page,
            {
                ("model_hub.column", "updated_at"),
                ("model_hub.column", "deleted_at"),
                ("model_hub.dataset", "updated_at"),
                ("model_hub.dataset", "deleted_at"),
            },
        ),
    ),
)
def test_relational_catalog_watermarks_include_every_lifecycle_clock(
    monkeypatch: pytest.MonkeyPatch,
    loader: Callable[..., Any],
    expected_columns: set[tuple[str, str]],
) -> None:
    captured: dict[str, Any] = {}

    def capture_keyset(queryset: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured["queryset"] = queryset
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(source_adapters, "_keyset_values", capture_keyset)

    assert loader(context=_context(), cursor=None, limit=10) == ()

    catalog_watermark = captured["queryset"].query.annotations["_catalog_updated_at"]
    assert isinstance(catalog_watermark, Greatest)
    assert _columns(catalog_watermark) == expected_columns
    assert captured["kwargs"]["order_field"] == "_catalog_updated_at"
    if loader in {
        _load_eval_template_page,
        _load_eval_config_page,
        _load_annotation_label_page,
    }:
        normalized_sql = str(captured["queryset"].query).replace("-", "")
        assert PROJECT.replace("-", "") in normalized_sql


def test_annotation_relationship_watermark_includes_score_and_project_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_keyset(queryset: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured["queryset"] = queryset
        return []

    monkeypatch.setattr(source_adapters, "_keyset_values", capture_keyset)
    assert _load_annotation_label_page(context=_context(), cursor=None, limit=10) == ()

    label_query = captured["queryset"]
    relationship = label_query.query.annotations["_score_relationship_updated_at"]
    assert isinstance(relationship, Subquery)
    score_watermark = relationship.query.annotations[
        "_latest_relationship_updated_at"
    ].get_source_expressions()[0]
    assert _columns(score_watermark) == {
        ("model_hub.score", "updated_at"),
        ("model_hub.score", "deleted_at"),
    }
    project_watermark = relationship.query.annotations["_project_updated_at"]
    assert isinstance(project_watermark, Subquery)
    assert _columns(project_watermark.query.annotations["_catalog_updated_at"]) == {
        ("tracer.project", "updated_at"),
        ("tracer.project", "deleted_at"),
    }


def test_bulk_deleted_score_or_project_is_not_an_active_label_visibility() -> None:
    deleted_score_projects, score_versions = _group_project_relationships(
        ((LABEL, PROJECT, 0, NOW),),
        project_states={PROJECT: (OLD, False, None)},
        relation_name="scores",
    )
    deleted_project_projects, project_versions = _group_project_relationships(
        ((LABEL, PROJECT, 1, OLD),),
        project_states={PROJECT: (OLD, True, NOW)},
        relation_name="scores",
    )

    assert deleted_score_projects.get(LABEL, ()) == ()
    assert deleted_project_projects.get(LABEL, ()) == ()
    assert any(":0:" in version for version in score_versions[LABEL])
    assert any(":true:" in version for version in project_versions[LABEL])


def test_relationship_grouping_is_independent_of_raw_relationship_cardinality() -> None:
    projects, versions = _group_project_relationships(
        ((LABEL, PROJECT, 250_000, NOW),),
        project_states={PROJECT: (OLD, False, None)},
        relation_name="scores",
    )

    assert projects[LABEL] == (PROJECT,)
    assert any(":250000:" in version for version in versions[LABEL])


@pytest.mark.parametrize(
    ("loader", "row"),
    (
        (
            _load_eval_config_page,
            {
                "id": CONFIG,
                "name": "Quality",
                "project_id": PROJECT,
                "project__updated_at": OLD,
                "project__deleted_at": NOW,
                "project__deleted": True,
                "eval_template_id": TEMPLATE,
                "eval_template__name": "Quality",
                "eval_template__config": {"output": "score"},
                "eval_template__choices": [],
                "eval_template__deleted": False,
                "eval_template__updated_at": OLD,
                "eval_template__deleted_at": None,
                "deleted": False,
                "deleted_at": None,
                "updated_at": OLD,
                "_catalog_updated_at": NOW,
            },
        ),
        (
            _load_simulation_eval_config_page,
            {
                "id": CONFIG,
                "name": "Quality",
                "run_test__agent_definition_id": AGENT,
                "run_test__deleted": False,
                "run_test__updated_at": OLD,
                "run_test__deleted_at": None,
                "run_test__agent_definition__deleted": False,
                "run_test__agent_definition__updated_at": OLD,
                "run_test__agent_definition__deleted_at": None,
                "eval_template_id": TEMPLATE,
                "eval_template__name": "Quality",
                "eval_template__config": {"output": "score"},
                "eval_template__choices": [],
                "eval_template__deleted": True,
                "eval_template__updated_at": OLD,
                "eval_template__deleted_at": NOW,
                "deleted": False,
                "deleted_at": None,
                "updated_at": OLD,
                "_catalog_updated_at": NOW,
            },
        ),
        (
            _load_annotation_label_page,
            {
                "id": LABEL,
                "name": "Correctness",
                "type": "numeric",
                "settings": {},
                "project_id": PROJECT,
                "project__deleted": True,
                "project__updated_at": OLD,
                "project__deleted_at": NOW,
                "deleted": False,
                "deleted_at": None,
                "updated_at": OLD,
                "_catalog_updated_at": NOW,
            },
        ),
        (
            _load_dataset_column_page,
            {
                "id": COLUMN,
                "name": "answer",
                "data_type": "text",
                "dataset_id": DATASET,
                "dataset__deleted": True,
                "dataset__updated_at": OLD,
                "dataset__deleted_at": NOW,
                "deleted": False,
                "deleted_at": None,
                "updated_at": OLD,
                "_catalog_updated_at": NOW,
            },
        ),
    ),
)
def test_bulk_dependency_soft_delete_emits_incremental_tombstone(
    monkeypatch: pytest.MonkeyPatch,
    loader: Callable[..., Any],
    row: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        source_adapters,
        "_keyset_values",
        lambda *args, **kwargs: [row],
    )
    monkeypatch.setattr(
        source_adapters,
        "_annotation_score_projects",
        lambda **kwargs: ({}, {}),
    )

    records = loader(context=_context(), cursor=None, limit=10)

    assert len(records) == 1
    assert records[0].source_updated_at == NOW
    assert records[0].is_deleted is True
    assert records[0].deleted_at == NOW


def _postgres_adapter(source_adapter: SourceAdapter) -> _BoundedSourceAdapter:
    return _BoundedSourceAdapter(
        source_adapter=source_adapter,
        page_loader=lambda **kwargs: (),
        postgres_snapshot=True,
        monotonic=lambda: 0.0,
    )


def test_revision_snapshot_reuses_one_transaction_across_adapters_and_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    opened_with: list[dict[str, Any]] = []

    @contextmanager
    def fake_transaction(**kwargs: Any) -> Iterator[None]:
        opened_with.append(kwargs)
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(
        source_adapters,
        "_django_repeatable_read_snapshot",
        fake_transaction,
    )
    postgres_budget = PostgresReadBudget(
        statement_timeout_ms=900,
        wall_timeout_seconds=1.0,
        max_rows_per_page=10,
        max_total_rows=10,
    )
    source_budget = SourceReadBudget(postgres=postgres_budget)
    first = _postgres_adapter(SourceAdapter.EVAL_CONFIG)
    second = _postgres_adapter(SourceAdapter.DATASET_COLUMN)

    with postgres_revision_snapshot(
        context=_context(),
        budget=postgres_budget,
        monotonic=lambda: 0.0,
    ):
        first.read_snapshot(context=_context(), budget=source_budget)
        first.read_snapshot(context=_context(), budget=source_budget, cursor=None)
        with postgres_revision_snapshot(
            context=_context(),
            budget=postgres_budget,
            monotonic=lambda: 99.0,
        ):
            second.read_snapshot(context=_context(), budget=source_budget)

    assert events == ["enter", "exit"]
    assert len(opened_with) == 1
    assert opened_with[0]["deadline"] == 1.0
    assert opened_with[0]["statement_timeout_ms"] == 900


def test_default_postgres_adapter_fails_closed_without_revision_snapshot() -> None:
    adapter = _postgres_adapter(SourceAdapter.DATASET_COLUMN)

    with pytest.raises(PropertySourceError, match="revision snapshot session"):
        adapter.read_snapshot(context=_context(), budget=SourceReadBudget())


def test_revision_snapshot_wall_is_shared_instead_of_reset_per_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def fake_transaction(**kwargs: Any) -> Iterator[None]:
        yield

    monkeypatch.setattr(
        source_adapters,
        "_django_repeatable_read_snapshot",
        fake_transaction,
    )
    now = [0.0]

    def monotonic() -> float:
        return now[0]

    postgres_budget = PostgresReadBudget(
        statement_timeout_ms=900,
        wall_timeout_seconds=1.0,
    )
    source_budget = SourceReadBudget(postgres=postgres_budget)

    with postgres_revision_snapshot(
        context=_context(),
        budget=postgres_budget,
        monotonic=monotonic,
    ):
        _postgres_adapter(SourceAdapter.EVAL_CONFIG).read_snapshot(
            context=_context(),
            budget=source_budget,
        )
        now[0] = 1.0
        with pytest.raises(PropertySourceError, match="deadline exceeded"):
            _postgres_adapter(SourceAdapter.DATASET_COLUMN).read_snapshot(
                context=_context(),
                budget=source_budget,
            )


def test_revision_snapshot_rejects_scope_or_budget_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def fake_transaction(**kwargs: Any) -> Iterator[None]:
        yield

    monkeypatch.setattr(
        source_adapters,
        "_django_repeatable_read_snapshot",
        fake_transaction,
    )
    budget = PostgresReadBudget(
        statement_timeout_ms=900,
        wall_timeout_seconds=1.0,
    )

    with postgres_revision_snapshot(
        context=_context(),
        budget=budget,
        monotonic=lambda: 0.0,
    ):
        with pytest.raises(PropertySourceError, match="changed scope"):
            with postgres_revision_snapshot(
                context=_context(revision=3),
                budget=budget,
            ):
                pass
        with pytest.raises(PropertySourceError, match="changed budget"):
            with postgres_revision_snapshot(
                context=_context(),
                budget=PostgresReadBudget(
                    statement_timeout_ms=800,
                    wall_timeout_seconds=1.0,
                ),
            ):
                pass


def test_injected_page_loader_remains_pure_without_postgres_session() -> None:
    adapter = DatasetColumnSourceAdapter(page_loader=lambda **kwargs: ())

    snapshot = adapter.read_snapshot(context=_context(), budget=SourceReadBudget())

    assert snapshot.terminal is True
    assert snapshot.records == ()


def test_extended_non_postgres_scan_can_outlive_postgres_wall() -> None:
    now = [0.0]

    def monotonic() -> float:
        return now[0]

    def slow_complete_page(**_kwargs: Any) -> tuple[()]:
        now[0] = 9.0
        return ()

    shared = SharedCatalogDeadline(wall_ms=20_000, clock=monotonic)
    budget = SourceReadBudget(
        adapter_wall_timeout_seconds=20.0,
        shared_deadline=shared,
    )
    adapter = _BoundedSourceAdapter(
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        page_loader=slow_complete_page,
        postgres_snapshot=False,
        monotonic=monotonic,
    )

    snapshot = adapter.read_snapshot(context=_context(), budget=budget)

    assert snapshot.terminal is True
    assert budget.postgres.wall_timeout_seconds == 8.5


def test_non_postgres_scan_cannot_outlive_shared_deadline() -> None:
    now = [0.0]

    def monotonic() -> float:
        return now[0]

    def page_finishing_at_shared_deadline(**_kwargs: Any) -> tuple[()]:
        now[0] = 10.0
        return ()

    shared = SharedCatalogDeadline(wall_ms=10_000, clock=monotonic)
    budget = SourceReadBudget(
        adapter_wall_timeout_seconds=540.0,
        shared_deadline=shared,
    )
    adapter = _BoundedSourceAdapter(
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        page_loader=page_finishing_at_shared_deadline,
        postgres_snapshot=False,
        monotonic=monotonic,
    )

    with pytest.raises(PropertySourceError, match="deadline exceeded"):
        adapter.read_snapshot(context=_context(), budget=budget)
