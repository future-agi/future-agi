from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from tracer.services.clickhouse.v2 import catalog_dev_schema
from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    CatalogLifecycleMode,
    ManifestStreamRole,
    RevisionBuildPlan,
    RevisionLease,
)
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    MAX_REVISION_LEASE_SECONDS,
    REVISION_LEASE_SECONDS,
)
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    DEV_CLOUD_DEPLOYMENT,
    DEV_INITIAL_BACKFILL_MAX_WALL_MS,
    DEV_ROLLOUT_ACK,
    DEV_SCHEDULED_RECONCILE_MAX_WALL_MS,
    DEV_STANDARD_MAX_WALL_MS,
    DevRolloutError,
    DevRolloutMode,
    DevRolloutRequest,
    DevRolloutStage,
    UnifiedDevRollout,
    run_configured_dev_rollout,
    run_workspace_reconcile,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    _RUNTIME_FACTORY_AUTHORITY,
    DEV_SIDECAR_ACK,
    CheckedInPropertyCatalogDevRuntime,
    ClickHouseDevIdentity,
    DevProvenanceEvidence,
    DevProvenanceExpectation,
    DevProvenanceObservation,
    DevRuntimeConfig,
    NativeConnectionConfig,
    NativeSchemaClient,
    PostgresDevIdentity,
    PostgresProjectTenantBinding,
    ProjectTenantAuthorization,
    PropertyCatalogDevRuntimeError,
    PropertyCatalogDevRuntimeFactory,
    _authorize_project_tenant_bindings,
    _clickhouse_dev_identity,
    _planned_streams,
    _postgres_dev_identity,
    _postgres_project_tenant_bindings,
    _validate_dev_provenance,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    FrozenLifecycleCutoffs,
    LifecycleRunMode,
    PreparedLifecycleRevision,
    PriorActiveEvidence,
    ReservationStatus,
    SourceWindow,
    StreamStart,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    CatalogWriteLease,
    PropertyCatalogPublishError,
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    SpanAttributeDefinitionSourceAdapter,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS,
    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    SPAN_AUDIT_CUTOFF_LABEL,
    FrozenSpanSource,
    RevisionPinnedSpanAttributeGroupPageLoader,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
OTHER_ORG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_WORKSPACE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OTHER_PROJECT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ATTESTED_AT = datetime(2026, 8, 14, 11, tzinfo=UTC)


def _provenance_expectation() -> DevProvenanceExpectation:
    return DevProvenanceExpectation(
        writer_clickhouse_hostname="catalog-dev-01",
        source_clickhouse_hostname="source-dev-01",
        postgres_database="futureagi_dev",
        postgres_user="property_catalog_reader",
        postgres_server_address="10.20.30.40",
        postgres_server_port=5432,
    )


def _provenance_observation() -> DevProvenanceObservation:
    return DevProvenanceObservation(
        writer_clickhouse=ClickHouseDevIdentity(
            hostname="catalog-dev-01",
            database="default",
            user="catalog_writer",
            readonly_value=0,
            readonly_locked=False,
        ),
        source_clickhouse=ClickHouseDevIdentity(
            hostname="source-dev-01",
            database="source_ch25",
            user="source_reader",
            readonly_value=1,
            readonly_locked=True,
        ),
        postgres=PostgresDevIdentity(
            database="futureagi_dev",
            user="property_catalog_reader",
            session_user="property_catalog_reader",
            server_address="10.20.30.40",
            server_port=5432,
            can_login=True,
            is_superuser=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            can_bypass_rls=False,
            default_transaction_read_only=True,
            transaction_read_only=True,
            writable_relation_count=0,
        ),
    )


def _project_bindings(
    project_ids: tuple[str, ...],
    _expected_postgres_identity: PostgresDevIdentity | None = None,
    *,
    organization_id: str = ORG,
    workspace_id: str | None = WORKSPACE,
    workspace_organization_id: str | None = None,
) -> tuple[PostgresProjectTenantBinding, ...]:
    if workspace_organization_id is None and workspace_id is not None:
        workspace_organization_id = organization_id
    return tuple(
        PostgresProjectTenantBinding(
            project_id=project_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            workspace_organization_id=workspace_organization_id,
        )
        for project_id in project_ids
    )


def _project_authorization(
    *,
    config: DevRuntimeConfig,
    request: DevRolloutRequest,
) -> ProjectTenantAuthorization:
    return _authorize_project_tenant_bindings(
        request=request,
        config=config,
        observation=_provenance_observation(),
        bindings=_project_bindings(config.project_ids),
        authorized_at=ATTESTED_AT,
    )


def _provenance_evidence(
    *,
    config: DevRuntimeConfig,
    request: DevRolloutRequest,
) -> DevProvenanceEvidence:
    return DevProvenanceEvidence(
        _provenance_observation(),
        ATTESTED_AT,
        project_tenant_authorization=_project_authorization(
            config=config,
            request=request,
        ),
    )


def _runtime_settings(
    runtime_directory: str,
    *,
    wall_ms: int = DEV_STANDARD_MAX_WALL_MS,
) -> SimpleNamespace:
    return SimpleNamespace(
        ENV_TYPE="development",
        CLOUD_DEPLOYMENT="DEV",
        CLICKHOUSE={},
        CLICKHOUSE_V2={
            "CH25_HOST": "source.dev.invalid",
            "CH25_TCP_PORT": 9000,
            "CH25_USER": "source_reader",
            "CH25_PASSWORD": "test-source",
            "CH25_DATABASE": "source_ch25",
            "CH25_SERVER_ENFORCED_READONLY": True,
        },
        PROPERTY_CATALOG_DEV_WRITE_CH_HOST="catalog.dev.invalid",
        PROPERTY_CATALOG_DEV_WRITE_CH_PORT=9000,
        PROPERTY_CATALOG_DEV_WRITE_CH_USER="catalog_writer",
        PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD="test-writer",
        PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE="property_catalog_dev_unit",
        PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME="catalog-dev-01",
        PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME="source-dev-01",
        PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE="futureagi_dev",
        PROPERTY_CATALOG_DEV_EXPECTED_PG_USER="property_catalog_reader",
        PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS="10.20.30.40",
        PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT=5432,
        PROPERTY_CATALOG_DEV_CATALOG_EPOCH=1,
        PROPERTY_CATALOG_DEV_PROJECTION_VERSION=1,
        PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST=(PROJECT,),
        PROPERTY_CATALOG_DEV_SPAN_SINCE="2026-08-14T12:00:00Z",
        PROPERTY_CATALOG_DEV_SPAN_UNTIL="2026-08-14T13:00:00Z",
        PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID=(
            "44444444-4444-4444-8444-444444444444"
        ),
        PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE=(f"{runtime_directory}/fence.json"),
        PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE=(
            f"{runtime_directory}/producer-drain-proof-v2.json"
        ),
        PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE=(
            f"{runtime_directory}/producer-state-retirements-v1.json"
        ),
        PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY=runtime_directory,
        PROPERTY_CATALOG_DEV_SIDECAR_ACK=DEV_SIDECAR_ACK,
        PROPERTY_CATALOG_DEV_MAX_WALL_MS=wall_ms,
    )


def _request(**overrides: object) -> DevRolloutRequest:
    values: dict[str, object] = {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "environment": "development",
        "cloud_deployment": "DEV",
        "dev_identity": "dev:unit-local",
        "source_database": "source_ch25",
        "target_database": "property_catalog_dev_unit",
        "acknowledgement": DEV_ROLLOUT_ACK,
    }
    values.update(overrides)
    return DevRolloutRequest(**values)  # type: ignore[arg-type]


def _unit_runtime_config(
    runtime_directory: str | None = None,
    *,
    now: datetime = datetime(2026, 8, 14, 12, tzinfo=UTC),
) -> DevRuntimeConfig:
    runtime_directory = runtime_directory or tempfile.gettempdir()
    return DevRuntimeConfig(
        catalog=NativeConnectionConfig(
            host="catalog.dev.invalid",
            port=9000,
            user="catalog_writer",
            password="test",
            database="property_catalog_dev_unit",
            server_enforced_readonly=False,
        ),
        source=NativeConnectionConfig(
            host="source.dev.invalid",
            port=9000,
            user="source_reader",
            password="test",
            database="source_ch25",
            server_enforced_readonly=True,
        ),
        catalog_epoch=1,
        projection_version=1,
        project_ids=(PROJECT,),
        hot_producer_stream_id="44444444-4444-4444-8444-444444444444",
        mutation_lock_directory=runtime_directory,
        revision_fence_file=f"{runtime_directory}/fence.json",
        drain_proof_file=f"{runtime_directory}/producer-drain-proof-v2.json",
        producer_retirement_file=(
            f"{runtime_directory}/producer-state-retirements-v1.json"
        ),
        span_since=now,
        span_until=now + timedelta(hours=1),
        sidecar_acknowledgement=DEV_SIDECAR_ACK,
        provenance_expectation=_provenance_expectation(),
        rollout_wall_ms=DEV_STANDARD_MAX_WALL_MS,
    )


@dataclass
class _PostgresResult:
    adapter_results: tuple[object, ...] = (object(),)
    postgres_snapshot_opened: bool = True


class _Runtime:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def status(self, _request: DevRolloutRequest) -> dict[str, object]:
        self.calls.append("status")
        return {"ready": False}

    def verify_schema(self, _request: DevRolloutRequest) -> dict[str, object]:
        self.calls.append("verify_schema")
        return {"exact_tables": 6}

    def reconcile_workspace(
        self,
        _request: DevRolloutRequest,
        *,
        mode: ReconcileMode,
    ) -> dict[str, object]:
        self.calls.append(f"workspace:{mode}")
        return {"mode": mode, "qualified": True}

    def apply_schema(self, _request: DevRolloutRequest) -> dict[str, object]:
        self.calls.append("schema")
        return {"tables": 6}

    def backfill(self, _request: DevRolloutRequest) -> dict[str, object]:
        self.calls.append("backfill")
        return {"authoritative": True}

    def postgres_reconciler(self, _request: DevRolloutRequest) -> object:
        self.calls.append("postgres_reconciler")
        return object()

    def postgres_request_factory(self, _request: DevRolloutRequest) -> object:
        self.calls.append("postgres_request_factory")
        return object()

    def postgres_snapshot_guard(self, _request: DevRolloutRequest) -> object:
        self.calls.append("postgres_snapshot_guard")
        return lambda: self.calls.append("postgres_snapshot_guard_run")

    def postgres_adapters(self, _request: DevRolloutRequest) -> tuple[object, ...]:
        self.calls.append("postgres_adapters")
        return ()

    def reconcile_non_postgres(
        self, _request: DevRolloutRequest, _postgres: object
    ) -> dict[str, object]:
        self.calls.append("reconcile_non_postgres")
        return {"span": "complete"}

    def qualify(self, _request: DevRolloutRequest) -> dict[str, object]:
        self.calls.append("qualify")
        return {"qualified": True}

    def activate(self, _request: DevRolloutRequest) -> dict[str, object]:
        self.calls.append("activate")
        return {"active": True}


def test_dry_run_is_zero_io_and_exposes_exact_six_table_plan() -> None:
    result = run_configured_dev_rollout(request=_request(), runtime=None)

    assert result.completed == ()
    assert result.plan.mode is DevRolloutMode.DRY_RUN
    assert result.plan.zero_io is True
    assert result.plan.stages == tuple(DevRolloutStage)
    assert set(result.plan.write_allowlist) == {
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_checkpoints",
        "property_catalog_activations",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    }


