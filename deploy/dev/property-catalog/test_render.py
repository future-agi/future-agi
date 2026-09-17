from __future__ import annotations

import contextlib
import copy
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render as workload  # noqa: E402, I001


ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT_A = "33333333-3333-4333-8333-333333333333"
PROJECT_B = "77777777-7777-4777-8777-777777777777"
HOT_STREAM = "55555555-5555-4555-8555-555555555555"
BACKEND_IMAGE = (
    "registry.dev.futureagi.test/futureagi/backend@sha256:" + "0123456789abcdef" * 4
)
COLLECTOR_IMAGE = (
    "registry.dev.futureagi.test/futureagi/fi-collector@sha256:"
    + "fedcba9876543210" * 4
)
ACTIVATION_SHA256 = "89abcdef01234567" * 4


def valid_raw() -> dict[str, Any]:
    """Return the canonical, insertion-ordered renderer input."""

    return {
        "format": workload.FORMAT,
        "version": workload.VERSION,
        "namespace": "futureagi-dev",
        "images": {
            "backend": BACKEND_IMAGE,
            "collector": COLLECTOR_IMAGE,
            "consumer": COLLECTOR_IMAGE,
        },
        "workspaces": [
            {
                "organization_id": ORG,
                "workspace_id": WORKSPACE,
                "project_ids": [PROJECT_A, PROJECT_B],
            }
        ],
        "catalog": {
            "epoch": 1,
            "projection_version": 1,
            "hot_producer_stream_id": HOT_STREAM,
            "source_database": "futureagi",
            "target_database": "property_catalog_dev_workspace_a",
            "span_since": "2026-08-14T00:00:00Z",
            "span_until": "2026-08-15T00:00:00Z",
            "otlp_traffic_authorized": False,
            "dev_identity": "dev:property-catalog/reviewer-a",
        },
        "runtime": {
            "directory": workload.RUNTIME_DIRECTORY,
            "revision_fence_file": workload.REVISION_FENCE_FILE,
            "drain_proof_file": workload.DRAIN_PROOF_FILE,
            "producer_retirement_file": workload.PRODUCER_RETIREMENT_FILE,
        },
        "infrastructure": {
            "temporal_host": "temporal.dev.svc:7233",
            "temporal_namespace": "futureagi-dev",
            "source_clickhouse_host": "clickhouse-source.dev.svc",
            "source_clickhouse_native_port": 9000,
            "source_clickhouse_http_url": "http://clickhouse-source.dev.svc:8123",
            "target_clickhouse_host": "clickhouse-target.dev.svc",
            "target_clickhouse_native_port": 9000,
            "target_clickhouse_http_url": "http://clickhouse-target.dev.svc:8123",
            "kafka_brokers": ["kafka.dev.svc:9092"],
            "kafka_topic": "futureagi.dev.property-catalog.v1",
            "kafka_consumer_group": "futureagi.dev.property-catalog.consumer.v1",
        },
        "provenance": {
            "write_clickhouse_hostname": "clickhouse-write-dev-0",
            "source_clickhouse_hostname": "clickhouse-source-dev-0",
            "postgres_database": "futureagi_dev",
            "postgres_user": "property_catalog_dev_readonly",
            "postgres_server_address": "10.24.8.17",
            "postgres_server_port": 5432,
        },
        "storage": {
            "storage_class": "posix-rwo-dev",
            "size": "10Gi",
        },
        "secrets": {
            "backend_env": "property-catalog-dev-backend-env",
            "collector_env": "property-catalog-dev-collector-env",
            "source_read_clickhouse": "property-catalog-dev-source-read-ch",
            "control_write_clickhouse": "property-catalog-dev-control-write-ch",
            "consumer_write_clickhouse": "property-catalog-dev-consumer-write-ch",
            "consumer_ledger_clickhouse": "property-catalog-dev-consumer-ledger-ch",
            "image_pull": "property-catalog-dev-image-pull",
        },
    }


