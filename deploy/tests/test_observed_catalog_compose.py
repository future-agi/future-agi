"""Deployment contracts. Compose rendering and a fake CH client; no services."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh"
SCHEMA = ROOT / "futureagi/tracer/services/clickhouse/v2/observed_catalog/schema.sql"
PRODUCTION_PINS = {key: "contract-test" for key in (
    "FUTURE_AGI_VERSION", "FRONTEND_VERSION", "FI_COLLECTOR_VERSION",
    "AGENTCC_GATEWAY_VERSION", "SERVING_VERSION", "CODE_EXECUTOR_VERSION",
    "SIMULATION_RUNNER_VERSION",
)}


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
    def test_collector_read_only_root_preserves_writable_spool_in_all_overlays(self):
        production_env = {
            key: "packaging-contract-only"
            for key in (
                "SECRET_KEY",
                "AGENTCC_INTERNAL_API_KEY",
                "AGENTCC_ADMIN_TOKEN",
                "PG_PASSWORD",
                "MINIO_ROOT_PASSWORD",
                "RABBITMQ_USER",
                "RABBITMQ_PASSWORD",
                "PROPERTY_CATALOG_API_PASSWORD",
                "PROPERTY_CATALOG_CONSUMER_PASSWORD",
            )
        }
        production_env.update(
            **PRODUCTION_PINS,
            FRONTEND_URL="http://localhost:3100", VITE_HOST_API="http://localhost:8100",
        )
        for overlays in (
            (),
            ("docker-compose.dev.yml",),
            ("deploy/docker-compose.production.yml",),
            ("e2e/stack/docker-compose.e2e.yml",),
        ):
            with self.subTest(overlays=overlays):
                services = compose(*overlays, **production_env)["services"]
                self.assertEqual(
                    {name for name, svc in services.items() if svc.get("read_only")},
                    {"fi-collector", "fi-property-catalog-consumer"},
                )
                collector = services["fi-collector"]
                self.assertEqual(
                    collector["environment"]["FI_OBSERVED_CATALOG_MODE"], "kafka"
                )
                for name in ("fi-collector", "fi-property-catalog-consumer"):
                    self.assertNotIn(
                        "user", services[name]
                    )  # Inherit image's nonroot UID.
                mount = next(
                    mount
                    for mount in collector["volumes"]
                    if mount["target"] == "/var/lib/fi-collector"
                )
                self.assertEqual(mount["type"], "volume")
                self.assertEqual(mount["source"], "fi-collector-data")
                self.assertFalse(mount.get("read_only", False))
                self.assertEqual(
                    collector["environment"]["FI_OBSERVED_CATALOG_SPOOL_DIR"],
                    "/var/lib/fi-collector/observed-catalog",
                )
                init = services["property-catalog-runtime-volume-init"]
                self.assertEqual(init["user"], "0:0")
                self.assertIn("chown 65532:65532", " ".join(init["command"]))
                self.assertEqual(
                    collector["depends_on"]["property-catalog-runtime-volume-init"][
                        "condition"
                    ],
                    "service_completed_successfully",
                )
        dockerfile = (ROOT / "fi-collector/Dockerfile").read_text()
        self.assertIn("USER nonroot:nonroot", dockerfile)
        self.assertIn("--chown=65532:65532 /out/data /var/lib/fi-collector", dockerfile)

    def test_cdc_destination_matches_the_native_analytics_database(self):
        for overlays in (
            (),
            ("docker-compose.dev.yml",),
            ("e2e/stack/docker-compose.e2e.yml",),
        ):
            for database in ("default", "analytics_custom"):
                with self.subTest(overlays=overlays, database=database):
                    services = compose(
                        *overlays,
                        COMPOSE_PROFILES="peerdb",
                        CH_DATABASE=database,
                        CH25_DATABASE="separate_native_spans",
                    )["services"]
                    self.assertEqual(
                        services["peerdb-init"]["environment"]["DST_CH_DB"],
                        services["backend"]["environment"]["CH_DATABASE"],
                    )
                    self.assertEqual(
                        services["clickhouse"]["environment"]["CLICKHOUSE_DB"],
                        database,
                    )

    def test_cdc_setup_uses_fixed_api_initializer_not_legacy_shell(self):
        for overlays in (
            (),
            ("docker-compose.dev.yml",),
            ("e2e/stack/docker-compose.e2e.yml",),
        ):
            for overrides in (
                {},
                {"CH25_DROP_LEGACY_CDC_CHAIN": "false"},
                {"CH25_DROP_LEGACY_CDC_CHAIN": "true"},
            ):
                with self.subTest(overlays=overlays, overrides=overrides):
                    services = compose(
                        *overlays, COMPOSE_PROFILES="peerdb", **overrides
                    )["services"]
                    self.assertEqual(
                        services["peerdb-init"]["entrypoint"],
                        ["python", "-m", "tracer.services.clickhouse.oss_cdc_setup"],
                    )
                    if "docker-compose.dev.yml" in overlays:
                        self.assertEqual(
                            services["backend"]["environment"][
                                "NO_STARTUP_DB_MUTATIONS"
                            ],
                            "true",
                        )
                        self.assertEqual(
                            services["backend"]["environment"][
                                "CH25_DROP_LEGACY_CDC_CHAIN"
                            ],
                            "false",
                        )

    def test_new_cdc_mirrors_preserve_nulls_without_an_operator_setting(self):
        for overlays in (
            (),
            ("docker-compose.dev.yml",),
            ("e2e/stack/docker-compose.e2e.yml",),
        ):
            with self.subTest(overlays=overlays):
                config = compose(
                    *overlays, COMPOSE_PROFILES="peerdb", PEERDB_NULLABLE="false"
                )
                for name in (
                    "peerdb-flow-api",
                    "peerdb-flow-worker",
                    "peerdb-flow-snapshot-worker",
                ):
                    self.assertEqual(
                        config["services"][name]["environment"]["PEERDB_NULLABLE"],
                        "true",
                    )

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

    def test_e2e_manages_exact_aggregation_worker_without_other_worker_profiles(self):
        # Parse only the literal list; never execute the harness or Compose.
        runner = (ROOT / "bin/e2e").read_text()
        services = shlex.split(
            runner.split("SERVICES=(", 1)[1].split(")", 1)[0], comments=True
        )
        self.assertEqual(
            services,
            [
                "frontend",
                "backend",
                "worker",
                "worker-exact-aggregation",
                "postgres",
                "clickhouse",
                "redis",
                "rabbitmq",
                "minio",
                "temporal",
                "agentcc-gateway",
                "fi-collector",
                "fi-property-catalog-consumer",
                "mock-llm",
                "peerdb-server",
                "peerdb-flow-worker",
                "peerdb-flow-snapshot-worker",
                "peerdb-minio",
            ],
        )
        self.assertIn(
            'up)      "${COMPOSE[@]}" up -d --wait --wait-timeout 1200 "${SERVICES[@]}"',
            runner,
        )

    def test_e2e_metric_log_merges_fit_the_bounded_clickhouse(self):
        config_path = ROOT / "e2e/stack/clickhouse-metric-log.xml"
        target = "/etc/clickhouse-server/config.d/e2e-metric-log.xml"
        service = compose("e2e/stack/docker-compose.e2e.yml")["services"]["clickhouse"]
        self.assertEqual(int(service["mem_limit"]), 3 * 1024**3)
        mount = next((v for v in service["volumes"] if v["target"] == target), None)
        self.assertIsNotNone(mount)
        self.assertEqual(mount["source"], str(config_path))
        self.assertTrue(mount["read_only"])
        # Restrict the override to log-table layout: retain telemetry, budgets,
        # source tables and their storage policy. This is not a query-limit fix.
        root = ET.parse(config_path).getroot()
        self.assertEqual(root.tag, "clickhouse")
        self.assertEqual([child.tag for child in root], ["metric_log"])
        metric_log = root.find("metric_log")
        self.assertEqual(metric_log.attrib, {})
        self.assertEqual([child.tag for child in metric_log], ["settings"])
        settings = dict(
            tuple(part.strip() for part in item.split("=", 1))
            for item in metric_log.findtext("settings").split(",")
        )
        self.assertEqual(
            settings,
            {
                "index_granularity": "8192",
                "min_bytes_for_wide_part": "134217728",
                "vertical_merge_algorithm_min_bytes_to_activate": "134217728",
            },
        )
        # SystemLog compares formatted CREATE definitions, including order.
        self.assertEqual(
            list(settings),
            [
                "index_granularity",
                "min_bytes_for_wide_part",
                "vertical_merge_algorithm_min_bytes_to_activate",
            ],
        )
        for overlays in ((), ("docker-compose.dev.yml",)):
            other = compose(*overlays)["services"]["clickhouse"]
            self.assertNotIn(target, [v["target"] for v in other["volumes"]])

    def test_e2e_temporal_leaves_postgres_capacity_for_api_and_cdc(self):
        settings = {
            "SQL_MAX_CONNS": "5",
            "SQL_MAX_IDLE_CONNS": "2",
            "SQL_VIS_MAX_CONNS": "2",
            "SQL_VIS_MAX_IDLE_CONNS": "1",
        }
        runtime = compose("e2e/stack/docker-compose.e2e.yml")["services"]
        for name, value in settings.items():
            self.assertEqual(runtime["temporal"]["environment"].get(name), value)
        # This local qualification budget must not silently tune deployments.
        base = compose()["services"]["temporal"]["environment"]
        self.assertTrue(settings.keys().isdisjoint(base))


class ProductionCatalogContractTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            key: "production-contract-only"
            for key in (
                "SECRET_KEY",
                "AGENTCC_INTERNAL_API_KEY",
                "AGENTCC_ADMIN_TOKEN",
                "PG_PASSWORD",
                "MINIO_ROOT_PASSWORD",
                "RABBITMQ_USER",
                "RABBITMQ_PASSWORD",
            )
        }
        # Distinct synthetic inputs, including characters needing shell/SQL care.
        self.env.update(
            **PRODUCTION_PINS,
            FRONTEND_URL="https://app.example.com",
            VITE_HOST_API="https://api.example.com",
            PROPERTY_CATALOG_API_PASSWORD="reader '\"\\$${not_an_env}\n\t ü",
            PROPERTY_CATALOG_CONSUMER_PASSWORD="writer '\"\\$${not_an_env}\n\t ø",
            COMPOSE_PROFILES="full",
        )

    def test_all_production_application_images_require_explicit_pins(self):
        for key in PRODUCTION_PINS:
            for empty in (False, True):
                with self.subTest(key=key, empty=empty):
                    env = {k: v for k, v in self.env.items() if k != key}
                    if empty:
                        env[key] = ""
                    with self.assertRaisesRegex(AssertionError, key):
                        compose("deploy/docker-compose.production.yml", **env)

    def test_production_pins_cover_bootstrap_and_specialized_workers(self):
        services = compose("deploy/docker-compose.production.yml", **self.env)["services"]
        expected = {
            "frontend": "futureagi/frontend:contract-test",
            "agentcc-gateway": "futureagi/agentcc-gateway:contract-test",
            "serving": "futureagi/serving:contract-test",
            "code-executor": "futureagi/code-executor:contract-test",
            "worker-simulation-runner": "futureagi/future-agi-simulation-runner:contract-test",
        }
        for name in ("backend", "postgres-schema-bootstrap", "clickhouse-native-bootstrap",
                     "clickhouse-cdc-bootstrap", "peerdb-init", "peerdb-temporal-init"):
            expected[name] = "futureagi/future-agi:contract-test"
        for name in services:
            if name.startswith("worker") and name != "worker-simulation-runner":
                expected[name] = "futureagi/future-agi:contract-test"
        for name, image in expected.items():
            self.assertEqual(services[name]["image"], image, name)
        # Requiring production pins must not change root local defaults.
        root = compose()["services"]
        self.assertEqual(root["backend"]["image"], "futureagi/future-agi:latest")
        self.assertEqual(root["frontend"]["image"], "futureagi/frontend:latest")

    def test_production_requires_explicit_collector_image_and_removes_source_build(self):
        services = compose("deploy/docker-compose.production.yml", **self.env)["services"]
        for name in ("fi-collector", "fi-property-catalog-consumer"):
            self.assertEqual(services[name]["image"], "futureagi/fi-collector:contract-test")
            self.assertFalse(services[name].get("build"))
        for value in (None, ""):
            env = {k: v for k, v in self.env.items() if k != "FI_COLLECTOR_VERSION"}
            if value is not None:
                env["FI_COLLECTOR_VERSION"] = value
            with self.assertRaisesRegex(AssertionError, "FI_COLLECTOR_VERSION"):
                compose("deploy/docker-compose.production.yml", **env)

    def test_production_bootstrap_remains_check_only(self):
        services = compose("deploy/docker-compose.production.yml", **self.env)["services"]
        expected = {
            "postgres-schema-bootstrap": ["manage.py", "migrate", "--check", "--noinput"],
            "property-catalog-clickhouse-bootstrap": ["--check"],
            "clickhouse-native-bootstrap": ["--phase", "native"],
            "peerdb-temporal-init": [],
            "peerdb-init": ["--wait-for-mirrors", "--timeout", "900"],
            "clickhouse-cdc-bootstrap": ["--phase", "cdc", "--wait-for-mirrors", "--timeout", "900"],
        }
        for name, command in expected.items():
            self.assertEqual(services[name]["command"], command)
        for name in ("postgres-schema-bootstrap", "backend", "worker", "worker-exact-aggregation"):
            self.assertEqual(services[name]["environment"]["NO_STARTUP_DB_MUTATIONS"], "true")

    def test_live_and_backfill_services_share_default_and_overridden_extraction_limits(self):
        for overlays in ((), ("deploy/docker-compose.production.yml",)):
            for values in (("128", "256"), ("512", "1024")):
                overrides = {} if values == ("128", "256") else {
                    "FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN": values[0],
                    "FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN": values[1],
                }
                services = compose(*overlays, **self.env, **overrides)["services"]
                for name in ("fi-collector", "fi-property-catalog-consumer"):
                    env = services[name]["environment"]
                    self.assertEqual(env.get("FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN"), values[0])
                    self.assertEqual(env.get("FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN"), values[1])

    def test_production_checks_with_reader_and_preserves_database_scope(self):
        for database in ("property_catalog", "customer_observations"):
            with self.subTest(database=database):
                services = compose(
                    "deploy/docker-compose.production.yml",
                    **self.env,
                    PROPERTY_CATALOG_DATABASE=database,
                    FI_CH_DATABASE="separate_source_spans",
                )["services"]
                bootstrap = services["property-catalog-clickhouse-bootstrap"]
                self.assertEqual(bootstrap.get("command"), ["--check"])
                self.assertEqual(
                    bootstrap["entrypoint"],
                    ["/bin/sh", "/bootstrap/bootstrap_clickhouse.sh"],
                )
                env = bootstrap["environment"]
                self.assertEqual(env["CLICKHOUSE_USER"], "observed_catalog_reader")
                self.assertEqual(env["PROPERTY_CATALOG_DATABASE"], database)
                self.assertEqual(
                    env["PROPERTY_CATALOG_SOURCE_DATABASE"], "separate_source_spans"
                )
                # Compose config escapes dollars for a reusable manifest, even
                # in JSON. Never print credentials in failure artifacts.
                reader = self.env["PROPERTY_CATALOG_API_PASSWORD"].replace("$", "$$")
                writer = self.env["PROPERTY_CATALOG_CONSUMER_PASSWORD"].replace(
                    "$", "$$"
                )
                self.assertTrue(env["CLICKHOUSE_PASSWORD"] == reader)
                self.assertTrue(env["PROPERTY_CATALOG_API_PASSWORD"] == reader)
                self.assertTrue(env["PROPERTY_CATALOG_CONSUMER_PASSWORD"] == writer)
                consumer = services["fi-property-catalog-consumer"]["environment"]
                self.assertEqual(
                    consumer["FI_OBSERVED_CATALOG_CH_USERNAME"],
                    "observed_catalog_writer",
                )
                self.assertTrue(consumer["FI_OBSERVED_CATALOG_CH_PASSWORD"] == writer)
                self.assertEqual(consumer["FI_OBSERVED_CATALOG_CH_DATABASE"], database)
                runtimes = {
                    name
                    for name in services
                    if name == "backend" or name.startswith("worker")
                }
                self.assertEqual(len(runtimes), 10)
                for name in sorted(runtimes):
                    with self.subTest(runtime=name):
                        runtime = services[name]["environment"]
                        self.assertEqual(
                            runtime["PROPERTY_CATALOG_CH_USER"],
                            "observed_catalog_reader",
                        )
                        self.assertTrue(
                            runtime["PROPERTY_CATALOG_CH_PASSWORD"] == reader
                        )
                        self.assertEqual(runtime["PROPERTY_CATALOG_DATABASE"], database)

    def assert_required_password(self, name, *, empty):
        env = dict(self.env)
        if empty:
            env[name] = ""
        else:
            del env[name]
        with self.assertRaisesRegex(AssertionError, name):
            compose("deploy/docker-compose.production.yml", **env)

    def test_production_rejects_missing_api_password(self):
        self.assert_required_password("PROPERTY_CATALOG_API_PASSWORD", empty=False)

    def test_production_rejects_empty_api_password(self):
        self.assert_required_password("PROPERTY_CATALOG_API_PASSWORD", empty=True)

    def test_production_rejects_missing_consumer_password(self):
        self.assert_required_password("PROPERTY_CATALOG_CONSUMER_PASSWORD", empty=False)

    def test_production_rejects_empty_consumer_password(self):
        self.assert_required_password("PROPERTY_CATALOG_CONSUMER_PASSWORD", empty=True)

    def test_local_bootstrap_keeps_no_argument_provisioning_defaults(self):
        services = compose()["services"]
        bootstrap = services["property-catalog-clickhouse-bootstrap"]
        self.assertFalse(bootstrap.get("command"))
        env = bootstrap["environment"]
        self.assertNotIn("CLICKHOUSE_USER", env)
        self.assertNotIn("CLICKHOUSE_PASSWORD", env)
        for name, value in (
            ("PROPERTY_CATALOG_API_PASSWORD", "oss-observed-reader-local-only"),
            ("PROPERTY_CATALOG_CONSUMER_PASSWORD", "oss-observed-writer-local-only"),
        ):
            self.assertTrue(env[name] == value)


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
            "if '--query' in sys.argv:\n"
            "    query = sys.argv[sys.argv.index('--query') + 1]\n"
            "    denied = os.environ.get('CATALOG_TEST_DENY_TABLE')\n"
            "    if denied and query.startswith('SELECT ') and denied in query: sys.exit(77)\n"
        )
        client.chmod(0o700)
        self.env = {
            "PATH": str(self.directory) + os.pathsep + os.defpath,
            "CATALOG_TEST_RECORD": str(self.record),
            "PROPERTY_CATALOG_SOURCE_DATABASE": "source_spans",
            "PROPERTY_CATALOG_DATABASE": "property_catalog",
            "PROPERTY_CATALOG_SCHEMA_FILE": str(SCHEMA),
        }

    def run_bootstrap(self, *args: str, **overrides: str):
        return subprocess.run(
            ["/bin/sh", str(BOOTSTRAP), *args],
            env={**self.env, **overrides},
            text=True,
            capture_output=True,
        )

    def queries(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_unknown_or_extra_arguments_fail_before_any_client_call(self):
        for args in (("--unknown",), ("--check", "extra"), ("--check", "--check")):
            with self.subTest(args=args):
                result = self.run_bootstrap(*args)
                self.assertEqual(result.returncode, 64)
                self.assertFalse(self.record.exists())

    def test_check_only_validates_schema_and_reads_both_indexes_with_reader(self):
        password = "reader '\"\\$${not_an_env}\n\t ü"
        result = self.run_bootstrap(
            "--check",
            PROPERTY_CATALOG_DATABASE="customer_observations",
            CLICKHOUSE_USER="observed_catalog_reader",
            CLICKHOUSE_PASSWORD=password,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.queries()
        self.assertEqual(len(calls), 3)
        validation = calls[0]
        self.assertEqual(
            validation[validation.index("--queries-file") + 1],
            str(BOOTSTRAP.with_name("validate_clickhouse.sql")),
        )
        self.assertEqual(
            validation[validation.index("--param_database") + 1],
            "customer_observations",
        )
        self.assertEqual(
            [call[call.index("--query") + 1] for call in calls[1:]],
            [
                f"SELECT * FROM `customer_observations`.{table} LIMIT 0"
                for table in ("observed_attribute_keys", "observed_attribute_values")
            ],
        )
        for call in calls:
            self.assertNotIn("--multiquery", call)
            self.assertNotIn("--param_password", call)
            self.assertEqual(call[call.index("--user") + 1], "observed_catalog_reader")
            self.assertTrue(call[call.index("--password") + 1] == password)
        self.assertNotIn(password, result.stdout + result.stderr)

    def test_check_incompatible_schema_stops_before_index_reads_or_writes(self):
        result = self.run_bootstrap("--check", CATALOG_TEST_SCHEMA_MATCH="0")
        self.assertEqual(result.returncode, 65)
        calls = self.queries()
        self.assertEqual(len(calls), 1)
        self.assertIn("--queries-file", calls[0])

    def test_check_requires_select_permission_on_each_index(self):
        for index, table in enumerate(
            ("observed_attribute_keys", "observed_attribute_values")
        ):
            with self.subTest(table=table):
                result = self.run_bootstrap("--check", CATALOG_TEST_DENY_TABLE=table)
                self.assertEqual(result.returncode, 77)
                calls = self.queries()
                self.assertEqual(len(calls), index + 2)
                self.assertIn("--queries-file", calls[0])
                self.assertEqual(
                    [call[call.index("--query") + 1] for call in calls[1:]],
                    [
                        f"SELECT * FROM `property_catalog`.{name} LIMIT 0"
                        for name in (
                            "observed_attribute_keys",
                            "observed_attribute_values",
                        )[: index + 1]
                    ],
                )
                self.record.unlink()

    def test_check_rejects_invalid_or_source_database_before_any_client_call(self):
        for database in ("source_spans", "system", "bad-name", "db;DROP", "1bad"):
            with self.subTest(database=database):
                result = self.run_bootstrap(
                    "--check", PROPERTY_CATALOG_DATABASE=database
                )
                self.assertEqual(result.returncode, 64)
                self.assertFalse(self.record.exists())

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
