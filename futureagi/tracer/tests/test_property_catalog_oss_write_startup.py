"""Managed OSS admission ordering, real producer integration, and local wiring."""

from __future__ import annotations

import importlib
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml
from django.core.management.base import CommandError

from tracer.management.commands import ch25_property_catalog_oss_supervisor as command
from tracer.services.clickhouse.server_readonly import ensure_read_statement
from tracer.services.clickhouse.v2.property_catalog import oss_write_startup as subject
from tracer.services.clickhouse.v2.property_catalog import (
    replicated_write_startup,
    write_admission,
)
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
)
from tracer.tests.test_property_catalog_replicated_write_startup import (
    SEED,
)
from tracer.tests.test_property_catalog_replicated_write_startup import (
    setup as replicated_setup,
)
from tracer.tests.test_property_catalog_write_admission import (
    DATABASE,
    Probe,
    installation,
)

ROOT = Path(__file__).resolve().parents[3]


class Factory:
    def __init__(self):
        self.probe = Probe()
        self.clients = []
        self.calls = []
        self.alter = lambda user, sql, rows: rows
        self.http_alter = lambda rows: rows
        self.origins = []
        self.scheme = "http"
        self.grants = [
            f"GRANT SELECT ON `{DATABASE}`.{table} TO catalog_proof"
            for table in sorted(subject.PROPERTY_CATALOG_TABLES)
        ]
        self.grants += [
            f"GRANT SELECT ON system.{table} TO catalog_proof"
            for table in sorted(
                subject._METADATA_TABLES | subject._OPTIONAL_PROOF_TABLES
            )
        ]

    def __call__(self, **kwargs):
        factory = self

        class Driver:
            def __init__(self):
                self.host, self.port, self.user = (
                    kwargs["host"],
                    kwargs["port"],
                    kwargs["user"],
                )
                self.database = kwargs["database"]
                self.closed = False

            def execute_read(self, sql, params, **limits):
                assert not self.closed
                ensure_read_statement(sql)
                factory.calls.append((self.user, sql))
                assert limits["settings"]["readonly"] == 2
                assert 0 < limits["timeout_ms"] <= 30000
                if sql == subject._IDENTITY_SQL:
                    names = (
                        "hostname",
                        "server_uuid",
                        "database",
                        "username",
                        "database_uuid",
                    )
                    rows = [
                        (
                            "host1",
                            str(UUID(int=1)),
                            DATABASE,
                            self.user,
                            str(UUID(int=11)),
                        )
                    ]
                elif sql == subject._TABLES_SQL:
                    names = ("name", "engine")
                    rows = [
                        (r["name"], r["engine"]) for r in factory.probe.rows["replica1"]
                    ]
                elif sql == "SHOW GRANTS":
                    names, rows = ("GRANTS",), [(grant,) for grant in factory.grants]
                elif "getServerPort" in sql:
                    assert params == {
                        "port_name": factory.scheme + "_port",
                        "database": DATABASE,
                    }
                    names = (
                        "hostname",
                        "connected_database",
                        "database_uuid",
                        "http_port",
                    )
                    rows = [("host1", DATABASE, str(UUID(int=11)), 18123)]
                else:
                    return factory.probe.driver_read("replica1")(sql, params, **limits)
                return (
                    factory.alter(self.user, sql, rows),
                    [(name, "") for name in names],
                    0,
                )

            def close(self):
                self.closed = True

        result = Driver()
        self.clients.append((result, kwargs))
        return result

    def http(self, connection, route, sql, timeout_ms, limit):
        self.origins.append(route.origin)
        assert connection.driver.host == "127.0.0.1" and connection.driver.port == 49120
        assert not connection.driver.closed
        assert sql in (
            write_admission._INVENTORY_SQL,
            write_admission._STANDALONE_INVENTORY_SQL,
        )
        return self.http_alter(deepcopy(self.probe.rows["replica1"]))


