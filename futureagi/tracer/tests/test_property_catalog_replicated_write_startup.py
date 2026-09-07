"""Offline discovery through the REAL schema/admission/Keeper producer.

Only native/HTTP I/O is replaced. No live resources, source or catalog writes,
schema installation, claimed admission hashes, or injected success proofs.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.server_readonly import ensure_read_statement
from tracer.services.clickhouse.v2.catalog_prod_schema import CatalogProdSchemaError
from tracer.services.clickhouse.v2.property_catalog import (
    replicated_write_startup as subject,
)
from tracer.services.clickhouse.v2.property_catalog import write_admission
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
)
from tracer.services.clickhouse.v2.property_catalog.keeper_membership import (
    KeeperMembershipError,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
)
from tracer.tests.test_property_catalog_write_admission import (
    DATABASE,
    Probe,
    installation,
)

pytestmark = pytest.mark.unit
SEED = "catalog.seed.internal"
PROD_DATABASE = "property_catalog"


class Factory:
    def __init__(self, count=2, *, database=DATABASE):
        self.probe = Probe(count)
        self.count, self.database = count, database
        if database != DATABASE:
            for rows in self.probe.rows.values():
                for row in rows:
                    for key in ("database", "create_table_query", "keeper_path"):
                        row[key] = row[key].replace(DATABASE, database)
                    row["create_sha256"] = hashlib.sha256(
                        row["create_table_query"].encode()
                    ).hexdigest()
        self.clients, self.calls, self.origins = [], [], []
        self.alter = lambda driver, kind, rows: rows
        self.http_alter = lambda rows: rows
        self.grants = [
            f"GRANT SELECT ON `{database}`.{table} TO catalog_proof"
            for table in sorted(subject.PROPERTY_CATALOG_TABLES)
        ] + [
            f"GRANT SELECT ON system.{table} TO catalog_proof"
            for table in sorted(subject._METADATA_TABLES)
        ]
        self.extra_clusters = []
        self.direct_alias = {}
        self.fail_connect = None

    def cluster_rows(self, local):
        return [
            {
                "cluster": "configured_catalog",
                "shard_num": 1,
                "replica_num": i,
                "host_name": f"replica{i}.internal",
                "port": 19000 + i,
                "is_local": int(i == local),
            }
            for i in range(1, self.count + 1)
        ] + deepcopy(self.extra_clusters)

    def __call__(self, **kwargs):
        if kwargs["host"] == self.fail_connect:
            raise OSError("offline unreachable node")
        factory = self
        index = (
            1
            if kwargs["host"] == SEED
            else int(kwargs["host"].split(".")[0].removeprefix("replica"))
        )
        index = self.direct_alias.get(index, index) if kwargs["host"] != SEED else index

        class Driver:
            def __init__(self):
                for key in (
                    "host",
                    "port",
                    "user",
                    "database",
                    "server_enforced_readonly",
                ):
                    setattr(self, key, kwargs[key])
                self.closed = False
                self.index, self.name = index, f"replica{index}"

            def execute_read(self, sql, params, **limits):
                assert not self.closed
                ensure_read_statement(sql)
                assert limits["settings"]["readonly"] == 2
                assert 0 < limits["timeout_ms"] <= 30000
                factory.calls.append((self.host, sql, deepcopy(params)))
                first = factory.probe.rows[self.name][0]
                if sql == subject._IDENTITY_SQL:
                    rows, kind = (
                        [
                            {
                                "hostname": first["hostname"],
                                "server_uuid": first["server_uuid"],
                                "database": self.database,
                                "username": self.user,
                                "database_uuid": first["database_uuid"],
                                "native_port": 19000 + self.index,
                            }
                        ],
                        "identity",
                    )
                elif sql == subject._REPLICAS_SQL:
                    rows, kind = (
                        [
                            {
                                "table": row["name"],
                                "replica_name": row["replica_name"],
                                "replica_names": deepcopy(row["replica_names"]),
                                "total_replicas": row["total_replicas"],
                                "active_replicas": row["active_replicas"],
                                "is_readonly": row["readonly"],
                                "is_session_expired": row["session_expired"],
                            }
                            for row in factory.probe.rows[self.name]
                        ],
                        "replicas",
                    )
                elif sql == subject._CLUSTERS_SQL:
                    rows = [
                        row
                        for row in factory.cluster_rows(self.index)
                        if not params["cluster"] or row["cluster"] == params["cluster"]
                    ]
                    kind = "clusters"
                elif sql == "SHOW GRANTS":
                    rows, kind = (
                        [{"GRANTS": grant} for grant in factory.grants],
                        "grants",
                    )
                elif "getServerPort" in sql:
                    assert params["database"] == self.database
                    assert params["port_name"] in {"http_port", "https_port"}
                    rows, kind = (
                        [
                            {
                                "hostname": first["hostname"],
                                "connected_database": self.database,
                                "database_uuid": first["database_uuid"],
                                "http_port": 18000 + self.index,
                            }
                        ],
                        "route",
                    )
                else:
                    return factory.probe.driver_read(self.name)(sql, params, **limits)
                columns = tuple(rows[0]) if rows else ()
                rows = factory.alter(self, kind, deepcopy(rows))
                return (
                    [tuple(row[c] for c in columns) for row in rows],
                    [(c, "") for c in columns],
                    0,
                )

            def close(self):
                self.closed = True

        client = Driver()
        self.clients.append((client, kwargs))
        return client

    def http(self, connection, route, sql, timeout_ms, limit):
        index = connection.driver.index
        assert (
            route.origin
            == f"{connection.http_scheme}://replica{index}.internal:{18000 + index}"
        )
        assert connection.driver.host != SEED and not connection.driver.closed
        assert sql == write_admission._INVENTORY_SQL and limit == 8
        self.origins.append(route.origin)
        rows = self.probe.alter(
            "http", connection.name, deepcopy(self.probe.rows[connection.name])
        )
        return self.http_alter(rows)


def setup(tmp_path, monkeypatch, *, count=2, production=False, shim=False):
    database = PROD_DATABASE if production else DATABASE
    identity = replace(
        installation(),
        environment="production" if production else "development",
        target_database=database,
    )
    path = tmp_path / IDENTITY_FILENAME
    path.write_bytes(identity.encode())
    path.chmod(0o600)
    prefix = subject._DEV if not production or shim else subject._PROD
    settings = SimpleNamespace(
        ENV_TYPE="production" if production else "development",
        CLOUD_DEPLOYMENT="US" if production else "",
        PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC=identity.candidate_topic,
        PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC=identity.ordered_topic,
        **{
            prefix + "REVISION_FENCE_FILE": str(tmp_path / "fence.json"),
            prefix + "WRITE_CH_DATABASE": database,
            prefix + "WRITE_CH_HOST": SEED,
            prefix + "WRITE_CH_PORT": "19999",
            prefix + "WRITE_CH_USER": "catalog_writer",
            prefix + "EXPECTED_WRITE_CH_HOSTNAMES": tuple(
                f"host{i}" for i in range(1, count + 1)
            ),
            (subject._PROD if production else subject._DEV)
            + "TARGET_DATABASE": database,
        },
    )
    if shim:
        # The real production overlay leaves unrelated DEV command defaults on its base.
        settings.PROPERTY_CATALOG_DEV_TARGET_DATABASE = "property_catalog_dev_oss"
    env = {
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "catalog_proof",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "proof-secret",
    }
    factory = Factory(count, database=database)

    def producer(*args, **kwargs):
        return write_admission.admit_catalog_writes(
            *args, **kwargs, http_read=factory.http
        )

    monkeypatch.setattr(subject, "admit_catalog_writes", producer)
    return SimpleNamespace(
        settings=settings,
        env=env,
        factory=factory,
        identity=identity,
        path=tmp_path,
        prefix=prefix,
    )


def context(lane, **kwargs):
    return subject.replicated_write_admission_context(
        lane.settings, environ=lane.env, client_factory=lane.factory, **kwargs
    )


@pytest.mark.parametrize(
    "production,shim,count", [(False, False, 2), (True, False, 3), (True, True, 3)]
)
def test_actual_producer_admits_exact_full_members_and_keeps_drivers_alive(
    tmp_path, monkeypatch, production, shim, count
):
    lane = setup(tmp_path, monkeypatch, production=production, shim=shim, count=count)
    with context(lane, **({"prefix": subject._DEV} if shim else {})) as (
        identity,
        admission,
        connections,
    ):
        assert identity == lane.identity and admission.family == "replicated"
        assert len(connections) == count and len(admission.members) == count
        assert tuple(c.name for c in connections) == tuple(
            m.name for m in admission.members
        )
        assert all(len(member.tables) == 7 for member in admission.members)
        assert all(not client.closed for client, _ in lane.factory.clients)
        assert all(
            c.driver.port == 19000 + c.driver.index and c.driver.host != SEED
            for c in connections
        )
        assert len({m.server_uuid for m in admission.members}) == count
        assert {m.url for m in admission.members} == {
            f"http://replica{i}.internal:{18000 + i}" for i in range(1, count + 1)
        }
        assert any(
            "system.zookeeper_connection" in sql
            for _, _, sql, _ in lane.factory.probe.calls
        )
        assert (
            lane.path / write_admission.WRITE_ADMISSION_FILENAME
        ).read_bytes() == admission.encode()
    assert len(lane.factory.clients) == count + 1
    assert all(client.closed for client, _ in lane.factory.clients)
    assert all(
        options["user"] == "catalog_proof"
        and options["server_enforced_readonly"]
        and options["pool_size"] == 1
        for _, options in lane.factory.clients
    )
    assert not any(
        ".spans" in sql or sql.startswith(("INSERT", "CREATE", "ALTER"))
        for _, sql, _ in lane.factory.calls
    )


def test_explicit_cluster_resolves_configured_alias_but_never_selects_arbitrarily(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    lane.factory.extra_clusters = [
        {**row, "cluster": "other_configured_alias"}
        for row in lane.factory.cluster_rows(1)
    ]
    with pytest.raises(write_admission.WriteAdmissionError, match="unique"):
        with context(lane):
            pytest.fail("ambiguous infrastructure admitted")
    with context(lane, cluster="configured_catalog") as (_, admission, _):
        assert len(admission.members) == 2


def test_development_without_hostname_override_uses_full_actual_cluster_and_keeper(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    lane.settings.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES = ()
    with context(lane) as (_, admission, _):
        assert len(admission.members) == 2 and admission.keeper_identity_sha256


def test_configured_https_service_is_protocol_only_not_member_route(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    lane.env[subject._LEDGER + "URL"] = "https://loadbalancer.internal:54321"
    with context(lane) as (_, admission, _):
        assert all(m.url.startswith("https://replica") for m in admission.members)
        assert all(
            "loadbalancer" not in m.url and ":54321" not in m.url
            for m in admission.members
        )


@pytest.mark.parametrize(
    "bad",
    [
        "environment",
        "cloud",
        "prefix",
        "relative_fence",
        "foreign_target",
        "foreign_ledger_db",
        "topic",
        "no_proof_user",
        "no_proof_password",
        "same_principal",
        "admin_proof",
        "admin_writer",
        "port_zero",
        "port_bool",
        "port_missing",
        "seed_url",
        "duplicate_hosts",
        "string_hosts",
        "single_host",
        "host_conflict",
        "http_missing_port",
        "http_credentials",
        "http_path",
        "http_redirect_scheme",
    ],
)
def test_local_configuration_rejected_before_constructing_any_client(
    tmp_path, monkeypatch, bad
):
    lane = setup(tmp_path, monkeypatch)
    s, e, kwargs = lane.settings, lane.env, {}
    if bad == "environment":
        s.ENV_TYPE = "unknown"
    elif bad == "cloud":
        s.CLOUD_DEPLOYMENT = "US"
    elif bad == "prefix":
        kwargs["prefix"] = subject._PROD
    elif bad == "relative_fence":
        s.PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE = "relative/fence"
    elif bad == "foreign_target":
        s.PROPERTY_CATALOG_DEV_TARGET_DATABASE = "other_database"
    elif bad == "foreign_ledger_db":
        e[subject._LEDGER + "DATABASE"] = "other_database"
    elif bad == "topic":
        s.PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC = "other.topic"
    elif bad == "no_proof_user":
        del e[subject._LEDGER + "USERNAME"]
    elif bad == "no_proof_password":
        del e[subject._LEDGER + "PASSWORD"]
    elif bad == "same_principal":
        e[subject._LEDGER + "USERNAME"] = "catalog_writer"
    elif bad == "admin_proof":
        e[subject._LEDGER + "USERNAME"] = "default"
    elif bad == "admin_writer":
        s.PROPERTY_CATALOG_DEV_WRITE_CH_USER = "admin"
    elif bad == "port_zero":
        s.PROPERTY_CATALOG_DEV_WRITE_CH_PORT = 0
    elif bad == "port_bool":
        s.PROPERTY_CATALOG_DEV_WRITE_CH_PORT = True
    elif bad == "port_missing":
        del s.PROPERTY_CATALOG_DEV_WRITE_CH_PORT
    elif bad == "seed_url":
        s.PROPERTY_CATALOG_DEV_WRITE_CH_HOST = "http://seed"
    elif bad == "duplicate_hosts":
        s.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES = ("host1", "host1")
    elif bad == "string_hosts":
        s.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES = "host1,host2"
    elif bad == "single_host":
        s.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAMES = ("host1",)
    elif bad == "host_conflict":
        s.PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME = "foreign"
    else:
        e[subject._LEDGER + "URL"] = {
            "http_missing_port": "https://seed",
            "http_credentials": "http://user:pass@seed:8123",
            "http_path": "http://seed:8123/catalog",
            "http_redirect_scheme": "ftp://seed:8123",
        }[bad]
    with pytest.raises(ValueError):
        with context(lane, **kwargs):
            pytest.fail("invalid local config admitted")
    assert lane.factory.clients == []


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_production_never_relaxes_exact_three_expected_members(
    tmp_path, monkeypatch, count
):
    lane = setup(tmp_path, monkeypatch, production=True, count=3)
    lane.settings.PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES = tuple(
        f"host{i}" for i in range(count)
    )
    with pytest.raises(write_admission.WriteAdmissionError, match="exactly three"):
        with context(lane):
            pytest.fail("wrong production N admitted")
    assert lane.factory.clients == []


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "extra",
        "duplicate",
        "two_shards",
        "replica_gap",
        "route_alias",
        "port_zero",
        "port_string",
        "no_local",
        "two_local",
        "foreign_cluster",
    ],
)
def test_malformed_or_incomplete_configured_routes_are_not_direct_admission(
    tmp_path, monkeypatch, bad
):
    lane = setup(tmp_path, monkeypatch)

    def alter(driver, kind, rows):
        if kind != "clusters":
            return rows
        if bad == "missing":
            return rows[:-1]
        if bad == "extra":
            return rows + [{**rows[0], "replica_num": 3, "host_name": "third.internal"}]
        if bad == "duplicate":
            return rows + [rows[0]]
        if bad == "two_shards":
            rows[1]["shard_num"] = 2
        elif bad == "replica_gap":
            rows[1]["replica_num"] = 3
        elif bad == "route_alias":
            rows[1].update(host_name=rows[0]["host_name"], port=rows[0]["port"])
        elif bad == "port_zero":
            rows[0]["port"] = 0
        elif bad == "port_string":
            rows[0]["port"] = "19001"
        elif bad == "no_local":
            rows[0]["is_local"] = 0
        elif bad == "two_local":
            rows[1]["is_local"] = 1
        elif bad == "foreign_cluster":
            rows[0]["cluster"] = "foreign"
        return rows

    lane.factory.alter = alter
    with pytest.raises(write_admission.WriteAdmissionError):
        with context(lane, cluster="configured_catalog"):
            pytest.fail("bad routes admitted")
    assert all(c.closed for c, _ in lane.factory.clients)
    assert not (lane.path / write_admission.WRITE_ADMISSION_FILENAME).exists()


@pytest.mark.parametrize(
    "bad",
    [
        "missing_table",
        "extra_table",
        "foreign_member",
        "inactive",
        "expired",
        "readonly",
        "mixed_tables",
        "bool_count",
        "unsorted_members",
    ],
)
def test_all_seven_tables_must_agree_on_exact_live_full_membership(
    tmp_path, monkeypatch, bad
):
    lane = setup(tmp_path, monkeypatch)

    def alter(driver, kind, rows):
        if kind != "replicas":
            return rows
        if bad == "missing_table":
            return rows[:-1]
        if bad == "extra_table":
            return rows + [{**rows[0], "table": "extra"}]
        if bad == "foreign_member":
            rows[0]["replica_name"] = "foreign"
        elif bad == "inactive":
            rows[0]["active_replicas"] = 1
        elif bad == "expired":
            rows[0]["is_session_expired"] = 1
        elif bad == "readonly":
            rows[0]["is_readonly"] = 1
        elif bad == "mixed_tables":
            rows[0]["replica_name"] = "replica2"
        elif bad == "bool_count":
            rows[0]["total_replicas"] = True
        elif bad == "unsorted_members":
            rows[0]["replica_names"].reverse()
        return rows

    lane.factory.alter = alter
    with pytest.raises(write_admission.WriteAdmissionError):
        with context(lane):
            pytest.fail("bad membership admitted")
    assert not (lane.path / write_admission.WRITE_ADMISSION_FILENAME).exists()


@pytest.mark.parametrize(
    "bad",
    [
        "wildcard",
        "insert",
        "admin",
        "role",
        "missing_table7",
        "missing_clusters",
        "missing_query_log",
        "foreign_table",
        "other_user",
    ],
)
def test_ledger_principal_has_exact_seven_table_and_proof_metadata_select_only(
    tmp_path, monkeypatch, bad
):
    lane = setup(tmp_path, monkeypatch)
    if bad.startswith("missing_"):
        needle = {
            "missing_table7": "property_catalog_activation_control_events",
            "missing_clusters": "system.clusters",
            "missing_query_log": "system.query_log",
        }[bad]
        lane.factory.grants = [g for g in lane.factory.grants if needle not in g]
    else:
        lane.factory.grants.append(
            {
                "wildcard": f"GRANT SELECT ON {DATABASE}.* TO catalog_proof",
                "insert": f"GRANT SELECT, INSERT ON {DATABASE}.property_catalog_deliveries TO catalog_proof",
                "admin": "GRANT ALL ON *.* TO catalog_proof",
                "role": "GRANT privileged_role TO catalog_proof",
                "foreign_table": "GRANT SELECT ON default.spans TO catalog_proof",
                "other_user": f"GRANT SELECT ON {DATABASE}.property_catalog_deliveries TO other_user",
            }[bad]
        )
    with pytest.raises(write_admission.WriteAdmissionError, match="grant|principal"):
        with context(lane):
            pytest.fail("unreviewed grants admitted")
    assert all(c.closed for c, _ in lane.factory.clients)


@pytest.mark.parametrize(
    "bad",
    [
        "native_alias",
        "foreign_host",
        "native_port",
        "native_server",
        "http_server",
        "http_database",
        "schema",
        "keeper_owner",
        "keeper_uuid",
        "shared_session",
    ],
)
def test_real_producer_rejects_alias_foreign_schema_http_and_keeper_evidence(
    tmp_path, monkeypatch, bad
):
    lane = setup(tmp_path, monkeypatch)
    if bad == "native_alias":
        lane.factory.direct_alias[2] = 1
    elif bad == "foreign_host":
        lane.factory.probe.rows["replica2"][0]["hostname"] = "foreign"
    elif bad in {"native_port", "native_server"}:

        def alter(driver, kind, rows):
            if kind == "identity" and driver.index == 2:
                rows[0]["native_port" if bad == "native_port" else "server_uuid"] = (
                    9999 if bad == "native_port" else str(UUID(int=1))
                )
            return rows

        lane.factory.alter = alter
    elif bad in {"http_server", "http_database"}:

        def http(rows):
            for row in rows:
                row["server_uuid" if bad == "http_server" else "database_uuid"] = str(
                    UUID(int=999)
                )
            return rows

        lane.factory.http_alter = http
    elif bad == "schema":
        row = lane.factory.probe.rows["replica2"][0]
        row["create_table_query"] += " SETTINGS index_granularity = 7"
        row["create_sha256"] = hashlib.sha256(
            row["create_table_query"].encode()
        ).hexdigest()
    elif bad == "shared_session":
        lane.factory.probe.sessions["replica2"] = lane.factory.probe.sessions[
            "replica1"
        ]
    else:

        def alter(kind, name, rows):
            if kind == "keeper":
                rows[0]["owner" if bad == "keeper_owner" else "value"] = (
                    999
                    if bad == "keeper_owner"
                    else "UUID_'00000000-0000-4000-8000-000000000999'"
                )
            return rows

        lane.factory.probe.alter = alter
    with pytest.raises((ValueError, KeeperMembershipError, CatalogProdSchemaError)):
        with context(lane):
            pytest.fail("unproven physical membership admitted")
    assert not (lane.path / write_admission.WRITE_ADMISSION_FILENAME).exists()
    assert all(c.closed for c, _ in lane.factory.clients)


def test_peer_infrastructure_config_disagreement_is_rejected_before_producer(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)

    def alter(driver, kind, rows):
        if kind == "clusters" and driver.index == 2:
            rows[0]["port"] += 1
        return rows

    lane.factory.alter = alter
    with pytest.raises(write_admission.WriteAdmissionError, match="cluster routes"):
        with context(lane):
            pytest.fail("divergent peer config admitted")
    assert lane.factory.origins == []


def test_metadata_race_after_actual_attestation_does_not_yield_context(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)

    def alter(driver, kind, rows):
        if kind == "identity" and lane.factory.origins:
            rows[0]["server_uuid"] = str(UUID(int=999))
        return rows

    lane.factory.alter = alter
    with pytest.raises(write_admission.WriteAdmissionError, match="changed"):
        with context(lane):
            pytest.fail("post-proof race yielded writable context")
    assert all(c.closed for c, _ in lane.factory.clients)
    # A valid immutable admission may already exist; retry reattests it, never overwrites it.
    before = (lane.path / write_admission.WRITE_ADMISSION_FILENAME).read_bytes()
    lane.factory.alter = lambda driver, kind, rows: rows
    with context(lane) as (_, admission, _):
        assert admission.encode() == before


def test_restart_reuses_exact_admission_without_resetting_identity(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    with context(lane) as (_, first, _):
        pass
    lane.factory.probe.sessions = {
        name: value + 100 for name, value in lane.factory.probe.sessions.items()
    }
    with context(lane) as (identity, second, _):
        assert first == second and identity == lane.identity
    assert all(c.closed for c, _ in lane.factory.clients)


def test_client_cleanup_on_partial_connection_failure_and_caller_error(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    lane.factory.fail_connect = "replica2.internal"
    with pytest.raises(OSError, match="unreachable"):
        with context(lane):
            pytest.fail("missing node admitted")
    assert all(c.closed for c, _ in lane.factory.clients)
    lane.factory.fail_connect = None
    with pytest.raises(RuntimeError, match="caller"):
        with context(lane):
            raise RuntimeError("caller")
    assert all(c.closed for c, _ in lane.factory.clients)


def test_expired_shared_budget_closes_seed_without_admission(tmp_path, monkeypatch):
    lane = setup(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(subject, "monotonic", lambda: clock[0])

    def alter(driver, kind, rows):
        clock[0] = 31
        return rows

    lane.factory.alter = alter
    with pytest.raises(write_admission.WriteAdmissionError):
        with context(lane):
            pytest.fail("expired discovery admitted")
    assert all(c.closed for c, _ in lane.factory.clients)
    assert not lane.factory.origins


def test_no_version_or_producer_env_setting_overrides_persisted_identity(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    lane.settings.PROPERTY_CATALOG_DEV_CATALOG_EPOCH = 60000
    lane.settings.PROPERTY_CATALOG_DEV_PROJECTION_VERSION = 60000
    lane.settings.PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID = str(UUID(int=700))
    with context(lane) as (identity, _, _):
        assert identity == lane.identity


def test_production_target_cannot_be_arbitrary_dev_namespace(tmp_path, monkeypatch):
    lane = setup(tmp_path, monkeypatch, production=True, count=3)
    lane.settings.PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_DATABASE = DATABASE
    lane.settings.PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE = DATABASE
    with pytest.raises(PropertyCatalogPublishError):
        with context(lane):
            pytest.fail("development database admitted as production")
    assert lane.factory.clients == []


def test_production_uses_existing_target_setting_without_new_writer_database_knob(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch, production=True, count=3)
    del lane.settings.PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_DATABASE
    with context(lane) as (identity, admission, _):
        assert admission.database == identity.target_database == PROD_DATABASE


def test_production_actual_two_member_inventory_cannot_use_three_member_expectations(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch, production=True, count=3)
    for rows in lane.factory.probe.rows.values():
        for row in rows:
            row.update(
                replica_names=["replica1", "replica2"],
                total_replicas=2,
                active_replicas=2,
            )
    with pytest.raises(write_admission.WriteAdmissionError, match="full membership"):
        with context(lane):
            pytest.fail("two real members admitted as production three")
    assert len(lane.factory.clients) == 1


def test_exact_metadata_grant_contract_has_no_source_or_write_privileges():
    assert subject._METADATA_TABLES == {
        "databases",
        "tables",
        "replicas",
        "clusters",
        "query_log",
        "zookeeper",
        "zookeeper_connection",
    }
    assert subject._OPTIONAL_METADATA_TABLES == {"parts"}


def test_existing_reviewed_parts_select_is_allowed_but_not_required(
    tmp_path, monkeypatch
):
    lane = setup(tmp_path, monkeypatch)
    assert not any("system.parts" in g for g in lane.factory.grants)
    with context(lane) as (_, original, _):
        assert original.family == "replicated"
    lane.factory.grants.append("GRANT SELECT ON system.parts TO catalog_proof")
    with context(lane) as (_, repeated, _):
        assert repeated == original


@pytest.mark.parametrize("budget", [0, -1, True, 30001, None, "1000", 1.5])
def test_invalid_remaining_deadline_rejected_before_connection(
    tmp_path, monkeypatch, budget
):
    lane = setup(tmp_path, monkeypatch)
    with pytest.raises(
        write_admission.WriteAdmissionError, match="bounded remaining deadline"
    ):
        with context(lane, timeout_ms=budget):
            pytest.fail("invalid deadline admitted")
    assert not lane.factory.clients


def test_reduced_deadline_is_not_reset_during_discovery(tmp_path, monkeypatch):
    lane = setup(tmp_path, monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(subject, "monotonic", lambda: clock[0])

    def consume(driver, kind, rows):
        clock[0] = 1.1
        return rows

    lane.factory.alter = consume
    with pytest.raises(write_admission.WriteAdmissionError):
        with context(lane, timeout_ms=1000):
            pytest.fail("child budget reset")
    assert len(lane.factory.clients) == 1 and not lane.factory.origins
    assert all(c.closed for c, _ in lane.factory.clients)
