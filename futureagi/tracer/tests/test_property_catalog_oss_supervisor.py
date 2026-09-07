from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from django.core.management.base import CommandError

from tracer.management.commands import ch25_property_catalog_oss_supervisor as subject
from tracer.services.clickhouse.v2.property_catalog import dev_rollout
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    ClickHouseDevIdentity,
    DevProvenanceObservation,
    PostgresDevIdentity,
    PostgresProjectTenantBinding,
    PostgresWorkspaceProjectInventory,
)

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE_DEFAULT = "33333333-3333-4333-8333-333333333331"
WORKSPACE_EMPTY = "33333333-3333-4333-8333-333333333332"
WORKSPACE_OTHER = "44444444-4444-4444-8444-444444444444"
PROJECT_LEGACY = "55555555-5555-4555-8555-555555555551"
PROJECT_DEFAULT = "55555555-5555-4555-8555-555555555552"
PROJECT_OTHER = "66666666-6666-4666-8666-666666666666"
PRODUCER = "77777777-7777-4777-8777-777777777777"


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "ENV_TYPE": "development",
        "CLOUD_DEPLOYMENT": "",
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": "default",
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": "property_catalog_dev_oss",
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": "property_catalog_dev_oss",
        "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS": 1_200_000,
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": "/runtime/fence.json",
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": (
            "/runtime/producer-drain-proof-v2.json"
        ),
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": (
            "/runtime/producer-state-retirements-v1.json"
        ),
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": "/runtime",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _environ(**overrides: str) -> dict[str, str]:
    values = {
        subject.OSS_SUPERVISOR_ACK_ENV: subject.OSS_SUPERVISOR_ACK,
        subject.OSS_CATALOG_EPOCH_ENV: "7",
        subject.OSS_PROJECTION_VERSION_ENV: "3",
        subject.OSS_PRODUCER_STREAM_ID_ENV: PRODUCER,
    }
    values.update(overrides)
    return values


def _config() -> subject.OssSupervisorConfig:
    return subject.OssSupervisorConfig(
        source_database="default",
        target_database="property_catalog_dev_oss",
        catalog_epoch=7,
        projection_version=3,
        producer_stream_id=PRODUCER,
        revision_fence_file="/runtime/fence.json",
        poll_seconds=60,
        workspace_batch_size=512,
        project_batch_size=512,
        scheduled_reconcile_wall_ms=1_200_000,
    )


def _scope(
    workspace_id: str = WORKSPACE_DEFAULT,
    *,
    organization_id: str = ORG_A,
    project_ids: tuple[str, ...] = (PROJECT_DEFAULT,),
    is_default: bool = False,
    legacy_project_ids: tuple[str, ...] = (),
) -> subject.WorkspaceScope:
    return subject.WorkspaceScope(
        organization_id=organization_id,
        workspace_id=workspace_id,
        is_default=is_default,
        project_ids=project_ids,
        legacy_project_ids=legacy_project_ids,
    )