def setup(tmp_path, monkeypatch):
    (tmp_path / IDENTITY_FILENAME).write_bytes(installation().encode())
    settings = SimpleNamespace(
        ENV_TYPE="development",
        CLOUD_DEPLOYMENT="",
        PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE=str(tmp_path / "fence.json"),
        PROPERTY_CATALOG_DEV_TARGET_DATABASE=DATABASE,
        PROPERTY_CATALOG_DEV_WRITE_CH_HOST="127.0.0.1",
        PROPERTY_CATALOG_DEV_WRITE_CH_PORT=49120,
        PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE=DATABASE,
        PROPERTY_CATALOG_DEV_WRITE_CH_USER="catalog_control",
        PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD="control-secret",
        PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC="catalog.candidates",
        PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC="catalog.ordered",
        CLICKHOUSE_V2={"CH25_HOST": "127.0.0.1", "CH25_HTTP_PORT": "49121"},
    )
    env = {
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "catalog_proof",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "proof-secret",
    }
    factory = Factory()

    def producer(*args, **kwargs):
        # Only network I/O is replaced. Pinned schema, descriptor, route identity,
        # persistence, and rechecks are the actual previously live-proven code.
        return write_admission.admit_catalog_writes(
            *args, **kwargs, http_read=factory.http
        )

    monkeypatch.setattr(subject, "admit_catalog_writes", producer)
    return settings, env, factory


def test_managed_startup_runs_real_producer_with_owned_nat_and_separate_proof(
    tmp_path, monkeypatch
):
    settings, env, factory = setup(tmp_path, monkeypatch)
    value = subject.prepare_oss_write_admission(
        settings, environ=env, client_factory=factory
    )
    assert (
        value.family == "standalone"
        and value.members[0].url == "http://127.0.0.1:49121"
    )
    assert value.members[0].name == "host1" and value.members[0].server_uuid == str(
        UUID(int=1)
    )
    assert len(factory.origins) == 2 and set(factory.origins) == {value.members[0].url}
    assert [c.user for c, _ in factory.clients] == ["catalog_proof", "catalog_control"]
    assert factory.calls.index(
        ("catalog_proof", subject._TABLES_SQL)
    ) < factory.calls.index(("catalog_control", subject._IDENTITY_SQL))
    assert all(c.closed for c, _ in factory.clients)
    assert all(
        kwargs["server_enforced_readonly"] and kwargs["pool_size"] == 1
        for _, kwargs in factory.clients
    )
    assert not any(".spans" in sql for _, sql in factory.calls)
    assert (
        tmp_path / write_admission.WRITE_ADMISSION_FILENAME
    ).read_bytes() == value.encode()


@pytest.mark.parametrize("replaced_http_node", [False, True])
def test_native_reproof_keeps_admitted_http_mapping_and_checks_current_node(
    tmp_path, monkeypatch, replaced_http_node
):
    from tracer.services.clickhouse.v2.property_catalog import native_write_proof

    settings, env, factory = setup(tmp_path, monkeypatch)
    # Run the real installed-admission proof, replacing network I/O only.
    monkeypatch.setattr(
        native_write_proof,
        "reattest_catalog_writes",
        lambda *args, **kwargs: write_admission.reattest_catalog_writes(
            *args, **kwargs, http_read=factory.http
        ),
    )
    with subject.oss_write_admission_context(
        settings, environ=env, client_factory=factory
    ) as (identity, admission, connections):
        saved = (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).read_bytes()
        factory.origins.clear()
        proof = native_write_proof.NativeWriteProof(
            directory=tmp_path,
            identity=identity,
            admission=admission,
            connections=connections,
        )
        if replaced_http_node:
            factory.http_alter = lambda rows: [
                {**row, "server_uuid": str(UUID(int=999))} for row in rows
            ]
            with pytest.raises(write_admission.WriteAdmissionError):
                proof.attest(timeout_ms=5000)
        else:
            proof.attest(timeout_ms=5000)
        assert factory.origins
        assert set(factory.origins) == {"http://127.0.0.1:49121"}
        assert (
            tmp_path / write_admission.WRITE_ADMISSION_FILENAME
        ).read_bytes() == saved
    assert all(client.closed for client, _ in factory.clients)


