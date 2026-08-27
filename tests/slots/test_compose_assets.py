"""Static contract tests for slot Compose and Traefik assets.

These tests parse files only.  They deliberately never invoke Docker or a
Compose binary, so the Wave 1 runtime approval gate remains intact.
"""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SLOTS = ROOT / "slots"
COMPOSE = SLOTS / "compose"


def load(relative: str) -> dict:
    with (SLOTS / relative).open(encoding="utf-8") as source:
        return yaml.safe_load(source)


def test_compose_and_traefik_assets_are_parseable() -> None:
    paths = sorted(COMPOSE.glob("*.yaml"))
    paths += sorted((SLOTS / "config").glob("*.yaml"))
    paths += sorted((SLOTS / "traefik").glob("*.yaml"))

    assert paths
    for path in paths:
        with path.open(encoding="utf-8") as source:
            assert yaml.safe_load(source) is not None, path


def test_control_plane_is_shared_external_and_safe_for_local_use() -> None:
    control = load("compose/control-plane.yaml")
    services = control["services"]

    assert {
        "traefik",
        "postgres",
        "clickhouse",
        "redis",
        "rabbitmq",
        "minio",
        "temporal",
    } <= set(services)
    assert services["redis"]["command"][-2:] == ["--databases", "64"]
    assert control["networks"]["slots"]["external"] is True
    assert control["networks"]["slots"]["name"] == "${SLOTS_NETWORK:-futureagi-slots}"
    assert all(volume.get("name") for volume in control["volumes"].values())
    assert services["traefik"]["ports"] == ["127.0.0.1:${SLOTS_HTTP_PORT:-80}:80"]
    clickhouse_mounts = services["clickhouse"]["volumes"]
    assert any("SLOTS_CLICKHOUSE_TEST_CONFIG" in mount for mount in clickhouse_mounts)

    joined = "\n".join((COMPOSE / "control-plane.yaml").read_text().splitlines())
    assert "/var/run/docker.sock" not in joined
    assert "docker.sock" not in joined


