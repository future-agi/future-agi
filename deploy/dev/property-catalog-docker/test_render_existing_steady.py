from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_DIRECTORY = Path(__file__).parent


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _DIRECTORY / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _module("property_catalog_docker_bootstrap", "render.py")
steady = _module("property_catalog_docker_steady", "render_existing_steady.py")
provision = _module(
    "property_catalog_docker_provision_existing", "provision_existing_steady.py"
)
one_shot_provision = _module(
    "property_catalog_docker_provision_one_shot", "provision_0816d.py"
)


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _config() -> bootstrap.DeploymentConfig:
    return bootstrap.DeploymentConfig(
        deployment_id="kartik-review-a",
        collector_image=f"sha256:{_digest('collector')}",
        operator_image="fi-property-catalog-current-select:review-a",
        host_root="/home/ubuntu/property-catalog-kartik-review-a",
        organization_id="11111111-1111-4111-8111-111111111111",
        workspace_id="22222222-2222-4222-8222-222222222222",
        project_ids=("33333333-3333-4333-8333-333333333333",),
        epoch=1,
        projection_version=1,
        hot_producer_stream_id="44444444-4444-4444-8444-444444444444",
        source_database="futureagi",
        target_database="property_catalog_dev_kartik_review_a",
        span_since="2025-08-15T10:00:00Z",
        span_until="2026-08-15T10:00:00Z",
        dev_identity="dev:property-catalog/kartik-review-a",
        application_docker_network="futureagi_default",
        kafka_docker_network="property-catalog-dev",
        source_clickhouse_host="clickhouse",
        source_clickhouse_native_port=9000,
        source_clickhouse_http_port=8123,
        target_clickhouse_host="clickhouse",
        target_clickhouse_native_port=9000,
        target_clickhouse_http_port=8123,
        kafka_brokers=("property-catalog-kafka-dev:9092",),
        write_clickhouse_hostname="clickhouse-dev",
        source_clickhouse_hostname="clickhouse-dev",
        postgres_database="tfc",
        postgres_user="catalog_reader",
        postgres_server_address="172.19.0.7",
        postgres_server_port=5432,
    )


class ExistingSteadyRendererTests(unittest.TestCase):
    def test_adds_one_dedicated_worker_and_opt_in_registrar(self) -> None:
        base = bootstrap.render_compose(_config())
        digest = _digest("active bootstrap")
        overlay = steady.render_overlay(base, activation_sha256=digest)
        self.assertEqual(
            list(overlay["services"]),
            ["property-catalog-control", "property-catalog-registrar"],
        )
        control = overlay["services"]["property-catalog-control"]
        registrar = overlay["services"]["property-catalog-registrar"]
        task_queue = "property_catalog_dev_sidecar_22222222222242228222222222222222"
        self.assertEqual(
            control["entrypoint"],
            ["python", "manage.py", "start_temporal_worker"],
        )
        self.assertEqual(control["restart"], "unless-stopped")
        self.assertNotIn("profiles", control)
        self.assertEqual(registrar["profiles"], ["registrar"])
        self.assertEqual(registrar["restart"], "no")
        for service in (control, registrar):
            environment = service["environment"]
            self.assertEqual(
                environment["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"], "true"
            )
            self.assertEqual(
                environment["PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS"],
                "1200000",
            )
            self.assertEqual(environment["PROPERTY_CATALOG_DEV_TASK_QUEUE"], task_queue)
            self.assertEqual(environment["TEMPORAL_TASK_QUEUE"], task_queue)
            self.assertEqual(
                environment["PROPERTY_CATALOG_DEV_BOOTSTRAP_ACTIVATION_SHA256"],
                digest,
            )
            self.assertEqual(environment["PROPERTY_CATALOG_READ_MODE"], "off")
            self.assertEqual(
                environment["PROPERTY_CATALOG_DEV_OTLP_TRAFFIC_AUTHORIZED"],
                "false",
            )
            self.assertNotIn("ports", service)
            self.assertNotIn("expose", service)
        self.assertEqual(control["command"][1], task_queue)

    def test_oss_profile_preserves_unset_cloud_in_steady_state(self) -> None:
        config = dataclasses.replace(
            _config(),
            operator_image="fi-property-catalog-oss-current-select:review-a",
            runtime_profile="oss",
        )
        base = bootstrap.render_compose(config)
        digest = _digest("active oss bootstrap")
        overlay = steady.render_overlay(base, activation_sha256=digest)
        for service in overlay["services"].values():
            environment = service["environment"]
            self.assertEqual(environment["CLOUD_DEPLOYMENT"], "")
            self.assertEqual(environment["PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT"], "")

    def test_accepts_safe_legacy_target_database_name(self) -> None:
        config = dataclasses.replace(
            _config(),
            target_database="th7247_catalog_dev_kartik_0817j",
        )
        base = bootstrap.render_compose(config)
        overlay = steady.render_overlay(
            base,
            activation_sha256=_digest("legacy database bootstrap"),
        )
        for service in overlay["services"].values():
            self.assertEqual(
                service["environment"]["PROPERTY_CATALOG_DEV_TARGET_DATABASE"],
                "th7247_catalog_dev_kartik_0817j",
            )

    def test_rejects_placeholder_or_invalid_activation(self) -> None:
        base = bootstrap.render_compose(_config())
        for value in ("abc", "0" * 64):
            with (
                self.subTest(value=value),
                self.assertRaises(steady.SteadyStateRenderError),
            ):
                steady.render_overlay(base, activation_sha256=value)

        unsafe_target = copy.deepcopy(base)
        unsafe_target["services"]["property-catalog-operator"]["environment"][
            "PROPERTY_CATALOG_DEV_TARGET_DATABASE"
        ] = "property_catalog"
        with self.assertRaisesRegex(
            steady.SteadyStateRenderError,
            "bootstrap operator safety contract drifted",
        ):
            steady.render_overlay(
                unsafe_target,
                activation_sha256=_digest("unsafe production target"),
            )

    def test_validator_rejects_public_routing_and_concurrency_drift(self) -> None:
        base = bootstrap.render_compose(_config())
        digest = _digest("active bootstrap")
        overlay = steady.render_overlay(base, activation_sha256=digest)
        operator = base["services"]["property-catalog-operator"]
        routed = copy.deepcopy(overlay)
        routed["services"]["property-catalog-control"]["ports"] = ["7233:7233"]
        with self.assertRaisesRegex(steady.SteadyStateRenderError, "routing"):
            steady.validate_overlay(routed, operator=operator, activation_sha256=digest)
        widened = copy.deepcopy(overlay)
        widened["services"]["property-catalog-control"]["command"][3] = "2"
        with self.assertRaisesRegex(steady.SteadyStateRenderError, "worker"):
            steady.validate_overlay(
                widened, operator=operator, activation_sha256=digest
            )