def _observation() -> DevProvenanceObservation:
    return DevProvenanceObservation(
        writer_clickhouse=ClickHouseDevIdentity(
            hostname="writer-host",
            database="default",
            user="catalog_writer",
            readonly_value=0,
            readonly_locked=False,
        ),
        source_clickhouse=ClickHouseDevIdentity(
            hostname="source-host",
            database="default",
            user="source_reader",
            readonly_value=1,
            readonly_locked=True,
        ),
        postgres=PostgresDevIdentity(
            database="futureagi",
            user="catalog_reader",
            session_user="catalog_reader",
            server_address="10.0.0.8",
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


def _stub_lifecycle_runtime(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Exercise the real rollout ordering with in-memory runtime IO doubles."""
    state = SimpleNamespace(active=set(), events=[], requests=[], failures={})

    class Runtime:
        def __init__(self, *, request: Any, proxy: Any, scope: Any) -> None:
            self.workspace_id = scope.workspace_id
            self.request = request
            state.requests.append((request, proxy))

        def step(self, name: str) -> dict[str, Any]:
            state.events.append((self.workspace_id, name))
            failure = state.failures.pop((self.workspace_id, name), None)
            if failure is not None:
                raise failure
            return {}

        def close(self) -> None:
            self.step("close-status" if self.request.status else "close-execute")

        def status(self, request: Any) -> dict[str, Any]:
            assert request.status and not request.execute
            self.step("status")
            return {
                "schema_ready": True,
                "active": self.workspace_id in state.active,
            }

        def apply_schema(self, request: Any) -> dict[str, Any]:
            assert request.execute and not request.status
            return self.step("schema")

        def backfill(self, request: Any) -> dict[str, Any]:
            assert not request.repair_expired_incomplete
            return self.step("backfill")

        def postgres_reconciler(self, request: Any) -> None:
            self.step("postgres")

        def postgres_request_factory(self, request: Any) -> None:
            return None

        def postgres_adapters(self, request: Any) -> None:
            return None

        def postgres_snapshot_guard(self, request: Any) -> None:
            return None

        def reconcile_non_postgres(self, request: Any, postgres: Any) -> dict[str, Any]:
            return self.step("reconcile")

        def qualify(self, request: Any) -> dict[str, Any]:
            return self.step("qualify")

        def activate(self, request: Any) -> dict[str, Any]:
            result = self.step("activate")
            state.active.add(self.workspace_id)
            return result

        def verify_schema(self, request: Any) -> dict[str, Any]:
            return self.step("verify-schema")

        def reconcile_workspace(self, request: Any, *, mode: Any) -> dict[str, Any]:
            assert mode is subject.ReconcileMode.INCREMENTAL
            assert self.workspace_id in state.active
            return self.step("incremental")

    monkeypatch.setattr(subject, "_runtime", Runtime)
    monkeypatch.setattr(
        dev_rollout,
        "reconcile_postgres_revision",
        lambda **_kwargs: SimpleNamespace(
            adapter_results=(), postgres_snapshot_opened=True
        ),
    )
    return state


@pytest.mark.parametrize(
    ("settings_overrides", "environment_overrides", "message"),
    (
        ({"ENV_TYPE": "production"}, {}, "ENV_TYPE=development"),
        ({"CLOUD_DEPLOYMENT": "DEV"}, {}, "CLOUD_DEPLOYMENT"),
        ({}, {subject.OSS_SUPERVISOR_ACK_ENV: "wrong"}, "acknowledgement"),
        (
            {"PROPERTY_CATALOG_DEV_TARGET_DATABASE": "default"},
            {},
            "safe isolated",
        ),
        (
            {"PROPERTY_CATALOG_DEV_SOURCE_DATABASE": "property_catalog_dev_oss"},
            {},
            "must differ",
        ),
        (
            {"PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": "property_catalog_dev_other"},
            {},
            "must equal",
        ),
        ({}, {subject.OSS_CATALOG_EPOCH_ENV: ""}, "must be set explicitly"),
        ({}, {subject.OSS_PROJECTION_VERSION_ENV: "0"}, "positive UInt16"),
        ({}, {subject.OSS_PRODUCER_STREAM_ID_ENV: "not-a-uuid"}, "must be a UUID"),
    ),
)
def test_supervisor_gate_fails_closed(
    settings_overrides: dict[str, Any],
    environment_overrides: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(subject.OssPropertyCatalogSupervisorError, match=message):
        subject._supervisor_config(
            settings_object=_settings(**settings_overrides),
            environ=_environ(**environment_overrides),
        )


def test_supervisor_gate_accepts_only_bounded_explicit_local_configuration() -> None:
    config = subject._supervisor_config(
        settings_object=_settings(),
        environ=_environ(
            **{subject.OSS_SUPERVISOR_POLL_SECONDS_ENV: "300"},
            **{subject.OSS_SUPERVISOR_WORKSPACE_BATCH_SIZE_ENV: "128"},
            **{subject.OSS_SUPERVISOR_PROJECT_BATCH_SIZE_ENV: "64"},
        ),
    )

    assert config == subject.OssSupervisorConfig(
        source_database="default",
        target_database="property_catalog_dev_oss",
        catalog_epoch=7,
        projection_version=3,
        producer_stream_id=PRODUCER,
        revision_fence_file="/runtime/fence.json",
        poll_seconds=300,
        workspace_batch_size=128,
        project_batch_size=64,
        scheduled_reconcile_wall_ms=1_200_000,
    )

    with pytest.raises(
        subject.OssPropertyCatalogSupervisorError,
        match=r"\[5, 3600\]",
    ):
        subject._supervisor_config(
            settings_object=_settings(),
            environ=_environ(
                **{subject.OSS_SUPERVISOR_POLL_SECONDS_ENV: "3601"},
            ),
        )


def test_supervisor_accepts_safe_legacy_target_name() -> None:
    config = subject._supervisor_config(
        settings_object=_settings(
            PROPERTY_CATALOG_DEV_TARGET_DATABASE="legacy_catalog_snapshot",
            PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE="legacy_catalog_snapshot",
        ),
        environ=_environ(),
    )

    assert config.target_database == "legacy_catalog_snapshot"


class _RowsQuery:
    def __init__(
        self, rows: list[tuple[Any, ...]], calls: list[tuple[Any, ...]]
    ) -> None:
        self.rows = rows
        self.calls = calls

    def order_by(self, *fields: str) -> _RowsQuery:
        self.calls.append(("order_by", *fields))
        return self

    def values_list(self, *fields: str) -> _RowsQuery:
        self.calls.append(("values_list", *fields))
        return self

    def iterator(self, *, chunk_size: int) -> Any:
        self.calls.append(("iterator", chunk_size))
        return iter(self.rows)

    def __getitem__(self, item: slice) -> list[tuple[Any, ...]]:
        self.calls.append(("slice", item.start, item.stop))
        return self.rows[item]


class _RowsManager:
    def __init__(self, batches: list[list[tuple[Any, ...]]]) -> None:
        self.batches = list(batches)
        self.calls: list[tuple[Any, ...]] = []

    def filter(self, *args: Any, **kwargs: Any) -> _RowsQuery:
        self.calls.append(("filter", args, kwargs))
        return _RowsQuery(self.batches.pop(0), self.calls)


def test_discovery_is_deterministic_maps_legacy_only_to_default_and_includes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_manager = _RowsManager(
        [
            [
                (WORKSPACE_DEFAULT, ORG_A, True),
                (WORKSPACE_EMPTY, ORG_A, False),
                (WORKSPACE_OTHER, ORG_B, False),
            ]
        ]
    )
    project_manager = _RowsManager(
        [
            [
                (PROJECT_DEFAULT, WORKSPACE_DEFAULT),
                (PROJECT_LEGACY, None),
            ],
            [],
            [(PROJECT_OTHER, WORKSPACE_OTHER)],
        ]
    )
    monkeypatch.setattr(
        subject,
        "Workspace",
        SimpleNamespace(no_workspace_objects=workspace_manager),
    )
    monkeypatch.setattr(
        subject,
        "Project",
        SimpleNamespace(no_workspace_objects=project_manager),
    )

    scopes, skipped = subject._discover_workspace_scopes(
        workspace_batch_size=512,
        project_batch_size=512,
    )

    assert [scope.workspace_id for scope in scopes] == [
        WORKSPACE_DEFAULT,
        WORKSPACE_EMPTY,
        WORKSPACE_OTHER,
    ]
    assert scopes[0].project_ids == (PROJECT_LEGACY, PROJECT_DEFAULT)
    assert scopes[0].legacy_project_ids == (PROJECT_LEGACY,)
    assert scopes[1].project_ids == ()
    assert scopes[2].project_ids == (PROJECT_OTHER,)
    assert skipped == ()
    assert all(
        call[2] == {"trace_type": "observe"}
        for call in project_manager.calls
        if call[0] == "filter"
    )
    assert workspace_manager.calls[0] == ("filter", (), {"is_active": True})
    assert ("order_by", "organization_id", "id") in workspace_manager.calls
    assert all(
        call == ("order_by", "id")
        for call in project_manager.calls
        if call[0] == "order_by"
    )


def test_discovery_has_no_total_workspace_or_project_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_rows = [(str(UUID(int=index + 10)), ORG_A, False) for index in range(3)]
    project_rows = [
        [(str(UUID(int=index + 100)), workspace_rows[index][0])] for index in range(3)
    ]
    workspace_manager = _RowsManager([workspace_rows])
    project_manager = _RowsManager(project_rows)
    monkeypatch.setattr(
        subject,
        "Workspace",
        SimpleNamespace(no_workspace_objects=workspace_manager),
    )
    monkeypatch.setattr(
        subject,
        "Project",
        SimpleNamespace(no_workspace_objects=project_manager),
    )
    scopes, skipped = subject._discover_workspace_scopes(
        workspace_batch_size=2,
        project_batch_size=1,
    )

    assert len(scopes) == 3
    assert skipped == ()
    assert ("iterator", 2) in workspace_manager.calls
    assert [call for call in project_manager.calls if call[0] == "iterator"] == [
        ("iterator", 1),
        ("iterator", 1),
        ("iterator", 1),
    ]


def test_workspace_routes_active_catalog_to_incremental_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    status_result = SimpleNamespace(
        evidence=(SimpleNamespace(evidence={"schema_ready": True, "active": True}),)
    )

    monkeypatch.setattr(subject, "_workspace_settings_proxy", lambda **_kwargs: "proxy")

    def request(**kwargs: Any) -> str:
        if kwargs.get("status"):
            value = "status-request"
        elif kwargs.get("scheduled_reconcile_wall_ms"):
            value = "incremental-request"
        else:
            value = "initial-request"
        calls.append(("request", value))
        return value

    class Runtime:
        def __init__(self, request: str) -> None:
            self.request = request

        def close(self) -> None:
            calls.append(("close", self.request))

    monkeypatch.setattr(subject, "_rollout_request", request)
    monkeypatch.setattr(
        subject,
        "_runtime",
        lambda *, request, **_kwargs: Runtime(request),
    )

    def configured(*, request: str, runtime: Runtime) -> Any:
        calls.append(("configured", (request, runtime.request)))
        return status_result if request == "status-request" else SimpleNamespace()

    monkeypatch.setattr(subject, "run_configured_dev_rollout", configured)
    monkeypatch.setattr(
        subject,
        "run_workspace_reconcile",
        lambda **kwargs: calls.append(("incremental", kwargs)),
    )

    processed = subject._run_workspace(
        scope=_scope(),
        settings_object=_settings(),
        config=_config(),
        observation=SimpleNamespace(),  # type: ignore[arg-type]
        now=datetime(2026, 8, 26, 12, 34, tzinfo=UTC),
    )

    assert calls[0] == ("request", "status-request")
    assert calls[1][0] == "configured"
    assert ("close", "status-request") in calls
    assert processed is True
    assert any(call[0] == "incremental" for call in calls)
    assert ("close", "incremental-request") in calls
    assert not any(call == ("request", "initial-request") for call in calls)


def test_explicit_backfill_skips_already_active_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(subject, "_workspace_settings_proxy", lambda **_kwargs: "proxy")
    monkeypatch.setattr(subject, "_rollout_request", lambda **_kwargs: "status")

    class Runtime:
        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(subject, "_runtime", lambda **_kwargs: Runtime())
    monkeypatch.setattr(
        subject,
        "run_configured_dev_rollout",
        lambda **_kwargs: SimpleNamespace(
            evidence=(SimpleNamespace(evidence={"schema_ready": True, "active": True}),)
        ),
    )
    monkeypatch.setattr(
        subject,
        "run_workspace_reconcile",
        lambda **_kwargs: calls.append("incremental"),
    )

    processed = subject._run_workspace(
        scope=_scope(),
        settings_object=_settings(),
        config=_config(),
        observation=SimpleNamespace(),  # type: ignore[arg-type]
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        bootstrap_only=True,
        initial_backfill_wall_ms=1_740_000,
    )

    assert processed is False
    assert calls == ["close"]


@pytest.mark.parametrize("bootstrap_only", (False, True))
def test_inactive_workspace_bootstraps_in_ordinary_and_operator_modes(
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_only: bool,
) -> None:
    calls: list[tuple[str, Any]] = []
    status_result = SimpleNamespace(
        evidence=(SimpleNamespace(evidence={"schema_ready": True, "active": False}),)
    )

    monkeypatch.setattr(subject, "_workspace_settings_proxy", lambda **_kwargs: "proxy")

    def request(**kwargs: Any) -> str:
        value = "status-request" if kwargs.get("status") else "initial-request"
        calls.append(("request", value))
        return value

    class Runtime:
        def __init__(self, request: str) -> None:
            self.request = request

        def close(self) -> None:
            calls.append(("close", self.request))

    monkeypatch.setattr(subject, "_rollout_request", request)
    monkeypatch.setattr(
        subject,
        "_runtime",
        lambda *, request, **_kwargs: Runtime(request),
    )

    def configured(*, request: str, runtime: Runtime) -> Any:
        calls.append(("configured", (request, runtime.request)))
        return status_result if request == "status-request" else SimpleNamespace()

    monkeypatch.setattr(subject, "run_configured_dev_rollout", configured)

    processed = subject._run_workspace(
        scope=_scope(),
        settings_object=_settings(),
        config=_config(),
        observation=SimpleNamespace(),  # type: ignore[arg-type]
        now=datetime(2026, 8, 26, 12, 34, tzinfo=UTC),
        bootstrap_only=bootstrap_only,
    )

    assert calls[0] == ("request", "status-request")
    assert ("close", "status-request") in calls
    assert processed is True
    assert ("request", "initial-request") in calls
    assert ("close", "initial-request") in calls


def test_workspace_refuses_initial_rollout_when_schema_is_not_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class Runtime:
        def close(self) -> None:
            closed.append("status")

    monkeypatch.setattr(subject, "_workspace_settings_proxy", lambda **_kwargs: "proxy")
    monkeypatch.setattr(subject, "_rollout_request", lambda **_kwargs: "status")
    monkeypatch.setattr(subject, "_runtime", lambda **_kwargs: Runtime())
    monkeypatch.setattr(
        subject,
        "run_configured_dev_rollout",
        lambda **_kwargs: SimpleNamespace(
            evidence=(
                SimpleNamespace(evidence={"schema_ready": False, "active": False}),
            )
        ),
    )

    with pytest.raises(subject.OssPropertyCatalogSupervisorError, match="not prepared"):
        subject._run_workspace(
            scope=_scope(),
            settings_object=_settings(),
            config=_config(),
            observation=SimpleNamespace(),  # type: ignore[arg-type]
            now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        )
    assert closed == ["status"]


def test_workspace_settings_are_isolated_and_hour_bounded() -> None:
    observation = SimpleNamespace(
        writer_clickhouse=SimpleNamespace(hostname="writer-host"),
        source_clickhouse=SimpleNamespace(hostname="source-host"),
        postgres=SimpleNamespace(
            database="futureagi",
            user="catalog_reader",
            server_address="10.0.0.8",
            server_port=5432,
        ),
    )
    now = datetime(2026, 8, 26, 12, 47, 33, tzinfo=UTC)
    first = subject._workspace_settings_proxy(
        settings_object=_settings(),
        config=_config(),
        scope=_scope(project_ids=(PROJECT_DEFAULT,)),
        observation=observation,  # type: ignore[arg-type]
        now=now,
    )
    second = subject._workspace_settings_proxy(
        settings_object=_settings(),
        config=_config(),
        scope=_scope(
            workspace_id=WORKSPACE_OTHER,
            organization_id=ORG_B,
            project_ids=(PROJECT_OTHER,),
        ),
        observation=observation,  # type: ignore[arg-type]
        now=now,
    )

    assert first.PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST == (PROJECT_DEFAULT,)
    assert second.PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST == (PROJECT_OTHER,)
    assert first.PROPERTY_CATALOG_DEV_SPAN_UNTIL == "2026-08-26T12:00:00Z"
    assert first.PROPERTY_CATALOG_DEV_SPAN_SINCE == "2025-08-25T12:00:00Z"
    assert first.PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS == "10.0.0.8"
    assert first.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME == "writer-host"


def test_cycle_reconciles_shared_fence_inventory_then_isolates_workspace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _scope()
    second = _scope(
        workspace_id=WORKSPACE_OTHER,
        organization_id=ORG_B,
        project_ids=(PROJECT_OTHER,),
    )
    attempted: list[str] = []
    errors: list[tuple[str, str]] = []
    events: list[tuple[str, Any]] = []

    class FenceRegistry:
        def __init__(self, path: str, *, now: Any) -> None:
            events.append(("registry", (path, now())))

        def reconcile_authorized_workspaces(
            self,
            workspace_ids: tuple[str, ...],
        ) -> int:
            events.append(("reconcile", workspace_ids))
            return 0

    def run(**kwargs: Any) -> bool:
        workspace_id = kwargs["scope"].workspace_id
        events.append(("workspace", workspace_id))
        attempted.append(workspace_id)
        if workspace_id == first.workspace_id:
            raise RuntimeError("isolated failure")
        return True

    monkeypatch.setattr(subject, "AtomicMultiTenantFenceFile", FenceRegistry)
    monkeypatch.setattr(subject, "_run_workspace", run)
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    result = subject._run_cycle(
        scopes=(first, second),
        skipped=(WORKSPACE_EMPTY,),
        settings_object=_settings(),
        config=_config(),
        observation=SimpleNamespace(),  # type: ignore[arg-type]
        now=now,
        on_error=lambda workspace_id, exc: errors.append((workspace_id, str(exc))),
    )

    assert events[:2] == [
        ("registry", ("/runtime/fence.json", now)),
        (
            "reconcile",
            tuple(sorted((WORKSPACE_DEFAULT, WORKSPACE_EMPTY, WORKSPACE_OTHER))),
        ),
    ]
    assert attempted == [first.workspace_id, second.workspace_id]
    assert result.processed == (second.workspace_id,)
    assert result.skipped == (WORKSPACE_EMPTY,)
    assert result.failures == {first.workspace_id: "isolated failure"}
    assert errors == [(first.workspace_id, "isolated failure")]


def test_runtime_uses_the_shared_multitenant_fence_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Factory:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __call__(self, request: object) -> object:
            captured["request"] = request
            return "runtime"

    monkeypatch.setattr(subject, "PropertyCatalogDevRuntimeFactory", Factory)
    monkeypatch.setattr(
        subject,
        "require_checked_in_property_catalog_dev_runtime",
        lambda runtime: runtime,
    )

    runtime = subject._runtime(
        request="request",  # type: ignore[arg-type]
        proxy="proxy",  # type: ignore[arg-type]
        scope=_scope(),
    )

    assert runtime == "runtime"
    assert captured["fence_sink_factory"] is subject.AtomicMultiTenantFenceFile
    assert captured["request"] == "request"


def test_default_workspace_probe_maps_only_discovered_legacy_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _scope(
        is_default=True,
        project_ids=(PROJECT_LEGACY, PROJECT_DEFAULT),
        legacy_project_ids=(PROJECT_LEGACY,),
    )
    bindings = (
        PostgresProjectTenantBinding(
            project_id=PROJECT_LEGACY,
            organization_id=ORG_A,
            workspace_id=None,
            workspace_organization_id=None,
        ),
        PostgresProjectTenantBinding(
            project_id=PROJECT_DEFAULT,
            organization_id=ORG_A,
            workspace_id=WORKSPACE_DEFAULT,
            workspace_organization_id=ORG_A,
        ),
    )
    monkeypatch.setattr(
        subject.dev_runtime,
        "_postgres_workspace_project_inventory",
        lambda *_args, **scope: subject.dev_runtime._parse_postgres_workspace_inventory(
            [
                (
                    WORKSPACE_DEFAULT,
                    ORG_A,
                    True,
                    binding.project_id,
                    binding.organization_id,
                    binding.workspace_id,
                )
                for binding in bindings
            ],
            **scope,
        ),
    )

    mapped = subject._legacy_aware_project_probe(scope)(
        scope.project_ids,
        SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert mapped.bindings[0].workspace_id == WORKSPACE_DEFAULT
    assert mapped.bindings[0].workspace_organization_id == ORG_A
    assert mapped.bindings[1] == bindings[1]
    assert mapped.is_default is True


def test_once_exits_nonzero_after_any_workspace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    scope = _scope()
    failure = subject._CycleResult(
        processed=(),
        skipped=(),
        failures={scope.workspace_id: "failed"},
    )
    monkeypatch.setattr(subject, "_supervisor_config", lambda **_kwargs: config)
    monkeypatch.setattr(
        subject,
        "_probe_remote_identities",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        subject,
        "_discover_workspace_scopes",
        lambda **_kwargs: ((scope,), ()),
    )
    monkeypatch.setattr(subject, "_run_cycle", lambda **_kwargs: failure)
    monkeypatch.setattr(
        subject,
        "_utc_now",
        lambda: datetime(2026, 8, 26, 12, tzinfo=UTC),
    )

    with pytest.raises(CommandError, match=scope.workspace_id):
        subject.Command().handle(once=True)


def test_once_forwards_explicit_initial_backfill_mode_and_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    success = subject._CycleResult(processed=(), skipped=(), failures={})
    monkeypatch.setattr(subject, "_supervisor_config", lambda **_kwargs: _config())
    monkeypatch.setattr(
        subject,
        "_probe_remote_identities",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        subject,
        "_discover_workspace_scopes",
        lambda **_kwargs: ((), ()),
    )

    def cycle(**kwargs: Any) -> subject._CycleResult:
        captured.update(kwargs)
        return success

    monkeypatch.setattr(subject, "_run_cycle", cycle)
    monkeypatch.setattr(
        subject,
        "_utc_now",
        lambda: datetime(2026, 8, 26, 12, tzinfo=UTC),
    )

    subject.Command().handle(
        once=True,
        initial_backfill=True,
        initial_backfill_wall_ms=1_740_000,
    )

    assert captured["bootstrap_only"] is True
    assert captured["initial_backfill_wall_ms"] == 1_740_000


def test_initial_backfill_is_refused_without_once() -> None:
    with pytest.raises(CommandError, match="requires --once"):
        subject.Command().handle(initial_backfill=True, once=False)


def test_initial_backfill_wall_requires_explicit_backfill() -> None:
    with pytest.raises(CommandError, match="requires --initial-backfill"):
        subject.Command().handle(
            initial_backfill=False,
            initial_backfill_wall_ms=1_740_000,
            once=True,
        )


def test_continuous_empty_install_discovers_new_workspace_then_reconciles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _stub_lifecycle_runtime(monkeypatch)
    workspace = (WORKSPACE_DEFAULT, ORG_A, True)
    project = (PROJECT_DEFAULT, WORKSPACE_DEFAULT)
    workspaces = _RowsManager([[], [workspace], [workspace], [workspace]])
    projects = _RowsManager([[], [project], [project]])
    monkeypatch.setattr(
        subject, "Workspace", SimpleNamespace(no_workspace_objects=workspaces)
    )
    monkeypatch.setattr(
        subject, "Project", SimpleNamespace(no_workspace_objects=projects)
    )
    config = replace(_config(), revision_fence_file=str(tmp_path / "fence.json"))
    monkeypatch.setattr(subject, "settings", _settings())
    monkeypatch.setattr(subject, "_supervisor_config", lambda **_kwargs: config)
    monkeypatch.setattr(
        subject, "_probe_remote_identities", lambda **_kwargs: _observation()
    )
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    monkeypatch.setattr(subject, "_utc_now", lambda: now)
    cycles: list[list[tuple[str, str]]] = []

    class StopSupervisor(Exception):
        pass

    def sleep(seconds: int) -> None:
        assert seconds == config.poll_seconds
        cycles.append(list(state.events))
        if len(cycles) == 4:
            raise StopSupervisor

    monkeypatch.setattr(subject.time, "sleep", sleep)
    with pytest.raises(StopSupervisor):
        subject.Command().handle(once=False)

    assert cycles[0] == []  # No workspace yet.
    bootstrap = [
        "status",
        "close-status",
        "schema",
        "backfill",
        "postgres",
        "reconcile",
        "qualify",
        "activate",
        "close-execute",
    ]
    # A real workspace bootstraps before its first Observe project exists.
    assert cycles[1] == [(WORKSPACE_DEFAULT, step) for step in bootstrap]
    scheduled = [
        (WORKSPACE_DEFAULT, step)
        for step in (
            "status",
            "close-status",
            "verify-schema",
            "incremental",
            "close-execute",
        )
    ]
    assert cycles[2][len(bootstrap) :] == scheduled
    assert cycles[3][len(bootstrap) :] == scheduled * 2
    assert state.active == {WORKSPACE_DEFAULT}
    assert workspaces.batches == projects.batches == []
    assert all(
        request.initial_backfill_wall_ms is None
        and request.repair_expired_incomplete is False
        for request, _proxy in state.requests
    )


@pytest.mark.parametrize(
    "failed_stage", ("backfill", "reconcile", "qualify", "activate")
)
@pytest.mark.parametrize("checkpoint_reload", (False, True))
def test_bootstrap_failure_retries_on_next_cycle_and_then_becomes_incremental(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_stage: str,
    checkpoint_reload: bool,
) -> None:
    from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
        NativeCheckpointReloadRequired,
    )

    state = _stub_lifecycle_runtime(monkeypatch)
    failure_type = NativeCheckpointReloadRequired if checkpoint_reload else RuntimeError
    failure = failure_type(f"{failed_stage} incomplete")
    state.failures[(WORKSPACE_DEFAULT, failed_stage)] = failure
    errors: list[Exception] = []
    config = replace(_config(), revision_fence_file=str(tmp_path / "fence.json"))

    def cycle(now: datetime) -> subject._CycleResult:
        return subject._run_cycle(
            scopes=(_scope(),),
            skipped=(),
            settings_object=_settings(),
            config=config,
            observation=_observation(),
            now=now,
            on_error=lambda _workspace_id, exc: errors.append(exc),
        )

    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    failed = cycle(now)
    assert failed.processed == ()
    assert failed.failures == {WORKSPACE_DEFAULT: str(failure)}
    assert state.active == set()
    assert errors == [failure]
    assert state.events[-1] == (WORKSPACE_DEFAULT, "close-execute")

    retried = cycle(now + timedelta(seconds=config.poll_seconds))
    assert retried.processed == (WORKSPACE_DEFAULT,)
    assert retried.failures == {}
    assert state.active == {WORKSPACE_DEFAULT}
    assert state.events.count((WORKSPACE_DEFAULT, "backfill")) == 2

    assert cycle(now + timedelta(seconds=2 * config.poll_seconds)).failures == {}
    assert state.events.count((WORKSPACE_DEFAULT, "backfill")) == 2
    assert state.events.count((WORKSPACE_DEFAULT, "incremental")) == 1
    assert all(
        request.repair_expired_incomplete is False for request, _proxy in state.requests
    )


def test_failed_bootstrap_does_not_block_another_new_or_active_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _stub_lifecycle_runtime(monkeypatch)
    state.active.add(WORKSPACE_EMPTY)
    state.failures[(WORKSPACE_DEFAULT, "backfill")] = RuntimeError("lease refused")
    result = subject._run_cycle(
        scopes=(
            _scope(),
            _scope(
                WORKSPACE_OTHER, organization_id=ORG_B, project_ids=(PROJECT_OTHER,)
            ),
            _scope(WORKSPACE_EMPTY, project_ids=(PROJECT_LEGACY,)),
        ),
        skipped=(),
        settings_object=_settings(),
        config=replace(_config(), revision_fence_file=str(tmp_path / "fence.json")),
        observation=_observation(),
        now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        on_error=lambda *_args: None,
    )

    assert result.failures == {WORKSPACE_DEFAULT: "lease refused"}
    assert result.processed == (WORKSPACE_OTHER, WORKSPACE_EMPTY)
    assert state.active == {WORKSPACE_OTHER, WORKSPACE_EMPTY}
    assert (WORKSPACE_OTHER, "activate") in state.events
    assert (WORKSPACE_EMPTY, "incremental") in state.events
    assert (WORKSPACE_DEFAULT, "activate") not in state.events


def test_each_workspace_uses_fresh_clock_and_reports_progress(monkeypatch, tmp_path):
    times = [datetime(2026, 9, 6, tzinfo=UTC), datetime(2026, 9, 6, 1, tzinfo=UTC)]
    clock = iter(times)
    observed = []
    progress = []

    def run(**kwargs):
        observed.append(kwargs["now"])
        return True

    monkeypatch.setattr(subject, "_run_workspace", run)
    subject._run_cycle(
        scopes=(
            _scope(),
            _scope(
                WORKSPACE_OTHER, organization_id=ORG_B, project_ids=(PROJECT_OTHER,)
            ),
        ),
        skipped=(),
        settings_object=_settings(),
        config=replace(_config(), revision_fence_file=str(tmp_path / "fence.json")),
        observation=_observation(),
        now=times[0],
        clock=lambda: next(clock),
        on_workspace=progress.append,
        on_error=lambda *_: None,
    )
    assert observed == times
    assert progress == [WORKSPACE_DEFAULT, WORKSPACE_OTHER]


@pytest.mark.parametrize("dependency_failure", [False, True])
def test_continuous_health_distinguishes_workspace_and_dependency_failures(
    monkeypatch,
    tmp_path,
    dependency_failure,
):
    config = replace(_config(), revision_fence_file=str(tmp_path / "fence.json"))
    path = tmp_path / "health.json"
    monkeypatch.setattr(subject, "settings", _settings())
    monkeypatch.setattr(subject, "_supervisor_config", lambda **_: config)

    def probe(**_):
        if dependency_failure:
            raise RuntimeError("dependency unavailable")
        return _observation()

    monkeypatch.setattr(subject, "_probe_remote_identities", probe)
    monkeypatch.setattr(subject, "_discover_workspace_scopes", lambda **_: ((), ()))
    monkeypatch.setattr(
        subject,
        "_run_cycle",
        lambda **_: subject._CycleResult(
            processed=(WORKSPACE_OTHER,),
            skipped=(),
            failures={WORKSPACE_DEFAULT: "local failure"},
        ),
    )

    class StopSupervisor(Exception):
        pass

    def sleep(_):
        record = json.loads(path.read_text())
        assert record["live"] is True
        assert record["ready"] is (not dependency_failure)
        assert record["healthy"] is False
        raise StopSupervisor

    monkeypatch.setattr(subject.time, "sleep", sleep)
    with pytest.raises(StopSupervisor):
        subject.Command().handle(once=False, health_file=str(path))
    assert json.loads(path.read_text())["live"] is False


def test_once_initial_backfill_is_idempotent_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _stub_lifecycle_runtime(monkeypatch)
    state.active.add(WORKSPACE_OTHER)
    config = replace(_config(), revision_fence_file=str(tmp_path / "fence.json"))
    monkeypatch.setattr(subject, "settings", _settings())
    monkeypatch.setattr(subject, "_supervisor_config", lambda **_kwargs: config)
    monkeypatch.setattr(
        subject, "_probe_remote_identities", lambda **_kwargs: _observation()
    )
    monkeypatch.setattr(
        subject,
        "_discover_workspace_scopes",
        lambda **_kwargs: (
            (
                _scope(),
                _scope(
                    WORKSPACE_OTHER, organization_id=ORG_B, project_ids=(PROJECT_OTHER,)
                ),
            ),
            (),
        ),
    )

    first = subject.Command().handle(
        once=True, initial_backfill=True, initial_backfill_wall_ms=1_740_000
    )
    assert first == subject.canonical_json(
        {"failed": {}, "processed": [WORKSPACE_DEFAULT], "skipped": [WORKSPACE_OTHER]}
    )
    offset = len(state.events)
    second = subject.Command().handle(once=True, initial_backfill=True)
    assert second == subject.canonical_json(
        {"failed": {}, "processed": [], "skipped": [WORKSPACE_DEFAULT, WORKSPACE_OTHER]}
    )
    assert state.events[offset:] == [
        (workspace, step)
        for workspace in (WORKSPACE_DEFAULT, WORKSPACE_OTHER)
        for step in ("status", "close-status")
    ]
    writes = [request for request, _proxy in state.requests if request.execute]
    assert len(writes) == 1
    assert writes[0].initial_backfill_wall_ms == 1_740_000


@pytest.mark.parametrize("active", (None, 0, 1, "false", "true"))
def test_ambiguous_status_cannot_trigger_automatic_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    active: Any,
) -> None:
    state = _stub_lifecycle_runtime(monkeypatch)
    monkeypatch.setattr(
        subject,
        "run_configured_dev_rollout",
        lambda **_kwargs: SimpleNamespace(
            evidence=(
                SimpleNamespace(evidence={"schema_ready": True, "active": active}),
            )
        ),
    )

    with pytest.raises(
        subject.OssPropertyCatalogSupervisorError, match="explicitly prove"
    ):
        subject._run_workspace(
            scope=_scope(),
            settings_object=_settings(),
            config=_config(),
            observation=_observation(),
            now=datetime(2026, 8, 26, 12, tzinfo=UTC),
        )
    assert len(state.requests) == 1
    assert state.requests[0][0].status is True
    assert state.events == [(WORKSPACE_DEFAULT, "close-status")]


def test_automatic_bootstrap_retains_bounded_runtime_scans_deadline_and_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _stub_lifecycle_runtime(monkeypatch)
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    settings_object = _settings(
        CLICKHOUSE={},
        CLICKHOUSE_V2={
            "CH25_HOST": "source.invalid",
            "CH25_TCP_PORT": 9000,
            "CH25_USER": "source_reader",
            "CH25_PASSWORD": "test-source",
            "CH25_DATABASE": "default",
            "CH25_SERVER_ENFORCED_READONLY": True,
        },
        PROPERTY_CATALOG_DEV_WRITE_CH_HOST="writer.invalid",
        PROPERTY_CATALOG_DEV_WRITE_CH_PORT=9000,
        PROPERTY_CATALOG_DEV_WRITE_CH_USER="catalog_writer",
        PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD="test-writer",
        PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY=str(tmp_path),
        PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE=str(
            tmp_path / "producer-drain-proof-v2.json"
        ),
        PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE=str(
            tmp_path / "producer-state-retirements-v1.json"
        ),
    )
    subject._run_workspace(
        scope=_scope(),
        settings_object=settings_object,
        config=replace(_config(), revision_fence_file=str(tmp_path / "fence.json")),
        observation=_observation(),
        now=now,
    )
    request, proxy = state.requests[1]
    assert request.execute and not request.status
    assert request.initial_backfill_wall_ms is None
    assert request.scheduled_reconcile_wall_ms is None
    assert request.repair_expired_incomplete is False

    class Driver:
        def __init__(self, config: Any) -> None:
            self.database = config.database
            self.server_enforced_readonly = config.server_enforced_readonly

    runtime = subject.PropertyCatalogDevRuntimeFactory(
        settings_object=proxy,
        native_client_factory=Driver,
        provenance_probe=lambda *_args: _observation(),
        project_tenant_binding_probe=lambda *_args: PostgresWorkspaceProjectInventory(
            ORG_A,
            WORKSPACE_DEFAULT,
            False,
            (
                PostgresProjectTenantBinding(
                    project_id=PROJECT_DEFAULT,
                    organization_id=ORG_A,
                    workspace_id=WORKSPACE_DEFAULT,
                    workspace_organization_id=ORG_A,
                ),
            ),
        ),
        now=lambda: now,
    )(request)
    try:
        assert runtime.config.span_until - runtime.config.span_since == timedelta(
            days=366
        )
        assert runtime.config.project_ids == (PROJECT_DEFAULT,)
        assert runtime.config.extended_rollout_wall is False
        assert 0 < runtime.deadline.wall_ms <= dev_rollout.DEV_STANDARD_MAX_WALL_MS
        assert (
            runtime.coordinator._lease_seconds
            == subject.dev_runtime.REVISION_LEASE_SECONDS
        )
        assert (
            runtime.coordinator._lease_seconds
            <= subject.dev_runtime.MAX_REVISION_LEASE_SECONDS
        )
        assert runtime.deadline.wall_ms < runtime.coordinator._lease_seconds * 1000
        assert runtime.coordinator._deadline is runtime.deadline
        assert runtime.state_store._deadline is runtime.deadline
        assert runtime.span_reader._deadline is runtime.deadline
        assert runtime.lifecycle_state._deadline is runtime.deadline
        assert (
            runtime.span_reader._timeout_ms
            == subject.dev_runtime.CANONICAL_SPAN_QUERY_TIMEOUT_MS
        )
        assert (
            0
            < runtime.span_reader._page_rows
            <= subject.dev_runtime.MAX_CANONICAL_SPAN_PAGE_ROWS
        )
        assert runtime.source_client._explicit_initial_backfill is False
        assert runtime.config.source.server_enforced_readonly is True
    finally:
        runtime.close()