def test_private_frontend_backend_and_provider_templates_are_present() -> None:
    expected = {
        "frontend.yaml": "frontend",
        "backend.yaml": "backend",
        "simulation.yaml": "simulation",
        "gateway.yaml": "gateway",
        "collector.yaml": "collector",
        "serving.yaml": "serving",
        "executor.yaml": "executor",
        "peerdb.yaml": "peerdb-ui",
        "observability.yaml": "observability",
    }
    for filename, service in expected.items():
        document = yaml.safe_load((COMPOSE / filename).read_text())
        assert service in document["services"]
        assert document["networks"]["slots"]["external"] is True

    frontend = load("compose/frontend.yaml")["services"]["frontend"]
    assert frontend["build"]["target"] == "dev"
    assert "VITE_HOST_API" in frontend["environment"]
    assert "SLOTS_HTTP_PORT" in frontend["environment"]["VITE_HOST_API"]
    assert frontend["volumes"][-1].endswith(":/app/node_modules")
    frontend_command = frontend["command"]
    assert frontend_command[:2] == ["/bin/sh", "-ec"]
    assert "yarn install --frozen-lockfile" in frontend_command[2]
    assert ".slot-lock-hash" in frontend_command[2]

    peerdb = load("compose/peerdb.yaml")["services"]
    assert {
        "peerdb",
        "peerdb-temporal-init",
        "peerdb-minio-init",
        "peerdb-init",
    } <= set(peerdb)
    assert peerdb["peerdb-init"]["environment"]["SRC_PG_DB"] == (
        "${SLOT_PG_DB:?SLOT_PG_DB is required}"
    )
    assert peerdb["peerdb-init"]["environment"]["DST_CH_DB"] == (
        "${SLOT_CH_DATABASE:?SLOT_CH_DATABASE is required}"
    )
    assert load("compose/backend.yaml")["volumes"]["slot-backend-media"][
        "name"
    ].startswith("${SLOT_BACKEND_MEDIA_VOLUME:-")
    assert load("compose/collector.yaml")["volumes"]["slot-collector-data"][
        "name"
    ].startswith("${SLOT_COLLECTOR_VOLUME:-")
    assert peerdb["peerdb-catalog"]["volumes"] == [
        "slot-peerdb-catalog-data:/var/lib/postgresql/data"
    ]
    assert peerdb["peerdb-minio"]["volumes"] == ["slot-peerdb-minio-data:/data"]
    assert peerdb["peerdb"]["depends_on"]["peerdb-init"] == {
        "condition": "service_completed_successfully"
    }
    assert peerdb["peerdb"]["depends_on"]["peerdb-minio-init"] == {
        "condition": "service_completed_successfully"
    }
    assert peerdb["peerdb-flow-api"]["depends_on"]["peerdb-minio-init"] == {
        "condition": "service_completed_successfully"
    }
    assert peerdb["peerdb-flow-worker"]["depends_on"]["peerdb-minio-init"] == {
        "condition": "service_completed_successfully"
    }
    minio_init_command = peerdb["peerdb-minio-init"]["command"]
    assert len(minio_init_command) == 1
    assert "mc mb --ignore-existing" in minio_init_command[0]
    temporal_init_command = peerdb["peerdb-temporal-init"]["command"]
    assert len(temporal_init_command) == 1
    temporal_init = temporal_init_command[0]
    assert "|| true" not in temporal_init
    assert "already exists" in temporal_init
    assert "search-attribute list" in temporal_init
    peerdb_identity_keys = {
        "peerdb": "SLOT_PEERDB_AGGREGATE_NAME",
        "peerdb-catalog": "SLOT_PEERDB_CATALOG_NAME",
        "peerdb-temporal": "SLOT_PEERDB_TEMPORAL_NAME",
        "peerdb-temporal-init": "SLOT_PEERDB_TEMPORAL_INIT_NAME",
        "peerdb-minio": "SLOT_PEERDB_MINIO_NAME",
        "peerdb-minio-init": "SLOT_PEERDB_MINIO_INIT_NAME",
        "peerdb-flow-api": "SLOT_PEERDB_FLOW_API_NAME",
        "peerdb-flow-worker": "SLOT_PEERDB_FLOW_WORKER_NAME",
        "peerdb-server": "SLOT_PEERDB_SERVER_NAME",
        "peerdb-ui": "SLOT_PEERDB_UI_NAME",
        "peerdb-init": "SLOT_PEERDB_INIT_NAME",
    }
    for service, key in peerdb_identity_keys.items():
        assert peerdb[service]["container_name"].startswith(f"${{{key}")

    backend = load("compose/backend.yaml")["services"]
    assert {"backend", "worker"} <= set(backend)
    assert backend["backend"]["environment"]["FAST_STARTUP"] == "false"
    assert backend["worker"]["environment"]["FAST_STARTUP"] == "true"
    assert backend["worker"]["environment"]["TEMPORAL_ALL_QUEUES"] == "true"
    state = backend["backend"]["environment"]
    assert state["CH_PORT"] == "9000"
    assert state["CH_HTTP_PORT"] == "8123"
    assert (
        state["UPLOAD_BUCKET_NAME"]
        == "${SLOT_MINIO_BUCKET:?SLOT_MINIO_BUCKET is required}"
    )
    assert "SLOTS_HTTP_PORT" in state["MINIO_URL"]
    assert state["REGISTER_TEMPORAL_SCHEDULES"] == (
        "${SLOT_REGISTER_TEMPORAL_SCHEDULES:-false}"
    )

    simulation = load("compose/simulation.yaml")["services"]["simulation"][
        "environment"
    ]
    for key in (
        "PG_HOST",
        "PG_DB",
        "CH_HOST",
        "CH_PORT",
        "CH_HTTP_PORT",
        "CH_DATABASE",
        "REDIS_URL",
        "REDIS_CACHE_URL",
        "REDIS_LOCK_URL",
        "CELERY_BROKER_URL",
        "S3_ENDPOINT_URL",
        "S3_BUCKET",
        "UPLOAD_BUCKET_NAME",
        "TEMPORAL_HOST",
        "TEMPORAL_NAMESPACE",
    ):
        assert key in simulation
    assert simulation["CH_PORT"] == "9000"

    gateway = load("compose/gateway.yaml")["services"]["gateway"]
    assert gateway["healthcheck"] == {"disable": True}
    assert (
        gateway["environment"]["AGENTCC_REDIS_DB"]
        == "${SLOT_REDIS_DB:?SLOT_REDIS_DB is required}"
    )
    assert not any(volume.endswith("/app:ro") for volume in gateway["volumes"])

    collector_text = (COMPOSE / "collector.yaml").read_text()
    assert (
        load("compose/collector.yaml")["services"]["collector"]["environment"][
            "FI_AUTH_REDIS_DB"
        ]
        == "${SLOT_REDIS_DB:?SLOT_REDIS_DB is required}"
    )
    assert "provider-bound database number" in collector_text
    assert (
        "--healthcheck"
        in load("compose/collector.yaml")["services"]["collector"]["healthcheck"][
            "test"
        ]
    )
    assert (
        load("compose/observability.yaml")["services"]["observability"]["image"]
        == "jaegertracing/all-in-one:1.65.0"
    )
    assert peerdb["peerdb-temporal-init"]["image"] == ("temporalio/admin-tools:1.29.6")