@override_settings(
    PROPERTY_CATALOG_DEV_ORGANIZATION_ID=ORG,
    PROPERTY_CATALOG_DEV_WORKSPACE_ID=WORKSPACE,
    PROPERTY_CATALOG_DEV_ENVIRONMENT="development",
    PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT="DEV",
    PROPERTY_CATALOG_DEV_IDENTITY="dev:unit-local",
    PROPERTY_CATALOG_DEV_SOURCE_DATABASE="source_ch25",
    PROPERTY_CATALOG_DEV_TARGET_DATABASE="property_catalog_dev_unit",
    PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT=DEV_ROLLOUT_ACK,
    PROPERTY_CATALOG_DEV_RUNTIME_FACTORY="must.not.be.imported.on.dry.run",
)
def test_management_command_defaults_to_zero_io_dry_run() -> None:
    stdout = StringIO()

    call_command("ch25_property_catalog_dev_rollout", stdout=stdout)

    output = stdout.getvalue()
    assert '"mode":"dry_run"' in output
    assert '"zero_io":true' in output
    assert "property_catalog_source_streams" in output


def test_management_command_refuses_wrong_uid_before_mutating_runtime_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.management.commands import ch25_property_catalog_dev_rollout as command

    monkeypatch.setattr(command.os, "geteuid", lambda: 0)
    with (
        override_settings(PROPERTY_CATALOG_RUNTIME_UID=65_532),
        pytest.raises(CommandError, match="refusing uid 0 before runtime I/O"),
    ):
        call_command("ch25_property_catalog_dev_rollout", "--execute")


def test_management_command_allows_read_only_modes_for_operator_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.management.commands import ch25_property_catalog_dev_rollout as command

    monkeypatch.setattr(command.os, "geteuid", lambda: 0)
    command._require_mutating_runtime_identity({"execute": False, "status": True})


def test_management_command_runtime_uid_is_overridable_for_reviewed_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.management.commands import ch25_property_catalog_dev_rollout as command

    monkeypatch.setattr(command.os, "geteuid", lambda: 12345)
    with override_settings(PROPERTY_CATALOG_RUNTIME_UID=12345):
        command._require_mutating_runtime_identity({"execute": True})


@override_settings(
    PROPERTY_CATALOG_DEV_ORGANIZATION_ID=ORG,
    PROPERTY_CATALOG_DEV_WORKSPACE_ID=WORKSPACE,
    PROPERTY_CATALOG_DEV_ENVIRONMENT="development",
    PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT="DEV",
    PROPERTY_CATALOG_DEV_IDENTITY="dev:unit-local",
    PROPERTY_CATALOG_DEV_SOURCE_DATABASE="source_ch25",
    PROPERTY_CATALOG_DEV_TARGET_DATABASE="property_catalog_dev_unit",
    PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT=DEV_ROLLOUT_ACK,
)
def test_management_command_maps_explicit_initial_backfill_wall() -> None:
    from tracer.management.commands.ch25_property_catalog_dev_rollout import (
        _request as command_request,
    )

    request = command_request(
        {
            "execute": True,
            "status": False,
            "initial_backfill_wall_ms": DEV_INITIAL_BACKFILL_MAX_WALL_MS,
            "repair_expired_incomplete": True,
        }
    )

    assert request.mode is DevRolloutMode.EXECUTE
    assert request.initial_backfill_wall_ms == DEV_INITIAL_BACKFILL_MAX_WALL_MS
    assert request.repair_expired_incomplete is True


@override_settings(
    PROPERTY_CATALOG_DEV_ORGANIZATION_ID=ORG,
    PROPERTY_CATALOG_DEV_WORKSPACE_ID=WORKSPACE,
    PROPERTY_CATALOG_DEV_ENVIRONMENT="development",
    PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT="DEV",
    PROPERTY_CATALOG_DEV_IDENTITY="dev:unit-local",
    PROPERTY_CATALOG_DEV_SOURCE_DATABASE="source_ch25",
    PROPERTY_CATALOG_DEV_TARGET_DATABASE="property_catalog_dev_unit",
    PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT=DEV_ROLLOUT_ACK,
    PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS=1_200_000,
)
def test_management_command_maps_explicit_scheduled_full_repair() -> None:
    from tracer.management.commands.ch25_property_catalog_dev_rollout import (
        _request as command_request,
    )

    request = command_request(
        {
            "execute": False,
            "status": False,
            "scheduled_reconcile": ReconcileMode.FULL_REPAIR.value,
            "scheduled_reconcile_wall_ms": None,
            "repair_expired_incomplete": True,
        }
    )

    assert request.mode is DevRolloutMode.EXECUTE
    assert request.initial_backfill_wall_ms is None
    assert request.scheduled_reconcile_wall_ms == 1_200_000
    assert request.repair_expired_incomplete is True


def test_management_command_status_uses_checked_in_factory_with_fake_clients(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    monkeypatch.setattr(
        dev_runtime,
        "RUNTIME_LIMITS",
        replace(
            dev_runtime.RUNTIME_LIMITS,
            canonical_span_query_timeout_ms=30_000,
            state_store_timeout_ms=8_500,
        ),
    )

    statements = catalog_dev_schema._load_pinned_statements()
    tables = tuple(
        (
            "property_catalog_dev_unit",
            statement.table,
            statement.engine,
            statement.sql,
        )
        for statement in statements
    )

    read_timeouts: list[tuple[str, int]] = []

    class Driver:
        def __init__(self, database: str, *, server_enforced_readonly: bool) -> None:
            self.database = database
            self.server_enforced_readonly = server_enforced_readonly

        def execute_read(
            self,
            query: str,
            params: Any = None,
            timeout_ms: int | None = None,
            settings: Any = None,
        ) -> tuple[list[tuple[Any, ...]], list[tuple[str, str]], float]:
            _ = params, settings
            assert timeout_ms is not None and timeout_ms > 0
            read_timeouts.append((self.database, timeout_ms))
            if "SELECT version()" in query:
                return [("25.3.8.23",)], [("version()", "String")], 0.1
            if "FROM system.tables" in query:
                return (
                    list(tables),
                    [
                        ("database", "String"),
                        ("name", "String"),
                        ("engine", "String"),
                        ("create_table_query", "String"),
                    ],
                    0.1,
                )
            if "property_catalog_activations" in query:
                return (
                    [(0, 0, 0)],
                    [
                        ("activation_rows", "UInt64"),
                        ("active_rows", "UInt64"),
                        ("latest_active_revision", "UInt64"),
                    ],
                    0.1,
                )
            raise AssertionError(query)

        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("status must not write")

    created: list[str] = []

    def fake_native(config: Any) -> Driver:
        created.append(config.database)
        return Driver(
            config.database,
            server_enforced_readonly=config.server_enforced_readonly,
        )

    monkeypatch.setattr(dev_runtime, "_default_native_client", fake_native)
    shared = str(tmp_path)
    settings_override = {
        "ENV_TYPE": "development",
        "CLOUD_DEPLOYMENT": "DEV",
        "CLICKHOUSE": {},
        "CLICKHOUSE_V2": {
            "CH25_HOST": "source.dev.invalid",
            "CH25_TCP_PORT": 9000,
            "CH25_USER": "source_reader",
            "CH25_PASSWORD": "test-source",
            "CH25_DATABASE": "source_ch25",
            "CH25_SERVER_ENFORCED_READONLY": True,
        },
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID": ORG,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ID": WORKSPACE,
        "PROPERTY_CATALOG_DEV_ENVIRONMENT": "development",
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "DEV",
        "PROPERTY_CATALOG_DEV_IDENTITY": "dev:unit-local",
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": "source_ch25",
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": "property_catalog_dev_unit",
        "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT": DEV_ROLLOUT_ACK,
        "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY": (
            "tracer.services.clickhouse.v2.property_catalog.dev_runtime."
            "configured_property_catalog_dev_runtime"
        ),
        "PROPERTY_CATALOG_DEV_WRITE_CH_HOST": "catalog.dev.invalid",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PORT": 9000,
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER": "catalog_writer",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": "test-writer",
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": "property_catalog_dev_unit",
        "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME": "catalog-dev-01",
        "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME": "source-dev-01",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE": "futureagi_dev",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER": "property_catalog_reader",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS": "10.20.30.40",
        "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT": 5432,
        "PROPERTY_CATALOG_DEV_CATALOG_EPOCH": 1,
        "PROPERTY_CATALOG_DEV_PROJECTION_VERSION": 1,
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": (
            "33333333-3333-4333-8333-333333333333",
        ),
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": "2026-08-14T12:00:00Z",
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": "2026-08-14T13:00:00Z",
        "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID": (
            "44444444-4444-4444-8444-444444444444"
        ),
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": f"{shared}/fence.json",
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": (
            f"{shared}/producer-drain-proof-v2.json"
        ),
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": (
            f"{shared}/producer-state-retirements-v1.json"
        ),
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": shared,
        "PROPERTY_CATALOG_DEV_SIDECAR_ACK": ("PROPERTY_CATALOG_PYTHON_GO_SIDECAR_V1"),
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS": 100_000,
    }
    probe_calls: list[tuple[str, str]] = []

    def fake_provenance_probe(
        config: DevRuntimeConfig,
        writer: Driver,
        source: Driver,
    ) -> DevProvenanceObservation:
        assert config.provenance_expectation == _provenance_expectation()
        probe_calls.append((writer.database, source.database))
        return _provenance_observation()

    monkeypatch.setattr(
        dev_runtime,
        "_default_dev_provenance_probe",
        fake_provenance_probe,
    )
    monkeypatch.setattr(
        dev_runtime,
        "_postgres_project_tenant_bindings",
        _project_bindings,
    )
    stdout = StringIO()
    with override_settings(**settings_override):
        call_command("ch25_property_catalog_dev_rollout", "--status", stdout=stdout)

    assert '"schema_ready":true' in stdout.getvalue()
    assert '"active":false' in stdout.getvalue()
    assert '"remote_dev_provenance"' in stdout.getvalue()
    assert probe_calls == [("default", "source_ch25")]
    assert set(created) == {
        "default",
        "source_ch25",
        "property_catalog_dev_unit",
    }
    catalog_timeouts = tuple(
        timeout_ms
        for database, timeout_ms in read_timeouts
        if database != "source_ch25"
    )
    assert catalog_timeouts
    assert set(catalog_timeouts) == {8_500}


def test_native_catalog_client_uses_configured_state_store_timeout_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    monkeypatch.setattr(
        dev_runtime,
        "RUNTIME_LIMITS",
        replace(dev_runtime.RUNTIME_LIMITS, state_store_timeout_ms=7_500),
    )

    class Driver:
        database = "property_catalog_dev_unit"
        server_enforced_readonly = False

        def execute_read(
            self,
            _query: str,
            _params: Any,
            *,
            timeout_ms: int,
            settings: Any,
        ) -> tuple[list[tuple[int]], list[tuple[str, str]], float]:
            assert settings == {"readonly": 2}
            assert timeout_ms == 7_500
            return [(1,)], [("rows", "UInt64")], 0.1

    client = dev_runtime.NativeCatalogClient(
        Driver(),  # type: ignore[arg-type]
        database="property_catalog_dev_unit",
    )
    sql = "SELECT count() AS rows FROM `property_catalog_dev_unit`.`property_catalog_activations`"

    assert client.query(sql, {}, timeout_ms=7_500) == ({"rows": 1},)
    with pytest.raises(
        PropertyCatalogDevRuntimeError,
        match=r"catalog query timeout must be in \[1, 7500\] ms",
    ):
        client.query(sql, {}, timeout_ms=7_501)