def test_context_keeps_real_native_driver_routes_alive_and_closes_on_error(
    tmp_path, monkeypatch
):
    settings, env, factory = setup(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="caller"):
        with subject.oss_write_admission_context(
            settings, environ=env, client_factory=factory
        ) as (identity, admission, connections):
            assert identity == installation() and admission.family == "standalone"
            assert len(connections) == 1
            assert (connections[0].driver.host, connections[0].driver.port) == (
                "127.0.0.1",
                49120,
            )
            assert not any(c.closed for c, _ in factory.clients)
            raise RuntimeError("caller")
    assert all(c.closed for c, _ in factory.clients)


def test_missing_http_mapping_uses_discovered_listener_not_default_port(
    tmp_path, monkeypatch
):
    settings, env, factory = setup(tmp_path, monkeypatch)
    settings.CLICKHOUSE_V2 = {}
    value = subject.prepare_oss_write_admission(
        settings, environ=env, client_factory=factory
    )
    assert value.members[0].url == "http://127.0.0.1:18123"


def test_explicit_existing_ledger_origin_supports_configured_tls(tmp_path, monkeypatch):
    settings, env, factory = setup(tmp_path, monkeypatch)
    env["FI_PROPERTY_CATALOG_LEDGER_CH_URL"] = "https://127.0.0.1:49222"
    factory.scheme = "https"
    value = subject.prepare_oss_write_admission(
        settings, environ=env, client_factory=factory
    )
    assert value.members[0].url == env["FI_PROPERTY_CATALOG_LEDGER_CH_URL"]


@pytest.mark.parametrize(
    "bad",
    [
        "prod",
        "cloud",
        "missing_user",
        "missing_password",
        "writer_user",
        "db",
        "topic",
        "native_port",
        "http_port",
        "partial_http",
        "relative_fence",
        "missing_identity",
    ],
)
def test_invalid_infrastructure_fails_before_writer(tmp_path, monkeypatch, bad):
    settings, env, factory = setup(tmp_path, monkeypatch)
    if bad == "prod":
        settings.ENV_TYPE = "production"
    elif bad == "cloud":
        settings.CLOUD_DEPLOYMENT = "gcp"
    elif bad == "missing_user":
        env.pop("FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME")
    elif bad == "missing_password":
        env.pop("FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD")
    elif bad == "writer_user":
        env["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"] = "catalog_control"
    elif bad == "db":
        env["FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE"] = "foreign"
    elif bad == "topic":
        settings.PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC = "foreign"
    elif bad == "native_port":
        settings.PROPERTY_CATALOG_DEV_WRITE_CH_PORT = 0
    elif bad == "http_port":
        settings.CLICKHOUSE_V2["CH25_HTTP_PORT"] = "0"
    elif bad == "partial_http":
        settings.CLICKHOUSE_V2.pop("CH25_HTTP_PORT")
    elif bad == "relative_fence":
        settings.PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE = "fence.json"
    else:
        (tmp_path / IDENTITY_FILENAME).unlink()
    with pytest.raises((ValueError, OSError)):
        subject.prepare_oss_write_admission(
            settings, environ=env, client_factory=factory
        )
    if bad in {"http_port", "partial_http"}:
        # HTTP route configuration belongs to the proven standalone branch.
        # Replicated discovery obtains actual per-node HTTP listeners instead.
        assert factory.calls and all(
            c.user == "catalog_proof" for c, _ in factory.clients
        )
    else:
        assert not factory.calls
    assert not (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).exists()


