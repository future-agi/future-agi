from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db.models.expressions import Col, Subquery
from django.db.models.functions import Greatest

from tracer.services.clickhouse.v2.property_catalog import source_adapters
from tracer.services.clickhouse.v2.property_catalog.models import (
    PropertyBindingRow,
    PropertyCategory,
    PropertyDefinition,
    PropertyKind,
    PropertyRole,
    SourceAdapter,
    VisibilityBinding,
    VisibilityScope,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
    project_definition,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    ReconcileMode,
    ReconcileRequest,
    _project_records,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    SourceKeysetCursor,
    SourceSnapshot,
    _group_project_relationships,
    _load_eval_template_page,
    _make_source_record,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
TEMPLATE = "44444444-4444-4444-8444-444444444444"
CONFIG = "55555555-5555-4555-8555-555555555555"
STREAM = "66666666-6666-4666-8666-666666666666"
BUILD = "77777777-7777-4777-8777-777777777777"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


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


def _definition() -> PropertyDefinition:
    return PropertyDefinition(
        property_kind=PropertyKind.EVAL_TEMPLATE,
        source_key=TEMPLATE,
        category=PropertyCategory.EVAL_METRIC,
        category_rank=1,
        source_rank=0,
        definition_source="eval_template",
        primary_source="all",
        source_tokens=("eval", "all"),
        value_adapter="eval_template",
        name=TEMPLATE,
        display_name="Quality",
        value_type="number",
        output_type="SCORE",
        role=PropertyRole.METRIC,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_relationship_grouping_keeps_deleted_rows_out_of_active_visibility() -> None:
    deleted_at = NOW - timedelta(minutes=1)
    projects, versions = _group_project_relationships(
        ((TEMPLATE, PROJECT, 0, deleted_at),),
        project_states={PROJECT: (NOW - timedelta(days=1), False, None)},
        relation_name="eval_configs",
    )

    assert projects.get(TEMPLATE, ()) == ()
    assert versions[TEMPLATE] == tuple(
        sorted(
            (
                ":".join(
                    (
                        "eval_configs",
                        PROJECT,
                        "0",
                        deleted_at.isoformat(timespec="microseconds"),
                    )
                ),
                ":".join(
                    (
                        "project",
                        PROJECT,
                        (NOW - timedelta(days=1)).isoformat(timespec="microseconds"),
                        "false",
                        "",
                    )
                ),
            )
        )
    )


def test_relationship_grouping_keeps_deleted_projects_out_of_active_visibility() -> (
    None
):
    deleted_at = NOW - timedelta(minutes=1)
    projects, versions = _group_project_relationships(
        ((TEMPLATE, PROJECT, 1, NOW - timedelta(days=1)),),
        project_states={PROJECT: (deleted_at, True, deleted_at)},
        relation_name="eval_configs",
    )

    assert projects.get(TEMPLATE, ()) == ()
    assert any(
        version.startswith(f"project:{PROJECT}:") and ":true:" in version
        for version in versions[TEMPLATE]
    )


def test_global_template_watermark_uses_all_relationship_deletion_timestamps(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def capture_keyset(queryset: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured["queryset"] = queryset
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(source_adapters, "_keyset_values", capture_keyset)

    assert _load_eval_template_page(context=_context(), cursor=None, limit=10) == ()

    queryset = captured["queryset"]
    assert set(queryset.query.annotations) == {
        "_has_workspace_relationship",
        "_relationship_updated_at",
        "_catalog_updated_at",
    }
    relationship_subquery = queryset.query.annotations["_relationship_updated_at"]
    assert isinstance(relationship_subquery, Subquery)
    relationship_watermark = relationship_subquery.query.annotations[
        "_latest_relationship_updated_at"
    ].get_source_expressions()[0]
    assert isinstance(relationship_watermark, Greatest)
    relationship_columns = {
        (expression.target.model._meta.label_lower, expression.target.name)
        for expression in relationship_watermark.get_source_expressions()
        if isinstance(expression, Col)
    }
    assert relationship_columns == {
        ("tracer.customevalconfig", "updated_at"),
        ("tracer.customevalconfig", "deleted_at"),
        ("tracer.project", "updated_at"),
        ("tracer.project", "deleted_at"),
    }
    assert relationship_subquery.query.order_by == ()

    catalog_watermark = queryset.query.annotations["_catalog_updated_at"]
    assert isinstance(catalog_watermark, Greatest)
    assert any(
        isinstance(expression, Subquery)
        for expression in catalog_watermark.get_source_expressions()
    )
    assert captured["kwargs"]["order_field"] == "_catalog_updated_at"


def test_deleted_last_relationship_emits_empty_complete_visibility_set(
    monkeypatch: Any,
) -> None:
    row = {
        "id": TEMPLATE,
        "name": "Quality",
        "config": {"output": "score"},
        "choices": [],
        "organization_id": None,
        "workspace_id": None,
        "deleted": False,
        "deleted_at": None,
        "updated_at": NOW - timedelta(days=1),
        "_catalog_updated_at": NOW,
    }

    monkeypatch.setattr(
        source_adapters,
        "_keyset_values",
        lambda *args, **kwargs: [row],
    )
    monkeypatch.setattr(
        source_adapters,
        "_eval_template_projects",
        lambda **kwargs: (
            {},
            {TEMPLATE: (f"config:{CONFIG}:{PROJECT}:deleted",)},
        ),
    )

    records = _load_eval_template_page(context=_context(), cursor=None, limit=10)

    assert len(records) == 1
    assert records[0].source_entity_id == TEMPLATE
    assert records[0].source_updated_at == NOW
    assert records[0].visibilities == ()


def test_organization_template_keeps_workspace_and_exact_project_bindings(
    monkeypatch: Any,
) -> None:
    row = {
        "id": TEMPLATE,
        "name": "Quality",
        "config": {"output": "score"},
        "choices": [],
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "deleted": False,
        "deleted_at": None,
        "updated_at": NOW - timedelta(days=1),
        "_catalog_updated_at": NOW,
    }

    monkeypatch.setattr(
        source_adapters,
        "_keyset_values",
        lambda *args, **kwargs: [row],
    )
    monkeypatch.setattr(
        source_adapters,
        "_eval_template_projects",
        lambda **kwargs: ({TEMPLATE: (PROJECT,)}, {TEMPLATE: ("live-config",)}),
    )

    records = _load_eval_template_page(context=_context(), cursor=None, limit=10)

    assert len(records) == 1
    assert records[0].visibilities == (
        VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
        VisibilityBinding(VisibilityScope.WORKSPACE_DEFAULT, WORKSPACE),
    )


def test_incremental_empty_visibility_tombstones_last_project_binding() -> None:
    definition = _definition()
    old: PropertyBindingRow = project_definition(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=1,
        build_token=BUILD,
        projection_version=1,
        visibility=VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
        definition=definition,
        source_adapter=SourceAdapter.EVAL_TEMPLATE,
        source_entity_id=TEMPLATE,
        source_version=1,
        source_fingerprint=_sha("before-delete"),
        producer_stream_id=STREAM,
        producer_sequence=1,
        emitted_at=NOW - timedelta(minutes=5),
    )
    record = _make_source_record(
        source_adapter=SourceAdapter.EVAL_TEMPLATE,
        source_entity_id=TEMPLATE,
        source_updated_at=NOW,
        definition=definition,
        visibilities=(),
        dependency_versions=("last-config-deleted",),
    )
    snapshot = SourceSnapshot(
        source_adapter=SourceAdapter.EVAL_TEMPLATE,
        records=(record,),
        next_cursor=None,
        terminal=True,
        source_count=1,
        source_bytes=record.encoded_bytes,
        source_digest=_sha("after-delete"),
        page_count=1,
    )
    request = ReconcileRequest(
        context=_context(),
        build_token=BUILD,
        producer_stream_id=STREAM,
        emitted_at=NOW,
        mode=ReconcileMode.INCREMENTAL,
        source_version=2,
        lower_watermark=SourceKeysetCursor(
            NOW - timedelta(minutes=2),
            TEMPLATE,
        ).encode(),
    )

    projected, conflicts = _project_records(
        snapshot=snapshot,
        current=(old,),
        request=request,
    )

    assert conflicts == 0
    assert len(projected) == 1
    assert projected[0].binding_id == old.binding_id
    assert projected[0].visibility_id == PROJECT
    assert projected[0].is_deleted is True
    assert projected[0].deleted_at == NOW
