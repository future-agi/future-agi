from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    reader as reader_module,
)
from tracer.services.clickhouse.v2.property_catalog import (
    value_reader as value_reader_module,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_TABLE,
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlRejected,
    ActivationControlRequest,
    ActivationControlScope,
    ActivationControlTarget,
    ActivationControlUnavailable,
    ClickHouseActivationControlSelector,
    ClickHouseActivationControlStore,
    PropertyCatalogActivationControlPlane,
    QualifiedActivation,
    activation_control_event_sql,
    activation_control_selector_for_deployment,
    canonical_control_events,
    qualified_activation_sql,
    selected_control_target,
)
from tracer.services.clickhouse.v2.property_catalog.reader import (
    PropertyCatalogActivation,
    PropertyCatalogReader,
    PropertyCatalogUnavailable,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValueReader,
    PropertyCatalogValueUnavailable,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
AT = datetime(2026, 8, 25, 20, tzinfo=UTC)
PROD_DATABASE = "property_catalog"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _uuid(index: int) -> str:
    return str(uuid.UUID(int=1000 + index))


def _target(revision: int, *, epoch: int = 7) -> ActivationControlTarget:
    return ActivationControlTarget(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=epoch,
        projection_version=1,
        catalog_revision=revision,
        build_token=_uuid(revision),
        activation_sha256=_sha(f"activation:{epoch}:{revision}"),
    )


def _qualified(
    revision: int,
    lifecycle_sequence: int,
    *,
    epoch: int = 7,
) -> QualifiedActivation:
    return QualifiedActivation(
        target=_target(revision, epoch=epoch),
        lifecycle_activation_sequence=lifecycle_sequence,
    )


class _Store:
    def __init__(
        self,
        qualified: tuple[QualifiedActivation, ...],
        events: tuple[ActivationControlEvent, ...] = (),
    ) -> None:
        self.qualified = qualified
        self.events = list(events)
        self.append_calls: list[ActivationControlEvent] = []
        self.concurrent: ActivationControlEvent | None = None

    def list_qualified_activations(
        self,
        _scope: ActivationControlScope,
    ) -> tuple[QualifiedActivation, ...]:
        return self.qualified

    def list_control_events(
        self,
        _scope: ActivationControlScope,
    ) -> tuple[ActivationControlEvent, ...]:
        return tuple(self.events)

    def append_control_event(self, event, *, expected_head):
        actual_head = self.events[-1].head if self.events else None
        if actual_head != expected_head:
            raise ActivationControlRejected("control_concurrent")
        if self.concurrent is not None:
            self.events.append(self.concurrent)
            self.concurrent = None
            raise ActivationControlRejected("control_concurrent")
        self.events.append(event)
        self.append_calls.append(event)
        return event


def _request(
    index: int,
    target: ActivationControlTarget,
    expected_head=None,
) -> ActivationControlRequest:
    return ActivationControlRequest(
        request_id=_uuid(100 + index),
        target=target,
        expected_head=expected_head,
    )


def _activate_latest(store: _Store, *, request_index: int = 1):
    target = store.qualified[-1].target
    return PropertyCatalogActivationControlPlane(store).activate(
        request=_request(request_index, target),
        now=AT,
    )


def test_control_sequence_is_independent_from_lifecycle_activation_sequence() -> None:
    store = _Store((_qualified(91, 4_001),))

    result = _activate_latest(store)

    assert result.event.control_sequence == 1
    assert store.qualified[0].lifecycle_activation_sequence == 4_001
    assert result.selected_target == _target(91)
    assert result.event.target.catalog_revision == 91


def test_disable_is_a_no_fallback_head_and_exact_replay_is_idempotent() -> None:
    store = _Store((_qualified(1, 10), _qualified(2, 11)))
    control = PropertyCatalogActivationControlPlane(store)
    activated = _activate_latest(store)
    request = _request(2, _target(2), activated.event.head)

    first = control.disable(request=request, now=AT + timedelta(minutes=1))
    replay = control.disable(request=request, now=AT + timedelta(hours=1))

    assert first.event.action is ActivationControlAction.DISABLE
    assert first.selected_target is None
    assert selected_control_target(store.events) is None
    assert replay.idempotent
    assert replay.event == first.event
    assert len(store.append_calls) == 2


def test_disabled_latest_target_can_be_explicitly_reenabled() -> None:
    store = _Store((_qualified(1, 10),))
    control = PropertyCatalogActivationControlPlane(store)
    activated = _activate_latest(store)
    disabled = control.disable(
        request=_request(2, _target(1), activated.event.head),
        now=AT + timedelta(minutes=1),
    )

    reenabled = control.activate(
        request=_request(3, _target(1), disabled.event.head),
        now=AT + timedelta(minutes=2),
    )

    assert reenabled.event.control_sequence == 3
    assert reenabled.selected_target == _target(1)


def test_rollback_selects_only_an_exact_prior_qualified_target() -> None:
    qualified = (_qualified(1, 70), _qualified(2, 71), _qualified(3, 72))
    store = _Store(qualified)
    before = tuple(store.qualified)
    control = PropertyCatalogActivationControlPlane(store)
    activated = _activate_latest(store)

    result = control.rollback(
        request=_request(2, _target(1), activated.event.head),
        now=AT + timedelta(minutes=1),
    )

    assert result.event.action is ActivationControlAction.ROLLBACK
    assert result.selected_target == _target(1)
    assert tuple(store.qualified) == before, "lifecycle activations are immutable"

    unknown = replace(_target(1), activation_sha256=_sha("not-qualified"))
    with pytest.raises(ActivationControlRejected, match="target_not_qualified"):
        control.rollback(
            request=_request(3, unknown, result.event.head),
            now=AT + timedelta(minutes=2),
        )


def test_stale_and_concurrent_writers_are_rejected_fail_closed() -> None:
    store = _Store((_qualified(1, 1), _qualified(2, 2)))
    activated = _activate_latest(store)
    stale = _request(2, _target(2), expected_head=None)

    with pytest.raises(ActivationControlRejected, match="control_stale"):
        PropertyCatalogActivationControlPlane(store).disable(
            request=stale,
            now=AT + timedelta(minutes=1),
        )

    concurrent = ActivationControlEvent.create(
        control_sequence=2,
        request_id=_uuid(999),
        action=ActivationControlAction.DISABLE,
        target=_target(2),
        previous_control_sha256=activated.event.control_sha256,
        controlled_at=AT + timedelta(minutes=1),
    )
    store.concurrent = concurrent
    with pytest.raises(ActivationControlRejected, match="control_concurrent"):
        PropertyCatalogActivationControlPlane(store).disable(
            request=_request(3, _target(2), activated.event.head),
            now=AT + timedelta(minutes=1),
        )


def test_physical_history_survives_exact_duplicates_but_never_forks() -> None:
    first = ActivationControlEvent.create(
        control_sequence=1,
        request_id=_uuid(201),
        action=ActivationControlAction.ACTIVATE,
        target=_target(1),
        previous_control_sha256="0" * 64,
        controlled_at=AT,
    )
    assert canonical_control_events((first, first)) == (first,)

    fork = ActivationControlEvent.create(
        control_sequence=1,
        request_id=_uuid(202),
        action=ActivationControlAction.ACTIVATE,
        target=_target(2),
        previous_control_sha256="0" * 64,
        controlled_at=AT,
    )
    with pytest.raises(ActivationControlRejected, match="control_sequence_conflict"):
        canonical_control_events((first, fork))

    sql = activation_control_event_sql(PROD_DATABASE)
    assert ACTIVATION_CONTROL_TABLE in sql
    assert " FINAL" not in sql.upper()
    assert "ARGMAX" not in sql.upper()


def test_qualified_activation_sql_uses_clickhouse25_safe_raw_inputs() -> None:
    sql = qualified_activation_sql(PROD_DATABASE)

    assert "FROM versioned AS versioned_rows" in sql
    for column in (
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
    ):
        assert f"versioned_rows.{column}" in sql
    for column in (
        "projection_version",
        "activation_sequence",
        "activation_sha256",
        "status",
    ):
        assert f"argMax(versioned_rows.{column}, versioned_rows._version)" in sql
        assert f"argMax({column}, _version)" not in sql
    assert "versioned_rows._version = versioned_rows.latest_version" in sql
    assert "_version = latest_version" not in sql


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("catalog_epoch", 0),
        ("catalog_epoch", "7"),
        ("projection_version", 1.0),
        ("catalog_revision", True),
    ),
)
def test_control_target_requires_exact_positive_integer_binding(
    field: str,
    value: Any,
) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_target(1), **{field: value})