def test_remote_provenance_validation_freezes_exact_dev_evidence() -> None:
    config = SimpleNamespace(
        deployment="dev",
        provenance_expectation=_provenance_expectation(),
        catalog_control_database="default",
        catalog=SimpleNamespace(user="catalog_writer"),
        source=SimpleNamespace(database="source_ch25", user="source_reader"),
    )

    evidence = _validate_dev_provenance(
        config=config,  # type: ignore[arg-type]
        observation=_provenance_observation(),
        attested_at=ATTESTED_AT,
    )

    payload = evidence.as_dict()
    assert payload["development_only"] is True
    assert payload["source_clickhouse"]["readonly_value"] == 1
    assert payload["source_clickhouse"]["readonly_locked"] is True
    assert payload["postgres"]["writable_relation_count"] == 0
    assert len(payload["attestation_sha256"]) == 64
    assert config.provenance_expectation.writer_clickhouse_hostnames == (
        "catalog-dev-01",
    )
    assert config.provenance_expectation.source_clickhouse_hostnames == (
        "source-dev-01",
    )


def test_remote_provenance_accepts_exact_replica_hostname_membership() -> None:
    expectation = DevProvenanceExpectation(
        writer_clickhouse_hostname="",
        source_clickhouse_hostname="",
        postgres_database="futureagi_dev",
        postgres_user="property_catalog_reader",
        postgres_server_address="10.20.30.40",
        postgres_server_port=5432,
        writer_clickhouse_hostnames=[
            "catalog-dev-03",
            "catalog-dev-01",
            "catalog-dev-02",
        ],
        source_clickhouse_hostnames=(
            "source-dev-03",
            "source-dev-01",
            "source-dev-02",
        ),
    )
    config = SimpleNamespace(
        deployment="dev",
        provenance_expectation=expectation,
        catalog_control_database="default",
        catalog=SimpleNamespace(user="catalog_writer"),
        source=SimpleNamespace(database="source_ch25", user="source_reader"),
    )
    observation = replace(
        _provenance_observation(),
        writer_clickhouse=replace(
            _provenance_observation().writer_clickhouse,
            hostname="catalog-dev-02",
        ),
        source_clickhouse=replace(
            _provenance_observation().source_clickhouse,
            hostname="source-dev-03",
        ),
    )

    evidence = _validate_dev_provenance(
        config=config,  # type: ignore[arg-type]
        observation=observation,
        attested_at=ATTESTED_AT,
    )

    assert expectation.writer_clickhouse_hostnames == (
        "catalog-dev-01",
        "catalog-dev-02",
        "catalog-dev-03",
    )
    assert expectation.source_clickhouse_hostnames == (
        "source-dev-01",
        "source-dev-02",
        "source-dev-03",
    )
    assert evidence.observation == observation


@pytest.mark.parametrize(
    ("hostnames", "message"),
    (
        ((), "must contain 1..16"),
        (("catalog-dev-01", "catalog-dev-01"), "unique exact hostnames"),
        (
            tuple(f"catalog-dev-{index:02d}" for index in range(17)),
            "must contain 1..16",
        ),
    ),
)
def test_replica_hostname_allowlist_is_bounded_nonempty_and_unique(
    hostnames: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(PropertyCatalogDevRuntimeError, match=message):
        DevProvenanceExpectation(
            writer_clickhouse_hostname="",
            source_clickhouse_hostname="source-dev-01",
            postgres_database="futureagi_dev",
            postgres_user="property_catalog_reader",
            postgres_server_address="10.20.30.40",
            postgres_server_port=5432,
            writer_clickhouse_hostnames=hostnames,
        )


def test_runtime_settings_accept_plural_replica_hostnames_and_reject_conflicts(
    tmp_path: Any,
) -> None:
    settings_object = _runtime_settings(str(tmp_path))
    settings_object.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME = ""
    settings_object.PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME = ""
    settings_object.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES = [
        "catalog-dev-03",
        "catalog-dev-01",
        "catalog-dev-02",
    ]
    settings_object.PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAMES = (
        "source-dev-03",
        "source-dev-01",
        "source-dev-02",
    )

    config = DevRuntimeConfig.from_settings(
        _request(execute=True),
        settings_object,
        now=ATTESTED_AT,
    )

    assert config.provenance_expectation.writer_clickhouse_hostnames == (
        "catalog-dev-01",
        "catalog-dev-02",
        "catalog-dev-03",
    )
    assert config.provenance_expectation.source_clickhouse_hostnames == (
        "source-dev-01",
        "source-dev-02",
        "source-dev-03",
    )

    settings_object.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME = (
        "unlisted-catalog-host"
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="conflicts"):
        DevRuntimeConfig.from_settings(
            _request(execute=True),
            settings_object,
            now=ATTESTED_AT,
        )


def test_native_schema_client_requires_explicit_deployment() -> None:
    with pytest.raises(TypeError, match="deployment"):
        NativeSchemaClient(  # type: ignore[call-arg]
            target_database="property_catalog_dev_unit",
            control_database="default",
            client_for_database=lambda _database: object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("deployment", "target_database", "message"),
    (
        ("dev", "property_catalog", "safe lowercase ClickHouse identifier"),
        (
            "prod",
            "property_catalog_dev_unit",
            "production catalog database must match the configured",
        ),
    ),
)
def test_native_schema_client_rejects_cross_deployment_database(
    deployment: str,
    target_database: str,
    message: str,
) -> None:
    with pytest.raises(PropertyCatalogPublishError, match=message):
        NativeSchemaClient(
            target_database=target_database,
            control_database="default",
            client_for_database=lambda _database: object(),  # type: ignore[arg-type]
            deployment=deployment,
        )


@pytest.mark.parametrize(
    ("deployment", "target_database"),
    (
        ("dev", "property_catalog_dev_unit"),
        ("dev", "legacy_catalog_snapshot"),
        ("prod", "property_catalog"),
    ),
)
def test_native_schema_client_accepts_only_matching_deployment_database(
    deployment: str,
    target_database: str,
) -> None:
    client = NativeSchemaClient(
        target_database=target_database,
        control_database="default",
        client_for_database=lambda _database: object(),  # type: ignore[arg-type]
        deployment=deployment,
    )

    assert client._target_database == target_database  # noqa: SLF001
    assert client._deployment == deployment  # noqa: SLF001


@pytest.mark.parametrize(
    ("observation", "reason"),
    (
        (
            replace(
                _provenance_observation(),
                writer_clickhouse=replace(
                    _provenance_observation().writer_clickhouse,
                    hostname="wrong-dev-host",
                ),
            ),
            "writer_clickhouse_hostname",
        ),
        (
            replace(
                _provenance_observation(),
                source_clickhouse=replace(
                    _provenance_observation().source_clickhouse,
                    readonly_locked=False,
                ),
            ),
            "source_clickhouse_readonly_lock",
        ),
        (
            replace(
                _provenance_observation(),
                postgres=replace(
                    _provenance_observation().postgres,
                    is_superuser=True,
                ),
            ),
            "postgres_superuser",
        ),
        (
            replace(
                _provenance_observation(),
                postgres=replace(
                    _provenance_observation().postgres,
                    session_user="privileged_login",
                ),
            ),
            "postgres_session_user",
        ),
        (
            replace(
                _provenance_observation(),
                postgres=replace(
                    _provenance_observation().postgres,
                    writable_relation_count=1,
                ),
            ),
            "postgres_dml_privileges",
        ),
    ),
)
def test_remote_provenance_rejects_identity_or_privilege_mismatch(
    observation: DevProvenanceObservation,
    reason: str,
) -> None:
    config = SimpleNamespace(
        provenance_expectation=_provenance_expectation(),
        catalog_control_database="default",
        catalog=SimpleNamespace(user="catalog_writer"),
        source=SimpleNamespace(database="source_ch25", user="source_reader"),
    )

    with pytest.raises(PropertyCatalogDevRuntimeError, match=reason):
        _validate_dev_provenance(
            config=config,  # type: ignore[arg-type]
            observation=observation,
            attested_at=ATTESTED_AT,
        )


def test_factory_rejects_provenance_before_target_client_or_runtime_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        provenance_expectation=_provenance_expectation(),
        catalog_control_database="default",
        catalog=NativeConnectionConfig(
            host="catalog.dev.invalid",
            port=9000,
            user="catalog_writer",
            password="test",
            database="property_catalog_dev_unit",
            server_enforced_readonly=False,
        ),
        source=NativeConnectionConfig(
            host="source.dev.invalid",
            port=9000,
            user="source_reader",
            password="test",
            database="source_ch25",
            server_enforced_readonly=True,
        ),
    )
    monkeypatch.setattr(
        DevRuntimeConfig,
        "from_settings",
        classmethod(lambda cls, *args, **kwargs: config),
    )
    constructed: list[str] = []
    closed: list[str] = []

    class Driver(SimpleNamespace):
        def close(self) -> None:
            closed.append(self.database)

    def native_client(connection: NativeConnectionConfig) -> Driver:
        constructed.append(connection.database)
        return Driver(
            database=connection.database,
            server_enforced_readonly=connection.server_enforced_readonly,
        )

    def mismatched_probe(*_args: Any) -> DevProvenanceObservation:
        return replace(
            _provenance_observation(),
            writer_clickhouse=replace(
                _provenance_observation().writer_clickhouse,
                hostname="not-approved",
            ),
        )

    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=object(),
        native_client_factory=native_client,  # type: ignore[arg-type]
        provenance_probe=mismatched_probe,
        now=lambda: ATTESTED_AT,
    )

    with pytest.raises(
        PropertyCatalogDevRuntimeError,
        match="writer_clickhouse_hostname",
    ):
        factory(_request(status=True))
    assert constructed == ["default", "source_ch25"]
    assert set(closed) == {"default", "source_ch25"}


def test_factory_runtime_closes_owned_native_clients_once(tmp_path: Any) -> None:
    clients: list[Any] = []

    class Driver:
        def __init__(self, config: NativeConnectionConfig) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly
            self.close_calls = 0
            clients.append(self)

        def close(self) -> None:
            self.close_calls += 1

    runtime = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=Driver,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=(
            lambda project_ids, _identity: _project_bindings(project_ids)
        ),
        now=lambda: ATTESTED_AT,
    )(_request(status=True))

    assert len(clients) == 3
    runtime.close()
    runtime.close()

    assert [client.close_calls for client in clients] == [1, 1, 1]
    assert runtime._native_drivers == ()


@pytest.mark.parametrize(
    ("bindings", "reason"),
    (
        ((), "missing exact canonical PostgreSQL ownership"),
        (
            _project_bindings((PROJECT,), organization_id=OTHER_ORG),
            "not owned by the exact rollout organization/workspace",
        ),
        (
            _project_bindings((PROJECT,), workspace_id=OTHER_WORKSPACE),
            "not owned by the exact rollout organization/workspace",
        ),
        (
            _project_bindings((PROJECT,), workspace_id=None),
            "not owned by the exact rollout organization/workspace",
        ),
        (
            _project_bindings((PROJECT, PROJECT)),
            "missing exact canonical PostgreSQL ownership",
        ),
        (
            _project_bindings((PROJECT, OTHER_PROJECT)),
            "missing exact canonical PostgreSQL ownership",
        ),
        (
            (
                PostgresProjectTenantBinding(
                    project_id=PROJECT,
                    organization_id=ORG,
                    workspace_id=WORKSPACE,
                    workspace_organization_id=OTHER_ORG,
                ),
            ),
            "not owned by the exact rollout organization/workspace",
        ),
    ),
)
def test_project_tenant_authorization_rejects_missing_or_foreign_bindings(
    bindings: tuple[PostgresProjectTenantBinding, ...],
    reason: str,
) -> None:
    config = _unit_runtime_config()
    request = _request(execute=True)
    with pytest.raises(PropertyCatalogDevRuntimeError, match=reason):
        _authorize_project_tenant_bindings(
            request=request,
            config=config,
            observation=_provenance_observation(),
            bindings=bindings,
            authorized_at=ATTESTED_AT,
        )


