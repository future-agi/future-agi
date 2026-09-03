from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.management.commands import (
    ch25_property_catalog_lifecycle_controller as subject,
)
from tracer.services.clickhouse.v2.property_catalog import dev_runtime
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    DEV_ROLLOUT_ACK,
    DevRolloutError,
    DevRolloutRequest,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    _RUNTIME_FACTORY_AUTHORITY,
    CheckedInPropertyCatalogDevRuntime,
    PropertyCatalogDevRuntimeError,
    PropertyCatalogDevRuntimeFactory,
    PropertyCatalogProductionRuntimeFactory,
    _validate_production_writer_grants,
    require_checked_in_property_catalog_dev_runtime,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    ReservationStatus,
)
from tracer.services.clickhouse.v2.property_catalog.production_rollout import (
    PRODUCTION_LIFECYCLE_ACK,
    ProductionRolloutRequest,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PROPERTY_CATALOG_TABLES,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode
from tracer.services.clickhouse.v2.property_catalog.revision_fence_registry import (
    AtomicMultiTenantFenceFile,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
SECOND_ORG = "44444444-4444-4444-8444-444444444444"
SECOND_WORKSPACE = "55555555-5555-4555-8555-555555555555"
SECOND_PROJECT = "66666666-6666-4666-8666-666666666666"
LEGACY_PROJECT = "77777777-7777-4777-8777-777777777777"


def _production_request(**overrides: Any) -> ProductionRolloutRequest:
    values = {
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "environment": "production",
        "cloud_deployment": "US",
        "dev_identity": "prod:property-catalog-lifecycle",
        "source_database": "spans",
        "target_database": "property_catalog",
        "acknowledgement": PRODUCTION_LIFECYCLE_ACK,
        "execute": True,
    }
    values.update(overrides)
    return ProductionRolloutRequest(**values)


def _settings(tmp_path: Path, **overrides: Any) -> SimpleNamespace:
    values = {
        "ENV_TYPE": "production",
        "CLOUD_DEPLOYMENT": "US",
        "PROPERTY_CATALOG_LIFECYCLE_ENABLED": True,
        "PROPERTY_CATALOG_LIFECYCLE_BOOTSTRAP_ENABLED": False,
        "PROPERTY_CATALOG_LIFECYCLE_REPAIR_EXPIRED_INCOMPLETE": False,
        "PROPERTY_CATALOG_LIFECYCLE_ACK": PRODUCTION_LIFECYCLE_ACK,
        "PROPERTY_CATALOG_LIFECYCLE_IDENTITY": ("prod:property-catalog-lifecycle"),
        "PROPERTY_CATALOG_LIFECYCLE_SOURCE_DATABASE": "spans",
        "PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE": "property_catalog",
        "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "allowlist",
        "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": (WORKSPACE,),
        "PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY": str(tmp_path),
        "PROPERTY_CATALOG_LIFECYCLE_HEALTH_FILE": str(tmp_path / "health.json"),
        "PROPERTY_CATALOG_LIFECYCLE_POLL_SECONDS": 60,
        "PROPERTY_CATALOG_LIFECYCLE_FAILURE_BACKOFF_SECONDS": 30,
        "PROPERTY_CATALOG_LIFECYCLE_SCHEDULED_RECONCILE_WALL_MS": 1_200_000,
        "PROPERTY_CATALOG_LIFECYCLE_SPAN_WINDOW_DAYS": 366,
        "PROPERTY_CATALOG_LIFECYCLE_MAX_WALL_MS": 100_000,
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST": "catalog.internal",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT": 9000,
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_USER": "catalog_writer",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PASSWORD": "secret",
        "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 1,
        "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION": 1,
        "PROPERTY_CATALOG_LIFECYCLE_PRODUCER_STREAM_ID": (
            "44444444-4444-4444-8444-444444444444"
        ),
        "PROPERTY_CATALOG_LIFECYCLE_REVISION_FENCE_FILE": str(
            tmp_path / "revision-fence.json"
        ),
        "PROPERTY_CATALOG_LIFECYCLE_DRAIN_PROOF_FILE": str(
            tmp_path / "producer-drain-proof-v2.json"
        ),
        "PROPERTY_CATALOG_LIFECYCLE_PRODUCER_RETIREMENT_FILE": str(
            tmp_path / "producer-state-retirements-v1.json"
        ),
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES": (
            "catalog-0",
            "catalog-1",
            "catalog-2",
        ),
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_SOURCE_CH_HOSTNAMES": (
            "spans-0",
            "spans-1",
            "spans-2",
        ),
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_PG_DATABASE": "futureagi",
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_PG_USER": "catalog_source_reader",
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_PG_SERVER_ADDRESS": "10.0.0.1",
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_PG_SERVER_PORT": 5432,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _scope() -> subject.WorkspaceScope:
    return subject.WorkspaceScope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        is_default=False,
        project_ids=(PROJECT,),
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("environment", "development", "environment='production'"),
        ("cloud_deployment", "DEV", "supported cloud"),
        ("dev_identity", "dev:controller", "production control-plane identity"),
        (
            "target_database",
            "property_catalog_dev_unit",
            "configured production catalog database",
        ),
        ("acknowledgement", DEV_ROLLOUT_ACK, "exact production lifecycle"),
    ),
)
def test_production_request_rejects_cross_wired_scope(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(DevRolloutError, match=message):
        _production_request(**{field: value})


def test_dev_request_still_rejects_production_database() -> None:
    with pytest.raises(DevRolloutError, match="isolated DEV"):
        DevRolloutRequest(
            organization_id=ORG,
            workspace_id=WORKSPACE,
            environment="development",
            cloud_deployment="DEV",
            dev_identity="dev:unit-controller",
            source_database="spans",
            target_database="property_catalog",
            acknowledgement=DEV_ROLLOUT_ACK,
            execute=True,
        )


def test_production_factory_defaults_to_multi_tenant_fence_registry() -> None:
    factory = PropertyCatalogProductionRuntimeFactory(settings_object=SimpleNamespace())
    assert factory._fence_sink_factory is AtomicMultiTenantFenceFile  # noqa: SLF001


def _exact_writer_grants() -> tuple[str, ...]:
    return tuple(
        f"GRANT SELECT, INSERT ON property_catalog.{table} TO catalog_writer"
        for table in sorted(PROPERTY_CATALOG_TABLES)
    )


def test_production_writer_accepts_only_exact_direct_catalog_grants() -> None:
    _validate_production_writer_grants(
        _exact_writer_grants(),
        database="property_catalog",
        user="catalog_writer",
    )


@pytest.mark.parametrize(
    "grants",
    (
        _exact_writer_grants()[:-1],
        (
            *_exact_writer_grants()[:-1],
            "GRANT SELECT, INSERT ON property_catalog.* TO catalog_writer",
        ),
        (
            *_exact_writer_grants()[:-1],
            "GRANT SELECT ON property_catalog.property_definition_catalog "
            "TO catalog_writer",
        ),
        (
            *_exact_writer_grants(),
            "GRANT CREATE TABLE ON property_catalog.* TO catalog_writer",
        ),
        (
            *_exact_writer_grants()[:-1],
            "GRANT SELECT, INSERT ON spans.spans TO catalog_writer",
        ),
        (
            *_exact_writer_grants()[:-1],
            "GRANT SELECT, INSERT ON property_catalog.property_definition_catalog "
            "TO catalog_writer WITH GRANT OPTION",
        ),
        (
            *_exact_writer_grants()[:-1],
            "GRANT property_catalog_writer_role TO catalog_writer",
        ),
    ),
)
def test_production_writer_rejects_missing_broad_or_delegated_grants(
    grants: tuple[str, ...],
) -> None:
    with pytest.raises(PropertyCatalogDevRuntimeError, match="production ClickHouse"):
        _validate_production_writer_grants(
            grants,
            database="property_catalog",
            user="catalog_writer",
        )


def test_dev_factory_rejects_production_request_before_settings_or_clients() -> None:
    clients: list[str] = []

    def forbidden_client(config: Any) -> object:
        clients.append(config.database)
        raise AssertionError("production request must fail before client construction")

    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=object(),
        native_client_factory=forbidden_client,  # type: ignore[arg-type]
    )

    with pytest.raises(TypeError, match="does not accept ProductionRolloutRequest"):
        factory(_production_request())

    assert clients == []


