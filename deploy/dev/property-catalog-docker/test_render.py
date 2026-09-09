from __future__ import annotations

import ast
import contextlib
import copy
import dataclasses
import hashlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

_MODULE_PATH = Path(__file__).with_name("render.py")
_SPEC = importlib.util.spec_from_file_location(
    "property_catalog_docker_render", _MODULE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
workload = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = workload
_SPEC.loader.exec_module(workload)


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("ascii")).hexdigest()


def _raw() -> dict[str, object]:
    return {
        "format": workload.FORMAT,
        "version": workload.VERSION,
        "deployment_id": "review-a",
        "images": {
            "collector_runtime": f"sha256:{_digest('reviewed collector image')}",
            "operator": "fi-property-catalog-current-select:0815a",
        },
        "host": {"root": "/home/ubuntu/property-catalog-review-a"},
        "workspace": {
            "organization_id": "11111111-1111-4111-8111-111111111111",
            "workspace_id": "22222222-2222-4222-8222-222222222222",
            "project_ids": [
                "33333333-3333-4333-8333-333333333333",
                "44444444-4444-4444-8444-444444444444",
            ],
        },
        "catalog": {
            "epoch": 3,
            "projection_version": 1,
            "hot_producer_stream_id": "55555555-5555-4555-8555-555555555555",
            "source_database": "futureagi",
            "target_database": "property_catalog_dev_review_a",
            "span_since": "2025-08-15T10:00:00Z",
            "span_until": "2026-08-15T10:00:00Z",
            "dev_identity": "dev:property-catalog/reviewer-a",
        },
        "infrastructure": {
            "application_docker_network": "futureagi_default",
            "kafka_docker_network": "property-catalog-dev",
            "source_clickhouse_host": "clickhouse",
            "source_clickhouse_native_port": 9000,
            "source_clickhouse_http_port": 8123,
            "target_clickhouse_host": "clickhouse",
            "target_clickhouse_native_port": 9000,
            "target_clickhouse_http_port": 8123,
            "kafka_brokers": ["property-catalog-kafka-dev:9092"],
        },
        "provenance": {
            "write_clickhouse_hostname": "7c7e694b9c13",
            "source_clickhouse_hostname": "7c7e694b9c13",
            "postgres_database": "tfc",
            "postgres_user": "user",
            "postgres_server_address": "172.19.0.7",
            "postgres_server_port": 5432,
        },
    }


def _write_config(directory: Path, raw: dict[str, object] | None = None) -> Path:
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(raw or _raw(), sort_keys=False), encoding="utf-8")
    return path