def test_project_tenant_authorization_is_canonical_bound_and_redacted() -> None:
    request = _request(execute=True)
    config = replace(
        _unit_runtime_config(),
        project_ids=(OTHER_PROJECT, PROJECT),
    )
    authorization = _authorize_project_tenant_bindings(
        request=request,
        config=config,
        observation=_provenance_observation(),
        bindings=tuple(reversed(_project_bindings(config.project_ids))),
        authorized_at=ATTESTED_AT,
    )

    assert authorization.project_ids == tuple(sorted((PROJECT, OTHER_PROJECT)))
    evidence = authorization.as_dict()
    assert set(evidence) == {
        "authorization_contract_sha256",
        "authorization_sha256",
        "authorized_at",
        "project_count",
    }
    assert evidence["project_count"] == 2
    assert PROJECT not in str(evidence)
    assert ORG not in str(evidence)
    assert WORKSPACE not in str(evidence)

    changed_passwords = replace(
        config,
        catalog=replace(config.catalog, password="different-writer-secret"),
        source=replace(config.source, password="different-source-secret"),
    )
    password_only = _authorize_project_tenant_bindings(
        request=request,
        config=changed_passwords,
        observation=_provenance_observation(),
        bindings=_project_bindings(changed_passwords.project_ids),
        authorized_at=ATTESTED_AT,
    )
    changed_epoch = _authorize_project_tenant_bindings(
        request=request,
        config=replace(config, catalog_epoch=2),
        observation=_provenance_observation(),
        bindings=_project_bindings(config.project_ids),
        authorized_at=ATTESTED_AT,
    )
    assert (
        password_only.authorization_contract_sha256
        == authorization.authorization_contract_sha256
    )
    assert (
        changed_epoch.authorization_contract_sha256
        != authorization.authorization_contract_sha256
    )


def test_foreign_allowlisted_project_with_spans_is_rejected_before_target_or_stage(
    tmp_path: Any,
) -> None:
    constructed: list[str] = []
    source_span_reads: list[str] = []

    class Driver:
        def __init__(self, config: NativeConnectionConfig) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly
            # Adversarial premise: the source has spans for PROJECT, but its
            # ClickHouse table carries no independently trusted tenant label.
            self.available_span_project_ids = (PROJECT,)

        def execute_read(self, query: str, *_args: Any, **_kwargs: Any) -> Any:
            source_span_reads.append(query)
            raise AssertionError("foreign project must fail before source span reads")

    def native_client(config: NativeConnectionConfig) -> Driver:
        constructed.append(config.database)
        return Driver(config)

    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=native_client,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=(
            lambda project_ids, _expected_identity: _project_bindings(
                project_ids,
                organization_id=OTHER_ORG,
                workspace_id=OTHER_WORKSPACE,
            )
        ),
        now=lambda: ATTESTED_AT,
    )

    with pytest.raises(
        PropertyCatalogDevRuntimeError,
        match="not owned by the exact rollout organization/workspace",
    ):
        factory(_request(execute=True))

    assert constructed == ["default", "source_ch25"]
    assert "property_catalog_dev_unit" not in constructed
    assert source_span_reads == []


def test_live_project_tenant_drift_rejects_before_schema_or_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    calls = 0
    stage_io: list[str] = []

    class Driver:
        def __init__(self, config: NativeConnectionConfig) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly

    def binding_probe(
        project_ids: tuple[str, ...],
        _identity: PostgresDevIdentity,
    ) -> tuple[PostgresProjectTenantBinding, ...]:
        nonlocal calls
        calls += 1
        return _project_bindings(
            project_ids,
            organization_id=ORG if calls == 1 else OTHER_ORG,
            workspace_id=WORKSPACE if calls == 1 else OTHER_WORKSPACE,
        )

    runtime = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=Driver,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=binding_probe,
        now=lambda: ATTESTED_AT,
    )(_request(execute=True))
    monkeypatch.setattr(
        dev_runtime,
        "ensure_dev_catalog_schema",
        lambda *_args, **_kwargs: stage_io.append("schema"),
    )

    with pytest.raises(
        PropertyCatalogDevRuntimeError,
        match="not owned by the exact rollout organization/workspace",
    ):
        runtime.apply_schema(runtime.bound_request)

    assert calls == 2
    assert stage_io == []
    assert runtime._execution is None


@pytest.mark.parametrize(
    "stage",
    (
        "apply_schema",
        "backfill",
        "reconcile_workspace",
        "postgres_reconciler",
        "postgres_request_factory",
        "postgres_snapshot_guard",
        "postgres_adapters",
        "reconcile_non_postgres",
        "qualify",
        "activate",
    ),
)
def test_read_only_runtime_request_cannot_invoke_any_mutation_stage(
    tmp_path: Any,
    stage: str,
) -> None:
    bound_request = _request(status=True)
    probe_calls: list[tuple[str, ...]] = []

    class Driver:
        def __init__(self, config: NativeConnectionConfig) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly

    def binding_probe(
        project_ids: tuple[str, ...],
        _identity: PostgresDevIdentity,
    ) -> tuple[PostgresProjectTenantBinding, ...]:
        probe_calls.append(project_ids)
        return _project_bindings(project_ids)

    runtime = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=Driver,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=binding_probe,
        now=lambda: ATTESTED_AT,
    )(bound_request)

    with pytest.raises(PropertyCatalogDevRuntimeError, match="execute-mode"):
        if stage == "reconcile_workspace":
            runtime.reconcile_workspace(
                bound_request,
                mode=ReconcileMode.INCREMENTAL,
            )
        elif stage == "reconcile_non_postgres":
            runtime.reconcile_non_postgres(bound_request, object())  # type: ignore[arg-type]
        else:
            getattr(runtime, stage)(bound_request)

    assert probe_calls == [(PROJECT,)]
    assert runtime._execution is None


def test_factory_rejects_dry_run_before_any_client(tmp_path: Any) -> None:
    clients: list[str] = []

    def forbidden_client(config: NativeConnectionConfig) -> object:
        clients.append(config.database)
        raise AssertionError("dry-run must not construct a client")

    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=forbidden_client,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=_project_bindings,
        now=lambda: ATTESTED_AT,
    )

    with pytest.raises(PropertyCatalogDevRuntimeError, match="must not construct"):
        factory(_request())
    assert clients == []


def test_postgres_project_binding_probe_is_one_bounded_readonly_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import django.db

    statements: list[tuple[str, object]] = []
    identity_row = (
        "futureagi_dev",
        "property_catalog_reader",
        "property_catalog_reader",
        "10.20.30.40",
        5432,
        True,
        False,
        False,
        False,
        False,
        False,
        "on",
        "on",
        0,
    )

    class Cursor:
        def __init__(self) -> None:
            self.identity_rows = iter((identity_row, None))

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, sql: str, params: object = None) -> None:
            statements.append((sql, params))

        def fetchone(self) -> object:
            return next(self.identity_rows)

        def fetchall(self) -> list[tuple[str, str, str, str]]:
            return [(PROJECT, ORG, WORKSPACE, ORG)]

    class Atomic:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: Any) -> None:
            return None

    connection = SimpleNamespace(
        vendor="postgresql",
        in_atomic_block=False,
        cursor=Cursor,
    )
    monkeypatch.setattr(django.db, "connections", {"default": connection})
    monkeypatch.setattr(django.db.transaction, "atomic", lambda **_kwargs: Atomic())

    result = _postgres_project_tenant_bindings(
        (PROJECT,),
        _provenance_observation().postgres,
    )

    assert result == _project_bindings((PROJECT,))
    assert len(statements) == 4
    assert "REPEATABLE READ, READ ONLY" in statements[0][0]
    assert "statement_timeout" in statements[1][0]
    assert "default_transaction_read_only" in statements[2][0]
    assert "FROM public.tracer_project AS project" in statements[3][0]
    assert "LEFT JOIN public.accounts_workspace AS workspace" in statements[3][0]
    assert "FROM tracer_project AS project" not in statements[3][0]
    assert "JOIN accounts_workspace AS workspace" not in statements[3][0]
    assert "deleted" not in statements[3][0].lower()
    assert statements[3][1] == ([PROJECT],)


def test_authoritative_clickhouse_probe_does_not_override_source_readonly() -> None:
    class Driver:
        def __init__(self, readonly: bool, row: tuple[Any, ...]) -> None:
            self.server_enforced_readonly = readonly
            self.row = row
            self.calls: list[str] = []

        def execute_read(self, query: str, *args: Any, **kwargs: Any) -> Any:
            self.calls.append("execute_read")
            assert "system.settings" in query
            assert kwargs["settings"] == {}
            return [self.row], [], 0.1

        def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
            self.calls.append("execute")
            assert "system.settings" in query
            assert not args and not kwargs
            return [self.row]

    writer = Driver(False, ("catalog-dev-01", "default", "catalog_writer", 0, 0))
    source = Driver(True, ("source-dev-01", "source_ch25", "source_reader", 1, 1))

    assert (
        _clickhouse_dev_identity(
            writer,  # type: ignore[arg-type]
            require_server_readonly=False,
        )
        == _provenance_observation().writer_clickhouse
    )
    assert (
        _clickhouse_dev_identity(
            source,  # type: ignore[arg-type]
            require_server_readonly=True,
        )
        == _provenance_observation().source_clickhouse
    )
    assert writer.calls == ["execute"]
    assert source.calls == ["execute_read"]


def test_authoritative_postgres_probe_checks_readonly_role_and_effective_dml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import django.db

    row = (
        "futureagi_dev",
        "property_catalog_reader",
        "property_catalog_reader",
        "10.20.30.40",
        5432,
        True,
        False,
        False,
        False,
        False,
        False,
        "on",
        "on",
        0,
    )

    class Cursor:
        def __init__(self) -> None:
            self.rows = iter((row, None))

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def execute(self, sql: str) -> None:
            assert "has_table_privilege" in sql
            assert "has_any_column_privilege" in sql
            assert "default_transaction_read_only" in sql

        def fetchone(self) -> Any:
            return next(self.rows)

    connection = SimpleNamespace(
        vendor="postgresql",
        in_atomic_block=False,
        cursor=Cursor,
    )
    monkeypatch.setattr(
        django.db,
        "connections",
        {"default": connection},
    )

    assert _postgres_dev_identity() == _provenance_observation().postgres


def test_status_is_read_only_and_does_not_run_a_stage() -> None:
    calls: list[str] = []
    result = run_configured_dev_rollout(
        request=_request(status=True),
        runtime=_Runtime(calls),  # type: ignore[arg-type]
    )

    assert result.completed == ()
    assert calls == ["status"]
    assert result.evidence[0].evidence == {"ready": False}


def test_execute_uses_revision_wide_postgres_executor_in_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_postgres(**kwargs: Any) -> _PostgresResult:
        assert set(kwargs) == {
            "reconciler",
            "request_factory",
            "adapters",
            "snapshot_guard",
        }
        calls.append("postgres_revision")
        return _PostgresResult()

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.dev_rollout.reconcile_postgres_revision",
        fake_postgres,
    )
    result = run_configured_dev_rollout(
        request=_request(execute=True),
        runtime=_Runtime(calls),  # type: ignore[arg-type]
    )

    assert result.completed == tuple(DevRolloutStage)
    assert calls == [
        "schema",
        "backfill",
        "postgres_reconciler",
        "postgres_request_factory",
        "postgres_adapters",
        "postgres_snapshot_guard",
        "postgres_revision",
        "reconcile_non_postgres",
        "qualify",
        "activate",
    ]