def _forged_checked_in_runtime(
    *,
    request: DevRolloutRequest,
    deployment: str,
    database: str,
) -> CheckedInPropertyCatalogDevRuntime:
    runtime = object.__new__(CheckedInPropertyCatalogDevRuntime)
    object.__setattr__(runtime, "bound_request", request)
    object.__setattr__(
        runtime,
        "config",
        SimpleNamespace(
            deployment=deployment,
            catalog=SimpleNamespace(database=database),
        ),
    )
    object.__setattr__(runtime, "_factory_authority", _RUNTIME_FACTORY_AUTHORITY)
    object.__setattr__(runtime, "_scope_locked", True)
    return runtime


def _dev_request() -> DevRolloutRequest:
    return DevRolloutRequest(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        environment="development",
        cloud_deployment="DEV",
        dev_identity="dev:unit-controller",
        source_database="spans",
        target_database="property_catalog_dev_unit",
        acknowledgement=DEV_ROLLOUT_ACK,
        execute=True,
    )


def test_dev_runtime_guard_rejects_every_production_cross_wire() -> None:
    invalid = (
        _forged_checked_in_runtime(
            request=_production_request(),
            deployment="prod",
            database="property_catalog",
        ),
        _forged_checked_in_runtime(
            request=_dev_request(),
            deployment="prod",
            database="property_catalog",
        ),
        _forged_checked_in_runtime(
            request=_dev_request(),
            deployment="dev",
            database="property_catalog",
        ),
    )

    for runtime in invalid:
        with pytest.raises(
            PropertyCatalogDevRuntimeError,
            match="reviewed checked-in DEV runtime",
        ):
            require_checked_in_property_catalog_dev_runtime(runtime)

    valid = _forged_checked_in_runtime(
        request=_dev_request(),
        deployment="dev",
        database="property_catalog_dev_unit",
    )
    assert require_checked_in_property_catalog_dev_runtime(valid) is valid


