"""Offline contracts for the new Linux helper; never imports Django apps."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Load by disjoint filename: do not replace either harness's generic run module.
HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "linux_application_runner", HERE / "linux_application_runner.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def manifest():
    return {
        "run_id": "0123456789abcdef",
        "project": "pcapplication-0123456789abcdef",
        "password": "b" * 64,
        "database": "th7247_catalog_prod_0123456789abcdef",
        "candidate_topic": "owned.candidates",
        "ordered_topic": "owned.ordered",
    }


def addresses():
    return {
        name: "172.27.0." + str(n) for n, name in enumerate(runner.SERVICE_PORTS, 2)
    }


class ConfigurationTests(unittest.TestCase):
    def test_production_normal_controller_routes_without_manual_installation_fields(
        self,
    ):
        env = runner.python_environment(Path("/fixture"), manifest(), addresses())
        self.assertEqual(env["ENV_TYPE"], "production")
        self.assertEqual(env["CLOUD_DEPLOYMENT"], "US")
        self.assertEqual(
            env["PROPERTY_CATALOG_LIFECYCLE_ACK"],
            "PROPERTY_CATALOG_PRODUCTION_LIFECYCLE_V1",
        )
        self.assertEqual(env["PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE"], "all")
        self.assertEqual(env["PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST"], "replica1")
        self.assertEqual(env["PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT"], "9000")
        self.assertEqual(
            env["PROPERTY_CATALOG_LIFECYCLE_EXPECTED_PG_SERVER_ADDRESS"],
            addresses()["postgres"],
        )
        self.assertEqual(
            len(
                env["PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES"].split(",")
            ),
            3,
        )
        self.assertEqual(env["PG_USER"], "property_catalog_oss_reader")
        self.assertEqual(env["CH25_SERVER_ENFORCED_READONLY"], "true")
        self.assertEqual(env["PROPERTY_CATALOG_LIFECYCLE_MAX_WALL_MS"], "100000")
        self.assertEqual(
            env["PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER"], "pc_activation"
        )
        for key in env:
            self.assertFalse(
                key.endswith(
                    (
                        "CATALOG_EPOCH",
                        "PROJECTION_VERSION",
                        "PRODUCER_STREAM_ID",
                        "RUNTIME_FACTORY",
                    )
                ),
                key,
            )
        self.assertNotIn("PROPERTY_CATALOG_OSS_SUPERVISOR_ACK", env)

    def test_each_actual_read_node_uses_existing_production_read_gate(self):
        for node in runner.NODES:
            env = runner.python_environment(
                Path("/fixture"), manifest(), addresses(), reader_node=node
            )
            self.assertEqual(env["PROPERTY_CATALOG_CH_HOST"], node)
            self.assertEqual(env["PROPERTY_CATALOG_READ_MODE"], "read")
            self.assertEqual(env["PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE"], "all")
            self.assertEqual(
                env["PROPERTY_CATALOG_PROD_READ_ACK"],
                "I_ACKNOWLEDGE_PROD_READ_ONLY_UNIFIED_PROPERTY_CATALOG",
            )
        with self.assertRaises(ValueError):
            runner.python_environment(
                Path("/fixture"), manifest(), addresses(), reader_node="foreign"
            )

    def test_actual_go_contract_shared_fence_and_spool_no_candidate_identity(self):
        seq, consumer, collector = runner.go_environments(Path("/fixture"), manifest())
        for env in (seq, consumer, collector):
            self.assertEqual(env["FI_PROPERTY_CATALOG_ENVIRONMENT"], "production")
            self.assertEqual(
                env["FI_PROPERTY_CATALOG_PROD_ACK"],
                "UNIFIED_PROPERTY_CATALOG_V1_PRODUCTION",
            )
            self.assertNotIn("FI_PROPERTY_CATALOG_DEV_ACK", env)
            self.assertTrue(
                all(
                    not k.endswith(
                        ("CATALOG_EPOCH", "PROJECTION_VERSION", "PRODUCER_STREAM_ID")
                    )
                    for k in env
                )
            )
        self.assertEqual(
            seq["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"],
            consumer["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"],
        )
        self.assertEqual(
            seq["FI_PROPERTY_CATALOG_SPOOL_DIR"], "/fixture/application-spool"
        )
        self.assertEqual(consumer["FI_PROPERTY_CATALOG_CH_URL"], "http://replica1:8123")
        self.assertEqual(consumer["FI_PROPERTY_CATALOG_CH_USERNAME"], "pc_consumer")
        self.assertEqual(
            consumer["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"], "pc_ledger"
        )
        self.assertEqual(collector["FI_PROPERTY_CATALOG_MODE"], "kafka")
        self.assertEqual(
            collector["FI_PROPERTY_CATALOG_KAFKA_TOPIC"], manifest()["candidate_topic"]
        )
        self.assertTrue(
            all(
                "LEDGER_CH" not in k and "PROPERTY_CATALOG_CH_" not in k
                for k in collector
            )
        )

    def test_environment_does_not_inherit_credentials_or_operator_knobs(self):
        with patch.dict(
            os.environ,
            {
                "PG_PASSWORD": "foreign",
                "DOCKER_HOST": "tcp://foreign",
                "PYTHONPATH": "/foreign",
                "FI_PROPERTY_CATALOG_CATALOG_EPOCH": "9",
                "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY": "foreign",
            },
        ):
            envs = [
                runner.python_environment(Path("/fixture"), manifest(), addresses()),
                *runner.go_environments(Path("/fixture"), manifest()),
            ]
        for env in envs:
            self.assertNotIn("DOCKER_HOST", env)
            self.assertNotIn("FI_PROPERTY_CATALOG_CATALOG_EPOCH", env)
            self.assertNotIn("PROPERTY_CATALOG_DEV_RUNTIME_FACTORY", env)
            self.assertNotEqual(env.get("PG_PASSWORD"), "foreign")

    def test_exact_table_grants_separate_production_principals(self):
        m = manifest()
        database = m["database"]
        capture_database = database + "_source_capture"
        sql = runner.grants(m)
        lifecycle = [
            s for s in sql if s.startswith("GRANT") and s.endswith(" TO pc_lifecycle")
        ]
        catalog_grants = [s for s in lifecycle if f" ON {database}." in s]
        self.assertEqual(
            catalog_grants,
            [
                f"GRANT SELECT, INSERT ON {database}.{table} TO pc_lifecycle"
                for table in runner.TABLES[:-1]
            ],
        )
        self.assertEqual(len(catalog_grants), 6)
        self.assertEqual(
            [s for s in lifecycle if s not in catalog_grants],
            [
                "GRANT SELECT ON default.spans TO pc_lifecycle",
                "GRANT SELECT(database, table, is_readonly, is_session_expired, queue_size, "
                "active_replicas, total_replicas) ON system.replicas TO pc_lifecycle",
                f"GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON {capture_database}.* TO pc_lifecycle",
            ],
        )
        self.assertFalse(any(runner.TABLES[-1] in s for s in lifecycle))
        control = [
            s for s in sql if s.startswith("GRANT") and s.endswith(" TO pc_activation")
        ]
        self.assertEqual(len(control), 2)
        self.assertEqual(
            len(
                [
                    s
                    for s in sql
                    if s.startswith("GRANT INSERT") and s.endswith(" TO pc_consumer")
                ]
            ),
            3,
        )
        self.assertEqual(
            len(
                [
                    s
                    for s in sql
                    if s.startswith("GRANT SELECT ON") and s.endswith(" TO pc_ledger")
                ]
            ),
            15,
        )
        self.assertEqual(
            [s for s in sql if s.startswith("GRANT") and s.endswith(" TO pc_ingest")],
            ["GRANT INSERT ON default.spans TO pc_ingest"],
        )
        self.assertEqual(
            [s for s in sql if ".*" in s],
            [
                f"GRANT SELECT ON {capture_database}.* TO pc_source",
                f"GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON {capture_database}.* TO pc_lifecycle",
            ],
        )
        self.assertFalse(
            any("ON CLUSTER" in s or "WITH GRANT OPTION" in s for s in sql)
        )

    def test_source_capture_metadata_stays_exact_and_readonly(self):
        m = manifest()
        sql = runner.grants(m)
        source = [
            s for s in sql if s.startswith("GRANT") and s.endswith(" TO pc_source")
        ]
        self.assertEqual(
            source,
            [
                "GRANT SELECT ON default.spans TO pc_source",
                f"GRANT SELECT ON {m['database']}_source_capture.* TO pc_source",
                "GRANT SELECT ON system.settings TO pc_source",
                "GRANT SELECT(database, table, active, name, hash_of_all_files, rows, bytes_on_disk, disk_name) ON system.parts TO pc_source",
                "GRANT SELECT(name, uuid, engine) ON system.databases TO pc_source",
                "GRANT SELECT(database, name, uuid, engine, create_table_query, storage_policy) ON system.tables TO pc_source",
                "GRANT SELECT(policy_name, disks) ON system.storage_policies TO pc_source",
                "GRANT SELECT(database, table, name, type, default_kind, default_expression) ON system.columns TO pc_source",
                "GRANT SELECT(database, table, active, name, rows, bytes_on_disk, column, type) ON system.parts_columns TO pc_source",
                "GRANT SELECT(name, path, total_space, unreserved_space, keep_free_space, type, is_read_only) ON system.disks TO pc_source",
                "GRANT SELECT(database, table, zookeeper_path, zookeeper_name) ON system.replicas TO pc_source",
            ],
        )
        profile = next(s for s in sql if s.startswith("ALTER USER pc_source "))
        self.assertIn("readonly=1,", profile)
        self.assertIn("max_result_bytes=67108864,", profile)
        self.assertFalse(any("CREATE DATABASE" in s for s in sql))


class BootstrapTests(unittest.TestCase):
    def bootstrap(self, clients):
        from tracer.services.clickhouse.v2 import catalog_prod_schema

        m = {**manifest(), "files": {"schema.json": "planned-hash"}}
        # Isolate filesystem planning and the catalog installer, while invoking
        # the actual bootstrap loop and exact grant generator on mocked clients.
        lane = SimpleNamespace(
            canonical=lambda _: b"planned-schema",
            digest=lambda _: "planned-hash",
            read_file=lambda _: b"planned-schema",
            schema=lambda _: {},
        )
        with (
            patch.dict(sys.modules, {"application_lane": lane}),
            patch.object(
                catalog_prod_schema, "install_catalog_prod_schema", return_value="{}"
            ),
            patch.object(runner, "save"),
            patch.object(runner, "client", side_effect=clients) as connect,
        ):
            runner.bootstrap(Path("/fixture"), m)
        return m, connect

    def test_each_member_gets_fresh_atomic_namespace_before_role_grants(self):
        catalog, *members = [Mock() for _ in range(4)]
        m, connect = self.bootstrap([catalog, *members])
        self.assertEqual(
            [call.args for call in connect.call_args_list],
            [(m,), *((m, node) for node in runner.NODES)],
        )
        expected = [
            f"CREATE DATABASE `{m['database']}_source_capture` ENGINE = Atomic",
            *runner.grants(m),
        ]
        for member in members:
            self.assertEqual(
                [call.args[0] for call in member.execute.call_args_list], expected
            )
            member.disconnect.assert_called_once_with()
        catalog.disconnect.assert_called_once_with()

    def test_namespace_create_failure_never_grants_or_retries_or_touches_next_member(
        self,
    ):
        catalog, first, later = Mock(), Mock(), Mock()
        first.execute.side_effect = RuntimeError("uncertain CREATE")
        with self.assertRaisesRegex(RuntimeError, "uncertain CREATE"):
            self.bootstrap([catalog, first, later])
        first.execute.assert_called_once_with(
            f"CREATE DATABASE `{manifest()['database']}_source_capture` ENGINE = Atomic"
        )
        first.disconnect.assert_called_once_with()
        later.execute.assert_not_called()
        catalog.disconnect.assert_called_once_with()


class BoundaryTests(unittest.TestCase):
    def test_socket_guard_only_exact_owned_addresses_and_ports(self):
        guard = runner.network_guard(addresses())
        for service, ports in runner.SERVICE_PORTS.items():
            for port in ports:
                guard("socket.connect", (None, (service, port)))
                guard("socket.connect", (None, (addresses()[service], port)))
        guard("socket.connect", (None, ("127.0.0.1", 4318)))
        for target in (
            ("1.1.1.1", 443),
            ("172.27.0.250", 8123),
            ("replica1", 5432),
            ("127.0.0.1", 9000),
            "/var/run/docker.sock",
        ):
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                guard("socket.connect", (None, target))

    def test_dns_rejects_shared_or_external_service_addresses(self):
        with (
            patch.object(
                runner.socket,
                "getaddrinfo",
                return_value=[(None, None, None, None, ("8.8.8.8", 0))],
            ),
            self.assertRaisesRegex(RuntimeError, "private"),
        ):
            runner.service_addresses()
        with (
            patch.object(
                runner.socket,
                "getaddrinfo",
                return_value=[(None, None, None, None, ("172.27.0.2", 0))],
            ),
            self.assertRaisesRegex(RuntimeError, "distinct"),
        ):
            runner.service_addresses()

    def test_dependency_failure_is_not_repaired_by_install_or_db_connection(self):
        with (
            patch.object(
                runner.importlib,
                "import_module",
                side_effect=ImportError("not installed"),
            ),
            patch.object(
                runner.subprocess, "run", side_effect=AssertionError("no install")
            ),
            patch.object(runner, "client", side_effect=AssertionError("no database")),
            self.assertRaises(ImportError),
        ):
            runner.dependency_evidence()

    def test_cleanup_only_signals_its_own_popen_handles(self):
        children = runner.Children(Path("/unused"), 1)
        first, second = Mock(), Mock()
        first.poll.return_value = None
        second.poll.return_value = 0
        stream1, stream2 = Mock(), Mock()
        children.children = [
            (first, stream1, Path("one")),
            (second, stream2, Path("two")),
        ]
        children.stop()
        first.terminate.assert_called_once_with()
        second.terminate.assert_not_called()
        first.wait.assert_called_once_with(timeout=7)
        stream1.close.assert_called_once_with()
        stream2.close.assert_called_once_with()
        self.assertEqual(children.children, [])


if __name__ == "__main__":
    unittest.main()