@pytest.mark.parametrize(
    "bad", ["missing", "wildcard", "insert", "source", "role", "column", "settings"]
)
def test_proof_grants_remain_exact_select_only(tmp_path, monkeypatch, bad):
    settings, env, factory = setup(tmp_path, monkeypatch)
    if bad == "missing":
        factory.grants.pop(0)
    else:
        factory.grants.append(
            {
                "wildcard": f"GRANT SELECT ON {DATABASE}.* TO catalog_proof",
                "insert": f"GRANT INSERT ON {DATABASE}.property_catalog_activations TO catalog_proof",
                "source": "GRANT SELECT ON default.spans TO catalog_proof",
                "role": "GRANT administrator TO catalog_proof",
                "column": "GRANT SELECT(name) ON system.tables TO catalog_proof",
                "settings": "GRANT SELECT ON system.settings TO catalog_proof",
            }[bad]
        )
    with pytest.raises(ValueError, match="grant"):
        subject.prepare_oss_write_admission(
            settings, environ=env, client_factory=factory
        )
    assert not factory.origins and all(c.closed for c, _ in factory.clients)
    assert not (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).exists()


def test_replicated_dispatch_failure_cannot_fall_back_to_single_node(
    tmp_path, monkeypatch
):
    settings, env, factory = setup(tmp_path, monkeypatch)
    factory.probe = Probe(2)
    calls = []

    def failed_discovery(*args, **kwargs):
        calls.append(kwargs)
        assert all(c.closed and c.user == "catalog_proof" for c, _ in factory.clients)
        raise ValueError("complete direct-member discovery failed")

    monkeypatch.setattr(subject, "replicated_write_admission_context", failed_discovery)
    with pytest.raises(ValueError, match="complete direct-member discovery"):
        subject.prepare_oss_write_admission(
            settings, environ=env, client_factory=factory
        )
    assert (
        not factory.origins
        and not (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).exists()
    )
    assert len(calls) == 1 and 0 < calls[0]["timeout_ms"] <= 30000
    assert all(c.user == "catalog_proof" for c, _ in factory.clients)


@pytest.mark.parametrize("where", ["native", "http"])
def test_source_or_ledger_endpoint_must_serve_writer_incarnation(
    tmp_path, monkeypatch, where
):
    settings, env, factory = setup(tmp_path, monkeypatch)

    def alter(user, sql, rows):
        if user == "catalog_proof" and sql == subject._IDENTITY_SQL:
            rows = [(r[0], str(UUID(int=900)), *r[2:]) for r in rows]
        return rows

    def http_alter(rows):
        for row in rows:
            row["server_uuid"] = str(UUID(int=900))
        return rows

    if where == "native":
        factory.alter = alter
    else:
        factory.http_alter = http_alter
    with pytest.raises(ValueError):
        subject.prepare_oss_write_admission(
            settings, environ=env, client_factory=factory
        )
    assert all(c.closed for c, _ in factory.clients)
    assert not (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).exists()


@pytest.mark.parametrize("failure", [False, True])
def test_supervisor_resolves_then_admits_before_any_discovery_or_lifecycle(
    monkeypatch, failure
):
    unresolved = SimpleNamespace(managed_identity=True)
    resolved = SimpleNamespace(managed_identity=True)
    events = []
    monkeypatch.setattr(command, "_supervisor_config", lambda **_: unresolved)

    def resolve(**kwargs):
        assert kwargs["config"] is unresolved
        events.append("identity")
        return resolved

    def admit(*args, **kwargs):
        events.append("admission")
        if failure:
            raise ValueError("unproven")

    def serve(self, **kwargs):
        assert kwargs["config"] is resolved
        events.append("serve")
        return "done"

    monkeypatch.setattr(command, "_resolve_supervisor_installation", resolve)
    monkeypatch.setattr(command, "prepare_oss_write_admission", admit)
    monkeypatch.setattr(command.Command, "_serve", serve)
    if failure:
        with pytest.raises(CommandError, match="before lifecycle"):
            command.Command().handle(once=True)
        assert events == ["identity", "admission"]
    else:
        assert command.Command().handle(once=True) == "done"
        assert events == ["identity", "admission", "serve"]


