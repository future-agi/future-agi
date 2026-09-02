from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

_DIRECTORY = Path(__file__).parent


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _DIRECTORY / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _module("property_catalog_handoff_bootstrap", "render.py")
handoff = _module(
    "property_catalog_live_collector_handoff",
    "render_live_collector_handoff.py",
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


class LiveCollectorHandoffTests(unittest.TestCase):
    def test_copies_only_catalog_contract_to_live_collector(self) -> None:
        base = bootstrap.render_compose(_config())
        overlay = handoff.render_overlay(base)
        self.assertEqual(list(overlay["services"]), ["fi-collector"])
        service = overlay["services"]["fi-collector"]
        producer = base["services"]["property-catalog-producer"]
        self.assertEqual(service["image"], producer["image"])
        self.assertEqual(service["pull_policy"], "never")
        self.assertEqual(
            service["networks"],
            ["default", handoff.KAFKA_NETWORK_KEY],
        )
        self.assertEqual(
            set(service["environment"]),
            set(handoff._CATALOG_ENV_KEYS),
        )
        self.assertNotIn("FI_PG_WRITE", service["environment"])
        self.assertNotIn("FI_CH_PASSWORD", service["environment"])
        self.assertNotIn("ports", service)
        self.assertNotIn("expose", service)
        self.assertEqual(
            service["volumes"][0]["target"],
            "/var/lib/property-catalog-runtime",
        )
        self.assertEqual(
            overlay["networks"][handoff.KAFKA_NETWORK_KEY],
            {
                "external": True,
                "name": "property-catalog-dev",
            },
        )

    def test_rejects_routing_credentials_and_unpinned_image(self) -> None:
        base = bootstrap.render_compose(_config())
        producer = base["services"]["property-catalog-producer"]
        overlay = handoff.render_overlay(base)

        routed = copy.deepcopy(overlay)
        routed["services"]["fi-collector"]["ports"] = ["4318:4318"]
        with self.assertRaisesRegex(
            handoff.LiveCollectorHandoffError,
            "routing",
        ):
            handoff.validate_overlay(routed, producer=producer)

        credentialed = copy.deepcopy(overlay)
        credentialed["services"]["fi-collector"]["environment"][
            "FI_PG_WRITE"
        ] = "forbidden"
        with self.assertRaisesRegex(
            handoff.LiveCollectorHandoffError,
            "contract",
        ):
            handoff.validate_overlay(credentialed, producer=producer)

        unpinned = copy.deepcopy(base)
        unpinned["services"]["property-catalog-producer"]["image"] = "latest"
        with self.assertRaisesRegex(
            handoff.LiveCollectorHandoffError,
            "safety contract",
        ):
            handoff.render_overlay(unpinned)


if __name__ == "__main__":
    unittest.main()
