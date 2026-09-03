from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tracer.management.commands import ch25_property_catalog_activate_latest as subject
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_TABLE,
    ActivationControlEvent,
    ActivationControlRejected,
    ActivationControlScope,
    ActivationControlTarget,
    QualifiedActivation,
    activation_control_event_sql,
)
from tracer.services.clickhouse.v2.property_catalog.production_rollout import (
    PRODUCTION_LIFECYCLE_ACK,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
AT = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "ENV_TYPE": "production",
        "CLOUD_DEPLOYMENT": "US",
        "PROPERTY_CATALOG_LIFECYCLE_ENABLED": True,
        "PROPERTY_CATALOG_LIFECYCLE_ACK": PRODUCTION_LIFECYCLE_ACK,
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_ACK": subject.ACTIVATION_CONTROL_ACK,
        "PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE": "property_catalog",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST": "catalog.internal",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT": 9000,
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER": "catalog_control_writer",
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD": "not-logged",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_USER": "catalog_lifecycle_writer",
        "PROPERTY_CATALOG_CH_USER": "catalog_api_reader",
        "CH25_USER": "catalog_source_reader",
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES": (
            "catalog-0",
            "catalog-1",
        ),
        "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 2,
        "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION": 1,
        "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "allowlist",
        "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": (WORKSPACE,),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _target(*, epoch: int = 2) -> ActivationControlTarget:
    return ActivationControlTarget(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=epoch,
        projection_version=1,
        catalog_revision=1,
        build_token=str(uuid.UUID(int=1001)),
        activation_sha256=hashlib.sha256(b"activation").hexdigest(),
    )


class _Store:
    def __init__(self, target: ActivationControlTarget) -> None:
        self.qualified = (QualifiedActivation(target, 1),)
        self.events: list[ActivationControlEvent] = []

    def list_qualified_activations(self, _scope: ActivationControlScope):
        return self.qualified

    def list_control_events(self, _scope: ActivationControlScope):
        return tuple(self.events)

    def append_control_event(self, event, *, expected_head):
        head = self.events[-1].head if self.events else None
        if head != expected_head:
            raise ActivationControlRejected("control_concurrent")
        self.events.append(event)
        return event


def test_config_requires_dedicated_control_writer() -> None:
    config = subject.activation_command_config(settings_object=_settings())
    assert config.catalog_epoch == 2
    assert config.user == "catalog_control_writer"
    assert config.expected_hostnames == ("catalog-0", "catalog-1")
    assert config.workspace_scope_mode == "allowlist"

    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="dedicated ClickHouse identity",
    ):
        subject.activation_command_config(
            settings_object=_settings(
                PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER=("catalog_lifecycle_writer")
            )
        )


def test_config_accepts_global_workspace_scope_without_an_allowlist() -> None:
    config = subject.activation_command_config(
        settings_object=_settings(
            PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE="all",
            PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST=(),
        )
    )

    assert config.workspace_scope_mode == "all"
    assert config.workspace_ids == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        (
            {"PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "invalid"},
            "must equal allowlist or all",
        ),
        (
            {
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "all",
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": (WORKSPACE,),
            },
            "requires an empty workspace allowlist",
        ),
        (
            {"PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": ()},
            "non-empty unique",
        ),
    ),
)
def test_config_rejects_inconsistent_workspace_scope(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(subject.ProductionActivationCommandError, match=message):
        subject.activation_command_config(settings_object=_settings(**overrides))


def test_status_is_read_only_and_execute_is_exactly_replayable() -> None:
    store = _Store(_target())
    scope = ActivationControlScope(ORG, WORKSPACE)

    status = subject.run_initial_activation(
        store=store,
        scope=scope,
        catalog_epoch=2,
        projection_version=1,
        execute=False,
        request_id=None,
        now=AT,
    )
    assert status["mode"] == "status"
    assert status["control_event_count"] == 0
    assert store.events == []

    first = subject.run_initial_activation(
        store=store,
        scope=scope,
        catalog_epoch=2,
        projection_version=1,
        execute=True,
        request_id=REQUEST,
        now=AT,
    )
    replay = subject.run_initial_activation(
        store=store,
        scope=scope,
        catalog_epoch=2,
        projection_version=1,
        execute=True,
        request_id=REQUEST,
        now=AT,
    )

    assert first["control_sequence"] == 1
    assert first["idempotent"] is False
    assert replay["idempotent"] is True
    assert len(store.events) == 1


def test_activation_rejects_a_qualified_target_from_another_epoch() -> None:
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="configured epoch/projection",
    ):
        subject.run_initial_activation(
            store=_Store(_target(epoch=1)),
            scope=ActivationControlScope(ORG, WORKSPACE),
            catalog_epoch=2,
            projection_version=1,
            execute=True,
            request_id=REQUEST,
            now=AT,
        )