def test_execute_fenced_recovery_never_reopens_or_republishes_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FencedRuntime(_Runtime):
        def backfill(self, _request: DevRolloutRequest) -> dict[str, object]:
            self.calls.append("backfill:fenced")
            return {"fenced_recovery": True, "terminal_streams": 2}

    def forbidden_postgres(**_kwargs: Any) -> object:
        raise AssertionError("fenced recovery must not open PostgreSQL")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.property_catalog.dev_rollout."
        "reconcile_postgres_revision",
        forbidden_postgres,
    )
    result = run_configured_dev_rollout(
        request=_request(execute=True),
        runtime=FencedRuntime(calls),  # type: ignore[arg-type]
    )

    assert result.completed == tuple(DevRolloutStage)
    assert calls == ["schema", "backfill:fenced", "qualify", "activate"]
    assert result.evidence[2].evidence == {
        "fenced_recovery": True,
        "source_reconciliation_skipped": True,
    }


@pytest.mark.parametrize("mode", (ReconcileMode.INCREMENTAL, ReconcileMode.FULL_REPAIR))
def test_workspace_tick_verifies_schema_without_running_ddl_or_initial_backfill(
    mode: ReconcileMode,
) -> None:
    calls: list[str] = []

    evidence = run_workspace_reconcile(
        request=_request(
            execute=True,
            scheduled_reconcile_wall_ms=1_200_000,
        ),
        runtime=_Runtime(calls),  # type: ignore[arg-type]
        mode=mode,
    )

    assert calls == ["verify_schema", f"workspace:{mode}"]
    assert evidence == {
        "mode": mode,
        "schema": {"exact_tables": 6},
        "reconcile": {"mode": mode, "qualified": True},
        "workspace_id": WORKSPACE,
    }


def test_workspace_tick_allows_explicit_incremental_repair() -> None:
    calls: list[str] = []

    evidence = run_workspace_reconcile(
        request=_request(
            execute=True,
            scheduled_reconcile_wall_ms=1_200_000,
            repair_expired_incomplete=True,
        ),
        runtime=_Runtime(calls),  # type: ignore[arg-type]
        mode=ReconcileMode.INCREMENTAL,
    )

    assert calls == ["verify_schema", "workspace:incremental"]
    assert evidence["mode"] is ReconcileMode.INCREMENTAL


def test_workspace_tick_rejects_dry_run_before_runtime_io() -> None:
    calls: list[str] = []

    with pytest.raises(DevRolloutError, match="execute mode"):
        run_workspace_reconcile(
            request=_request(),
            runtime=_Runtime(calls),  # type: ignore[arg-type]
            mode=ReconcileMode.INCREMENTAL,
        )

    assert calls == []


def test_workspace_tick_rejects_initial_backfill_wall_before_runtime_io() -> None:
    calls: list[str] = []

    with pytest.raises(DevRolloutError, match="refuses an initial backfill wall"):
        run_workspace_reconcile(
            request=_request(
                execute=True,
                initial_backfill_wall_ms=DEV_STANDARD_MAX_WALL_MS + 1,
            ),
            runtime=_Runtime(calls),  # type: ignore[arg-type]
            mode=ReconcileMode.INCREMENTAL,
        )

    assert calls == []


def test_workspace_tick_requires_explicit_scheduled_wall_before_runtime_io() -> None:
    calls: list[str] = []

    with pytest.raises(DevRolloutError, match="requires an explicit extended wall"):
        run_workspace_reconcile(
            request=_request(execute=True),
            runtime=_Runtime(calls),  # type: ignore[arg-type]
            mode=ReconcileMode.INCREMENTAL,
        )

    assert calls == []


def test_concrete_scheduled_incremental_uses_auto_and_fenced_resume_skips_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    request = _request(execute=True)
    seen: list[LifecycleRunMode] = []
    execution = SimpleNamespace(
        prepared=SimpleNamespace(
            reservation_status=ReservationStatus.FENCED,
            lifecycle_mode=CatalogLifecycleMode.FULL_REPAIR,
            lineage_anchor_revision=12,
        ),
        qualification=SimpleNamespace(qualified=True),
        lease=SimpleNamespace(build_lease_sha256="a" * 64),
    )

    def prepare(
        _runtime: CheckedInPropertyCatalogDevRuntime,
        mode: LifecycleRunMode,
    ) -> object:
        seen.append(mode)
        return execution

    def activate(
        _runtime: CheckedInPropertyCatalogDevRuntime,
        _request: DevRolloutRequest,
    ) -> dict[str, object]:
        return {"activated": True}

    def forbidden_source(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("fenced scheduled recovery must not read a source")

    schema_verifications: list[tuple[str, str]] = []

    def verify_schema(
        _client: object,
        *,
        target_database: str,
        deployment: str,
    ) -> dict[str, object]:
        schema_verifications.append((target_database, deployment))
        return {}

    monkeypatch.setattr(dev_runtime, "verify_runtime_catalog_schema", verify_schema)
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime, "_prepare_revision", prepare
    )
    monkeypatch.setattr(CheckedInPropertyCatalogDevRuntime, "activate", activate)
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_run_authoritative_span",
        forbidden_source,
    )
    runtime = object.__new__(CheckedInPropertyCatalogDevRuntime)
    config = _unit_runtime_config()
    runtime.config = config
    runtime.bound_request = request
    runtime.provenance = _provenance_evidence(config=config, request=request)
    runtime.project_tenant_binding_probe = _project_bindings
    runtime._factory_authority = _RUNTIME_FACTORY_AUTHORITY
    runtime.now = lambda: ATTESTED_AT
    runtime.schema_client = object()  # type: ignore[assignment]

    result = runtime.reconcile_workspace(request, mode=ReconcileMode.INCREMENTAL)

    assert seen == [LifecycleRunMode.AUTO]
    assert schema_verifications == [("property_catalog_dev_unit", "dev")]
    assert result == {
        "activated": True,
        "build_lease_sha256": "a" * 64,
        "lifecycle_mode": "full_repair",
        "lineage_anchor_revision": 12,
        "qualified": True,
        "resumed": True,
    }