def test_explicit_legacy_startup_does_not_initialize_new_admission(monkeypatch):
    config = SimpleNamespace(managed_identity=False)
    monkeypatch.setattr(command, "_supervisor_config", lambda **_: config)
    monkeypatch.setattr(command, "_resolve_supervisor_installation", lambda **_: config)
    monkeypatch.setattr(
        command,
        "prepare_oss_write_admission",
        lambda *a, **k: pytest.fail("legacy mutated"),
    )
    monkeypatch.setattr(command.Command, "_serve", lambda *a, **k: "legacy")
    assert command.Command().handle(once=True) == "legacy"


def test_oss_compose_shared_writable_attempt_mount_and_private_initialization():
    document = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = document["services"]
    consumer, supervisor = (
        services["fi-property-catalog-consumer"],
        services["property-catalog-supervisor"],
    )
    assert (
        consumer["environment"]["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"]
        == supervisor["environment"]["PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"]
    )
    assert "fi-collector-data:/var/lib/fi-collector" in consumer["volumes"]
    assert (
        consumer["depends_on"]["property-catalog-runtime-volume-init"]["condition"]
        == "service_completed_successfully"
    )
    assert (
        supervisor["environment"]["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"]
        == consumer["environment"]["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"]
    )
    assert (
        supervisor["environment"]["FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD"]
        == consumer["environment"]["FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD"]
    )
    assert supervisor["user"] == "65532:65532" and supervisor["read_only"] is True
    assert supervisor["cap_drop"] == ["ALL"]
    assert (
        "chmod 700 /runtime/property-catalog /sequencer"
        in services["property-catalog-runtime-volume-init"]["command"][0]
    )
    assert "USER nonroot:nonroot" in (ROOT / "fi-collector/Dockerfile").read_text()
    for env in (consumer["environment"], supervisor["environment"]):
        assert not any(
            k.endswith(("CATALOG_EPOCH", "PROJECTION_VERSION", "PRODUCER_STREAM_ID"))
            for k in env
        )


def test_oss_compose_pins_node_hostname_alongside_persistent_admission():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    clickhouse = services["clickhouse"]
    supervisor = services["property-catalog-supervisor"]
    consumer = services["fi-property-catalog-consumer"]
    # Without hostname, recreation changes hostName() to the new container ID
    # while server/database/table UUIDs and the immutable admission survive.
    assert clickhouse["hostname"] == "clickhouse"
    assert "clickhouse-data:/var/lib/clickhouse" in clickhouse["volumes"]
    assert "fi-collector-data:/var/lib/fi-collector" in supervisor["volumes"]
    assert "fi-collector-data:/var/lib/fi-collector" in consumer["volumes"]
    assert (
        supervisor["environment"]["PROPERTY_CATALOG_DEV_WRITE_CH_HOST"]
        == clickhouse["hostname"]
    )
    assert supervisor["environment"]["CH25_HOST"] == clickhouse["hostname"]
    for key in ("FI_PROPERTY_CATALOG_CH_URL", "FI_PROPERTY_CATALOG_LEDGER_CH_URL"):
        assert consumer["environment"][key] == "http://clickhouse:8123"