def resource(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    matches = [
        value
        for value in documents
        if value["kind"] == kind and value["metadata"]["name"] == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}/{name}, found {len(matches)}")
    return matches[0]


def container(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        value
        for value in deployment["spec"]["template"]["spec"]["containers"]
        if value["name"] == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one container {name}, found {len(matches)}")
    return matches[0]


def env_values(value: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"]: item["value"] for item in value.get("env", []) if "value" in item
    }


class RenderTests(unittest.TestCase):
    def test_default_is_one_workspace_schedule_off_and_no_overlap(self) -> None:
        config = workload.validate_config(valid_raw())
        documents = workload.render_documents(config)

        self.assertEqual(len(documents), 6)
        self.assertNotIn("Secret", {value["kind"] for value in documents})
        config_map = resource(documents, "ConfigMap", workload.CONFIG_MAP_NAME)
        self.assertEqual(
            config_map["data"]["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"],
            "false",
        )
        self.assertEqual(
            config_map["data"]["PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED"],
            "false",
        )
        self.assertEqual(config_map["data"]["SPAN_ATTRIBUTE_CATALOG_READ_MODE"], "off")
        self.assertEqual(
            config_map["data"]["SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED"],
            "false",
        )
        self.assertEqual(
            config_map["data"]["PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST"],
            WORKSPACE,
        )
        self.assertEqual(
            config_map["data"]["PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST"],
            f"{PROJECT_A},{PROJECT_B}",
        )
        self.assertEqual(
            config_map["data"]["PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE"],
            workload.PRODUCER_RETIREMENT_FILE,
        )
        self.assertEqual(
            {
                key: config_map["data"][key]
                for key in (
                    "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME",
                    "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME",
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE",
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER",
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS",
                    "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT",
                )
            },
            {
                "PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME": (
                    "clickhouse-write-dev-0"
                ),
                "PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME": (
                    "clickhouse-source-dev-0"
                ),
                "PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE": "futureagi_dev",
                "PROPERTY_CATALOG_DEV_EXPECTED_PG_USER": (
                    "property_catalog_dev_readonly"
                ),
                "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS": "10.24.8.17",
                "PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT": "5432",
            },
        )
        for name in (workload.WORKLOAD_NAME, workload.CONSUMER_NAME):
            deployment = resource(documents, "Deployment", name)
            self.assertEqual(deployment["spec"]["replicas"], 1)
            self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
            self.assertEqual(
                deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"],
                180,
            )

    def test_live_collector_and_worker_share_exact_runtime_contract(self) -> None:
        documents = workload.render_documents(workload.validate_config(valid_raw()))
        deployment = resource(documents, "Deployment", workload.WORKLOAD_NAME)
        pod_spec = deployment["spec"]["template"]["spec"]
        self.assertEqual(
            pod_spec["volumes"][0],
            {
                "name": "runtime",
                "persistentVolumeClaim": {"claimName": workload.PVC_NAME},
            },
        )
        expected_mount = [{"name": "runtime", "mountPath": workload.RUNTIME_DIRECTORY}]
        collector = container(deployment, "live-otlp-collector")
        control = container(deployment, "control-plane")
        self.assertEqual(collector["volumeMounts"], expected_mount)
        self.assertEqual(control["volumeMounts"][0], expected_mount[0])
        self.assertEqual(
            {value["mountPath"] for value in control["volumeMounts"][1:]},
            {"/tmp", "/app/backend/logs", "/app/backend/tfc/logs"},
        )
        self.assertEqual(collector["image"], COLLECTOR_IMAGE)
        self.assertEqual(collector["command"], ["/usr/local/bin/fi-collector"])
        self.assertEqual(
            control["command"],
            [
                "python",
                "manage.py",
                "start_temporal_worker",
                "--task-queue",
                workload.TASK_QUEUE,
                "--max-concurrent-activities",
                "1",
                "--max-concurrent-workflow-tasks",
                "1",
                "--graceful-timeout",
                "180",
            ],
        )
        values = env_values(collector)
        self.assertEqual(values["FI_CATALOG_MODE"], "disabled")
        self.assertEqual(values["FI_PROPERTY_CATALOG_MODE"], "kafka")
        self.assertEqual(values["FI_GRPC_ADDR"], "127.0.0.1:4317")
        self.assertEqual(values["FI_HTTP_ADDR"], "127.0.0.1:4318")
        collector_secret_refs = {
            item["name"]: item["valueFrom"]["secretKeyRef"]["name"]
            for item in collector["env"]
            if "valueFrom" in item
        }
        self.assertEqual(
            collector_secret_refs,
            {
                "FI_CH_USERNAME": "property-catalog-dev-source-read-ch",
                "FI_CH_PASSWORD": "property-catalog-dev-source-read-ch",
            },
        )
        self.assertEqual(
            values["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"],
            workload.REVISION_FENCE_FILE,
        )
        self.assertEqual(
            values["FI_PROPERTY_CATALOG_SPOOL_DIR"],
            workload.RUNTIME_DIRECTORY,
        )
        self.assertEqual(
            [value["containerPort"] for value in collector["ports"]],
            [4317, 4318, 9464],
        )

    def test_pods_are_hardened_without_unbounded_or_host_volumes(self) -> None:
        documents = workload.render_documents(workload.validate_config(valid_raw()))
        workload_deployment = resource(documents, "Deployment", workload.WORKLOAD_NAME)
        consumer_deployment = resource(documents, "Deployment", workload.CONSUMER_NAME)
        workload_pod = workload_deployment["spec"]["template"]["spec"]
        consumer_pod = consumer_deployment["spec"]["template"]["spec"]
        for pod in (workload_pod, consumer_pod):
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertEqual(
                pod["securityContext"]["seccompProfile"],
                {"type": "RuntimeDefault"},
            )
        volumes = workload_pod["volumes"]
        self.assertEqual(
            [value["name"] for value in volumes],
            ["runtime", "backend-tmp", "backend-logs", "backend-tfc-logs"],
        )
        self.assertEqual(sum("persistentVolumeClaim" in value for value in volumes), 1)
        self.assertFalse(any("hostPath" in value for value in volumes))
        for value in volumes[1:]:
            self.assertRegex(value["emptyDir"]["sizeLimit"], r"^[0-9]+Mi$")
        init = workload_pod["initContainers"]
        self.assertEqual(len(init), 1)
        self.assertEqual(init[0]["command"], ["python", "-c"])
        self.assertIn("os.chown(path, 65532, 65532", init[0]["args"][0])
        self.assertIn("os.chmod(path, 0o770)", init[0]["args"][0])
        self.assertEqual(
            init[0]["securityContext"],
            {
                "runAsUser": 0,
                "runAsGroup": 0,
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {
                    "drop": ["ALL"],
                    "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
                },
            },
        )
        for value in [
            *workload_pod["containers"],
            *consumer_pod["containers"],
        ]:
            security = value["securityContext"]
            self.assertTrue(security["runAsNonRoot"])
            self.assertFalse(security["allowPrivilegeEscalation"])
            self.assertTrue(security["readOnlyRootFilesystem"])
            self.assertEqual(security["capabilities"], {"drop": ["ALL"]})

    def test_consumer_uses_same_image_durable_command_and_distinct_secrets(
        self,
    ) -> None:
        documents = workload.render_documents(workload.validate_config(valid_raw()))
        deployment = resource(documents, "Deployment", workload.CONSUMER_NAME)
        consumer = container(deployment, "consumer")
        self.assertEqual(consumer["image"], COLLECTOR_IMAGE)
        self.assertEqual(
            consumer["command"],
            ["/usr/local/bin/fi-property-catalog-consumer"],
        )
        self.assertEqual(consumer["args"], ["--seed-from-delivery-ledger"])
        secret_names = {
            item["valueFrom"]["secretKeyRef"]["name"]
            for item in consumer["env"]
            if "valueFrom" in item
        }
        self.assertEqual(
            secret_names,
            {
                "property-catalog-dev-consumer-write-ch",
                "property-catalog-dev-consumer-ledger-ch",
            },
        )
        values = env_values(consumer)
        self.assertEqual(
            values["FI_PROPERTY_CATALOG_CH_URL"],
            values["FI_PROPERTY_CATALOG_LEDGER_CH_URL"],
        )
        self.assertEqual(
            values["FI_PROPERTY_CATALOG_CH_DATABASE"],
            values["FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE"],
        )

    def test_pvc_and_service_are_exact(self) -> None:
        documents = workload.render_documents(workload.validate_config(valid_raw()))
        pvc = resource(documents, "PersistentVolumeClaim", workload.PVC_NAME)
        self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(pvc["spec"]["volumeMode"], "Filesystem")
        service = resource(documents, "Service", workload.SERVICE_NAME)
        self.assertTrue(workload.SERVICE_NAME.endswith("-canary"))
        self.assertEqual(
            service["metadata"]["annotations"],
            {
                "futureagi.com/routing": "manual-canary-only",
                "futureagi.com/default-traffic": "disabled",
                "futureagi.com/current-phase": "no-otlp-traffic",
            },
        )
        self.assertEqual(
            service["spec"]["selector"]["futureagi.com/otlp-admission"],
            "separate-approval-required",
        )
        producer = resource(documents, "Deployment", workload.WORKLOAD_NAME)
        self.assertNotIn(
            "futureagi.com/otlp-admission",
            producer["spec"]["template"]["metadata"]["labels"],
        )
        self.assertEqual(
            [(value["name"], value["port"]) for value in service["spec"]["ports"]],
            [("otlp-grpc", 4317), ("otlp-http", 4318), ("admin", 9464)],
        )

    def test_schedule_enablement_requires_explicit_bootstrap_digest(self) -> None:
        config = workload.validate_config(valid_raw())
        with self.assertRaisesRegex(
            workload.WorkloadValidationError, "activation SHA-256"
        ):
            workload.render_documents(
                config, bootstrap_activation_sha256="not-a-digest"
            )
        with self.assertRaisesRegex(
            workload.WorkloadValidationError, "placeholder bootstrap"
        ):
            workload.render_documents(config, bootstrap_activation_sha256="0" * 64)
        documents = workload.render_documents(
            config, bootstrap_activation_sha256=ACTIVATION_SHA256
        )
        config_map = resource(documents, "ConfigMap", workload.CONFIG_MAP_NAME)
        self.assertEqual(
            config_map["data"]["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"],
            "true",
        )
        self.assertEqual(
            config_map["metadata"]["annotations"][
                "futureagi.com/bootstrap-activation-sha256"
            ],
            ACTIVATION_SHA256,
        )

    def test_yaml_round_trip_is_applyable_and_contains_no_secret_object(self) -> None:
        rendered = workload.render_yaml(workload.validate_config(valid_raw()))
        documents = list(yaml.safe_load_all(rendered))
        self.assertEqual(len(documents), 6)
        self.assertNotIn("kind: Secret\n", rendered)
        self.assertTrue(rendered.startswith("---\n"))

    def test_atomic_output_is_private(self) -> None:
        rendered = workload.render_yaml(workload.validate_config(valid_raw()))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.yaml"
            workload.write_rendered(output, rendered)
            self.assertEqual(output.read_text(encoding="utf-8"), rendered)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(
                [path.name for path in output.parent.iterdir()], ["manifest.yaml"]
            )

    def test_accepts_safe_legacy_target_database_name(self) -> None:
        raw = valid_raw()
        raw["catalog"]["target_database"] = "th7247_catalog_dev_kartik_0817j"
        config = workload.validate_config(raw)
        self.assertEqual(
            config.target_database,
            "th7247_catalog_dev_kartik_0817j",
        )

    def test_rejects_unsafe_operator_inputs(self) -> None:
        mutations: list[tuple[str, Any]] = [
            (
                "extra workspace",
                lambda raw: raw["workspaces"].append(
                    copy.deepcopy(raw["workspaces"][0])
                ),
            ),
            ("placeholder", lambda raw: raw.__setitem__("namespace", "replace-me-dev")),
            (
                "production namespace",
                lambda raw: raw.__setitem__("namespace", "futureagi-prod"),
            ),
            (
                "mismatched consumer image",
                lambda raw: raw["images"].__setitem__(
                    "consumer",
                    "registry.dev.futureagi.test/other@sha256:"
                    + "abcdef0123456789" * 4,
                ),
            ),
            (
                "tag-only image",
                lambda raw: raw["images"].__setitem__("backend", "backend:v1"),
            ),
            (
                "mismatched runtime path",
                lambda raw: raw["runtime"].__setitem__(
                    "directory", "/tmp/property-catalog"
                ),
            ),
            (
                "mismatched fence path",
                lambda raw: raw["runtime"].__setitem__(
                    "revision_fence_file", "/var/lib/property-catalog-runtime/old.json"
                ),
            ),
            (
                "mismatched proof path",
                lambda raw: raw["runtime"].__setitem__(
                    "drain_proof_file",
                    "/var/lib/property-catalog-runtime/producer-drain-proof-v1.json",
                ),
            ),
            (
                "mismatched producer retirement path",
                lambda raw: raw["runtime"].__setitem__(
                    "producer_retirement_file",
                    "/var/lib/property-catalog-runtime/producer-retirement.json",
                ),
            ),
            (
                "retired topic",
                lambda raw: raw["infrastructure"].__setitem__(
                    "kafka_topic", "futureagi.dev.span-attribute-catalog.v1"
                ),
            ),
            (
                "OTLP traffic authorization",
                lambda raw: raw["catalog"].__setitem__("otlp_traffic_authorized", True),
            ),
            (
                "production host",
                lambda raw: raw["infrastructure"].__setitem__(
                    "temporal_host", "temporal.prod.svc:7233"
                ),
            ),
            (
                "duplicate project",
                lambda raw: raw["workspaces"][0].__setitem__(
                    "project_ids", [PROJECT_A, PROJECT_A]
                ),
            ),
            (
                "unisolated target database",
                lambda raw: raw["catalog"].__setitem__("target_database", "futureagi"),
            ),
            (
                "production target database",
                lambda raw: raw["catalog"].__setitem__(
                    "target_database", "property_catalog"
                ),
            ),
            (
                "unsafe target database identifier",
                lambda raw: raw["catalog"].__setitem__(
                    "target_database", "Property-Catalog-Dev"
                ),
            ),
            (
                "production source database",
                lambda raw: raw["catalog"].__setitem__(
                    "source_database", "futureagi_prod"
                ),
            ),
            (
                "runtime-rejected live identity",
                lambda raw: raw["catalog"].__setitem__(
                    "dev_identity", "dev:delivery-worker"
                ),
            ),
            (
                "invalid HTTP port",
                lambda raw: raw["infrastructure"].__setitem__(
                    "target_clickhouse_http_url",
                    "http://clickhouse-target.dev.svc:99999",
                ),
            ),
            (
                "split ClickHouse endpoint",
                lambda raw: raw["infrastructure"].__setitem__(
                    "source_clickhouse_http_url",
                    "http://other-clickhouse.dev.svc:8123",
                ),
            ),
            (
                "production provenance",
                lambda raw: raw["provenance"].__setitem__(
                    "write_clickhouse_hostname", "clickhouse-prod-0"
                ),
            ),
            (
                "DNS PostgreSQL provenance",
                lambda raw: raw["provenance"].__setitem__(
                    "postgres_server_address", "postgres.dev.svc"
                ),
            ),
            (
                "noncanonical PostgreSQL provenance IP",
                lambda raw: raw["provenance"].__setitem__(
                    "postgres_server_address", "2001:0db8::1"
                ),
            ),
            (
                "generic backend Secret",
                lambda raw: raw["secrets"].__setitem__(
                    "backend_env", "core-backend-dev-secret"
                ),
            ),
            (
                "non-DEV property catalog Secret",
                lambda raw: raw["secrets"].__setitem__(
                    "collector_env", "property-catalog-collector-secret"
                ),
            ),
            (
                "shared writer and ledger identity",
                lambda raw: raw["secrets"].__setitem__(
                    "consumer_ledger_clickhouse",
                    raw["secrets"]["consumer_write_clickhouse"],
                ),
            ),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                raw = valid_raw()
                mutate(raw)
                with self.assertRaises(workload.WorkloadValidationError):
                    workload.validate_config(raw)

    def test_rejects_noncanonical_field_order_and_duplicate_yaml_keys(self) -> None:
        raw = valid_raw()
        raw["images"] = {
            "collector": COLLECTOR_IMAGE,
            "backend": BACKEND_IMAGE,
            "consumer": COLLECTOR_IMAGE,
        }
        with self.assertRaisesRegex(
            workload.WorkloadValidationError, "fields/order are not canonical"
        ):
            workload.validate_config(raw)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.yaml"
            path.write_text("format: one\nformat: two\n", encoding="utf-8")
            with self.assertRaisesRegex(
                workload.WorkloadValidationError, "duplicate key"
            ):
                workload._load_raw(path)

    def test_checked_in_example_is_inert_until_reviewed(self) -> None:
        example = Path(__file__).resolve().parent / "config.example.yaml"
        with self.assertRaisesRegex(workload.WorkloadValidationError, "placeholder"):
            workload.load_config(example)

    def test_cli_refuses_invalid_config_before_creating_output(self) -> None:
        raw = valid_raw()
        raw["namespace"] = "futureagi-production"
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "unsafe.yaml"
            output_path = Path(directory) / "manifest.yaml"
            config_path.write_text(
                yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = workload.main(
                    [
                        "--config",
                        os.fspath(config_path),
                        "--output",
                        os.fspath(output_path),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertFalse(output_path.exists())
            self.assertIn("workload rejected", stderr.getvalue())

    def test_consumer_binary_is_built_and_copied_by_collector_image(self) -> None:
        dockerfile = (
            Path(__file__).resolve().parents[3] / "fi-collector" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "-o /out/fi-property-catalog-consumer ./cmd/fi-property-catalog-consumer",
            dockerfile,
        )
        self.assertIn(
            "COPY --from=build /out/fi-property-catalog-consumer "
            "/usr/local/bin/fi-property-catalog-consumer",
            dockerfile,
        )

    def test_runbook_pins_scoped_schedule_and_bounded_ledger_grants(self) -> None:
        readme = (Path(__file__).resolve().parent / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "register_temporal_schedules --property-catalog-only",
            readme,
        )
        self.assertIn(
            "SELECT only on `property_catalog_source_streams`, "
            "`property_catalog_checkpoints`, `property_catalog_activations`, "
            "and `property_catalog_deliveries`",
            readme,
        )
        self.assertNotIn(
            "reads only `property_catalog_deliveries` and "
            "`property_catalog_source_streams`",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