def test_controller_config_is_production_exact_and_bootstrap_off(
    tmp_path: Path,
) -> None:
    config = subject.controller_config(settings_object=_settings(tmp_path))
    assert config.target_database == "property_catalog"
    assert config.workspace_scope_mode == "allowlist"
    assert config.workspace_ids == (WORKSPACE,)
    assert config.revision_fence_file == str(tmp_path / "revision-fence.json")
    assert config.bootstrap_enabled is False


def test_global_discovery_batches_projects_and_preserves_exact_tenancy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQuery:
        def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
            self.rows = rows
            self.filters: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def filter(self, *args: object, **kwargs: object) -> FakeQuery:
            self.filters.append((args, kwargs))
            return self

        def order_by(self, *_fields: str) -> FakeQuery:
            return self

        def values_list(self, *_fields: str) -> tuple[tuple[object, ...], ...]:
            return self.rows

    workspace_query = FakeQuery(
        (
            (WORKSPACE, ORG, True),
            (SECOND_WORKSPACE, SECOND_ORG, False),
        )
    )
    project_query = FakeQuery(
        (
            (PROJECT, ORG, WORKSPACE),
            (LEGACY_PROJECT, ORG, None),
            (SECOND_PROJECT, SECOND_ORG, SECOND_WORKSPACE),
            ("88888888-8888-4888-8888-888888888888", SECOND_ORG, WORKSPACE),
        )
    )
    monkeypatch.setattr(
        subject,
        "Workspace",
        SimpleNamespace(no_workspace_objects=workspace_query),
    )
    monkeypatch.setattr(
        subject,
        "Project",
        SimpleNamespace(no_workspace_objects=project_query),
    )

    scopes, skipped = subject.discover_workspace_scopes(None)

    assert workspace_query.filters == [((), {"is_active": True})]
    assert len(project_query.filters) == 1
    assert tuple(scope.workspace_id for scope in scopes) == (
        WORKSPACE,
        SECOND_WORKSPACE,
    )
    assert scopes[0].project_ids == (PROJECT, LEGACY_PROJECT)
    assert scopes[0].legacy_project_ids == (LEGACY_PROJECT,)
    assert scopes[1].project_ids == (SECOND_PROJECT,)
    assert skipped == ()


