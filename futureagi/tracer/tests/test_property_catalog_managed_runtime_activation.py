"""Factory and runtime wiring for automatic selection, not a live API test."""

from dataclasses import replace
from types import MethodType, SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    reader_activation as selection,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlScope,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    PropertyCatalogDevRuntimeError,
    PropertyCatalogDevRuntimeFactory,
)
from tracer.tests.test_property_catalog_dev_rollout import (
    ATTESTED_AT,
    ORG,
    WORKSPACE,
    _project_bindings,
    _provenance_observation,
    _request,
    _runtime_settings,
    _unit_runtime_config,
)


class Driver:
    def __init__(self, config):
        self.database = config.database
        self.server_enforced_readonly = config.server_enforced_readonly
        self.config = config
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("with_control", [False, True])
def test_runtime_status_accepts_verified_optional_reader_control(with_control):
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        PROPERTY_CATALOG_TABLES,
        _status_target_tables,
    )

    tables = [{"name": name, "engine": "MergeTree"} for name in PROPERTY_CATALOG_TABLES]
    if with_control:
        tables.append(
            {
                "name": "property_catalog_activation_control_events",
                "engine": "MergeTree",
            }
        )
    assert len(_status_target_tables({"target_tables": tables})) == len(tables)
    for invalid in (
        tables + [tables[0]],
        tables[:-1] + [tables[0]],
        tables + [{"name": "unexpected", "engine": "MergeTree"}],
    ):
        with pytest.raises(PropertyCatalogDevRuntimeError):
            _status_target_tables({"target_tables": invalid})


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("status", [False, True])
def test_factory_wires_selection_only_for_managed_execute_runtime(
    tmp_path, managed, status
):
    settings = _runtime_settings(str(tmp_path))
    settings._PROPERTY_CATALOG_MANAGED_INSTALLATION = managed
    calls = []
    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=settings,
        native_client_factory=Driver,
        provenance_probe=lambda *_: _provenance_observation(),
        project_tenant_binding_probe=lambda ids, _: _project_bindings(ids),
        now=lambda: ATTESTED_AT,
    )
    factory._reconcile_reader_activation = lambda runtime: (
        calls.append(runtime) or "selected"
    )
    # This test isolates callback ownership, not live write admission. Separate
    # native-writer integration tests exercise the managed transport boundary.
    factory._managed_native_writer = lambda *args, **kwargs: None
    runtime = factory(_request(status=status, execute=not status))
    try:
        assert (runtime._reader_activation_callback is not None) == (
            managed and not status
        )
        assert runtime.reconcile_reader_selection() == (
            "selected" if managed and not status else None
        )
        assert calls == ([runtime] if managed and not status else [])
        with pytest.raises(AttributeError, match="immutable"):
            runtime._reader_activation_callback = lambda _: "unsafe override"
    finally:
        runtime.close()


@pytest.mark.parametrize("deployment", ["dev", "prod"])
@pytest.mark.parametrize("fail_attestation", [False, True])
def test_selection_uses_exact_scope_credentials_and_closes_its_client(
    monkeypatch,
    tmp_path,
    deployment,
    fail_attestation,
):
    config = _unit_runtime_config(str(tmp_path))
    if deployment == "prod":
        config = replace(
            config,
            deployment="prod",
            catalog=replace(config.catalog, database="property_catalog"),
        )
    settings = SimpleNamespace(
        PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER="activation_writer",
        PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD="fixture-only",
    )
    clients, queries, authorizations = [], [], []

    def driver_factory(connection):
        driver = Driver(connection)
        clients.append(driver)
        return driver

    class Guarded:
        def __init__(self, driver, **kwargs):
            queries.append(kwargs)
            if fail_attestation:
                raise RuntimeError("unattested grants")

    class Automatic:
        def __init__(self, client, **kwargs):
            self.options = kwargs

        def reconcile(self, scope):
            assert scope == ActivationControlScope(ORG, WORKSPACE)
            assert self.options["catalog_epoch"] == config.catalog_epoch
            assert self.options["projection_version"] == config.projection_version
            assert self.options["state_directory"] == str(tmp_path)
            assert self.options["deployment"] == deployment
            authorize = self.options["authorize_scope"]
            assert not authorize(ActivationControlScope(WORKSPACE, ORG))
            assert authorize(scope)
            return "selected"

    monkeypatch.setattr(selection, "ReaderActivationClient", Guarded)
    monkeypatch.setattr(selection, "AutomaticReaderActivation", Automatic)
    runtime = SimpleNamespace(
        config=config,
        bound_request=_request(execute=True),
        _refresh_project_tenant_authorization=lambda: authorizations.append(True),
    )
    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=settings,
        native_client_factory=driver_factory,
    )
    if fail_attestation:
        with pytest.raises(RuntimeError, match="unattested"):
            factory._reconcile_reader_activation(runtime)
        assert authorizations == []
    else:
        assert factory._reconcile_reader_activation(runtime) == "selected"
        assert authorizations == [True]
    assert len(clients) == 1 and clients[0].closed == 1
    connection = clients[0].config
    assert connection.database == config.catalog.database
    assert connection.user == (
        "activation_writer" if deployment == "prod" else config.catalog.user
    )
    assert (
        queries[0]["expected_hostnames"]
        == config.provenance_expectation.writer_clickhouse_hostnames
    )