class _QueryExecutor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, query, params, **_kwargs):
        self.calls.append((query, params))
        return SimpleNamespace(data=list(self.rows))


def test_production_selector_honors_activate_and_disable_and_dev_is_unwired() -> None:
    store = _Store((_qualified(1, 9),))
    activated = _activate_latest(store)
    executor = _QueryExecutor([activated.event.as_row()])
    selector = ClickHouseActivationControlSelector(executor, database=PROD_DATABASE)

    assert selector.select_target(
        scope={"organization_id": ORG, "workspace_id": WORKSPACE},
        timeout_ms=500,
    ) == _target(1)
    assert (
        activation_control_selector_for_deployment(
            executor,
            database="th7247_catalog_dev_unwired",
            deployment="dev",
        )
        is None
    )

    disabled = PropertyCatalogActivationControlPlane(store).disable(
        request=_request(2, _target(1), activated.event.head),
        now=AT + timedelta(minutes=1),
    )
    executor.rows = [event.as_row() for event in store.events]
    assert disabled.selected_target is None
    with pytest.raises(ActivationControlUnavailable) as unavailable:
        selector.select_target(
            scope={"organization_id": ORG, "workspace_id": WORKSPACE},
            timeout_ms=500,
        )
    assert unavailable.value.reason == "control_disabled"


