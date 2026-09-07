"""Default OSS factory ownership and native adapter wiring; no live transport."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeCatalogClient,
    NativeConnectionConfig,
    PropertyCatalogDevRuntimeFactory,
    PropertyCatalogProductionRuntimeFactory,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
)
from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
    _ActivationControlClient,
)
from tracer.tests.test_property_catalog_native_ack import Driver, adapter
from tracer.tests.test_property_catalog_native_write_proof import make_proof


@pytest.mark.parametrize("kind", ["catalog", "reader_control"])
def test_existing_identity_and_sql_guards_precede_durable_delegation(kind):
    driver = Driver()
    _, table, columns, rows = adapter(kind, driver)
    durable = object.__new__(DurableNativeCatalogWriter)
    durable.driver, durable.database = driver, driver.database
    durable.insert, durable.query = Mock(), Mock(return_value=({"state": "active"},))
    if kind == "catalog":
        client = NativeCatalogClient(
            driver, database=driver.database, durable_writer=durable
        )
    else:
        client = _ActivationControlClient(
            driver,
            database=driver.database,
            user=driver.user,
            expected_hostnames=("catalog-0",),
            durable_writer=durable,
        )
    client.insert(
        table, rows, columns=columns, timeout_ms=1000, deduplication_token="exact"
    )
    durable.insert.assert_called_once_with(
        table,
        rows,
        columns=columns,
        timeout_ms=1000,
        deduplication_token="exact",
    )
    assert not driver.writes  # The old synchronous-only adapter is not used.
    with pytest.raises(RuntimeError):
        client.insert(
            "`source`.`spans`",
            rows,
            columns=columns,
            timeout_ms=1000,
            deduplication_token="exact",
        )
    assert durable.insert.call_count == 1


@pytest.mark.parametrize("kind", ["catalog", "reader_control"])
def test_read_adapter_uses_agreed_state_and_rejects_nonreviewed_tables(kind):
    driver = Driver()
    durable = object.__new__(DurableNativeCatalogWriter)
    durable.driver, durable.database = driver, driver.database
    durable.query = Mock(return_value=({"state": "active"},))
    if kind == "catalog":
        client = NativeCatalogClient(
            driver, database=driver.database, durable_writer=durable
        )
        sql = "SELECT * FROM `property_catalog`.`property_catalog_activations`"
    else:
        client = _ActivationControlClient(
            driver,
            database=driver.database,
            user=driver.user,
            expected_hostnames=("catalog-0",),
            durable_writer=durable,
        )
        sql = next(iter(client._allowed_reads))
    assert client.query(sql, {}, timeout_ms=1000) == ({"state": "active"},)
    durable.query.assert_called_once_with(
        sql, {}, timeout_ms=1000,
        **({"agreement": client._read_agreements[sql]} if kind == "reader_control" else {}),
    )
    with pytest.raises(RuntimeError):
        client.query("SELECT * FROM `source`.`spans`", {}, timeout_ms=1000)
    assert durable.query.call_count == 1


def factory_inputs(
    tmp_path, *, managed=True, execute=True, cloud="", replicas=1, deployment="dev"
):
    proof, _ = make_proof(
        tmp_path,
        replicas=replicas,
        environment="production" if deployment == "prod" else "development",
    )
    for index, connection in enumerate(proof.connections):
        connection.driver.host = "clickhouse" if replicas == 1 else f"member-{index}"
        connection.driver.port = 9000
    driver = SimpleNamespace(
        host="clickhouse",
        port=9000,
        database=proof.admission.database,
        user="catalog_writer",
        server_enforced_readonly=False,
    )
    calls = []

    @contextmanager
    def context(settings):
        calls.append("open")
        try:
            yield proof.identity, proof.admission, proof.connections
        finally:
            calls.append("close")

    def native_client(config):
        calls.append(("direct", config))
        return SimpleNamespace(
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.user,
            server_enforced_readonly=config.server_enforced_readonly,
            close=lambda: calls.append("direct-close"),
        )

    factory_type = (
        PropertyCatalogProductionRuntimeFactory
        if deployment == "prod"
        else PropertyCatalogDevRuntimeFactory
    )
    factory = factory_type(
        settings_object=SimpleNamespace(
            _PROPERTY_CATALOG_MANAGED_INSTALLATION=managed,
            CLOUD_DEPLOYMENT=cloud,
        ),
        native_write_context_factory=context,
        native_client_factory=native_client,
    )
    config = SimpleNamespace(
        deployment=deployment,
        revision_fence_file=str(tmp_path / "revision-fence.json"),
        catalog=NativeConnectionConfig(
            host=driver.host,
            port=driver.port,
            database=driver.database,
            user=driver.user,
            password="fixture-writer-password",
            server_enforced_readonly=False,
        ),
    )
    request = SimpleNamespace(execute=execute)
    return factory, driver, config, request, calls


def test_default_oss_factory_keeps_proof_connections_for_runtime_lifetime(tmp_path):
    factory, driver, config, request, calls = factory_inputs(tmp_path)
    with ExitStack() as resources:
        writer = factory._managed_native_writer(
            driver, config=config, request=request, resources=resources
        )
        assert type(writer) is DurableNativeCatalogWriter
        assert calls == ["open"]
        assert writer.proof.connections[0].driver.host == driver.host
    assert calls == ["open", "close"]


def test_factory_failure_closes_opened_proof_context(tmp_path):
    factory, driver, config, request, calls = factory_inputs(tmp_path)
    driver.host = "wrong-node"
    with pytest.raises(RuntimeError, match="unique admitted route"):
        with ExitStack() as resources:
            factory._managed_native_writer(
                driver, config=config, request=request, resources=resources
            )
    assert calls == ["open", "close"]


@pytest.mark.parametrize("options", [{"managed": False}, {"execute": False}])
def test_explicit_legacy_and_status_paths_do_not_create_write_admission(
    tmp_path, options
):
    factory, driver, config, request, calls = factory_inputs(tmp_path, **options)
    with ExitStack() as resources:
        assert (
            factory._managed_native_writer(
                driver, config=config, request=request, resources=resources
            )
            is None
        )
    assert calls == []


@pytest.mark.parametrize("deployment,replicas", [("dev", 2), ("prod", 3)])
def test_replicated_seed_is_replaced_by_proven_direct_writer_and_closed(
    tmp_path, deployment, replicas
):
    factory, seed, config, request, calls = factory_inputs(
        tmp_path, deployment=deployment, replicas=replicas
    )
    with ExitStack() as resources:
        writer = factory._managed_native_writer(
            seed, config=config, request=request, resources=resources
        )
        assert writer.driver is not seed
        assert writer.driver.host == "member-0"
        assert writer.member.name == writer.proof.connections[0].name
        direct_config = calls[1][1]
        assert direct_config == replace(config.catalog, host="member-0")
        assert writer.driver.user == seed.user  # Never proof credentials.
        assert "direct-close" not in calls
    assert calls[-2:] == ["direct-close", "close"]


def test_exact_direct_member_is_reused_without_allocating_another_writer(tmp_path):
    factory, direct, config, request, calls = factory_inputs(tmp_path, replicas=2)
    direct.host = "member-1"
    with ExitStack() as resources:
        writer = factory._managed_native_writer(
            direct, config=config, request=request, resources=resources
        )
        assert writer.driver is direct and writer.member.name == "replica2"
        assert calls == ["open"]
    assert calls == ["open", "close"]


@pytest.mark.parametrize("mismatch", ["database", "environment"])
def test_admission_destination_mismatch_precedes_direct_writer_creation(
    tmp_path, mismatch
):
    factory, seed, config, request, calls = factory_inputs(tmp_path, replicas=2)
    if mismatch == "database":
        seed.database = "wrong_database"
    else:
        config.deployment = "prod"
    with pytest.raises(RuntimeError, match="runtime destination"):
        with ExitStack() as resources:
            factory._managed_native_writer(
                seed, config=config, request=request, resources=resources
            )
    assert calls == ["open", "close"]


def test_new_direct_driver_must_preserve_the_admitted_route_and_identity(tmp_path):
    factory, seed, config, request, calls = factory_inputs(tmp_path, replicas=2)
    wrong = SimpleNamespace(
        host="elsewhere",
        port=9000,
        database=seed.database,
        user=seed.user,
        server_enforced_readonly=False,
        close=Mock(),
    )
    factory._native_client_factory = lambda _: wrong
    with pytest.raises(ValueError, match="admitted direct"):
        with ExitStack() as resources:
            factory._managed_native_writer(
                seed, config=config, request=request, resources=resources
            )
    wrong.close.assert_called_once()
    assert calls == ["open", "close"]


@pytest.mark.parametrize("deployment", ["dev", "prod"])
@pytest.mark.parametrize("failure", [False, True])
def test_reader_control_follows_same_direct_node_with_its_own_credentials(
    tmp_path, monkeypatch, deployment, failure
):
    from tracer.services.clickhouse.v2.property_catalog import reader_activation

    factory, seed, config, request, calls = factory_inputs(
        tmp_path, replicas=3 if deployment == "prod" else 2, deployment=deployment
    )
    config.source = SimpleNamespace(user="source_reader")
    config.provenance_expectation = SimpleNamespace(
        writer_clickhouse_hostnames=("host1", "host2", "host3")
    )
    config.mutation_lock_directory = str(tmp_path)
    config.catalog_epoch = config.projection_version = 1
    request.organization_id = "00000000-0000-0000-0000-000000000001"
    request.workspace_id = "00000000-0000-0000-0000-000000000002"
    factory._settings.PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER = "control_writer"
    factory._settings.PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD = "control-fixture"
    observed = []

    def guarded(driver, **kwargs):
        observed.append((driver, kwargs))
        assert kwargs["durable_writer"].driver is driver
        if failure:
            raise RuntimeError("control attestation failed")
        return object()

    monkeypatch.setattr(reader_activation, "ReaderActivationClient", guarded)
    monkeypatch.setattr(
        reader_activation,
        "AutomaticReaderActivation",
        lambda *args, **kwargs: SimpleNamespace(reconcile=lambda scope: "selected"),
    )
    with ExitStack() as resources:
        writer = factory._managed_native_writer(
            seed, config=config, request=request, resources=resources
        )
        runtime = SimpleNamespace(
            config=config,
            bound_request=request,
            catalog_client=SimpleNamespace(_durable_writer=writer),
            _refresh_project_tenant_authorization=Mock(),
        )
        if failure:
            with pytest.raises(RuntimeError, match="control attestation"):
                factory._reconcile_reader_activation(runtime)
        else:
            assert factory._reconcile_reader_activation(runtime) == "selected"
        connections = [entry[1] for entry in calls if isinstance(entry, tuple)]
        assert len(connections) == 2
        control = connections[1]
        assert (control.host, control.port) == (writer.driver.host, writer.driver.port)
        assert control.user == ("control_writer" if deployment == "prod" else seed.user)
        assert control.password == (
            "control-fixture" if deployment == "prod" else config.catalog.password
        )
        assert calls.count("direct-close") == 1  # Control closed on both outcomes.
        assert observed[0][1]["durable_writer"].proof is writer.proof
    assert calls.count("direct-close") == 2


@pytest.mark.parametrize(
    "deployment,cloud", [("dev", ""), ("dev", "gcp"), ("prod", "gcp")]
)
def test_default_factory_uses_automatic_dev_dispatch_or_production_discovery(
    tmp_path, monkeypatch, deployment, cloud
):
    from tracer.services.clickhouse.v2.property_catalog import (
        oss_write_startup,
        replicated_write_startup,
    )

    factory, seed, config, request, calls = factory_inputs(
        tmp_path,
        replicas=3 if deployment == "prod" else 1,
        deployment=deployment,
        cloud=cloud,
    )
    context = factory._native_write_context_factory
    factory._native_write_context_factory = None
    invoked = []

    def standalone(settings):
        invoked.append("auto-dev")
        return context(settings)

    def replicated(settings, **kwargs):
        invoked.append(("replicated", kwargs))
        return context(settings)

    monkeypatch.setattr(oss_write_startup, "oss_write_admission_context", standalone)
    monkeypatch.setattr(
        replicated_write_startup, "replicated_write_admission_context", replicated
    )
    with ExitStack() as resources:
        factory._managed_native_writer(
            seed, config=config, request=request, resources=resources
        )
    assert invoked == (
        [("replicated", {"prefix": "PROPERTY_CATALOG_DEV_"})]
        if deployment == "prod"
        else ["auto-dev"]
    )