def test_mutating_cycle_reconciles_exact_workspace_inventory_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings(tmp_path)
    config = subject.controller_config(settings_object=settings_object)
    calls: list[tuple[str, object, tuple[str, ...]]] = []

    class FakeFenceFile:
        def __init__(self, path: str, *, now: object) -> None:
            self.path = path
            self.now = now

        def reconcile_authorized_workspaces(
            self,
            workspace_ids: tuple[str, ...],
        ) -> int:
            calls.append((self.path, self.now, workspace_ids))
            return 0

    monkeypatch.setattr(subject, "AtomicMultiTenantFenceFile", FakeFenceFile)
    monkeypatch.setattr(subject, "run_workspace", lambda **_kwargs: {})
    result = subject.run_cycle(
        scopes=(_scope(),),
        skipped=(),
        settings_object=settings_object,
        config=config,
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        status_only=False,
        stop=threading.Event(),
        on_error=lambda _workspace_id, _exc: None,
    )

    assert result.processed == (WORKSPACE,)
    assert len(calls) == 1
    assert calls[0][0] == config.revision_fence_file
    assert calls[0][2] == config.workspace_ids


def test_global_cycle_reconciles_discovered_workspace_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings(
        tmp_path,
        PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE="all",
        PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST=(),
    )
    config = subject.controller_config(settings_object=settings_object)
    calls: list[tuple[str, ...]] = []

    class FakeFenceFile:
        def __init__(self, _path: str, *, now: object) -> None:
            assert now is not None

        def reconcile_authorized_workspaces(
            self,
            workspace_ids: tuple[str, ...],
        ) -> int:
            calls.append(workspace_ids)
            return 0

    monkeypatch.setattr(subject, "AtomicMultiTenantFenceFile", FakeFenceFile)
    monkeypatch.setattr(subject, "run_workspace", lambda **_kwargs: {})
    result = subject.run_cycle(
        scopes=(_scope(),),
        skipped=(),
        settings_object=settings_object,
        config=config,
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        status_only=False,
        stop=threading.Event(),
        on_error=lambda _workspace_id, _exc: None,
    )

    assert config.workspace_scope_mode == "all"
    assert config.workspace_ids == ()
    assert result.processed == (WORKSPACE,)
    assert calls == [(WORKSPACE,)]


def test_allowlist_scope_has_no_fixed_workspace_count_cap(tmp_path: Path) -> None:
    workspace_ids = tuple(
        f"00000000-0000-4000-8000-{index:012x}" for index in range(1, 301)
    )
    config = subject.controller_config(
        settings_object=_settings(
            tmp_path,
            PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST=workspace_ids,
        )
    )

    assert config.workspace_ids == workspace_ids