def test_dynamic_routes_use_file_provider_tokens_and_cover_public_hosts() -> None:
    static = load("traefik/traefik.yaml")
    template = load("traefik/routes.template.yaml")
    routes = template["http"]["routers"]

    assert static["providers"]["file"] == {
        "directory": "/etc/traefik/dynamic",
        "watch": True,
    }
    assert static["ping"] == {}
    assert {
        "frontend",
        "backend",
        "temporal",
        "minio",
        "console",
        "rabbitmq",
        "gateway",
        "serving",
        "collector",
        "executor",
        "jaeger",
        "peerdb",
    } <= {name.rsplit("-", 1)[-1] for name in routes}
    content = (SLOTS / "traefik" / "routes.template.yaml").read_text()
    assert "__SLOT_ID__" in content and "__FRONTEND_NAME__" in content
    assert "traefik.http." not in content
    assert "flower" not in content
    services = template["http"]["services"]
    assert services["slot-__SLOT_ID__-minio-api"]["loadBalancer"]["servers"] == [
        {"url": "http://__MINIO_NAME__:9000"}
    ]
    assert services["slot-__SLOT_ID__-minio-console"]["loadBalancer"]["servers"] == [
        {"url": "http://__MINIO_NAME__:9001"}
    ]
    assert services["slot-__SLOT_ID__-jaeger"]["loadBalancer"]["servers"] == [
        {"url": "http://__OBSERVABILITY_NAME__:16686"}
    ]
    assert all(name.startswith("slot-__SLOT_ID__-") for name in services)
    assert all(
        router["service"].startswith("slot-__SLOT_ID__-") for router in routes.values()
    )

    # Every rendered file owns its service namespace, so loading two slot route
    # files through Traefik's file provider cannot overwrite another slot.
    rendered = []
    for slot_id, slot in (("01", "1"), ("02", "2")):
        contents = content.replace("__SLOT_ID__", slot_id).replace("__SLOT__", slot)
        rendered.append(set(yaml.safe_load(contents)["http"]["services"]))
    assert rendered[0].isdisjoint(rendered[1])


def test_isolated_infra_defaults_match_state_and_initialize_provider_assets() -> None:
    isolated = load("compose/isolated-infra.yaml")
    services = isolated["services"]
    volumes = isolated["volumes"]
    assert services["isolated-temporal-init"]["image"] == (
        "temporalio/admin-tools:1.29.6"
    )
    assert any(
        "SLOTS_CLICKHOUSE_TEST_CONFIG" in mount
        for mount in services["isolated-clickhouse"]["volumes"]
    )

    for engine in ("postgres", "clickhouse", "redis", "rabbitmq", "minio", "temporal"):
        assert volumes[f"slot-{engine}-data"]["name"] == (
            f"${{SLOT_{engine.upper()}_VOLUME:-futureagi_slot_"
            f"${{SLOT_ID:?SLOT_ID is required}}_{engine}_data}}"
        )
    assert services["isolated-rabbitmq"]["environment"]["RABBITMQ_DEFAULT_VHOST"] == (
        "${SLOT_RABBITMQ_VHOST:?SLOT_RABBITMQ_VHOST is required}"
    )
    assert services["isolated-minio"]["command"] == [
        "server",
        "/data",
        "--console-address",
        ":9001",
    ]
    assert services["isolated-minio"]["healthcheck"]["test"] == [
        "CMD",
        "mc",
        "ready",
        "http://127.0.0.1:9000",
    ]
    minio_init = services["isolated-minio-init"]
    assert minio_init["profiles"] == ["isolated-minio"]
    assert len(minio_init["command"]) == 1
    assert "mc mb --ignore-existing" in minio_init["command"][0]
    assert "SLOT_MINIO_BUCKET" in minio_init["command"][0]
    assert minio_init["depends_on"] == {
        "isolated-minio": {"condition": "service_healthy"}
    }
    assert {
        "isolated-temporal-postgres",
        "isolated-temporal",
        "isolated-temporal-init",
        "isolated-temporal-ui",
    } <= set(services)
    assert services["isolated-temporal-init"]["profiles"] == ["isolated-temporal"]
    assert len(services["isolated-temporal-init"]["command"]) == 1
    assert "SLOT_TEMPORAL_NAMESPACE" in services["isolated-temporal-init"]["command"][0]
    assert services["isolated-temporal-ui"]["profiles"] == ["isolated-temporal"]
    assert services["isolated-temporal-postgres"]["volumes"] == [
        "slot-temporal-data:/var/lib/postgresql/data"
    ]
    assert all(
        port.startswith("127.0.0.1:")
        for service in services.values()
        for port in service.get("ports", [])
    )