def test_bootstrap_ledger_adds_only_exact_reviewed_selects_and_preserves_writer_grants():
    text = (
        ROOT / "futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh"
    ).read_text()
    loops = re.findall(r"for table in ([^\n]+)\ndo\n(.*?)\ndone", text, re.S)
    ledger = [
        (set(tables.split()), body)
        for tables, body in loops
        if "TO $LEDGER_USER" in body
    ]
    assert len(ledger) == 2
    assert ledger[0][0] == set(subject.PROPERTY_CATALOG_TABLES)
    assert ledger[1][0] == subject._METADATA_TABLES | subject._OPTIONAL_PROOF_TABLES
    assert all(
        "GRANT SELECT ON" in body
        and "INSERT" not in body
        and ".*" not in body
        and "$SOURCE_DATABASE" not in body
        for _, body in ledger
    )
    consumer = [
        (set(tables.split()), body)
        for tables, body in loops
        if "TO $CONSUMER_USER" in body
    ]
    assert len(consumer) == 1 and consumer[0][0] == {
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_deliveries",
    }
    assert "GRANT INSERT ON" in consumer[0][1] and "SELECT" not in consumer[0][1]


def test_actual_application_mapping_shares_fence_and_proof_identity(
    tmp_path, monkeypatch
):
    path = ROOT / "futureagi/scripts/property_catalog_oss/tests/managed_smoke"
    monkeypatch.syspath_prepend(str(path))
    application = importlib.import_module("application_smoke")
    run = SimpleNamespace(
        directory=tmp_path,
        env={},
        manifest={
            "run_id": "owned",
            "project": "owned-project",
            "database": DATABASE,
            "ports": {
                "native": 49120,
                "http": 49121,
                "postgres": 49122,
                "kafka": 49123,
            },
            "candidate_topic": "catalog.candidates",
            "ordered_topic": "catalog.ordered",
        },
    )
    env = application.backend_environment(run, reader=True)
    seq, consumer = application.application_transport_environments(run, env)
    assert (
        env["CH25_HTTP_PORT"] == "49121"
        and env["PROPERTY_CATALOG_DEV_WRITE_CH_PORT"] == "49120"
    )
    assert (
        consumer["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"]
        == seq["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"]
        == env["PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"]
    )
    for name in ("USERNAME", "PASSWORD"):
        assert (
            env["FI_PROPERTY_CATALOG_LEDGER_CH_" + name]
            == consumer["FI_PROPERTY_CATALOG_LEDGER_CH_" + name]
        )
    assert consumer["FI_PROPERTY_CATALOG_CH_URL"] == "http://127.0.0.1:49121"
    assert not (
        tmp_path / IDENTITY_FILENAME
    ).exists()  # Configuration is not a fake initializer.


def automatic_replicated_setup(tmp_path, monkeypatch):
    """Replace wire I/O only; exercise dispatch plus the complete real producer."""
    lane = replicated_setup(tmp_path, monkeypatch)
    lane.settings.PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD = "writer-secret"
    # This is not a usable standalone HTTP route: replicated listeners must be
    # discovered from actual direct nodes, not this partial seed/LB mapping.
    lane.settings.CLICKHOUSE_V2 = {"CH25_HOST": SEED}
    lane.limits, lane.delegations = [], []
    lane.after_preliminary = lambda sql: None

    def factory(**kwargs):
        assert kwargs["user"] == "catalog_proof", "writer opened on discovery seed"
        previous = [c for c, _ in lane.factory.clients if c.host == SEED]
        if kwargs["host"] == SEED and previous:
            assert previous[0].closed, "preliminary driver leaked into delegation"
        driver = lane.factory(**kwargs)
        # A load balancer can choose a different physical node on its next
        # connection. Neither preliminary observation is a writer identity.
        if kwargs["host"] == SEED and previous:
            driver.index, driver.name = 2, "replica2"
        execute = driver.execute_read

        def read(sql, params, **limits):
            lane.limits.append((sql, limits["timeout_ms"]))
            if sql not in {subject._IDENTITY_SQL, subject._TABLES_SQL}:
                return execute(sql, params, **limits)
            assert not driver.closed and limits["settings"]["readonly"] == 2
            ensure_read_statement(sql)
            lane.factory.calls.append((driver.host, sql, params))
            first = lane.factory.probe.rows[driver.name][0]
            if sql == subject._IDENTITY_SQL:
                columns = (
                    "hostname",
                    "server_uuid",
                    "database",
                    "username",
                    "database_uuid",
                )
                rows = [
                    (
                        first["hostname"],
                        first["server_uuid"],
                        driver.database,
                        driver.user,
                        first["database_uuid"],
                    )
                ]
            else:
                columns = ("name", "engine")
                rows = [
                    (r["name"], r["engine"])
                    for r in lane.factory.probe.rows[driver.name]
                ]
            lane.after_preliminary(sql)
            return rows, [(c, "") for c in columns], 0

        driver.execute_read = read
        return driver

    def delegate(*args, **kwargs):
        lane.delegations.append(kwargs)
        return replicated_write_startup.replicated_write_admission_context(
            *args, **kwargs
        )

    monkeypatch.setattr(subject, "replicated_write_admission_context", delegate)
    monkeypatch.setattr(
        subject,
        "admit_catalog_writes",
        lambda *a, **k: pytest.fail("standalone fallback"),
    )
    lane.dispatch_factory = factory
    return lane