@pytest.mark.parametrize("user", ["", "catalog_writer", "source_reader"])
def test_managed_production_selection_never_reuses_broad_or_source_credentials(
    tmp_path, user
):
    config = _unit_runtime_config(str(tmp_path))
    config = replace(
        config,
        deployment="prod",
        catalog=replace(config.catalog, database="property_catalog"),
    )

    def forbidden(_):
        pytest.fail("must refuse before any network client")

    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=SimpleNamespace(
            PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER=user,
            PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD="fixture-only",
        ),
        native_client_factory=forbidden,
    )
    with pytest.raises(PropertyCatalogDevRuntimeError, match="dedicated control"):
        factory._reconcile_reader_activation(SimpleNamespace(config=config))


def publication_runtime(monkeypatch, tmp_path, *, failure=None, callback_enabled=True):
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime as subject
    from tracer.services.clickhouse.v2.property_catalog.activation import (
        CatalogLifecycleMode,
    )
    from tracer.services.clickhouse.v2.property_catalog.activation_control import (
        ActivationControlBootstrapPending,
    )
    from tracer.tests.test_property_catalog_initial_reader_publication import (
        ReaderExecutor,
        empty_client,
        select,
    )
    from tracer.tests.test_property_catalog_reader_activation import (
        SCOPE,
        service,
        target,
    )

    client, steps = empty_client(), []
    item = target()
    manifest = SimpleNamespace(
        organization_id=item.organization_id, workspace_id=item.workspace_id,
        catalog_epoch=item.catalog_epoch, projection_version=item.projection_version,
        catalog_revision=item.catalog_revision, build_token=item.build_token,
        lifecycle_mode=CatalogLifecycleMode.INITIAL_BACKFILL, sha256="a" * 64,
    )
    fence = SimpleNamespace(manifest_sha256=manifest.sha256,
                            build_token=item.build_token, catalog_revision=item.catalog_revision)
    record = SimpleNamespace(
        **vars(manifest), activation_sha256=item.activation_sha256,
        source_manifest_sha256=manifest.sha256, activation_sequence=1,
        build_plan=SimpleNamespace(sha256="b" * 64), live_definition_rows=0,
        tombstone_rows=0, value_rows=0,
    )
    execution = SimpleNamespace(
        manifest=manifest, fence=fence, activation=None,
        qualification=SimpleNamespace(qualified=True, activation_sha256=item.activation_sha256),
        prepared=SimpleNamespace(mode=subject.LifecycleRunMode.INITIAL_BACKFILL,
                                 scope=SCOPE, prior_active=None),
        lease=SimpleNamespace(build_lease_sha256="b" * 64),
    )

    def guard(*, fence, operation):
        steps.append("guard")
        if failure == "revoked":
            raise RuntimeError("revoked fence")
        return operation()

    class Activator:
        def __init__(self, store, *, coordinator):
            self.coordinator = coordinator

        def activate(self, *, manifest, fence, inventory, now):
            def append():
                if callback_enabled:
                    # Pause exactly inside the publication gap, for as long as
                    # needed: admission is pending without an ACTIVE/data read.
                    with pytest.raises(ActivationControlBootstrapPending):
                        select(ReaderExecutor(client))
                steps.append("append_active")
                if failure == "append":
                    raise RuntimeError("append failed before ACTIVE")
                client.targets = [item]
                if callback_enabled:
                    assert select(ReaderExecutor(client)) == item
                return SimpleNamespace(record=record, idempotent=False)

            return self.coordinator.serialize_activation(fence=fence, operation=append)

    monkeypatch.setattr(subject, "PropertyCatalogActivator", Activator)

    def callback(runtime, *, initial_target=None):
        automatic = service(client, tmp_path)
        if initial_target is not None:
            steps.append("prepare_follow")
            assert initial_target == item
            return automatic.prepare_initial(initial_target)
        steps.append("reconcile_follow")
        return automatic.reconcile(SCOPE)

    runtime = SimpleNamespace(
        config=SimpleNamespace(deployment="dev"),
        _source_capture=None,
        bound_request=SimpleNamespace(execute=True, status=False),
        _reader_activation_callback=callback if callback_enabled else None,
        _validate_mutation_request=lambda *_: None,
        _refresh_project_tenant_authorization=lambda: SimpleNamespace(authorization_contract_sha256="c" * 64),
        _require_execution=lambda: execution,
        _activation_inventory=lambda _: object(),
        _notice_historical_source_change=lambda _: None,
        _notice_source_part_changes=lambda _: None,
        _load_latest_active_retirement=lambda _: record,
        _publish_producer_retirement=lambda *_: steps.append("retire_producer"),
        _candidate_repair=None, _source_repair=None,
        _authorized_build_binding_sha256=None,
        coordinator=SimpleNamespace(serialize_activation=guard), state_store=object(),
        now=lambda: ATTESTED_AT,
    )
    runtime.reconcile_reader_selection = MethodType(subject.CheckedInPropertyCatalogDevRuntime.reconcile_reader_selection, runtime)
    runtime._activator = MethodType(subject.CheckedInPropertyCatalogDevRuntime._activator, runtime)
    runtime._prepare_initial_reader_selection = MethodType(subject.CheckedInPropertyCatalogDevRuntime._prepare_initial_reader_selection, runtime)
    runtime.activate = MethodType(subject.CheckedInPropertyCatalogDevRuntime.activate, runtime)
    return runtime, execution, client, steps