class RenderTests(unittest.TestCase):
    def test_renderer_has_no_process_network_or_docker_client(self) -> None:
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {
                "asyncio",
                "clickhouse_connect",
                "docker",
                "httpx",
                "kafka",
                "psycopg",
                "requests",
                "socket",
                "subprocess",
            }
        )

    def test_render_is_two_unrouted_services_plus_dry_operator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary)))
        compose = workload.render_compose(config)
        self.assertEqual(
            list(compose["services"]),
            [
                "property-catalog-producer",
                "property-catalog-consumer",
                "property-catalog-operator",
            ],
        )
        producer = compose["services"]["property-catalog-producer"]
        consumer = compose["services"]["property-catalog-consumer"]
        operator = compose["services"]["property-catalog-operator"]
        self.assertEqual(producer["image"], config.collector_image)
        self.assertEqual(consumer["image"], config.collector_image)
        self.assertEqual(producer["entrypoint"], ["/usr/local/bin/fi-collector"])
        self.assertEqual(
            consumer["entrypoint"],
            ["/usr/local/bin/fi-property-catalog-consumer"],
        )
        self.assertEqual(producer["environment"]["FI_GRPC_ADDR"], "127.0.0.1:4317")
        self.assertEqual(producer["environment"]["FI_HTTP_ADDR"], "127.0.0.1:4318")
        self.assertEqual(
            producer["environment"]["FI_PROPERTY_CATALOG_SPOOL_DIR"],
            "/var/lib/property-catalog-runtime/catalog-spool",
        )
        self.assertEqual(consumer["command"], ["--seed-from-delivery-ledger"])
        self.assertEqual(operator["profiles"], ["operator"])
        self.assertEqual(operator["command"], [])
        self.assertEqual(
            operator["entrypoint"],
            ["python", "manage.py", "ch25_property_catalog_dev_rollout"],
        )
        for service in compose["services"].values():
            self.assertNotIn("ports", service)
            self.assertNotIn("expose", service)
            self.assertNotIn("network_mode", service)
            self.assertNotIn("depends_on", service)
            self.assertEqual(service["pull_policy"], "never")
            self.assertTrue(service["read_only"])

    def test_topic_group_target_and_external_networks_are_derived(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary)))
        self.assertEqual(config.target_database, "property_catalog_dev_review_a")
        self.assertEqual(config.kafka_topic, "futureagi.dev.property-catalog.review-a")
        self.assertEqual(
            config.kafka_consumer_group,
            "futureagi.dev.property-catalog.consumer.review-a",
        )
        compose = workload.render_compose(config)
        self.assertEqual(
            compose["networks"],
            {
                "application-existing": {
                    "external": True,
                    "name": "futureagi_default",
                },
                "kafka-existing": {
                    "external": True,
                    "name": "property-catalog-dev",
                },
            },
        )
        self.assertEqual(
            compose["services"]["property-catalog-operator"]["networks"],
            ["application-existing"],
        )

    def test_accepts_safe_legacy_target_database_name(self) -> None:
        raw = copy.deepcopy(_raw())
        raw["catalog"]["target_database"] = "th7247_catalog_dev_kartik_0817j"
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary), raw))
        self.assertEqual(
            config.target_database,
            "th7247_catalog_dev_kartik_0817j",
        )

    def test_credentials_are_only_exact_private_env_file_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary)))
        compose = workload.render_compose(config)
        producer = compose["services"]["property-catalog-producer"]
        consumer = compose["services"]["property-catalog-consumer"]
        operator = compose["services"]["property-catalog-operator"]
        private = "/home/ubuntu/property-catalog-review-a/private"
        self.assertEqual(producer["env_file"], [f"{private}/producer.env"])
        self.assertEqual(
            consumer["env_file"],
            [
                f"{private}/consumer-write-clickhouse.env",
                f"{private}/consumer-ledger-clickhouse.env",
            ],
        )
        self.assertEqual(
            operator["env_file"],
            [
                f"{private}/operator-runtime.env",
                f"{private}/operator-postgres.env",
                f"{private}/operator-source-clickhouse.env",
                f"{private}/operator-target-clickhouse.env",
            ],
        )
        credential_keys = {
            key for keys in workload._ENV_FILE_KEYS.values() for key in keys
        }
        for service in compose["services"].values():
            self.assertFalse(set(service["environment"]) & credential_keys)

    def test_shared_runtime_and_image_native_binaries_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary)))
        services = workload.render_compose(config)["services"]
        producer = services["property-catalog-producer"]
        consumer = services["property-catalog-consumer"]
        operator = services["property-catalog-operator"]
        producer_targets = {value["target"]: value for value in producer["volumes"]}
        consumer_targets = {
            value["target"]: value for value in consumer.get("volumes", [])
        }
        operator_targets = {value["target"]: value for value in operator["volumes"]}
        runtime = "/home/ubuntu/property-catalog-review-a/runtime"
        self.assertEqual(
            producer_targets[workload.CONTAINER_RUNTIME_DIRECTORY]["source"], runtime
        )
        self.assertEqual(
            operator_targets[workload.CONTAINER_RUNTIME_DIRECTORY]["source"], runtime
        )
        self.assertNotIn(workload.CONTAINER_RUNTIME_DIRECTORY, consumer_targets)
        self.assertEqual(list(producer_targets), [workload.CONTAINER_RUNTIME_DIRECTORY])
        self.assertEqual(consumer_targets, {})
        spool = producer["environment"]["FI_PROPERTY_CATALOG_SPOOL_DIR"]
        operator_environment = operator["environment"]
        self.assertEqual(
            producer["environment"]["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"],
            f"{spool}/revision-fence-v2.json",
        )
        self.assertEqual(
            operator_environment["PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"],
            f"{spool}/revision-fence-v2.json",
        )
        self.assertEqual(
            operator_environment["PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE"],
            f"{spool}/producer-drain-proof-v2.json",
        )
        self.assertEqual(
            operator_environment["PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE"],
            f"{spool}/producer-state-retirements-v1.json",
        )
        self.assertEqual(
            operator_environment["PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY"],
            spool,
        )
        self.assertEqual(producer["entrypoint"], ["/usr/local/bin/fi-collector"])
        self.assertEqual(
            consumer["entrypoint"],
            ["/usr/local/bin/fi-property-catalog-consumer"],
        )

    def test_collector_requires_exact_immutable_local_image_id(self) -> None:
        invalid = (
            "registry.dev.futureagi.test/fi-collector:0815a",
            f"registry.dev.futureagi.test/fi-collector@sha256:{_digest('image')}",
            f"sha256:{_digest('image').upper()}",
            f"sha256:{'0' * 64}",
        )
        for index, image in enumerate(invalid):
            raw = copy.deepcopy(_raw())
            raw["images"]["collector_runtime"] = image
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(
                    workload.DeploymentValidationError, "exact immutable local"
                ):
                    workload.load_config(_write_config(Path(temporary), raw))

    def test_operator_forces_reads_schedule_and_startup_hooks_off(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary)))
        environment = workload.render_compose(config)["services"][
            "property-catalog-operator"
        ]["environment"]
        self.assertEqual(environment["SERVICE_TYPE"], "bootstrap")
        self.assertEqual(environment["STARTUP_DB_MUTATION_MODE"], "operator")
        self.assertEqual(environment["NO_STARTUP_DB_MUTATIONS"], "true")
        self.assertEqual(environment["SENTRY_ENABLED"], "false")
        self.assertEqual(environment["OTEL_ENABLED"], "false")
        self.assertEqual(environment["FUTURE_AGI_TELEMETRY_DISABLED"], "true")
        self.assertEqual(environment["CLOUD_DEPLOYMENT"], "DEV")
        self.assertEqual(environment["PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT"], "DEV")
        self.assertEqual(environment["PROPERTY_CATALOG_READ_MODE"], "off")
        self.assertEqual(environment["SPAN_ATTRIBUTE_CATALOG_READ_MODE"], "off")
        self.assertEqual(
            environment["SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED"], "false"
        )
        self.assertEqual(environment["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"], "false")
        self.assertEqual(
            environment["PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED"], "false"
        )

    def test_rendered_validator_rejects_routing_and_safety_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary)))
        compose = workload.render_compose(config)
        routed = copy.deepcopy(compose)
        routed["services"]["property-catalog-producer"]["ports"] = ["4317:4317"]
        with self.assertRaisesRegex(workload.DeploymentValidationError, "routing"):
            workload._validate_compose(routed, config)
        enabled = copy.deepcopy(compose)
        enabled["services"]["property-catalog-operator"]["environment"][
            "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"
        ] = "true"
        with self.assertRaisesRegex(workload.DeploymentValidationError, "operator"):
            workload._validate_compose(enabled, config)
        mounted = copy.deepcopy(compose)
        mounted["services"]["property-catalog-consumer"]["volumes"] = [
            workload._bind(
                config.root / "bin" / "fi-property-catalog-consumer",
                "/usr/local/bin/fi-property-catalog-consumer",
                read_only=True,
            )
        ]
        with self.assertRaisesRegex(
            workload.DeploymentValidationError, "only the producer"
        ):
            workload._validate_compose(mounted, config)

    def test_oss_profile_keeps_cloud_unset_and_requires_oss_image(self) -> None:
        raw = copy.deepcopy(_raw())
        raw["format"] = workload.OSS_FORMAT
        raw["images"]["operator"] = "fi-property-catalog-oss-current-select:0815a"
        with tempfile.TemporaryDirectory() as temporary:
            config = workload.load_config(_write_config(Path(temporary), raw))
        self.assertEqual(config.runtime_profile, "oss")
        compose = workload.render_compose(config)
        operator = compose["services"]["property-catalog-operator"]
        environment = operator["environment"]
        self.assertEqual(environment["CLOUD_DEPLOYMENT"], "")
        self.assertEqual(environment["PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT"], "")
        self.assertEqual(
            operator["labels"]["futureagi.property-catalog-runtime-profile"],
            "oss",
        )

        tampered = copy.deepcopy(compose)
        tampered["services"]["property-catalog-operator"]["environment"][
            "CLOUD_DEPLOYMENT"
        ] = "DEV"
        with self.assertRaisesRegex(workload.DeploymentValidationError, "operator"):
            workload._validate_compose(tampered, config)

        raw["images"]["operator"] = "fi-property-catalog-current-select:0815a"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                workload.DeploymentValidationError, "OSS backend image"
            ):
                workload.load_config(_write_config(Path(temporary), raw))

    def test_config_rejects_unknown_duplicate_prod_and_scope_drift(self) -> None:
        cases: list[dict[str, object]] = []
        unknown = copy.deepcopy(_raw())
        unknown["extra"] = True
        cases.append(unknown)
        production = copy.deepcopy(_raw())
        production["infrastructure"]["source_clickhouse_host"] = "clickhouse-prod"
        cases.append(production)
        wrong_target = copy.deepcopy(_raw())
        wrong_target["catalog"]["target_database"] = "futureagi"
        cases.append(wrong_target)
        production_target = copy.deepcopy(_raw())
        production_target["catalog"]["target_database"] = "property_catalog"
        cases.append(production_target)
        unsafe_target = copy.deepcopy(_raw())
        unsafe_target["catalog"]["target_database"] = "Property-Catalog-Dev"
        cases.append(unsafe_target)
        wrong_root = copy.deepcopy(_raw())
        wrong_root["host"]["root"] = "/home/ubuntu/future-agi"
        cases.append(wrong_root)
        same_network = copy.deepcopy(_raw())
        same_network["infrastructure"]["kafka_docker_network"] = "futureagi_default"
        cases.append(same_network)
        other_clickhouse = copy.deepcopy(_raw())
        other_clickhouse["infrastructure"]["target_clickhouse_host"] = (
            "other-clickhouse"
        )
        cases.append(other_clickhouse)
        for index, raw in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(workload.DeploymentValidationError):
                    workload.load_config(_write_config(Path(temporary), raw))

        duplicate = yaml.safe_dump(_raw(), sort_keys=False) + "version: 1\n"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.yaml"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(
                workload.DeploymentValidationError, "duplicate"
            ):
                workload.load_config(path)

    def test_cli_atomic_output_and_exact_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config_path = _write_config(directory)
            output = directory / "compose.yaml"
            self.assertEqual(
                workload.main(
                    ["--config", os.fspath(config_path), "--output", os.fspath(output)]
                ),
                0,
            )
            self.assertTrue(output.exists())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = workload.main(
                    [
                        "--config",
                        os.fspath(config_path),
                        "--validate-rendered",
                        os.fspath(output),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("no published ports", stdout.getvalue())
            parsed = yaml.safe_load(output.read_text(encoding="utf-8"))
            parsed["services"]["property-catalog-producer"]["environment"][
                "FI_GRPC_ADDR"
            ] = ":4317"
            output.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = workload.main(
                    [
                        "--config",
                        os.fspath(config_path),
                        "--validate-rendered",
                        os.fspath(output),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertIn("rejected", stderr.getvalue())

    def test_example_is_fail_closed_and_runbook_keeps_schedule_absent(self) -> None:
        for filename in ("config.example.yaml", "config.oss.example.yaml"):
            example = Path(__file__).with_name(filename)
            with (
                self.subTest(filename=filename),
                self.assertRaisesRegex(
                    workload.DeploymentValidationError, "placeholder"
                ),
            ):
                workload.load_config(example)
        readme = Path(__file__).with_name("README.md").read_text(encoding="utf-8")
        self.assertIn("SERVICE_TYPE=bootstrap", readme)
        self.assertIn("STARTUP_DB_MUTATION_MODE=operator", readme)
        self.assertIn("NO_STARTUP_DB_MUTATIONS=true", readme)
        self.assertIn("property-catalog-operator --execute", readme)
        self.assertNotIn("register_temporal_schedules", readme)

    def test_runbooks_separate_unified_and_legacy_catalog_paths(self) -> None:
        repository = Path(__file__).parents[3]
        unified_readme = (
            Path(__file__).with_name("README.md").read_text(encoding="utf-8")
        )
        collector_readme = (repository / "fi-collector" / "README.md").read_text(
            encoding="utf-8"
        )
        legacy_readme = (repository / "fi-collector" / "CATALOG_DEV.md").read_text(
            encoding="utf-8"
        )
        legacy_compose = yaml.safe_load(
            (
                repository / "fi-collector" / "docker-compose.catalog-kafka.dev.yml"
            ).read_text(encoding="utf-8")
        )

        self.assertIn("FI_PROPERTY_CATALOG_MODE=kafka", unified_readme)
        self.assertIn("FI_CATALOG_MODE=disabled", unified_readme)
        self.assertIn("KAFKA_NETWORK=property-catalog-dev", unified_readme)
        self.assertNotIn("KAFKA_NETWORK=property_catalog_dev", unified_readme)
        self.assertIn(
            "../deploy/dev/property-catalog-docker/README.md", collector_readme
        )
        self.assertIn("legacy `FI_CATALOG_MODE`", legacy_readme)
        self.assertIn("`FI_PROPERTY_CATALOG_MODE`", legacy_readme)

        topic_init = legacy_compose["services"]["topic-init"]
        self.assertEqual(topic_init["profiles"], ["legacy-span-attribute-catalog"])
        topic_command = "\n".join(topic_init["command"])
        self.assertIn("PROPERTY_CATALOG_ACK_LEGACY_SPAN_ATTRIBUTE_ONLY", topic_command)
        self.assertIn("property-catalog.dev.span-attribute-catalog.v1", topic_command)
        self.assertNotIn("futureagi.dev.property-catalog", topic_command)


class HostPreflightTests(unittest.TestCase):
    @staticmethod
    def _env(path: Path, values: dict[str, str]) -> None:
        path.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _host_fixture(self, directory: Path) -> workload.DeploymentConfig:
        with tempfile.TemporaryDirectory() as config_directory:
            config = workload.load_config(_write_config(Path(config_directory)))
        root = directory / "property-catalog-review-a"
        root.mkdir(mode=0o700)
        (root / "private").mkdir(mode=0o700)
        (root / "runtime").mkdir(mode=0o770)
        (root / "runtime").chmod(0o770)
        for child in ("cache", "home", "span-dead-letter"):
            (root / "runtime" / child).mkdir(mode=0o770)
            (root / "runtime" / child).chmod(0o770)
        (root / "runtime" / "catalog-spool").mkdir(mode=0o700)
        (root / "runtime" / "catalog-spool").chmod(0o700)
        private = root / "private"
        self._env(
            private / "producer.env",
            {
                "FI_PG_WRITE": "postgres://user:pg_secret@pgbouncer:6432/tfc",
                "FI_CH_USERNAME": "source_reader",
                "FI_CH_PASSWORD": "source_secret",
            },
        )
        self._env(
            private / "operator-runtime.env",
            {"SECRET_KEY": "isolated-test-secret-key"},
        )
        self._env(
            private / "operator-postgres.env",
            {
                "PGBOUNCER_HOST": "pgbouncer",
                "PGBOUNCER_PORT": "6432",
                "PG_DB": "tfc",
                "PG_USER": "user",
                "PG_PASSWORD": "pg_secret",
            },
        )
        self._env(
            private / "operator-source-clickhouse.env",
            {"CH25_USER": "source_reader", "CH25_PASSWORD": "source_secret"},
        )
        self._env(
            private / "operator-target-clickhouse.env",
            {
                "PROPERTY_CATALOG_DEV_WRITE_CH_USER": "control_writer",
                "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": "control_secret",
            },
        )
        self._env(
            private / "consumer-write-clickhouse.env",
            {
                "FI_PROPERTY_CATALOG_CH_USERNAME": "consumer_writer",
                "FI_PROPERTY_CATALOG_CH_PASSWORD": "consumer_secret",
            },
        )
        self._env(
            private / "consumer-ledger-clickhouse.env",
            {
                "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "ledger_reader",
                "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "ledger_secret",
            },
        )
        return dataclasses.replace(config, host_root=os.fspath(root))

    def test_host_preflight_accepts_exact_physical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._host_fixture(Path(temporary))
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid()),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
            ):
                workload.validate_host(config, owner_uid=os.geteuid())

    def test_host_preflight_rejects_loose_env_without_leaking_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._host_fixture(Path(temporary))
            secret = "never-print-this-secret"
            path = config.private_directory / "consumer-write-clickhouse.env"
            self._env(
                path,
                {
                    "FI_PROPERTY_CATALOG_CH_USERNAME": "consumer_writer",
                    "FI_PROPERTY_CATALOG_CH_PASSWORD": secret,
                },
            )
            path.chmod(0o644)
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid()),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
                self.assertRaises(workload.DeploymentValidationError) as caught,
            ):
                workload.validate_host(config, owner_uid=os.geteuid())
            self.assertNotIn(secret, str(caught.exception))

    def test_host_preflight_rejects_identity_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._host_fixture(Path(temporary))
            private = config.private_directory
            self._env(
                private / "consumer-ledger-clickhouse.env",
                {
                    "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "consumer_writer",
                    "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "ledger_secret",
                },
            )
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid()),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
                self.assertRaisesRegex(
                    workload.DeploymentValidationError, "distinct roles"
                ),
            ):
                workload.validate_host(config, owner_uid=os.geteuid())

    def test_host_preflight_rejects_spool_owner_or_mode_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._host_fixture(Path(temporary))
            spool = config.runtime_directory / "catalog-spool"
            spool.chmod(0o770)
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid()),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
                self.assertRaisesRegex(workload.DeploymentValidationError, "mode=0700"),
            ):
                workload.validate_host(config, owner_uid=os.geteuid())
            spool.chmod(0o700)
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid() + 1),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
                self.assertRaisesRegex(workload.DeploymentValidationError, "owner="),
            ):
                workload.validate_host(config, owner_uid=os.geteuid())

    def test_host_preflight_rejects_missing_or_unsafe_operator_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._host_fixture(Path(temporary))
            runtime_env = config.private_directory / "operator-runtime.env"
            runtime_env.unlink()
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid()),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
                self.assertRaisesRegex(
                    workload.DeploymentValidationError, "required host file"
                ),
            ):
                workload.validate_host(config, owner_uid=os.geteuid())
            self._env(runtime_env, {"SECRET_KEY": "REPLACE_WITH_SECRET"})
            with (
                mock.patch.object(workload, "RUNTIME_UID", os.geteuid()),
                mock.patch.object(workload, "RUNTIME_GID", os.getegid()),
                self.assertRaisesRegex(
                    workload.DeploymentValidationError, "unsafe value"
                ),
            ):
                workload.validate_host(config, owner_uid=os.geteuid())


if __name__ == "__main__":
    unittest.main()