def test_activation_rejects_a_workspace_without_qualified_state() -> None:
    store = _Store(_target())
    store.qualified = ()
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="no qualified catalog activation",
    ):
        subject.run_initial_activation(
            store=store,
            scope=ActivationControlScope(ORG, WORKSPACE),
            catalog_epoch=2,
            projection_version=1,
            execute=False,
            request_id=None,
            now=AT,
        )


class _Driver:
    database = "property_catalog"
    user = "catalog_control_writer"
    server_enforced_readonly = False

    def __init__(
        self,
        *,
        hostname: str = "catalog-0",
        grants: tuple[tuple[str], ...] | None = None,
    ) -> None:
        self.hostname = hostname
        self.grants = grants or (
            (
                "GRANT SELECT ON property_catalog.property_catalog_activations "
                "TO catalog_control_writer",
            ),
            (
                "GRANT SELECT, INSERT ON "
                "property_catalog.property_catalog_activation_control_events "
                "TO catalog_control_writer",
            ),
        )
        self.attestations: list[str] = []
        self.reads: list[str] = []
        self.writes: list[str] = []

    def execute_read(self, sql, _params, **_kwargs):
        self.reads.append(sql)
        return [], [("organization_id", "UUID")], 1.0

    def execute(self, sql, _params=None, **_kwargs):
        if sql == subject._CLICKHOUSE_PROVENANCE_SQL:  # noqa: SLF001
            self.attestations.append("identity")
            return [
                (
                    self.hostname,
                    self.database,
                    self.user,
                    0,
                    0,
                )
            ]
        if sql == subject._CLICKHOUSE_GRANTS_SQL:  # noqa: SLF001
            self.attestations.append("grants")
            return self.grants
        self.writes.append(sql)

    def close(self) -> None:
        return None


def test_native_adapter_is_closed_over_two_reads_and_one_insert() -> None:
    driver = _Driver()
    client = subject._ActivationControlClient(  # noqa: SLF001
        driver,  # type: ignore[arg-type]
        database="property_catalog",
        user="catalog_control_writer",
        expected_hostnames=("catalog-0", "catalog-1"),
    )
    client.query(
        activation_control_event_sql("property_catalog"),
        {
            "catalog_organization_id": ORG,
            "catalog_workspace_id": WORKSPACE,
            "catalog_control_result_limit": 2,
        },
        timeout_ms=500,
    )
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="non-reviewed read",
    ):
        client.query("SELECT 1", {}, timeout_ms=500)

    row = dict.fromkeys(ACTIVATION_CONTROL_COLUMNS, "value")
    client.insert(
        f"`property_catalog`.`{ACTIVATION_CONTROL_TABLE}`",
        (row,),
        columns=ACTIVATION_CONTROL_COLUMNS,
        timeout_ms=500,
        deduplication_token="request:digest",
    )
    assert len(driver.reads) == 1
    assert len(driver.writes) == 1
    assert driver.attestations == ["identity", "grants"] * 2


def test_native_adapter_rejects_wrong_server_or_extra_grants() -> None:
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="server identity",
    ):
        subject._ActivationControlClient(  # noqa: SLF001
            _Driver(hostname="catalog-unknown"),  # type: ignore[arg-type]
            database="property_catalog",
            user="catalog_control_writer",
            expected_hostnames=("catalog-0", "catalog-1"),
        )

    grants = _Driver().grants + (
        (
            "GRANT SELECT ON property_catalog.property_catalog_checkpoints "
            "TO catalog_control_writer",
        ),
    )
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="two-table contract exactly",
    ):
        subject._ActivationControlClient(  # noqa: SLF001
            _Driver(grants=grants),  # type: ignore[arg-type]
            database="property_catalog",
            user="catalog_control_writer",
            expected_hostnames=("catalog-0", "catalog-1"),
        )