@pytest.mark.parametrize(
    "overrides",
    (
        {"environment": "production"},
        {"cloud_deployment": "PROD"},
        {"dev_identity": "dev:production"},
        {"dev_identity": "dev:live-west"},
        {"source_database": "source-db"},
        {"source_database": "property_catalog_dev_unit"},
        {"target_database": "futureagi"},
        {"target_database": "property_catalog"},
        {"target_database": "PROPERTY_CATALOG_DEV_UNIT"},
        {"target_database": "property_catalog_dev_unit-x"},
        {"acknowledgement": "wrong"},
        {"execute": True, "status": True},
        {"repair_expired_incomplete": True},
        {"execute": True, "repair_expired_incomplete": 1},
    ),
)
def test_rollout_rejects_non_dev_or_ambiguous_targets(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(DevRolloutError):
        _request(**overrides)


@pytest.mark.parametrize("cloud_deployment", ("", DEV_CLOUD_DEPLOYMENT))
def test_rollout_accepts_oss_empty_and_existing_dev_cloud(
    cloud_deployment: str,
) -> None:
    request = _request(cloud_deployment=cloud_deployment)

    assert request.environment == "development"
    assert request.cloud_deployment == cloud_deployment
    assert request.target_database == "property_catalog_dev_unit"
    assert request.acknowledgement == DEV_ROLLOUT_ACK


@pytest.mark.parametrize(
    "target_database",
    ("legacy_catalog_snapshot", "production_dev"),
)
def test_rollout_accepts_safe_dev_target_names(target_database: str) -> None:
    request = _request(target_database=target_database)

    assert request.target_database == target_database


@pytest.mark.parametrize(
    "overrides",
    (
        {"target_database": "futureagi"},
        {"target_database": "property_catalog"},
        {"acknowledgement": "wrong"},
        {"environment": "staging"},
    ),
)
def test_oss_empty_cloud_keeps_exact_dev_isolation_gates(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(DevRolloutError):
        _request(cloud_deployment="", **overrides)


@pytest.mark.parametrize(
    "wall_ms",
    (True, DEV_STANDARD_MAX_WALL_MS, DEV_INITIAL_BACKFILL_MAX_WALL_MS + 1),
)
def test_explicit_initial_backfill_wall_is_strictly_bounded(wall_ms: object) -> None:
    with pytest.raises(DevRolloutError, match="initial backfill wall"):
        _request(execute=True, initial_backfill_wall_ms=wall_ms)


def test_explicit_initial_backfill_wall_requires_execute() -> None:
    with pytest.raises(DevRolloutError, match="requires --execute"):
        _request(initial_backfill_wall_ms=DEV_STANDARD_MAX_WALL_MS + 1)


@pytest.mark.parametrize(
    "wall_ms",
    (True, DEV_STANDARD_MAX_WALL_MS, DEV_SCHEDULED_RECONCILE_MAX_WALL_MS + 1),
)
def test_explicit_scheduled_reconcile_wall_is_strictly_bounded(
    wall_ms: object,
) -> None:
    with pytest.raises(DevRolloutError, match="scheduled reconcile wall"):
        _request(execute=True, scheduled_reconcile_wall_ms=wall_ms)


def test_scheduled_reconcile_wall_requires_execute_and_excludes_initial() -> None:
    with pytest.raises(DevRolloutError, match="requires execute mode"):
        _request(scheduled_reconcile_wall_ms=1_200_000)
    with pytest.raises(DevRolloutError, match="mutually exclusive"):
        _request(
            execute=True,
            initial_backfill_wall_ms=1_200_000,
            scheduled_reconcile_wall_ms=1_200_000,
        )


def test_expired_repair_is_explicit_in_execute_plan() -> None:
    request = _request(execute=True, repair_expired_incomplete=True)

    plan = UnifiedDevRollout.plan(request)

    assert plan.repair_expired_incomplete is True
    assert plan.as_dict()["repair_expired_incomplete"] is True


def test_checked_in_runtime_builds_exact_ten_stream_plan() -> None:
    build_token = "55555555-5555-4555-8555-555555555555"
    hot_stream = "44444444-4444-4444-8444-444444444444"

    streams = _planned_streams(
        build_token=build_token,
        hot_producer_stream_id=hot_stream,
        postgres_source_fence=1_723_638_000_000_000,
        span_audit_generation=8_888,
    )

    assert len(streams) == len({stream.key for stream in streams}) == 10
    assert sum(stream.role is ManifestStreamRole.DEFINITIONS for stream in streams) == 7
    span = tuple(
        stream
        for stream in streams
        if stream.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
    )
    assert {stream.role for stream in span} == set(ManifestStreamRole)
    assert all(stream.source_cutoff_label == SPAN_AUDIT_CUTOFF_LABEL for stream in span)
    assert all(stream.source_version_fence == 8_888 for stream in span)
    assert (
        next(
            stream for stream in span if stream.role is ManifestStreamRole.HOT_VALUES
        ).producer_stream_id
        == hot_stream
    )
    postgres = tuple(
        stream
        for stream in streams
        if stream.source_adapter
        not in {SourceAdapter.SYSTEM_MANIFEST, SourceAdapter.SPAN_ATTRIBUTE}
    )
    assert len(postgres) == 5
    assert {stream.source_version_fence for stream in postgres} == {
        1_723_638_000_000_000
    }


def test_dev_runtime_requires_v2_shared_proof_and_temporal_headroom(
    tmp_path: Any,
) -> None:
    shared = str(tmp_path)
    config = DevRuntimeConfig(
        catalog=NativeConnectionConfig(
            host="catalog.dev.invalid",
            port=9000,
            user="catalog_writer",
            password="test",
            database="property_catalog_dev_unit",
            server_enforced_readonly=False,
        ),
        source=NativeConnectionConfig(
            host="source.dev.invalid",
            port=9000,
            user="source_reader",
            password="test",
            database="source_ch25",
            server_enforced_readonly=True,
        ),
        catalog_epoch=1,
        projection_version=1,
        project_ids=("33333333-3333-4333-8333-333333333333",),
        hot_producer_stream_id="44444444-4444-4444-8444-444444444444",
        mutation_lock_directory=shared,
        revision_fence_file=f"{shared}/fence.json",
        drain_proof_file=f"{shared}/producer-drain-proof-v2.json",
        producer_retirement_file=(f"{shared}/producer-state-retirements-v1.json"),
        span_since=datetime(2026, 8, 14, 12, tzinfo=UTC),
        span_until=datetime(2026, 8, 14, 13, tzinfo=UTC),
        sidecar_acknowledgement=DEV_SIDECAR_ACK,
        provenance_expectation=_provenance_expectation(),
        rollout_wall_ms=DEV_STANDARD_MAX_WALL_MS,
    )

    assert config.span_page_rows == 256
    assert config.rollout_wall_ms == DEV_STANDARD_MAX_WALL_MS
    assert config.span_query_timeout_ms == CANONICAL_SPAN_QUERY_TIMEOUT_MS
    assert (
        config.source.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert (
        config.catalog.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="fixed v2 filename"):
        replace(
            config,
            drain_proof_file=f"{shared}/producer-drain-proof-v1.json",
        )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="fixed v1 filename"):
        replace(
            config,
            producer_retirement_file=f"{shared}/producer-retirement.json",
        )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="100000"):
        replace(config, rollout_wall_ms=100_001)
    with pytest.raises(PropertyCatalogDevRuntimeError, match=r"\[1, 1024\]"):
        replace(config, span_page_rows=1_025)
    with pytest.raises(PropertyCatalogDevRuntimeError, match="reviewed values"):
        replace(
            config.catalog,
            read_timeout_ceiling_ms=max(
                django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS,
                DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
            )
            + 1,
        )
    extended = replace(
        config,
        rollout_wall_ms=DEV_INITIAL_BACKFILL_MAX_WALL_MS,
        explicit_initial_backfill_wall=True,
    )
    assert (
        extended.span_query_timeout_ms
        == DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
    )
    assert (MAX_REVISION_LEASE_SECONDS * 1_000 - extended.rollout_wall_ms) == 60_000
    with pytest.raises(PropertyCatalogDevRuntimeError, match="1740000"):
        replace(
            config,
            rollout_wall_ms=DEV_INITIAL_BACKFILL_MAX_WALL_MS + 1,
            explicit_initial_backfill_wall=True,
        )
    scheduled = replace(
        config,
        rollout_wall_ms=1_200_000,
        explicit_scheduled_reconcile_wall=True,
    )
    assert scheduled.extended_rollout_wall is True
    assert scheduled.span_query_timeout_ms == CANONICAL_SPAN_QUERY_TIMEOUT_MS
    with pytest.raises(PropertyCatalogDevRuntimeError, match="mutually exclusive"):
        replace(
            scheduled,
            explicit_initial_backfill_wall=True,
        )


def test_runtime_settings_keep_standard_wall_capped_and_select_explicit_wall(
    tmp_path: Any,
) -> None:
    settings_object = _runtime_settings(str(tmp_path))
    request = _request(
        execute=True,
        initial_backfill_wall_ms=DEV_INITIAL_BACKFILL_MAX_WALL_MS,
    )

    config = DevRuntimeConfig.from_settings(
        request,
        settings_object,
        now=ATTESTED_AT,
    )

    assert config.rollout_wall_ms == DEV_INITIAL_BACKFILL_MAX_WALL_MS
    assert config.explicit_initial_backfill_wall is True
    assert (
        config.span_query_timeout_ms
        == DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
    )
    assert config.source.read_timeout_ceiling_ms == 30_000
    assert (
        config.catalog.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    default_config = DevRuntimeConfig.from_settings(
        _request(),
        settings_object,
        now=ATTESTED_AT,
    )
    assert default_config.explicit_initial_backfill_wall is False
    assert default_config.span_query_timeout_ms == CANONICAL_SPAN_QUERY_TIMEOUT_MS
    assert (
        default_config.source.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="must remain"):
        DevRuntimeConfig.from_settings(
            request,
            _runtime_settings(
                str(tmp_path),
                wall_ms=DEV_STANDARD_MAX_WALL_MS + 1,
            ),
            now=ATTESTED_AT,
        )


@pytest.mark.parametrize(
    ("environment", "cloud_deployment", "request_cloud_deployment"),
    (
        ("development", None, ""),
        ("development", DEV_CLOUD_DEPLOYMENT, DEV_CLOUD_DEPLOYMENT),
        ("staging", DEV_CLOUD_DEPLOYMENT, DEV_CLOUD_DEPLOYMENT),
    ),
)
def test_runtime_accepts_oss_empty_cloud_and_preserves_dev_cloud_behavior(
    tmp_path: Any,
    environment: str,
    cloud_deployment: str | None,
    request_cloud_deployment: str,
) -> None:
    settings_object = _runtime_settings(str(tmp_path))
    settings_object.ENV_TYPE = environment
    if cloud_deployment is None:
        del settings_object.CLOUD_DEPLOYMENT
    else:
        settings_object.CLOUD_DEPLOYMENT = cloud_deployment

    config = DevRuntimeConfig.from_settings(
        _request(cloud_deployment=request_cloud_deployment),
        settings_object,
        now=ATTESTED_AT,
    )

    assert config.catalog.database == "property_catalog_dev_unit"


@pytest.mark.parametrize(
    (
        "request_cloud_deployment",
        "environment",
        "cloud_deployment",
        "message",
    ),
    (
        ("", "development", DEV_CLOUD_DEPLOYMENT, "differs"),
        (DEV_CLOUD_DEPLOYMENT, "development", None, "differs"),
        ("", "staging", None, "only when ENV_TYPE=development"),
        (DEV_CLOUD_DEPLOYMENT, "production", DEV_CLOUD_DEPLOYMENT, "non-DEV"),
        ("", "production", None, "non-DEV"),
    ),
)
def test_runtime_rejects_cloud_mismatches_staging_unset_and_production(
    tmp_path: Any,
    request_cloud_deployment: str,
    environment: str,
    cloud_deployment: str | None,
    message: str,
) -> None:
    settings_object = _runtime_settings(str(tmp_path))
    settings_object.ENV_TYPE = environment
    if cloud_deployment is None:
        del settings_object.CLOUD_DEPLOYMENT
    else:
        settings_object.CLOUD_DEPLOYMENT = cloud_deployment

    with pytest.raises(PropertyCatalogDevRuntimeError, match=message):
        DevRuntimeConfig.from_settings(
            _request(cloud_deployment=request_cloud_deployment),
            settings_object,
            now=ATTESTED_AT,
        )


def test_runtime_settings_select_scheduled_aggregate_wall_without_widening_queries(
    tmp_path: Any,
) -> None:
    config = DevRuntimeConfig.from_settings(
        _request(
            execute=True,
            scheduled_reconcile_wall_ms=1_200_000,
        ),
        _runtime_settings(str(tmp_path)),
        now=ATTESTED_AT,
    )

    assert config.rollout_wall_ms == 1_200_000
    assert config.explicit_scheduled_reconcile_wall is True
    assert config.explicit_initial_backfill_wall is False
    assert config.extended_rollout_wall is True
    assert config.span_query_timeout_ms == CANONICAL_SPAN_QUERY_TIMEOUT_MS
    assert (
        config.source.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )


def test_factory_shares_one_extended_deadline_with_lease_headroom(
    tmp_path: Any,
) -> None:
    request = _request(
        execute=True,
        initial_backfill_wall_ms=DEV_INITIAL_BACKFILL_MAX_WALL_MS,
    )

    class Driver:
        def __init__(self, config: NativeConnectionConfig) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly

    runtime = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=Driver,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=_project_bindings,
        now=lambda: ATTESTED_AT,
    )(request)

    assert runtime.deadline.wall_ms == DEV_INITIAL_BACKFILL_MAX_WALL_MS
    assert runtime.state_store._deadline is runtime.deadline
    assert runtime.coordinator._deadline is runtime.deadline
    assert runtime.span_reader._deadline is runtime.deadline
    assert (
        runtime.span_reader._timeout_ms
        == DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
    )
    assert runtime.source_client._explicit_initial_backfill is True
    assert (
        runtime.config.span_page_rows == DEV_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS
    )
    assert runtime.config.source.read_timeout_ceiling_ms == 30_000
    assert (
        runtime.config.catalog.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert runtime.lifecycle_state is not None
    assert runtime.lifecycle_state._deadline is runtime.deadline
    assert runtime.hot_proof_source.deadline is runtime.deadline
    assert runtime.coordinator._lease_seconds == MAX_REVISION_LEASE_SECONDS
    assert (
        runtime.coordinator._lease_seconds * 1_000 - runtime.deadline.wall_ms >= 60_000
    )
    source_budget = runtime._source_budget()
    assert 8.5 < source_budget.adapter_wall_timeout_seconds <= 540.0
    assert source_budget.shared_deadline is runtime.deadline
    assert source_budget.postgres.statement_timeout_ms == 8_000
    assert source_budget.postgres.wall_timeout_seconds == 540.0
    assert source_budget.postgres.initial_backfill is True
    authorization = runtime.provenance.project_tenant_authorization
    assert authorization is not None
    assert authorization.organization_id == ORG
    assert authorization.workspace_id == WORKSPACE
    assert authorization.project_ids == (PROJECT,)
    assert len(authorization.as_dict()["authorization_sha256"]) == 64


@pytest.mark.parametrize(
    ("request_overrides", "expected_wall_seconds", "scheduled_reconcile"),
    (
        ({}, RUNTIME_LIMITS.source_adapter_wall_seconds, False),
        (
            {"scheduled_reconcile_wall_ms": 1_200_000},
            RUNTIME_LIMITS.scheduled_reconcile_source_adapter_wall_seconds,
            True,
        ),
    ),
)
def test_runtime_source_budget_matches_the_explicit_reconcile_mode(
    tmp_path: Any,
    request_overrides: dict[str, object],
    expected_wall_seconds: float,
    scheduled_reconcile: bool,
) -> None:
    class Driver:
        def __init__(self, config: NativeConnectionConfig) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly

    runtime = PropertyCatalogDevRuntimeFactory(
        settings_object=_runtime_settings(str(tmp_path)),
        native_client_factory=Driver,  # type: ignore[arg-type]
        provenance_probe=lambda *_args: _provenance_observation(),
        project_tenant_binding_probe=_project_bindings,
        now=lambda: ATTESTED_AT,
    )(_request(execute=True, **request_overrides))

    source_budget = runtime._source_budget()

    assert runtime.span_reader._timeout_ms == CANONICAL_SPAN_QUERY_TIMEOUT_MS
    assert runtime.source_client._explicit_initial_backfill is False
    if scheduled_reconcile:
        assert (
            runtime.coordinator._lease_seconds * 1_000 - runtime.deadline.wall_ms
            >= 60_000
        )
    else:
        assert runtime.coordinator._lease_seconds == REVISION_LEASE_SECONDS
    assert (
        runtime.config.source.read_timeout_ceiling_ms
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert source_budget.adapter_wall_timeout_seconds == expected_wall_seconds
    assert source_budget.shared_deadline is runtime.deadline
    assert source_budget.postgres.statement_timeout_ms <= 8_000
    assert source_budget.postgres.wall_timeout_seconds == expected_wall_seconds
    assert source_budget.postgres.initial_backfill is False
    assert source_budget.postgres.scheduled_reconcile is scheduled_reconcile


def test_concrete_runtime_publishes_prior_retirement_then_opens_all_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    shared = str(tmp_path)
    config = DevRuntimeConfig(
        catalog=NativeConnectionConfig(
            host="catalog.dev.invalid",
            port=9000,
            user="catalog_writer",
            password="test",
            database="property_catalog_dev_unit",
            server_enforced_readonly=False,
        ),
        source=NativeConnectionConfig(
            host="source.dev.invalid",
            port=9000,
            user="source_reader",
            password="test",
            database="source_ch25",
            server_enforced_readonly=True,
        ),
        catalog_epoch=1,
        projection_version=1,
        project_ids=("33333333-3333-4333-8333-333333333333",),
        hot_producer_stream_id="44444444-4444-4444-8444-444444444444",
        mutation_lock_directory=shared,
        revision_fence_file=f"{shared}/fence.json",
        drain_proof_file=f"{shared}/producer-drain-proof-v2.json",
        producer_retirement_file=(f"{shared}/producer-state-retirements-v1.json"),
        span_since=now,
        span_until=now + timedelta(hours=1),
        sidecar_acknowledgement=DEV_SIDECAR_ACK,
        provenance_expectation=_provenance_expectation(),
        rollout_wall_ms=100_000,
    )

    class CatalogClient:
        catalog_database = "property_catalog_dev_unit"

    class SpanReader:
        def freeze(self, **kwargs: Any) -> FrozenSpanSource:
            assert kwargs == {
                "project_ids": config.project_ids,
                "since": config.span_since,
                "until": config.span_until,
            }
            return FrozenSpanSource(
                config.project_ids,
                config.span_since,
                config.span_until,
                77,
            )

    events: list[str] = []

    def publish_prior_retirement(
        _runtime: CheckedInPropertyCatalogDevRuntime,
        active: PriorActiveEvidence,
        scope: object,
    ) -> None:
        assert active is prior_active
        assert scope is not None
        events.append("publish_retirement")

    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_publish_producer_retirement",
        publish_prior_retirement,
    )
    # The concrete PriorActiveEvidence contract is independently exercised by
    # durable-lifecycle and producer-retirement tests. Here only its runtime
    # ordering identity is relevant.
    prior_active = object.__new__(PriorActiveEvidence)

    class Coordinator:
        def allocate(self, **kwargs: Any) -> RevisionLease:
            events.append("allocate")
            plan = RevisionBuildPlan(
                organization_id=kwargs["organization_id"],
                workspace_id=kwargs["workspace_id"],
                catalog_epoch=kwargs["catalog_epoch"],
                catalog_revision=1,
                build_token=kwargs["build_token"],
                projection_version=kwargs["projection_version"],
                source_scope=kwargs["source_scope"],
                streams=kwargs["planned_streams"],
            )
            return RevisionLease(
                organization_id=plan.organization_id,
                workspace_id=plan.workspace_id,
                catalog_epoch=plan.catalog_epoch,
                catalog_revision=plan.catalog_revision,
                projection_version=plan.projection_version,
                build_token=plan.build_token,
                build_plan_json=plan.canonical_json,
                build_lease_sha256=plan.sha256,
                issued_at=now,
                expires_at=now + timedelta(minutes=10),
            )

        def open_stream(self, **kwargs: Any) -> CatalogWriteLease:
            lease = kwargs["lease"]
            events.append(
                f"open:{kwargs['source_adapter']}:{kwargs['producer_stream_id']}"
            )
            return CatalogWriteLease(
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                catalog_epoch=lease.catalog_epoch,
                catalog_revision=lease.catalog_revision,
                build_token=lease.build_token,
                projection_version=lease.projection_version,
                source_adapter=kwargs["source_adapter"],
                producer_stream_id=kwargs["producer_stream_id"],
                build_plan_json=lease.build_plan_json,
                build_lease_sha256=lease.build_lease_sha256,
                expires_at=lease.expires_at,
            )

        def publish_building_assignment(self, **kwargs: Any) -> None:
            assert isinstance(kwargs["lease"], RevisionLease)
            events.append("publish_building")

    coordinator = Coordinator()

    class Lifecycle:
        def prepare(self, **kwargs: Any) -> PreparedLifecycleRevision:
            scope = kwargs["scope"]
            mode = kwargs["mode"]
            assert mode is LifecycleRunMode.INITIAL_BACKFILL
            frozen = SpanReader().freeze(
                project_ids=scope.project_ids,
                since=config.span_since,
                until=config.span_until,
            )
            build_token = "55555555-5555-4555-8555-555555555555"
            planned = _planned_streams(
                build_token=build_token,
                hot_producer_stream_id=config.hot_producer_stream_id,
                postgres_source_fence=int(config.span_until.timestamp() * 1_000_000),
                span_audit_generation=frozen.audit_generation,
            )
            lease = coordinator.allocate(
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                catalog_epoch=scope.catalog_epoch,
                projection_version=scope.projection_version,
                build_token=build_token,
                source_scope=BuildPlanSourceScope(
                    project_ids=scope.project_ids,
                    span_since_us=int(config.span_since.timestamp() * 1_000_000),
                    span_until_us=int(config.span_until.timestamp() * 1_000_000),
                ),
                planned_streams=planned,
                now=now,
            )
            return PreparedLifecycleRevision(
                scope=scope,
                mode=mode,
                lease=lease,
                cutoffs=FrozenLifecycleCutoffs(
                    snapshot_upper=config.span_until,
                    span_window=SourceWindow(config.span_since, config.span_until),
                    span_audit_generation=frozen.audit_generation,
                ),
                prior_active=prior_active,
                streams=tuple(
                    StreamStart(
                        source_adapter=stream.source_adapter,
                        role=stream.role,
                        producer_stream_id=stream.producer_stream_id,
                        lower_watermark="",
                        resume=None,
                    )
                    for stream in lease.build_plan.streams
                ),
                reservation_status=ReservationStatus.OPEN,
                resumed=False,
            )

    request = _request(execute=True)
    runtime = CheckedInPropertyCatalogDevRuntime(
        config=config,
        bound_request=request,
        provenance=_provenance_evidence(config=config, request=request),
        schema_client=object(),  # type: ignore[arg-type]
        catalog_client=CatalogClient(),  # type: ignore[arg-type]
        source_client=object(),  # type: ignore[arg-type]
        serializer=object(),  # type: ignore[arg-type]
        deadline=SharedCatalogDeadline(wall_ms=100_000),
        state_store=object(),  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
        lifecycle=Lifecycle(),  # type: ignore[arg-type]
        span_reader=SpanReader(),  # type: ignore[arg-type]
        hot_proof_source=object(),  # type: ignore[arg-type]
        now=lambda: now,
        new_build_token=lambda: "55555555-5555-4555-8555-555555555555",
        project_tenant_binding_probe=_project_bindings,
        _factory_authority=_RUNTIME_FACTORY_AUTHORITY,
    )

    execution = runtime._prepare_revision(LifecycleRunMode.INITIAL_BACKFILL)

    assert events[0] == "allocate"
    assert events[1] == "publish_retirement"
    assert len([event for event in events if event.startswith("open:")]) == 10
    assert events[-1] == "publish_building"
    assert len(execution.lease.build_plan.streams) == 10
    assert len(execution.publishers_by_role) == 9

    # A durable fence outlives the write lease. Recovery must not renew that
    # lease or construct publishers which reject (or could write under) it.
    recovery_events_start = len(events)
    restored: list[object] = []

    class FencedLifecycle:
        def prepare(self, **kwargs: Any) -> PreparedLifecycleRevision:
            prepared = Lifecycle().prepare(**kwargs)
            return replace(
                prepared,
                prior_active=None,
                reservation_status=ReservationStatus.FENCED,
                resumed=True,
            )

    def restore_fenced(
        _runtime: CheckedInPropertyCatalogDevRuntime,
        fenced_execution: object,
    ) -> None:
        restored.append(fenced_execution)

    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_restore_fenced_execution",
        restore_fenced,
    )
    fenced_runtime = CheckedInPropertyCatalogDevRuntime(
        config=config,
        bound_request=request,
        provenance=_provenance_evidence(config=config, request=request),
        schema_client=object(),  # type: ignore[arg-type]
        catalog_client=CatalogClient(),  # type: ignore[arg-type]
        source_client=object(),  # type: ignore[arg-type]
        serializer=object(),  # type: ignore[arg-type]
        deadline=SharedCatalogDeadline(wall_ms=100_000),
        state_store=object(),  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
        lifecycle=FencedLifecycle(),  # type: ignore[arg-type]
        span_reader=SpanReader(),  # type: ignore[arg-type]
        hot_proof_source=object(),  # type: ignore[arg-type]
        now=lambda: now + timedelta(minutes=11),
        new_build_token=lambda: "55555555-5555-4555-8555-555555555555",
        project_tenant_binding_probe=_project_bindings,
        _factory_authority=_RUNTIME_FACTORY_AUTHORITY,
    )

    fenced = fenced_runtime._prepare_revision(LifecycleRunMode.INITIAL_BACKFILL)

    assert fenced.lease.expires_at < fenced_runtime.now()
    assert fenced.publishers_by_role == {}
    assert restored == [fenced]
    assert events[recovery_events_start:] == ["allocate"]

    with pytest.raises(AttributeError, match="immutable"):
        runtime.config = replace(config, catalog_epoch=2)
    with pytest.raises(AttributeError, match="immutable"):
        runtime._authorized_revision_proof = None

    original_context = execution.context
    execution.context = replace(original_context, workspace_id=OTHER_WORKSPACE)
    with pytest.raises(PropertyCatalogDevRuntimeError, match="live revision execution"):
        runtime.postgres_request_factory(request)
    execution.context = replace(
        original_context,
        snapshot_cutoff=original_context.snapshot_cutoff - timedelta(minutes=1),
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="live revision execution"):
        runtime.postgres_request_factory(request)
    execution.context = original_context

    original_frozen = execution.frozen
    execution.frozen = replace(
        original_frozen,
        since=original_frozen.since + timedelta(minutes=1),
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="live revision execution"):
        runtime.postgres_request_factory(request)
    execution.frozen = original_frozen

    execution.mode = ReconcileMode.INCREMENTAL
    with pytest.raises(PropertyCatalogDevRuntimeError, match="live revision execution"):
        runtime.postgres_request_factory(request)
    execution.mode = ReconcileMode.FULL_REPAIR

    execution.emitted_at += timedelta(minutes=1)
    with pytest.raises(PropertyCatalogDevRuntimeError, match="live revision execution"):
        runtime.postgres_request_factory(request)
    execution.emitted_at -= timedelta(minutes=1)

    original_planned = execution.planned_by_role
    execution.planned_by_role = dict(tuple(original_planned.items())[:-1])
    with pytest.raises(PropertyCatalogDevRuntimeError, match="live revision execution"):
        runtime.postgres_request_factory(request)
    execution.planned_by_role = original_planned


def test_activation_rereads_exact_active_before_retirement_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(execute=True)
    plan_sha = "a" * 64
    manifest_sha = "b" * 64
    activation_sha = "c" * 64
    record = SimpleNamespace(
        catalog_revision=17,
        build_token="55555555-5555-4555-8555-555555555555",
        projection_version=1,
        lifecycle_mode=CatalogLifecycleMode.INCREMENTAL,
        activation_sequence=9,
        activation_sha256=activation_sha,
        source_manifest_sha256=manifest_sha,
        live_definition_rows=4,
        tombstone_rows=1,
        value_rows=8,
    )
    active = SimpleNamespace(
        catalog_revision=record.catalog_revision,
        build_token=record.build_token,
        projection_version=record.projection_version,
        lifecycle_mode=record.lifecycle_mode,
        activation_sequence=record.activation_sequence,
        activation_sha256=record.activation_sha256,
        source_manifest_sha256=record.source_manifest_sha256,
        build_plan=SimpleNamespace(sha256=plan_sha),
    )

    class LifecycleState:
        def __init__(self) -> None:
            self.active = active
            self.calls = 0

        def load_latest_active(self, _scope: object) -> object:
            self.calls += 1
            return self.active

    state = LifecycleState()
    published: list[tuple[object, object]] = []

    def publish_retirement(
        _runtime: CheckedInPropertyCatalogDevRuntime,
        value: object,
        scope: object,
    ) -> None:
        published.append((value, scope))

    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_publish_producer_retirement",
        publish_retirement,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_validate_execution_authorization",
        lambda *_args, **_kwargs: None,
    )
    scope = object()
    execution = SimpleNamespace(
        activation=SimpleNamespace(record=record, idempotent=False),
        prepared=SimpleNamespace(scope=scope),
        lease=SimpleNamespace(build_lease_sha256=plan_sha),
    )
    config = _unit_runtime_config()
    runtime = CheckedInPropertyCatalogDevRuntime(
        config=config,
        bound_request=request,
        provenance=_provenance_evidence(config=config, request=request),
        schema_client=object(),  # type: ignore[arg-type]
        catalog_client=object(),  # type: ignore[arg-type]
        source_client=object(),  # type: ignore[arg-type]
        serializer=object(),  # type: ignore[arg-type]
        deadline=SharedCatalogDeadline(wall_ms=100_000),
        state_store=object(),  # type: ignore[arg-type]
        coordinator=object(),  # type: ignore[arg-type]
        lifecycle=object(),  # type: ignore[arg-type]
        span_reader=object(),  # type: ignore[arg-type]
        hot_proof_source=object(),  # type: ignore[arg-type]
        now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        new_build_token=lambda: record.build_token,
        lifecycle_state=state,  # type: ignore[arg-type]
        producer_retirement_sink=object(),  # type: ignore[arg-type]
        _execution=execution,  # type: ignore[arg-type]
        project_tenant_binding_probe=_project_bindings,
        _factory_authority=_RUNTIME_FACTORY_AUTHORITY,
    )

    result = runtime.activate(request)

    assert result["activated"] is True
    assert state.calls == 1
    assert published == [(active, scope)]

    state.active = SimpleNamespace(
        **{
            **active.__dict__,
            "source_manifest_sha256": "d" * 64,
        }
    )
    published.clear()
    with pytest.raises(PropertyCatalogDevRuntimeError, match="durably reread"):
        runtime.activate(request)
    assert published == []


