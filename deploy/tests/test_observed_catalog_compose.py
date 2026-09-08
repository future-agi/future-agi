"""Deployment contracts. Compose rendering and a fake CH client; no services."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh"
SCHEMA = ROOT / "futureagi/tracer/services/clickhouse/v2/observed_catalog/schema.sql"


def compose(
    *overlays: str, base_file: str = "docker-compose.yml", **overrides: str
) -> dict:
    # Do not inherit credentials, .env contents or another running project's
    # transport configuration. 'config' does not contact the Docker daemon.
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}
    env.update(overrides)
    args = ["docker", "compose", "--env-file", os.devnull, "-f", base_file]
    for overlay in overlays:
        args.extend(["-f", overlay])
    result = subprocess.run(
        [*args, "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ComposeContractTests(unittest.TestCase):
    def test_default_and_dev_dependencies_are_complete(self):
        for overlays in ((), ("docker-compose.dev.yml",)):
            with self.subTest(overlays=overlays):
                config = compose(*overlays)
                services = config["services"]
                for retired in (
                    "fi-property-catalog-sequencer",
                    "property-catalog-supervisor",
                    "property-catalog-postgres-bootstrap",
                ):
                    self.assertNotIn(retired, services)
                for service in services.values():
                    for dependency in service.get("depends_on", {}):
                        self.assertIn(dependency, services)
                consumer = services["fi-property-catalog-consumer"]
                self.assertEqual(
                    consumer["entrypoint"],
                    ["/usr/local/bin/fi-property-catalog-consumer"],
                )
                self.assertEqual(consumer["command"], [])
                self.assertIn(
                    "property-catalog-clickhouse-bootstrap", consumer["depends_on"]
                )
                self.assertIn("property-catalog-topic-init", consumer["depends_on"])
                self.assertIn("fi-collector-data", config["volumes"])
                self.assertIn("property-catalog-kafka-data", config["volumes"])
                self.assertNotIn("property-catalog-sequencer-data", config["volumes"])

    def test_old_topic_overrides_do_not_change_observation_namespace(self):
        config = compose(
            PROPERTY_CATALOG_KAFKA_TOPIC="old-ordered",
            PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC="old-candidates",
            PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC="old-ordered",
            PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP="old-group",
        )
        services = config["services"]
        topic = services["property-catalog-topic-init"]["environment"][
            "OBSERVED_CATALOG_KAFKA_TOPIC"
        ]
        self.assertEqual(topic, "futureagi.observed-attributes.v1")
        for name in ("fi-collector", "fi-property-catalog-consumer"):
            values = list(services[name]["environment"].values())
            self.assertIn(topic, values)
            self.assertFalse(
                {"old-ordered", "old-candidates", "old-group"} & set(values)
            )
            self.assertFalse(
                any(
                    word in key
                    for key in services[name]["environment"]
                    for word in ("EPOCH", "REVISION", "PROJECTION", "LEDGER", "DEV_ACK")
                )
            )

    def test_explicit_new_topic_and_group_are_wired_without_environment_labels(self):
        config = compose(
            OBSERVED_CATALOG_KAFKA_TOPIC="customer.observed-attributes.v1",
            OBSERVED_CATALOG_KAFKA_GROUP="customer.observed-consumer.v1",
        )
        services = config["services"]
        for name in ("fi-collector", "fi-property-catalog-consumer"):
            self.assertEqual(
                services[name]["environment"]["FI_OBSERVED_CATALOG_KAFKA_TOPIC"],
                "customer.observed-attributes.v1",
            )
        self.assertEqual(
            services["fi-property-catalog-consumer"]["environment"][
                "FI_OBSERVED_CATALOG_KAFKA_GROUP"
            ],
            "customer.observed-consumer.v1",
        )
        self.assertEqual(
            services["property-catalog-topic-init"]["environment"][
                "OBSERVED_CATALOG_KAFKA_TOPIC"
            ],
            "customer.observed-attributes.v1",
        )

    def test_bootstrap_and_reader_share_isolated_database_and_credentials(self):
        config = compose(PROPERTY_CATALOG_DATABASE="attributes_custom")
        services = config["services"]
        bootstrap = services["property-catalog-clickhouse-bootstrap"]
        reader = services["backend"]["environment"]
        self.assertEqual(reader["PROPERTY_CATALOG_DATABASE"], "attributes_custom")
        self.assertEqual(
            bootstrap["environment"]["PROPERTY_CATALOG_DATABASE"], "attributes_custom"
        )
        self.assertEqual(
            reader["PROPERTY_CATALOG_CH_PASSWORD"],
            bootstrap["environment"]["PROPERTY_CATALOG_API_PASSWORD"],
        )
        mounts = {mount["target"]: mount for mount in bootstrap["volumes"]}
        self.assertTrue(mounts["/observed-catalog"]["read_only"])
        self.assertEqual(Path(mounts["/observed-catalog"]["source"]), SCHEMA.parent)
        self.assertNotIn("/property-catalog-schema", mounts)

    def test_e2e_uses_one_collector_image_and_separate_kafka_port(self):
        config = compose(
            "e2e/stack/docker-compose.e2e.yml",
            E2E_FI_COLLECTOR_VERSION="contract-test",
            PROPERTY_CATALOG_KAFKA_PORT="29093",
        )
        services = config["services"]
        self.assertEqual(
            services["fi-collector"]["image"], "futureagi/fi-collector:contract-test"
        )
        self.assertEqual(
            services["fi-collector"]["image"],
            services["fi-property-catalog-consumer"]["image"],
        )
        self.assertEqual(
            str(services["property-catalog-kafka"]["ports"][0]["published"]), "29093"
        )
        self.assertIn(
            "PROPERTY_CATALOG_KAFKA_PORT=29093",
            (ROOT / "e2e/stack/e2e.env").read_text(),
        )
        runner = (ROOT / "bin/e2e").read_text()
        self.assertIn(
            "fi-property-catalog-consumer",
            runner.split("SERVICES=(", 1)[1].split(")", 1)[0],
        )


class IntegrationHarnessContractTests(unittest.TestCase):
    def test_fixture_has_only_bounded_loopback_dependencies(self):
        config = compose(base_file="fi-collector/test/observed-catalog/compose.yml")
        services = config["services"]
        self.assertEqual(set(services), {"clickhouse", "kafka", "postgres"})
        for name, published in (
            ("clickhouse", "18143"),
            ("kafka", "19094"),
            ("postgres", "15543"),
        ):
            service = services[name]
            self.assertGreater(int(service["mem_limit"]), 0)
            self.assertEqual(len(service["ports"]), 1)
            self.assertEqual(service["ports"][0]["host_ip"], "127.0.0.1")
            self.assertEqual(str(service["ports"][0]["published"]), published)
        self.assertFalse(config.get("volumes"))


class BootstrapContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.record = self.directory / "queries.jsonl"
        client = self.directory / "clickhouse-client"
        client.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "with open(os.environ['CATALOG_TEST_RECORD'], 'a') as output:\n"
            "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if '--multiquery' in sys.argv: sys.stdin.read()\n"
            "if '--format' in sys.argv: print(os.environ.get('CATALOG_TEST_SCHEMA_MATCH', '1'))\n"
        )
        client.chmod(0o700)
        self.env = {
            "PATH": str(self.directory) + os.pathsep + os.defpath,
            "CATALOG_TEST_RECORD": str(self.record),
            "PROPERTY_CATALOG_SOURCE_DATABASE": "source_spans",
            "PROPERTY_CATALOG_DATABASE": "property_catalog",
            "PROPERTY_CATALOG_SCHEMA_FILE": str(SCHEMA),
        }

    def run_bootstrap(self, **overrides: str):
        return subprocess.run(
            ["/bin/sh", str(BOOTSTRAP)],
            env={**self.env, **overrides},
            text=True,
            capture_output=True,
        )

    def queries(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_bootstrap_can_repeat_without_source_or_historical_data_writes(self):
        for _ in range(2):
            result = self.run_bootstrap()
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.queries()
        ddl_calls = [args for args in calls if "--multiquery" in args]
        self.assertEqual(len(ddl_calls), 2)
        for args in ddl_calls:
            self.assertEqual(args[args.index("--database") + 1], "property_catalog")
        queries = [
            args[args.index("--query") + 1] for args in calls if "--query" in args
        ]
        for query in queries:
            self.assertNotRegex(query, r"(?i)\b(DROP|TRUNCATE|DELETE|REVOKE)\b")
            self.assertNotIn("source_spans", query)
            self.assertNotIn("property_catalog_activations", query)
        grants = [query for query in queries if query.startswith("GRANT")]
        self.assertEqual(len(grants), 8)
        for query in grants:
            self.assertRegex(
                query, r"ON `property_catalog`\.observed_attribute_(keys|values) TO"
            )

    def test_bad_or_source_database_is_rejected_before_any_client_call(self):
        for database in ("source_spans", "system", "bad-name", "db;DROP", "1bad"):
            with self.subTest(database=database):
                result = self.run_bootstrap(PROPERTY_CATALOG_DATABASE=database)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.record.exists())

    def test_custom_database_has_no_environment_label_restriction(self):
        result = self.run_bootstrap(PROPERTY_CATALOG_DATABASE="customer_observations")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_passwords_are_bound_as_values_not_sql_literals(self):
        writer = "quote'\\\"+slash/=and\\\\backslash"
        reader = "spaces and\nnewlines\t+slash/= ü"
        result = self.run_bootstrap(
            PROPERTY_CATALOG_CONSUMER_PASSWORD=writer,
            PROPERTY_CATALOG_API_PASSWORD=reader,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.queries()
        credentials = [call for call in calls if "--param_password" in call]
        self.assertEqual(len(credentials), 4)
        for call in credentials:
            query = call[call.index("--query") + 1]
            parameter = call[call.index("--param_password") + 1]
            self.assertIn("BY {password:String}", query)
            password = writer if "observed_catalog_writer" in query else reader
            self.assertNotIn(password, query)
            self.assertEqual(
                parameter, "".join(f"\\x{byte:02x}" for byte in password.encode())
            )
        self.assertTrue(
            any("SETTINGS readonly=2" in arg for call in credentials for arg in call)
        )
        validation = [call for call in calls if "--queries-file" in call]
        self.assertEqual(len(validation), 1)
        self.assertIn("property_catalog", validation[0])
        self.assertLess(calls.index(validation[0]), calls.index(credentials[0]))

    def test_incompatible_new_tables_fail_before_grants(self):
        result = self.run_bootstrap(CATALOG_TEST_SCHEMA_MATCH="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("GRANT" in arg for call in self.queries() for arg in call))


if __name__ == "__main__":
    unittest.main()