def test_catalog_connects_template_names_to_provider_and_route_contracts() -> None:
    catalog = load("config/compose-catalog.yaml")

    assert catalog["provider_groups"]["backend"] == ["backend", "worker"]
    assert catalog["provider_groups"]["peerdb"] == [
        "peerdb",
        "peerdb-catalog",
        "peerdb-temporal",
        "peerdb-temporal-init",
        "peerdb-minio",
        "peerdb-minio-init",
        "peerdb-flow-api",
        "peerdb-flow-worker",
        "peerdb-server",
        "peerdb-ui",
        "peerdb-init",
    ]
    overrides = catalog["shared_provider_overrides"]
    assert overrides["backend"]["SLOT_BACKEND_MEDIA_VOLUME"] == (
        "futureagi-shared-backend-media"
    )
    assert overrides["collector"]["SLOT_COLLECTOR_VOLUME"] == (
        "futureagi-shared-collector-data"
    )
    assert set(overrides["peerdb"]) == {
        "SLOT_PEERDB_AGGREGATE_NAME",
        "SLOT_PEERDB_CATALOG_NAME",
        "SLOT_PEERDB_TEMPORAL_NAME",
        "SLOT_PEERDB_TEMPORAL_INIT_NAME",
        "SLOT_PEERDB_MINIO_NAME",
        "SLOT_PEERDB_MINIO_INIT_NAME",
        "SLOT_PEERDB_FLOW_API_NAME",
        "SLOT_PEERDB_FLOW_WORKER_NAME",
        "SLOT_PEERDB_SERVER_NAME",
        "SLOT_PEERDB_UI_NAME",
        "SLOT_PEERDB_INIT_NAME",
        "SLOT_PEERDB_CATALOG_VOLUME",
        "SLOT_PEERDB_MINIO_VOLUME",
    }
    assert catalog["state_coupled_provider_closure"]["backend"] == [
        "backend",
        "worker",
    ]
    for group in ("simulation", "collector", "peerdb"):
        assert catalog["state_coupled_provider_closure"][group]["requires"] == [
            "backend"
        ]
    assert catalog["teardown_contract"]["ordinary_down"] == (
        "docker compose down without --volumes; provider data is retained"
    )
    assert "frontend" not in catalog["provider_groups"]["all"]
    assert catalog["environment"]["slot"]["SLOT_PG_DB"] == "required"
    assert catalog["environment"]["slot"]["SLOT_TEMPORAL_NAMESPACE"] == "required"
    assert catalog["routes"]["backend"]["host"] == "api.${slot}.localhost"
    assert catalog["routes"]["minio"]["port"] == 9000
    assert catalog["routes"]["minio_console"]["port"] == 9001
    assert catalog["routes"]["jaeger"] == {
        "host": "jaeger.${slot}.localhost",
        "target_env": "SLOT_OBSERVABILITY_NAME",
        "port": 16686,
    }
    assert "flower" not in catalog["routes"]
    assert catalog["public_urls"]["port_env"] == "SLOTS_HTTP_PORT"
    assert (
        catalog["isolated_infra"]["volumes"]["temporal"]
        == "futureagi_slot_${slot_id}_temporal_data"
    )
    requirements = catalog["runtime_requirements"]
    assert requirements["completion_contracts"]["peerdb"] == {
        "aggregate_service": "peerdb",
        "initializers": [
            "peerdb-temporal-init",
            "peerdb-minio-init",
            "peerdb-init",
        ],
        "success_condition": "service_completed_successfully",
        "startup_gate": "compose_up_dependency_conditions",
    }
    assert requirements["route_target_selection"]["isolated"] == {
        "minio": "SLOT_ISOLATED_MINIO_NAME",
        "rabbitmq": "SLOT_ISOLATED_RABBITMQ_NAME",
        "temporal": "SLOT_TEMPORAL_UI_NAME",
    }
    assert any(
        item.startswith("Start isolated-temporal-init")
        for item in requirements["isolated_compose"]
    )
    assert catalog["isolated_infra"]["startup_services"]["temporal"] == [
        "isolated-temporal-postgres",
        "isolated-temporal",
        "isolated-temporal-init",
        "isolated-temporal-ui",
    ]
    assert catalog["isolated_infra"]["startup_services"]["minio"] == [
        "isolated-minio",
        "isolated-minio-init",
    ]
    assert any(
        item.startswith("Traefik only reads generated file-provider routes")
        for item in catalog["invariants"]
    )