def test_full_repair_span_definition_ignores_the_prior_active_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_token = "55555555-5555-4555-8555-555555555555"
    context = PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=(PROJECT,),
        catalog_epoch=1,
        catalog_revision=12,
        projection_version=1,
        snapshot_cutoff=datetime(2026, 8, 14, 13, tzinfo=UTC),
    )

    class CatalogClient:
        catalog_database = "th7247_catalog_dev_unit"

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any], int]] = []

        def query(
            self,
            sql: str,
            params: dict[str, Any],
            *,
            timeout_ms: int,
        ) -> tuple[object, ...]:
            self.calls.append((sql, dict(params), timeout_ms))
            return ()

    proof = SimpleNamespace(state_conflict_count=0, count=0, digest="0" * 64)

    class SpanReader:
        def audit(self, _frozen: object) -> object:
            return proof

    result = SimpleNamespace(
        checkpoint_write=SimpleNamespace(
            checkpoint=SimpleNamespace(definition_count=0),
        )
    )
    authoritative = SimpleNamespace(
        values=SimpleNamespace(source_count=0, source_digest=proof.digest),
        source_audit=SimpleNamespace(source_count=0, source_digest=proof.digest),
    )
    execution = SimpleNamespace(
        context=context,
        lease=SimpleNamespace(build_token=build_token),
        prepared=SimpleNamespace(
            mode=LifecycleRunMode.FULL_REPAIR,
            lineage_anchor_revision=context.catalog_revision,
            prior_active=SimpleNamespace(catalog_revision=11),
        ),
        frozen=object(),
    )
    captured_loaders: list[RevisionPinnedSpanAttributeGroupPageLoader] = []

    def run_definition_adapter(
        _runtime: CheckedInPropertyCatalogDevRuntime,
        _execution: object,
        adapter: object,
    ) -> object:
        if getattr(adapter, "source_adapter", None) is SourceAdapter.SPAN_ATTRIBUTE:
            assert isinstance(adapter, SpanAttributeDefinitionSourceAdapter)
            loaders = tuple(
                cell.cell_contents
                for cell in (adapter._page_loader.__closure__ or ())
                if isinstance(
                    cell.cell_contents,
                    RevisionPinnedSpanAttributeGroupPageLoader,
                )
            )
            assert len(loaders) == 1
            captured_loaders.extend(loaders)
            assert adapter._page_loader(context=context, cursor=None, limit=2) == ()
        return result

    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_validate_mutation_request",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_refresh_project_tenant_authorization",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_require_execution",
        lambda *_args, **_kwargs: execution,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_record_postgres_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_run_definition_adapter",
        run_definition_adapter,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "_require_authoritative",
        lambda *_args, **_kwargs: authoritative,
    )

    client = CatalogClient()
    request = _request(execute=True)
    config = _unit_runtime_config()
    runtime = CheckedInPropertyCatalogDevRuntime(
        config=config,
        bound_request=request,
        provenance=_provenance_evidence(config=config, request=request),
        schema_client=object(),  # type: ignore[arg-type]
        catalog_client=client,  # type: ignore[arg-type]
        source_client=object(),  # type: ignore[arg-type]
        serializer=object(),  # type: ignore[arg-type]
        deadline=SharedCatalogDeadline(wall_ms=100_000),
        state_store=object(),  # type: ignore[arg-type]
        coordinator=object(),  # type: ignore[arg-type]
        lifecycle=object(),  # type: ignore[arg-type]
        span_reader=SpanReader(),  # type: ignore[arg-type]
        hot_proof_source=object(),  # type: ignore[arg-type]
        now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        new_build_token=lambda: build_token,
        project_tenant_binding_probe=_project_bindings,
        _factory_authority=_RUNTIME_FACTORY_AUTHORITY,
    )

    output = runtime.reconcile_non_postgres(
        request,
        SimpleNamespace(adapter_results=()),
    )

    assert output["span_definition_rows"] == 0
    assert len(captured_loaders) == 1
    assert len(client.calls) == 1
    _sql, params, timeout_ms = client.calls[0]
    assert params["lineage_anchor_revision"] == context.catalog_revision
    assert params["prior_active_revision"] == 0
    assert params["has_prior_lineage"] == 0
    assert timeout_ms > 0