def test_initial_publication_is_guarded_prepare_then_active_then_retirement(monkeypatch, tmp_path):
    runtime, _, client, steps = publication_runtime(monkeypatch, tmp_path)
    assert runtime.activate(object())["activated"] is True
    assert steps == ["guard", "prepare_follow", "append_active", "retire_producer", "reconcile_follow"]
    assert len(client.events) == 1


@pytest.mark.parametrize("failure", ["revoked", "append"])
def test_revoked_fence_cannot_prepare_and_failed_append_restarts_same_follow(monkeypatch, tmp_path, failure):
    runtime, _, client, steps = publication_runtime(monkeypatch, tmp_path, failure=failure)
    with pytest.raises(RuntimeError):
        runtime.activate(object())
    assert steps == (["guard"] if failure == "revoked" else ["guard", "prepare_follow", "append_active"])
    assert len(client.events) == int(failure == "append")
    if failure == "append":
        from tracer.tests.test_property_catalog_reader_activation import service, target

        # Recovery never claims the failed append qualified and does not write
        # another control event merely because the process restarted.
        recovered = service(client, tmp_path).prepare_initial(target())
        assert recovered.selected_target is None
        assert len(client.events) == 1


def test_unmanaged_runtime_keeps_postpublication_behavior(monkeypatch, tmp_path):
    runtime, _, client, steps = publication_runtime(monkeypatch, tmp_path, callback_enabled=False)
    assert runtime.activate(object())["activated"] is True
    assert steps == ["guard", "append_active", "retire_producer"]
    assert not client.events


@pytest.mark.parametrize("bad", ["unqualified", "missing_fence", "manifest_mismatch", "prior_active", "status"])
def test_initial_preparation_requires_fenced_qualification_and_execute_mode(monkeypatch, tmp_path, bad):
    runtime, execution, client, _ = publication_runtime(monkeypatch, tmp_path)
    if bad == "unqualified":
        execution.qualification.qualified = False
    elif bad == "missing_fence":
        execution.fence = None
    elif bad == "manifest_mismatch":
        execution.manifest.sha256 = "d" * 64
    elif bad == "prior_active":
        execution.prepared.prior_active = object()
    else:
        runtime.bound_request.status = True
    with pytest.raises(PropertyCatalogDevRuntimeError):
        runtime.reconcile_reader_selection(prepare_initial=True)
    assert not client.events


@pytest.mark.parametrize("mode", ["incremental", "full_repair"])
@pytest.mark.parametrize("deployment", ["dev", "prod"])
def test_only_initial_prepares_before_publication(monkeypatch, tmp_path, mode, deployment):
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        LifecycleRunMode,
    )

    runtime, execution, client, _ = publication_runtime(monkeypatch, tmp_path)
    runtime.config.deployment = deployment
    execution.prepared.mode = LifecycleRunMode(mode)
    assert runtime.reconcile_reader_selection(prepare_initial=True) is None
    assert not client.events


def test_factory_dispatches_preparation_and_closes_attested_client(monkeypatch, tmp_path):
    from tracer.services.clickhouse.v2.property_catalog.activation_control import (
        ActivationControlTarget,
    )

    config = _unit_runtime_config(str(tmp_path))
    item = ActivationControlTarget(ORG, WORKSPACE, config.catalog_epoch, config.projection_version,
                                   1, "00000000-0000-0000-0000-000000000003", "a" * 64)
    clients, calls = [], []

    def driver_factory(connection):
        clients.append(Driver(connection))
        return clients[-1]

    class Guarded:
        def __init__(self, driver, **kwargs):
            calls.append("attested_client")

    class Automatic:
        def __init__(self, client, **kwargs):
            self.options = kwargs

        def prepare_initial(self, target):
            assert target == item
            assert self.options["authorize_scope"](target.scope)
            calls.append("prepare_initial")
            return "prepared"

        def reconcile(self, scope):
            pytest.fail("prepublication must not fall through to post-ACTIVE reconciliation")

    monkeypatch.setattr(selection, "ReaderActivationClient", Guarded)
    monkeypatch.setattr(selection, "AutomaticReaderActivation", Automatic)
    factory = PropertyCatalogDevRuntimeFactory(settings_object=SimpleNamespace(), native_client_factory=driver_factory)
    runtime = SimpleNamespace(config=config, bound_request=_request(execute=True),
                              _refresh_project_tenant_authorization=lambda: None)
    assert factory._reconcile_reader_activation(runtime, initial_target=item) == "prepared"
    assert calls == ["attested_client", "prepare_initial"] and clients[0].closed == 1