def test_status_only_cycle_does_not_reconcile_fence_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings(tmp_path)
    config = subject.controller_config(settings_object=settings_object)

    class UnexpectedFenceFile:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("status-only cycle attempted a fence mutation")

    monkeypatch.setattr(subject, "AtomicMultiTenantFenceFile", UnexpectedFenceFile)
    monkeypatch.setattr(subject, "run_workspace", lambda **_kwargs: {})
    result = subject.run_cycle(
        scopes=(_scope(),),
        skipped=(),
        settings_object=settings_object,
        config=config,
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        status_only=True,
        stop=threading.Event(),
        on_error=lambda _workspace_id, _exc: None,
    )

    assert result.processed == (WORKSPACE,)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"ENV_TYPE": "development"}, "ENV_TYPE=production"),
        ({"PROPERTY_CATALOG_LIFECYCLE_ENABLED": False}, "must be true"),
        ({"PROPERTY_CATALOG_LIFECYCLE_ACK": "wrong"}, "acknowledgement"),
        (
            {"PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE": "default"},
            "configured production database",
        ),
        (
            {"PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": ()},
            "non-empty unique",
        ),
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
    ),
)
def test_controller_config_fails_closed(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(subject.ProductionLifecycleControllerError, match=message):
        subject.controller_config(settings_object=_settings(tmp_path, **overrides))


def test_workspace_overlay_binds_prod_settings_to_shared_runtime(
    tmp_path: Path,
) -> None:
    settings_object = _settings(tmp_path)
    config = subject.controller_config(settings_object=settings_object)
    overlay = subject.workspace_settings_overlay(
        settings_object=settings_object,
        config=config,
        scope=_scope(),
        now=datetime(2026, 8, 26, 12, 34, tzinfo=UTC),
    )

    assert overlay.PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE == "property_catalog"
    assert overlay.PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST == (PROJECT,)
    assert overlay.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES == (
        "catalog-0",
        "catalog-1",
        "catalog-2",
    )
    assert overlay.PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAMES == (
        "spans-0",
        "spans-1",
        "spans-2",
    )
    assert overlay.PROPERTY_CATALOG_DEV_SPAN_UNTIL == "2026-08-26T12:00:00Z"
    assert overlay.PROPERTY_CATALOG_DEV_SPAN_SINCE == "2025-08-25T12:00:00Z"


def test_execute_request_requires_explicit_expired_revision_repair_gate(
    tmp_path: Path,
) -> None:
    settings_object = _settings(
        tmp_path,
        PROPERTY_CATALOG_LIFECYCLE_REPAIR_EXPIRED_INCOMPLETE=True,
    )
    config = subject.controller_config(settings_object=settings_object)
    overlay = subject.workspace_settings_overlay(
        settings_object=settings_object,
        config=config,
        scope=_scope(),
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
    )

    execute = subject.rollout_request(
        scope=_scope(),
        proxy=overlay,
        config=config,
    )
    status = subject.rollout_request(
        scope=_scope(),
        proxy=overlay,
        config=config,
        status=True,
    )

    assert execute.repair_expired_incomplete is True
    assert status.repair_expired_incomplete is False


def test_initial_backfill_requires_one_shot_bootstrap_gate() -> None:
    with pytest.raises(
        subject.ProductionLifecycleControllerError,
        match="requires --once",
    ):
        subject._validate_initial_backfill_mode(  # noqa: SLF001
            once=False,
            status_only=False,
            bootstrap_enabled=True,
            initial_backfill_wall_ms=120_000,
        )
    with pytest.raises(
        subject.ProductionLifecycleControllerError,
        match="bootstrap gate",
    ):
        subject._validate_initial_backfill_mode(  # noqa: SLF001
            once=True,
            status_only=False,
            bootstrap_enabled=False,
            initial_backfill_wall_ms=120_000,
        )


def test_bootstrap_request_receives_explicit_initial_wall(tmp_path: Path) -> None:
    settings_object = _settings(
        tmp_path,
        PROPERTY_CATALOG_LIFECYCLE_BOOTSTRAP_ENABLED=True,
    )
    config = subject.controller_config(settings_object=settings_object)
    overlay = subject.workspace_settings_overlay(
        settings_object=settings_object,
        config=config,
        scope=_scope(),
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
    )

    request = subject.rollout_request(
        scope=_scope(),
        proxy=overlay,
        config=config,
        initial_backfill_wall_ms=120_000,
    )

    assert request.initial_backfill_wall_ms == 120_000


def test_bootstrap_retry_does_not_create_incremental_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings(
        tmp_path,
        PROPERTY_CATALOG_LIFECYCLE_BOOTSTRAP_ENABLED=True,
    )
    config = subject.controller_config(settings_object=settings_object)

    @contextmanager
    def fake_runtime(**_kwargs: Any):
        yield object()

    evidence = {"schema_ready": True, "active": True, "catalog_revision": 1}
    monkeypatch.setattr(subject, "managed_runtime", fake_runtime)
    monkeypatch.setattr(
        subject,
        "run_configured_production_rollout",
        lambda **_kwargs: SimpleNamespace(
            evidence=(SimpleNamespace(evidence=evidence),)
        ),
    )
    monkeypatch.setattr(
        subject,
        "run_workspace_reconcile",
        lambda **_kwargs: pytest.fail("bootstrap retry reconciled active workspace"),
    )

    result = subject.run_workspace(
        scope=_scope(),
        settings_object=settings_object,
        config=config,
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        status_only=False,
        initial_backfill_wall_ms=120_000,
    )

    assert result == evidence


def test_active_workspace_runs_incremental_reconcile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings(tmp_path)
    config = subject.controller_config(settings_object=settings_object)
    runtimes: list[object] = []
    cancellation_probes: list[Any] = []
    stop = threading.Event()

    @contextmanager
    def fake_runtime(**kwargs: Any):
        runtime = object()
        runtimes.append(runtime)
        cancellation_probes.append(kwargs["cancellation_probe"])
        yield runtime

    status_result = SimpleNamespace(
        evidence=(SimpleNamespace(evidence={"schema_ready": True, "active": True}),)
    )
    monkeypatch.setattr(subject, "managed_runtime", fake_runtime)
    monkeypatch.setattr(
        subject,
        "run_configured_production_rollout",
        lambda **_kwargs: status_result,
    )
    observed: dict[str, object] = {}

    def reconcile(**kwargs: Any) -> dict[str, object]:
        observed.update(kwargs)
        return {"reconciled": True}

    monkeypatch.setattr(subject, "run_workspace_reconcile", reconcile)
    result = subject.run_workspace(
        scope=_scope(),
        settings_object=settings_object,
        config=config,
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        status_only=False,
        stop=stop,
    )

    assert result == {"reconciled": True}
    assert observed["mode"] is ReconcileMode.INCREMENTAL
    assert len(runtimes) == 2
    assert all(probe() is False for probe in cancellation_probes)
    stop.set()
    assert all(probe() is True for probe in cancellation_probes)


def test_production_incremental_reconcile_uses_production_schema_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _production_request()
    schema_calls: list[tuple[str, str]] = []
    execution = SimpleNamespace(
        prepared=SimpleNamespace(
            reservation_status=ReservationStatus.FENCED,
            lifecycle_mode="auto",
            lineage_anchor_revision=7,
        ),
        qualification=SimpleNamespace(qualified=True),
        lease=SimpleNamespace(build_lease_sha256="a" * 64),
    )

    def verify_schema(
        _client: object,
        *,
        target_database: str,
        deployment: str,
    ) -> dict[str, object]:
        schema_calls.append((target_database, deployment))
        return {}

    monkeypatch.setattr(dev_runtime, "verify_runtime_catalog_schema", verify_schema)
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
        "_prepare_revision",
        lambda *_args, **_kwargs: execution,
    )
    monkeypatch.setattr(
        CheckedInPropertyCatalogDevRuntime,
        "activate",
        lambda *_args, **_kwargs: {"activated": True},
    )
    runtime = object.__new__(CheckedInPropertyCatalogDevRuntime)
    object.__setattr__(runtime, "config", SimpleNamespace(deployment="prod"))
    object.__setattr__(runtime, "schema_client", object())

    result = runtime.reconcile_workspace(request, mode=ReconcileMode.INCREMENTAL)

    assert schema_calls == [("property_catalog", "prod")]
    assert result["activated"] is True