def test_activation_value_inventory_joins_exact_anchor_lineage_and_current_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    prior_build = "66666666-6666-4666-8666-666666666666"
    proof = {
        "value_rows": 9,
        "value_state_conflicts": 0,
        "activation_state_conflicts": 0,
        "activation_lineage_conflicts": 0,
        "activation_sequence_conflicts": 0,
        "activation_anchor_conflicts": 0,
        "active_lineage_rows": 3,
        "latest_active_revision": 4,
        "latest_active_sequence": 7,
        "latest_active_build_token": prior_build,
        "observed_lineage_anchor_revision": 2,
        "prior_active_matches": 1,
    }

    class CatalogClient:
        catalog_database = "property_catalog_dev_unit"

        def __init__(self) -> None:
            self.sql = ""
            self.params: dict[str, Any] = {}

        def query(
            self,
            sql: str,
            params: dict[str, Any],
            *,
            timeout_ms: int,
        ) -> tuple[dict[str, Any], ...]:
            assert timeout_ms > 0
            self.sql = sql
            self.params = params
            return (proof,)

    class CurrentBindings:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def read_current(self, **_kwargs: Any) -> tuple[object, ...]:
            return ()

    monkeypatch.setattr(dev_runtime, "ClickHouseCurrentBindingReader", CurrentBindings)
    client = CatalogClient()
    request = _request(execute=True)
    config = _unit_runtime_config()
    runtime = CheckedInPropertyCatalogDevRuntime(
        config=config,
        bound_request=request,
        provenance=_provenance_evidence(config=config, request=request),
        schema_client=object(),  # type: ignore[arg-type]
        catalog_client=client,  # type: ignore[arg-type]
        source_client=object(),  # type: ignore[arg-type]
        serializer=object(),  # type: ignore[arg-type]
        deadline=SharedCatalogDeadline(wall_ms=100_000),
        state_store=object(),  # type: ignore[arg-type]
        coordinator=object(),  # type: ignore[arg-type]
        lifecycle=object(),  # type: ignore[arg-type]
        span_reader=object(),  # type: ignore[arg-type]
        hot_proof_source=object(),  # type: ignore[arg-type]
        now=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        new_build_token=lambda: "55555555-5555-4555-8555-555555555555",
        project_tenant_binding_probe=_project_bindings,
        _factory_authority=_RUNTIME_FACTORY_AUTHORITY,
    )
    manifest = SimpleNamespace(
        catalog_revision=5,
        build_token="55555555-5555-4555-8555-555555555555",
        projection_version=1,
        lifecycle_mode=CatalogLifecycleMode.INCREMENTAL,
        lineage_anchor_revision=2,
    )
    prior = SimpleNamespace(
        catalog_revision=4,
        build_token=prior_build,
        activation_sequence=7,
        lineage_anchor=SimpleNamespace(active_revisions_since=2),
    )
    execution = SimpleNamespace(
        manifest=manifest,
        context=SimpleNamespace(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            catalog_epoch=1,
            catalog_revision=5,
            projection_version=1,
        ),
        lease=SimpleNamespace(build_token=manifest.build_token),
        prepared=SimpleNamespace(
            mode=LifecycleRunMode.INCREMENTAL,
            lifecycle_mode=CatalogLifecycleMode.INCREMENTAL,
            lineage_anchor_revision=2,
            prior_active=prior,
        ),
    )

    inventory = runtime._activation_inventory(execution)  # type: ignore[arg-type]

    assert inventory.value_rows == 9
    assert "INNER JOIN admitted_lineage AS lineage" in client.sql
    assert "span_attribute_value_catalog` AS catalog_value" in client.sql
    assert "SELECT catalog_value.*" in client.sql
    assert "span_attribute_value_catalog` AS value_rows" not in client.sql
    assert "any(candidate.build_token) AS build_token" in client.sql
    assert "min(source_value.first_seen) AS first_seen" in client.sql
    assert "max(source_value.last_seen) AS last_seen" in client.sql
    assert "catalog_revision >= %(lineage_anchor_revision)s" in client.sql
    assert "toUUID(%(build_token)s) AS build_token" in client.sql
    assert client.params["lineage_anchor_revision"] == 2
    assert client.params["prior_revision"] == 4

    proof["value_state_conflicts"] = 1
    with pytest.raises(PropertyCatalogDevRuntimeError, match="conflicting lineage"):
        runtime._activation_inventory(execution)  # type: ignore[arg-type]
