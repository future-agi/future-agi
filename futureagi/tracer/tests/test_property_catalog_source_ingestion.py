from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tracer.services.clickhouse.v2.property_catalog import source_adapters
from tracer.services.clickhouse.v2.property_catalog.codec import ZERO_UUID
from tracer.services.clickhouse.v2.property_catalog.models import (
    PropertyCatalogEnvelope,
    SourceAdapter,
    VisibilityBinding,
    VisibilityScope,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import (
    CheckpointWrite,
    PropertyCatalogReconciler,
    ReconcileRequest,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    SYSTEM_MANIFEST_EXPECTED_COUNT,
    SYSTEM_MANIFEST_EXPECTED_SHA256,
    SourceReadBudget,
    SystemManifestAdapter,
    _load_annotation_label_page,
    _load_dataset_column_page,
    _load_eval_config_page,
    _load_eval_template_page,
    _load_simulation_eval_config_page,
    system_manifest_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.wire import (
    WireEnvelope,
    encode_envelope,
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
BUILD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
STREAM = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=1)
RESTORED = NOW + timedelta(minutes=1)


def _context(*, cutoff: datetime = RESTORED) -> PostgresSnapshotContext:
    return PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=2,
        projection_version=1,
        snapshot_cutoff=cutoff,
    )


class _WirePublisher:
    def __init__(self) -> None:
        self.envelopes: list[PropertyCatalogEnvelope] = []
        self.wires: list[WireEnvelope] = []

    def publish(self, envelope: PropertyCatalogEnvelope) -> str:
        wire = encode_envelope(envelope)
        self.envelopes.append(envelope)
        self.wires.append(wire)
        return wire.payload_sha256


class _CheckpointWriter:
    def __init__(self) -> None:
        self.writes: list[CheckpointWrite] = []

    def append(self, checkpoint: CheckpointWrite) -> None:
        self.writes.append(checkpoint)


class _NoCurrentBindings:
    def read_current(self, **_kwargs: object) -> tuple[()]:
        return ()


def test_checked_in_system_manifest_reconciles_to_definition_rows_only() -> None:
    adapter = SystemManifestAdapter()
    snapshot = adapter.read_snapshot(
        context=_context(),
        budget=SourceReadBudget(),
    )

    assert snapshot.terminal is True
    assert snapshot.source_count == SYSTEM_MANIFEST_EXPECTED_COUNT
    assert system_manifest_sha256() == SYSTEM_MANIFEST_EXPECTED_SHA256
    assert all(
        record.source_adapter is SourceAdapter.SYSTEM_MANIFEST
        and record.visibilities
        == (VisibilityBinding(VisibilityScope.ALWAYS, ZERO_UUID),)
        and not record.is_deleted
        for record in snapshot.records
    )

    publisher = _WirePublisher()
    checkpoints = _CheckpointWriter()
    result = PropertyCatalogReconciler(
        publisher=publisher,
        checkpoint_writer=checkpoints,
        current_bindings=_NoCurrentBindings(),
    ).reconcile(
        adapter,
        ReconcileRequest(
            context=_context(),
            build_token=BUILD,
            producer_stream_id=STREAM,
            emitted_at=RESTORED,
        ),
    )

    assert result.complete is True
    assert len(checkpoints.writes) == 1
    assert sum(len(envelope.definitions) for envelope in result.envelopes) == (
        SYSTEM_MANIFEST_EXPECTED_COUNT
    )
    assert result.envelopes[-1].terminal is True
    assert all(
        chunk["table"] == "property_definition_catalog"
        for wire in publisher.wires
        for chunk in wire.document["payload"]["chunks"]
    )
    assert all(wire.document["payload"]["value_rows"] == 0 for wire in publisher.wires)


def _eval_template_row() -> dict[str, Any]:
    return {
        "id": TEMPLATE,
        "name": "Quality",
        "config": {"output": "score"},
        "choices": [],
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "deleted": False,
        "deleted_at": None,
        "updated_at": OLD,
        "_catalog_updated_at": OLD,
    }


def _eval_config_row() -> dict[str, Any]:
    return {
        "id": CONFIG,
        "name": "Quality",
        "project_id": PROJECT,
        "project__updated_at": OLD,
        "project__deleted_at": None,
        "project__deleted": False,
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
        "_catalog_updated_at": OLD,
    }


def _simulation_eval_config_row() -> dict[str, Any]:
    return {
        "id": CONFIG,
        "name": "Simulation quality",
        "run_test__agent_definition_id": AGENT,
        "run_test__deleted": False,
        "run_test__updated_at": OLD,
        "run_test__deleted_at": None,
        "run_test__agent_definition__deleted": False,
        "run_test__agent_definition__updated_at": OLD,
        "run_test__agent_definition__deleted_at": None,
        "eval_template_id": TEMPLATE,
        "eval_template__name": "Quality",
        "eval_template__config": {"output": "Pass/Fail"},
        "eval_template__choices": [],
        "eval_template__deleted": False,
        "eval_template__updated_at": OLD,
        "eval_template__deleted_at": None,
        "deleted": False,
        "deleted_at": None,
        "updated_at": OLD,
        "_catalog_updated_at": OLD,
    }


def _annotation_row() -> dict[str, Any]:
    return {
        "id": LABEL,
        "name": "Disposition",
        "type": "categorical",
        "settings": {"options": ["Accepted", "Rejected"]},
        "project_id": PROJECT,
        "project__deleted": False,
        "project__updated_at": OLD,
        "project__deleted_at": None,
        "deleted": False,
        "deleted_at": None,
        "updated_at": OLD,
        "_catalog_updated_at": OLD,
    }


def _dataset_column_row() -> dict[str, Any]:
    return {
        "id": COLUMN,
        "name": "attempt_count",
        "data_type": "integer",
        "dataset_id": DATASET,
        "dataset__deleted": False,
        "dataset__updated_at": OLD,
        "dataset__deleted_at": None,
        "deleted": False,
        "deleted_at": None,
        "updated_at": OLD,
        "_catalog_updated_at": OLD,
    }


@pytest.mark.parametrize(
    (
        "loader",
        "row_factory",
        "source_adapter",
        "visibilities",
        "primary_source",
        "value_adapter",
    ),
    (
        (
            _load_eval_template_page,
            _eval_template_row,
            SourceAdapter.EVAL_TEMPLATE,
            {
                VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
                VisibilityBinding(VisibilityScope.WORKSPACE_DEFAULT, WORKSPACE),
            },
            "all",
            "eval_template",
        ),
        (
            _load_eval_config_page,
            _eval_config_row,
            SourceAdapter.EVAL_CONFIG,
            {VisibilityBinding(VisibilityScope.PROJECT, PROJECT)},
            "all",
            "eval_config",
        ),
        (
            _load_simulation_eval_config_page,
            _simulation_eval_config_row,
            SourceAdapter.SIMULATION_EVAL_CONFIG,
            {VisibilityBinding(VisibilityScope.AGENT_DEFINITION, AGENT)},
            "simulation",
            "eval_config",
        ),
        (
            _load_annotation_label_page,
            _annotation_row,
            SourceAdapter.ANNOTATION_LABEL,
            {VisibilityBinding(VisibilityScope.PROJECT, PROJECT)},
            "both",
            "annotation_label",
        ),
        (
            _load_dataset_column_page,
            _dataset_column_row,
            SourceAdapter.DATASET_COLUMN,
            {VisibilityBinding(VisibilityScope.DATASET, DATASET)},
            "datasets",
            "dataset_column",
        ),
    ),
)
def test_relational_sources_generate_active_catalog_properties(
    monkeypatch: pytest.MonkeyPatch,
    loader: Callable[..., Any],
    row_factory: Callable[[], dict[str, Any]],
    source_adapter: SourceAdapter,
    visibilities: set[VisibilityBinding],
    primary_source: str,
    value_adapter: str,
) -> None:
    row = row_factory()
    _install_relational_rows(monkeypatch, row)

    records = loader(context=_context(), cursor=None, limit=10)

    assert len(records) == 1
    record = records[0]
    assert record.source_adapter is source_adapter
    assert set(record.visibilities) == visibilities
    assert record.is_deleted is False
    assert record.deleted_at is None
    assert record.definition.primary_source == primary_source
    assert record.definition.value_adapter == value_adapter


@pytest.mark.parametrize(
    ("loader", "row_factory"),
    (
        (_load_eval_template_page, _eval_template_row),
        (_load_eval_config_page, _eval_config_row),
        (_load_simulation_eval_config_page, _simulation_eval_config_row),
        (_load_annotation_label_page, _annotation_row),
        (_load_dataset_column_page, _dataset_column_row),
    ),
)
def test_relational_source_delete_and_restore_advance_catalog_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    loader: Callable[..., Any],
    row_factory: Callable[[], dict[str, Any]],
) -> None:
    current: dict[str, Mapping[str, Any]] = {"row": row_factory()}
    _install_relational_rows(monkeypatch, current)

    active = loader(context=_context(), cursor=None, limit=10)[0]

    deleted_row = dict(current["row"])
    deleted_row.update(
        deleted=True,
        deleted_at=NOW,
        updated_at=NOW,
        _catalog_updated_at=NOW,
    )
    current["row"] = deleted_row
    deleted = loader(context=_context(), cursor=None, limit=10)[0]

    restored_row = dict(current["row"])
    restored_row.update(
        deleted=False,
        deleted_at=None,
        updated_at=RESTORED,
        _catalog_updated_at=RESTORED,
    )
    current["row"] = restored_row
    restored = loader(context=_context(), cursor=None, limit=10)[0]

    assert active.is_deleted is False
    assert deleted.is_deleted is True
    assert deleted.deleted_at == NOW
    assert restored.is_deleted is False
    assert restored.deleted_at is None
    assert restored.source_updated_at == RESTORED
    assert deleted.source_fingerprint != active.source_fingerprint
    assert restored.source_fingerprint == active.source_fingerprint


def _install_relational_rows(
    monkeypatch: pytest.MonkeyPatch,
    row: Mapping[str, Any] | Mapping[str, Mapping[str, Any]],
) -> None:
    def current_row() -> Mapping[str, Any]:
        nested = row.get("row")
        return nested if isinstance(nested, Mapping) else row

    monkeypatch.setattr(
        source_adapters,
        "_keyset_values",
        lambda *args, **kwargs: [dict(current_row())],
    )
    monkeypatch.setattr(
        source_adapters,
        "_eval_template_projects",
        lambda **kwargs: ({TEMPLATE: (PROJECT,)}, {TEMPLATE: ("active-config",)}),
    )
    monkeypatch.setattr(
        source_adapters,
        "_annotation_score_projects",
        lambda **kwargs: ({}, {}),
    )