@pytest.mark.parametrize("wrapper", [False, True])
@pytest.mark.parametrize(
    "environment,cloud",
    [("development", ""), ("development", "DEV"), ("dev", "DEV"), ("staging", "DEV")],
)
def test_auto_replicated_dispatch_proves_full_members_even_when_lb_changes_node(
    tmp_path, monkeypatch, wrapper, environment, cloud
):
    lane = automatic_replicated_setup(tmp_path, monkeypatch)
    lane.settings.ENV_TYPE, lane.settings.CLOUD_DEPLOYMENT = environment, cloud
    kwargs = {"environ": lane.env, "client_factory": lane.dispatch_factory}
    if wrapper:
        admission = subject.prepare_oss_write_admission(lane.settings, **kwargs)
    else:
        with subject.oss_write_admission_context(lane.settings, **kwargs) as (
            identity,
            admission,
            connections,
        ):
            assert identity == lane.identity
            assert len(connections) == 2
            assert all(
                not c.driver.closed and c.driver.host != SEED for c in connections
            )
            assert {c.driver.port for c in connections} == {19001, 19002}
    assert admission.family == "replicated" and len(admission.members) == 2
    assert all(len(m.tables) == 7 for m in admission.members)
    assert len(lane.factory.clients) == 4  # two seed observations + two direct members
    assert all(c.closed for c, _ in lane.factory.clients)
    assert (
        lane.factory.clients[0][0].index == 1 and lane.factory.clients[1][0].index == 2
    )
    assert any(
        "system.zookeeper_connection" in sql
        for _, _, sql, _ in lane.factory.probe.calls
    )
    assert (
        len(lane.delegations) == 1 and lane.delegations[0]["prefix"] == subject._PREFIX
    )