class _CatalogClient:
    catalog_database = PROD_DATABASE

    def __init__(self, qualified: tuple[QualifiedActivation, ...]) -> None:
        self.qualified = qualified
        self.events: list[dict[str, Any]] = []
        self.inserts: list[tuple[str, str]] = []

    def query(self, sql, _params, *, timeout_ms):
        assert timeout_ms > 0
        if ACTIVATION_CONTROL_TABLE in sql:
            return list(self.events)
        return [
            {
                "organization_id": item.target.organization_id,
                "workspace_id": item.target.workspace_id,
                "catalog_epoch": item.target.catalog_epoch,
                "projection_version": item.target.projection_version,
                "catalog_revision": item.target.catalog_revision,
                "build_token": item.target.build_token,
                "activation_sequence": item.lifecycle_activation_sequence,
                "activation_sha256": item.target.activation_sha256,
                "latest_variants": 1,
            }
            for item in self.qualified
        ]

    def insert(
        self,
        table,
        rows,
        *,
        columns,
        timeout_ms,
        deduplication_token,
    ):
        assert tuple(columns)
        assert timeout_ms > 0
        self.inserts.append((table, deduplication_token))
        self.events.extend(dict(row) for row in rows)


def test_concrete_clickhouse_store_writes_only_the_new_ledger() -> None:
    client = _CatalogClient((_qualified(1, 900),))
    store = ClickHouseActivationControlStore(client, database=PROD_DATABASE)

    result = PropertyCatalogActivationControlPlane(store).activate(
        request=_request(1, _target(1)),
        now=AT,
    )

    assert result.event.control_sequence == 1
    assert client.inserts == [
        (
            f"`{PROD_DATABASE}`.`{ACTIVATION_CONTROL_TABLE}`",
            "property-catalog-activation-control-v1:"
            f"{result.event.request_id}:{result.event.control_sha256}",
        )
    ]

    assert store.append_control_event(result.event, expected_head=None) == result.event
    assert len(client.inserts) == 1