def test_inactive_workspace_requires_separate_bootstrap_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings(tmp_path)
    config = subject.controller_config(settings_object=settings_object)

    @contextmanager
    def fake_runtime(**_kwargs: Any):
        yield object()

    monkeypatch.setattr(subject, "managed_runtime", fake_runtime)
    monkeypatch.setattr(
        subject,
        "run_configured_production_rollout",
        lambda **_kwargs: SimpleNamespace(
            evidence=(
                SimpleNamespace(evidence={"schema_ready": True, "active": False}),
            )
        ),
    )

    with pytest.raises(
        subject.ProductionLifecycleControllerError,
        match="bootstrap is disabled",
    ):
        subject.run_workspace(
            scope=_scope(),
            settings_object=settings_object,
            config=config,
            now=datetime(2026, 8, 26, 12, tzinfo=UTC),
            status_only=False,
        )


def test_health_file_is_private_canonical_and_atomically_replaceable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "health.json"
    observed_at = datetime(2026, 8, 26, 12, tzinfo=UTC)
    subject._write_health(  # noqa: SLF001
        str(path),
        healthy=True,
        observed_at=observed_at,
        detail={"processed": [WORKSPACE]},
    )
    first = path.read_bytes()
    subject._write_health(  # noqa: SLF001
        str(path),
        healthy=False,
        observed_at=observed_at,
        detail={"cycle_error": "injected"},
    )

    assert first.endswith(b"\n")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes().endswith(b"\n")
    assert not tuple(tmp_path.glob(".property-catalog-lifecycle-health-*"))


def test_health_file_retries_partial_kernel_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "health.json"
    original_write = subject.os.write
    calls = 0

    def partial_write(descriptor: int, value: object) -> int:
        nonlocal calls
        calls += 1
        payload = bytes(value)
        return original_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(subject.os, "write", partial_write)
    subject._write_health(  # noqa: SLF001
        str(path),
        healthy=True,
        observed_at=datetime(2026, 8, 26, 12, tzinfo=UTC),
        detail={"processed": [WORKSPACE]},
    )

    assert calls > 1
    assert path.read_bytes().endswith(b"\n")


def test_workspace_scope_maps_only_default_legacy_projects() -> None:
    scope = _scope()
    with pytest.raises(subject.ProductionLifecycleControllerError, match="default"):
        replace(scope, legacy_project_ids=(PROJECT,))