@pytest.mark.parametrize("bad", ["http_uuid", "missing_clusters", "keeper"])
def test_auto_replicated_real_proof_failure_never_falls_back(
    tmp_path, monkeypatch, bad
):
    lane = automatic_replicated_setup(tmp_path, monkeypatch)
    if bad == "missing_clusters":
        lane.factory.grants = [
            g for g in lane.factory.grants if "system.clusters" not in g
        ]
    elif bad == "http_uuid":

        def corrupt(rows):
            rows[0]["server_uuid"] = str(UUID(int=900))
            return rows

        lane.factory.http_alter = corrupt
    else:

        def expired(driver, kind, rows):
            if kind == "replicas":
                rows[0]["is_session_expired"] = 1
            return rows

        lane.factory.alter = expired
    with pytest.raises(ValueError):
        subject.prepare_oss_write_admission(
            lane.settings, environ=lane.env, client_factory=lane.dispatch_factory
        )
    assert len(lane.delegations) == 1
    assert all(c.closed for c, _ in lane.factory.clients)
    assert not (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).exists()


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "extra",
        "duplicate",
        "unsorted",
        "short_row",
        "columns",
        "mixed",
        "unknown",
        "wrong_base",
    ],
)
def test_topology_contract_rejects_malformed_inventory_before_any_writer(
    tmp_path, monkeypatch, bad
):
    settings, env, factory = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subject,
        "replicated_write_admission_context",
        lambda *a, **k: pytest.fail("malformed topology delegated"),
    )

    def alter(user, sql, rows):
        if sql != subject._TABLES_SQL:
            return rows
        if bad == "missing":
            return rows[:-1]
        if bad == "extra":
            return rows + [("source", "MergeTree")]
        if bad == "duplicate":
            return [rows[0], *rows[:-1]]
        if bad == "unsorted":
            return rows[::-1]
        if bad == "short_row":
            return [(rows[0][0],), *rows[1:]]
        if bad == "columns":
            subject._catalog_family(rows, ("engine", "name"))
        if bad == "mixed":
            return [(rows[0][0], "Replicated" + rows[0][1]), *rows[1:]]
        if bad == "unknown":
            return [(name, "ReplicatedFutureTree") for name, _ in rows]
        return [(name, "MergeTree") for name, _ in rows]

    factory.alter = alter
    with pytest.raises(ValueError):
        subject.prepare_oss_write_admission(
            settings, environ=env, client_factory=factory
        )
    assert all(c.user == "catalog_proof" and c.closed for c, _ in factory.clients)
    assert not factory.origins


@pytest.mark.parametrize(
    "environment,cloud",
    [("development", ""), ("development", "DEV"), ("dev", "DEV"), ("staging", "DEV")],
)
@pytest.mark.parametrize("hosts", [(), ("host1",), ("foreign",), ("host1", "host2")])
def test_standalone_auto_dispatch_preserves_exact_infrastructure_expectations(
    tmp_path, monkeypatch, environment, cloud, hosts
):
    settings, env, factory = setup(tmp_path, monkeypatch)
    settings.ENV_TYPE, settings.CLOUD_DEPLOYMENT = environment, cloud
    settings.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES = hosts
    monkeypatch.setattr(
        subject,
        "replicated_write_admission_context",
        lambda *a, **k: pytest.fail("standalone failure delegated"),
    )
    if hosts in {(), ("host1",)}:
        admission = subject.prepare_oss_write_admission(
            settings, environ=env, client_factory=factory
        )
        assert admission.family == "standalone"
    else:
        with pytest.raises(ValueError, match="infrastructure member expectations"):
            subject.prepare_oss_write_admission(
                settings, environ=env, client_factory=factory
            )
        assert all(c.user == "catalog_proof" for c, _ in factory.clients)
    assert all(c.closed for c, _ in factory.clients)


@pytest.mark.parametrize("elapsed", [29, 31])
def test_auto_dispatch_carries_only_remaining_wall_budget(
    tmp_path, monkeypatch, elapsed
):
    lane = automatic_replicated_setup(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(subject, "monotonic", lambda: clock[0])
    monkeypatch.setattr(replicated_write_startup, "monotonic", lambda: clock[0])

    def consume(sql):
        if sql == subject._TABLES_SQL:
            clock[0] = elapsed

    lane.after_preliminary = consume
    if elapsed > 30:
        with pytest.raises(ValueError, match="timed out"):
            subject.prepare_oss_write_admission(
                lane.settings, environ=lane.env, client_factory=lane.dispatch_factory
            )
        assert not lane.delegations and len(lane.factory.clients) == 1
    else:
        value = subject.prepare_oss_write_admission(
            lane.settings, environ=lane.env, client_factory=lane.dispatch_factory
        )
        assert value.family == "replicated"
        assert lane.delegations[0]["timeout_ms"] == 1000
        assert all(0 < budget <= 1000 for _, budget in lane.limits[2:])
        assert all(
            options["connect_timeout"] <= 1 for _, options in lane.factory.clients[1:]
        )
    assert all(c.closed for c, _ in lane.factory.clients)