class _TargetSelector:
    def __init__(self, target: ActivationControlTarget) -> None:
        self.target = target
        self.calls = 0

    def select_target(self, **_kwargs):
        self.calls += 1
        return self.target


@pytest.mark.parametrize("family", ("definitions", "values"))
def test_production_list_and_value_readers_pin_the_control_target(
    family: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(4)
    activation = PropertyCatalogActivation(
        catalog_epoch=target.catalog_epoch,
        catalog_revision=target.catalog_revision,
        build_token=target.build_token,
        projection_version=target.projection_version,
        lifecycle_mode="full_repair",
        lineage_anchor_revision=target.catalog_revision,
        activation_sequence=888,
        source_manifest_sha256=_sha("manifest"),
        activation_sha256=target.activation_sha256,
    )
    selector = _TargetSelector(target)
    executor = _QueryExecutor([])
    scope = {"organization_id": ORG, "workspace_id": WORKSPACE}

    if family == "definitions":
        monkeypatch.setattr(
            reader_module,
            "verify_property_catalog_activation",
            lambda *_args, **_kwargs: activation,
        )
        reader = PropertyCatalogReader(
            executor,
            catalog_database=PROD_DATABASE,
            activation_selector=selector,
        )
        result = reader._activation(
            scope=scope,
            cursor=None,
            budget=reader_module._ReadBudget.start(),
        )
    else:
        monkeypatch.setattr(
            value_reader_module,
            "verify_property_catalog_activation",
            lambda *_args, **_kwargs: activation,
        )
        reader = PropertyCatalogValueReader(
            executor,
            catalog_database=PROD_DATABASE,
            activation_selector=selector,
        )
        result = reader._activation(
            scope=scope,
            cursor=None,
            budget=value_reader_module._ReadBudget.start(reader._clock),
        )

    assert result == activation
    assert selector.calls == 1
    params = executor.calls[-1][1]
    assert params["catalog_exact_activation"] == 1
    assert params["catalog_epoch"] == target.catalog_epoch
    assert params["catalog_revision"] == target.catalog_revision


def test_reader_rejects_a_lifecycle_row_not_exactly_bound_to_control() -> None:
    target = _target(4)
    mismatched = PropertyCatalogActivation(
        catalog_epoch=target.catalog_epoch,
        catalog_revision=target.catalog_revision,
        build_token=_uuid(999),
        projection_version=target.projection_version,
        lifecycle_mode="full_repair",
        lineage_anchor_revision=target.catalog_revision,
        activation_sequence=888,
        source_manifest_sha256=_sha("manifest"),
        activation_sha256=target.activation_sha256,
    )
    executor = _QueryExecutor([])
    selector = _TargetSelector(target)

    original = reader_module.verify_property_catalog_activation
    reader_module.verify_property_catalog_activation = lambda *_a, **_k: mismatched
    try:
        reader = PropertyCatalogReader(
            executor,
            catalog_database=PROD_DATABASE,
            activation_selector=selector,
        )
        with pytest.raises(PropertyCatalogUnavailable, match="temporarily unavailable"):
            reader._activation(
                scope={"organization_id": ORG, "workspace_id": WORKSPACE},
                cursor=None,
                budget=reader_module._ReadBudget.start(),
            )
    finally:
        reader_module.verify_property_catalog_activation = original

    with pytest.raises(ValueError, match="requires control selection"):
        PropertyCatalogValueReader(executor, catalog_database=PROD_DATABASE)
    assert issubclass(PropertyCatalogValueUnavailable, PropertyCatalogUnavailable)