class ExistingSteadyProvisionerTests(unittest.TestCase):
    def test_one_shot_provisioner_accepts_safe_legacy_database_name(self) -> None:
        self.assertEqual(
            one_shot_provision._catalog_database("th7247_catalog_dev_kartik_0817j"),
            "th7247_catalog_dev_kartik_0817j",
        )
        for unsafe in ("property_catalog", "futureagi", "Property-Catalog-Dev"):
            with (
                self.subTest(unsafe=unsafe),
                self.assertRaises(RuntimeError),
            ):
                one_shot_provision._catalog_database(unsafe)

    def test_settings_accept_safe_legacy_target_database_name(self) -> None:
        suffix, _, target, _ = provision._settings(
            "0817j",
            target_database="th7247_catalog_dev_kartik_0817j",
        )
        self.assertEqual(suffix, "0817j")
        self.assertEqual(target, "th7247_catalog_dev_kartik_0817j")

        for unsafe in ("property_catalog", "futureagi", "Property-Catalog-Dev"):
            with (
                self.subTest(unsafe=unsafe),
                self.assertRaises(provision.ProvisioningError),
            ):
                provision._settings("0817j", target_database=unsafe)

    def test_preflight_rejects_incomplete_lifecycle_reservation(self) -> None:
        activation_sha256 = _digest("active bootstrap")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "private").mkdir()
            (root / "compose.yaml").touch()

            def chq(sql: str) -> str:
                if "system.databases" in sql:
                    return "1\n"
                if "system.tables" in sql:
                    return "\n".join(
                        f'{{"name":"{name}","engine":"ReplacingMergeTree"}}'
                        for name in provision.EXPECTED_TABLES
                    )
                if "property_catalog_activations" in sql:
                    return (
                        f'{{"activation_sha256":"{activation_sha256}",'
                        '"status":"active","catalog_epoch":5,'
                        '"catalog_revision":1}'
                    )
                if "property_catalog_source_streams" in sql:
                    return "1\n"
                raise AssertionError(sql)

            users = {
                "source": "source",
                "control": "control",
                "consumer": "consumer",
                "ledger": "ledger",
                "postgres": "postgres",
            }
            with (
                patch.object(provision, "_chq", side_effect=chq),
                patch.object(provision, "_pgq") as pgq,
                self.assertRaisesRegex(
                    provision.ProvisioningError, "incomplete lifecycle reservation"
                ),
            ):
                provision._preflight(
                    root=root,
                    target="property_catalog_dev_kartik_test",
                    users=users,
                    activation_sha256=activation_sha256,
                )
            pgq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
